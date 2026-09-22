"""
Route-level tests.

These run the real ingest -> merge -> review -> revision paths with a fake
extractor, so they cover the wiring that the projections unit-test cannot: the
handoff from agent result to canonical graph, which is where the MVP was broken.
"""

import pytest

from core.knowledge import RevisionStore, graph_from_extraction
from tests.conftest import FakeExtractor, FakeResult


def store_for(app) -> RevisionStore:
    return RevisionStore(app.config["STORE_ROOT"]).ensure()


# ============================================================================
# Pages render
# ============================================================================


def test_every_page_renders_on_an_empty_working_set(client):
    for path in ["/", "/ingest", "/review", "/reconcile", "/c4", "/gaps", "/changes"]:
        response = client.get(path)
        assert response.status_code == 200, f"{path} -> {response.status_code}"


def test_viewpoint_and_gap_routes_exist(client):
    """The old nav linked to /graph and /gaps with no routes behind them (404)."""
    assert client.get("/c4").status_code == 200
    assert client.get("/gaps").status_code == 200


def test_the_old_graph_url_still_resolves(client):
    """`/graph` named the C4 diagram, which reads as "the knowledge graph" — the
    exact ambiguity the projection/viewpoint split removes. Kept as a redirect so
    nothing that linked to it breaks."""
    response = client.get("/graph?level=container")
    assert response.status_code == 301
    assert "/c4?level=container" in response.headers["Location"]


def test_c4_page_renders_for_architecture_data(client, load_working):
    """Exercises the populated branch of the C4 template, not just the empty state."""
    client.post("/ingest", data={"text": "architecture body", "type": "architecture"})

    response = client.get("/c4?level=container")
    assert response.status_code == 200
    assert b'id="c4"' in response.data
    assert b"Payment Orchestrator" in response.data
    assert b"container view" in response.data.lower()


def test_gap_report_page_shows_unresolved_references(seeded_client):
    response = seeded_client.get("/gaps")
    assert response.status_code == 200
    assert b"Unresolved cross-graph references" in response.data
    # Extraction is COMPLETE here (the fake reports 3 successful model calls),
    # but nothing has been reviewed, so the audit gate must still be shut.
    assert b"Not auditable yet" in response.data
    assert b"are outstanding" in response.data


def test_review_detail_partial_renders(seeded_client, load_working):
    target = next(iter(load_working().active()))
    response = seeded_client.get(f"/review/{target.id}", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert b"Decision history" in response.data or b"Corrected target" in response.data


def test_unknown_assertion_detail_is_a_404(client):
    assert client.get("/review/a_does_not_exist").status_code == 404


# ============================================================================
# Ingest
# ============================================================================


def test_ingest_produces_a_non_empty_graph(client, load_working):
    """Regression: the route passed `result.model_dump()` to ingest, so every
    ingest produced an empty graph while reporting success."""
    response = client.post("/ingest", data={"text": "requirements body", "type": "requirements"})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/review")

    graph = load_working()
    assert graph.nodes, "ingest produced no nodes"
    assert graph.assertions, "ingest produced no assertions"
    assert graph.runs, "ingest recorded no extraction run"


def test_the_envelope_would_have_produced_an_empty_graph(requirements_output):
    """Characterisation of the original bug, kept so the fix cannot regress.

    Ingest reads `triples`/`elements` from the top level. `model_dump()` nests
    the payload under `output`, so ingest finds nothing and reports success.
    """
    envelope = FakeResult(requirements_output).model_dump()
    graph, _run = graph_from_extraction(envelope)
    assert graph.nodes == {} and graph.assertions == {}

    payload_graph, _run = graph_from_extraction(envelope["output"])
    assert payload_graph.nodes and payload_graph.assertions


def test_ingest_keeps_the_document_text_so_the_run_is_traceable(client, load_working):
    client.post("/ingest", data={"text": "FR-PM-001 body text", "type": "requirements"})
    run = next(iter(load_working().runs.values()))
    assert run.document_chars > 0
    assert run.document_hash
    assert run.document_ref == "pasted-document.md"


def test_ingest_honours_the_document_type(client, load_working):
    """The old form read `doc_type` and then ignored it, always using the
    requirements extractor."""
    client.post("/ingest", data={"text": "architecture body", "type": "architecture"})
    kinds = {n.kind for n in load_working().nodes.values()}
    assert "SoftwareSystem" in kinds
    assert "Container" in kinds, f"architecture extractor did not run: {kinds}"


def test_ingest_requires_input(client, load_working):
    response = client.post(
        "/ingest", data={"text": "", "type": "requirements"}, follow_redirects=True
    )
    assert response.status_code == 200
    assert not load_working().nodes


def test_ingest_commits_a_revision(client, app):
    client.post("/ingest", data={"text": "body", "type": "requirements"})
    revisions = store_for(app).list_revisions()
    assert len(revisions) == 1
    assert "Ingest" in revisions[0].label


def test_ingest_surfaces_a_failed_extraction(app):
    from app import create_app

    class Failing:
        def run(self, _input):
            return FakeResult({}, success=False, errors=["model unreachable"])

    broken = create_app(
        {"TESTING": True, "STORE_ROOT": app.config["STORE_ROOT"]},
        store_root=app.config["STORE_ROOT"],
        extractor_factory=lambda _t: Failing(),
    )
    response = broken.test_client().post("/ingest", data={"text": "body"}, follow_redirects=True)
    assert b"model unreachable" in response.data


# ============================================================================
# Review
# ============================================================================


def test_review_lists_ingested_assertions(seeded_client):
    response = seeded_client.get("/review")
    assert response.status_code == 200
    assert b"Payment Gateway Platform" in response.data
    assert b"UNVERIFIED" in response.data


def test_review_table_is_inside_its_scroll_container(seeded_client):
    """The sticky column header is positioned relative to `.table-wrap`.

    That wrapper is the nearest scrolling ancestor, which is why the header uses
    `top: 0` rather than clearing the page topbar. If the table ever leaves the
    wrapper the header sticks to the page instead and covers the first rows.
    """
    html = seeded_client.get("/review").get_data(as_text=True)
    assert 'class="table-wrap"' in html, "the table lost its scroll container"
    assert html.index('class="table-wrap"') < html.index("<thead>")


def test_review_filters_are_applied(seeded_client):
    response = seeded_client.get("/review?q=zzz-nothing")
    assert b"No assertions match these filters" in response.data


def test_htmx_decision_returns_the_updated_row(seeded_client, load_working):
    target = next(iter(load_working().active()))
    response = seeded_client.post(
        f"/review/{target.id}/decision",
        data={"action": "verify", "actor": "tester"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert b"VERIFIED" in response.data
    assert b"Reopen" in response.data

    # The decision must be persisted, not merely rendered.
    assert load_working().assertions[target.id].status == "VERIFIED"


def test_full_page_decision_redirects_and_records(seeded_client, load_working):
    target = next(iter(load_working().active()))
    response = seeded_client.post(
        f"/review/{target.id}/decision", data={"action": "dispute", "actor": "tester", "note": "no"}
    )
    assert response.status_code == 302
    assert load_working().assertions[target.id].status == "DISPUTED"


def test_decision_on_an_unknown_assertion_is_a_redirect_not_a_crash(seeded_client):
    response = seeded_client.post("/review/a_nope/decision", data={"action": "verify"})
    assert response.status_code == 302


def test_correction_from_the_detail_form_supersedes(seeded_client, load_working):
    target = next(iter(load_working().active()))
    seeded_client.post(
        f"/review/{target.id}/decision",
        data={
            "action": "correct",
            "target": "A Corrected Target",
            "actor": "tester",
            "note": "per §3.2",
        },
    )

    graph = load_working()
    assert graph.assertions[target.id].status == "SUPERSEDED"
    replacement = graph.assertions[graph.assertions[target.id].superseded_by]
    assert replacement.value == "A Corrected Target"
    assert replacement.provenance.correction_note == "per §3.2"


def test_correction_survives_a_re_ingest(seeded_client, load_working):
    """The point of merge-not-replace: re-running extraction must not destroy
    a human decision."""
    target = next(iter(load_working().active()))
    seeded_client.post(
        f"/review/{target.id}/decision",
        data={"action": "correct", "target": "Human Wins", "actor": "tester"},
    )

    seeded_client.post("/ingest", data={"text": "requirements body again", "type": "requirements"})

    human = [a for a in load_working().active() if a.is_human and a.value == "Human Wins"]
    assert human, "re-ingest destroyed the human correction"


def test_bulk_verify_by_threshold(seeded_client, load_working):
    expected = {a.id for a in load_working().active() if a.confidence <= 0.75}
    assert expected, "the fixture has low-confidence facts"

    seeded_client.post("/review/bulk", data={"bulk_scope": "threshold", "threshold": "0.75"})

    verified = {a.id for a in load_working().active() if a.status == "VERIFIED"}
    assert verified == expected


def test_bulk_verify_selected_ids(seeded_client, load_working):
    from werkzeug.datastructures import MultiDict

    ids = [a.id for a in list(load_working().active())[:2]]
    seeded_client.post(
        "/review/bulk", data=MultiDict([("bulk_scope", "selected")] + [("ids", i) for i in ids])
    )

    graph = load_working()
    assert all(graph.assertions[i].status == "VERIFIED" for i in ids)


def test_bulk_verify_with_no_selection_warns(seeded_client):
    response = seeded_client.post(
        "/review/bulk", data={"bulk_scope": "selected"}, follow_redirects=True
    )
    assert b"No assertions selected" in response.data


# ============================================================================
# Change management
# ============================================================================


def test_changes_page_lists_revisions_and_audit_trail(seeded_client, load_working):
    target = next(iter(load_working().active()))
    seeded_client.post(
        f"/review/{target.id}/decision",
        data={"action": "verify", "actor": "tester", "note": "looks right"},
    )

    response = seeded_client.get("/changes")
    assert response.status_code == 200
    assert b"Ingest" in response.data
    assert b"Audit trail" in response.data
    assert b"looks right" in response.data


def test_commit_creates_a_named_revision(seeded_client, app):
    seeded_client.post("/ingest", data={"text": "body", "type": "requirements"})
    seeded_client.post("/changes/commit", data={"label": "Pre-audit checkpoint", "note": "ready"})

    labels = [r.label for r in store_for(app).list_revisions()]
    assert "Pre-audit checkpoint" in labels


def test_freeze_is_refused_while_assertions_are_outstanding(seeded_client, app):
    seeded_client.post("/ingest", data={"text": "body", "type": "requirements"})
    revision = store_for(app).latest()

    response = seeded_client.post(
        "/changes/freeze", data={"revision_id": revision.id}, follow_redirects=True
    )
    assert b"Refused to freeze" in response.data
    assert not store_for(app).get_revision(revision.id).is_baseline


def test_freeze_succeeds_once_everything_is_verified(seeded_client, app):
    seeded_client.post("/ingest", data={"text": "body", "type": "requirements"})
    seeded_client.post("/review/bulk", data={"bulk_scope": "threshold", "threshold": "1.0"})
    # A baseline is a revision, so the reviewed state must be committed first —
    # freezing the ingest-time revision would freeze the unreviewed graph.
    seeded_client.post("/changes/commit", data={"label": "Reviewed"})

    revision = store_for(app).latest()
    response = seeded_client.post(
        "/changes/freeze",
        data={"revision_id": revision.id, "label": "Baseline v1"},
        follow_redirects=True,
    )
    assert b"Frozen as baseline" in response.data
    assert store_for(app).get_revision(revision.id).is_baseline


def test_freeze_refusal_points_at_the_commit_step(seeded_client, app):
    """Reviewing the working set does not retroactively review a revision."""
    seeded_client.post("/ingest", data={"text": "body", "type": "requirements"})
    seeded_client.post("/review/bulk", data={"bulk_scope": "threshold", "threshold": "1.0"})

    stale = store_for(app).latest()
    response = seeded_client.post(
        "/changes/freeze", data={"revision_id": stale.id}, follow_redirects=True
    )
    assert b"commit it as a revision first" in response.data


def test_promote_to_baseline_reports_what_it_left_behind(seeded_client, load_working):
    target = next(iter(load_working().active()))
    seeded_client.post(
        f"/review/{target.id}/decision", data={"action": "verify", "actor": "tester"}
    )

    response = seeded_client.post("/changes/promote", follow_redirects=True)
    assert b"Promoted 1 verified fact" in response.data
    assert b"unverified" in response.data


def test_diff_page_compares_revision_to_working_set(seeded_client, app):
    seeded_client.post("/ingest", data={"text": "body", "type": "requirements"})
    revision = store_for(app).latest()

    response = seeded_client.get(f"/changes/diff?from={revision.id}&to=working")
    assert response.status_code == 200
    assert b"No structural difference" in response.data


def test_diff_page_from_empty_graph_lists_additions(seeded_client):
    response = seeded_client.get("/changes/diff?to=working")
    assert response.status_code == 200
    assert b"Facts added" in response.data
    assert b"Payment Gateway Platform" in response.data
    assert b"No structural difference" not in response.data


def test_diff_page_lists_changed_fields(seeded_client, app, load_working):
    """Exercises the changed-assertion branch, including its rowspan grouping."""
    target = next(iter(load_working().active()))
    revision = store_for(app).latest()

    seeded_client.post(
        f"/review/{target.id}/decision", data={"action": "verify", "actor": "tester"}
    )

    response = seeded_client.get(f"/changes/diff?from={revision.id}&to=working")
    assert response.status_code == 200
    assert b"Changed" in response.data
    assert b"status" in response.data
    assert b"VERIFIED" in response.data


def test_diff_page_handles_an_unknown_revision(seeded_client):
    response = seeded_client.get("/changes/diff?from=rev_nope&to=working", follow_redirects=True)
    assert response.status_code == 200
    assert b"no such revision" in response.data


def test_discard_working_set(seeded_client, app):
    seeded_client.post("/ingest", data={"text": "body", "type": "requirements"})
    seeded_client.post("/changes/discard")
    assert not store_for(app).has_working()
    assert store_for(app).list_revisions(), "revisions must survive a discard"


# ============================================================================
# APIs and exports
# ============================================================================


def test_api_endpoints_return_json(seeded_client):
    c4 = seeded_client.get("/api/c4").get_json()
    assert {"nodes", "links", "level"} <= set(c4)

    gaps = seeded_client.get("/api/gaps").get_json()
    assert {"unresolved_count", "dangling_count", "completeness", "is_auditable"} <= set(gaps)


def test_exports_download(seeded_client):
    graph_json = seeded_client.get("/export/graph.json")
    assert graph_json.status_code == 200
    assert b"assertions" in graph_json.data

    turtle = seeded_client.get("/export/graph.ttl")
    assert turtle.status_code == 200
    assert b"@prefix" in turtle.data or b"PREFIX" in turtle.data


def test_reviewer_defaults_come_from_config(seeded_client, load_working):
    target = next(iter(load_working().active()))
    seeded_client.post(f"/review/{target.id}/decision", data={"action": "verify"})
    assert load_working().assertions[target.id].provenance.asserted_by == "tester"


# ============================================================================
# Ontology reference
# ============================================================================


def test_ontology_page_renders_on_an_empty_working_set(client):
    response = client.get("/ontology")
    assert response.status_code == 200
    assert b"Foundational ontologies" in response.data
    assert b"one-way import rule" in response.data


def test_ontology_is_in_the_navigation(client):
    assert b'href="/ontology"' in client.get("/").data


def test_ontology_page_shows_all_four_layers(client):
    html = client.get("/ontology").get_data(as_text=True)
    for layer in ("Common", "Enterprise", "Business Requirements", "Architecture"):
        assert layer in html


def test_ontology_focus_shows_own_and_inherited_slots(client):
    html = client.get("/ontology?focus=NonFunctionalRequirement").get_data(as_text=True)
    assert "declared here" in html
    assert "quality_category" in html
    assert "Requirement" in html


def test_ontology_search_by_slot_name(client):
    html = client.get("/ontology?q=quality_scenario").get_data(as_text=True)
    assert "NonFunctionalRequirement" in html
    assert "No class matches that filter" not in html


def test_ontology_enum_values_render(client):
    """`e.values` on a dict resolves to `dict.values` in Jinja, so this pins the
    subscript access that actually works."""
    html = client.get("/ontology").get_data(as_text=True)
    assert "QualityAttributeCategory" in html
    assert "Controlled vocabularies" in html


def test_ontology_focus_on_an_unknown_class_is_graceful(client):
    response = client.get("/ontology?focus=NoSuchClass")
    assert response.status_code == 200
    assert b"Foundational ontologies" in response.data


def test_api_ontology_returns_the_schema(client):
    payload = client.get("/api/ontology").get_json()
    assert len(payload["classes"]) == 58
    assert len(payload["enums"]) == 37
    assert payload["overview"]["stats"]["classes"] == 58
    assert payload["overview"]["diagnostics"]["is_clean"] is True


def test_a_broken_ontology_degrades_one_page_not_the_app(store_root, tmp_path):
    """The schema is read at startup, but a failure there must not take ingest,
    review or reconcile down with it."""
    from app import create_app

    application = create_app(
        {"TESTING": True, "STORE_ROOT": str(store_root), "ONTOLOGY_DIR": str(tmp_path / "absent")},
        store_root=str(store_root),
    )
    broken = application.test_client()

    response = broken.get("/ontology")
    assert response.status_code == 200
    assert b"could not be read" in response.data

    assert broken.get("/").status_code == 200
    assert broken.get("/review").status_code == 200
    assert broken.get("/api/ontology").status_code == 503


# ============================================================================
# Reconciliation
# ============================================================================

# Built so that all three outcomes are present at once: one reference resolvable
# by wording, one resolvable only by the document's own key, and one with no
# candidate at all.
RECONCILE_OUTPUT = {
    "triples": [
        {
            "subject": "Payment Gateway Platform",
            "predicate": "supports_capability",
            "object": "Payment Processing",
            "confidence": 0.6,
            "source_text": "the platform supports payment processing",
        },
        {
            "subject": "Payment Gateway Platform",
            "predicate": "implements_requirement",
            "object": "FR-PM-001",
            "confidence": 0.5,
            "source_text": "handles acceptance",
        },
        {
            "subject": "Payment Gateway Platform",
            "predicate": "traces_to_goal",
            "object": "Zzzz Entirely Unrelated",
            "confidence": 0.3,
            "source_text": "?",
        },
    ],
    "entities": [
        {"name": "Payment Gateway Platform", "ontology_class": "System"},
        {"name": "Unified Payment Processing", "ontology_class": "BusinessCapability"},
        {
            "name": "Payment Acceptance",
            "ontology_class": "FunctionalRequirement",
            "external_references": [{"identifier": "FR-PM-001"}],
        },
    ],
}

CAPABILITY_NODE = "businesscapability:unified_payment_processing"
REQUIREMENT_NODE = "functionalrequirement:payment_acceptance"


@pytest.fixture
def reconcile_client(store_root):
    from app import create_app

    application = create_app(
        {"TESTING": True, "STORE_ROOT": str(store_root), "REVIEWER": "tester"},
        store_root=str(store_root),
        extractor_factory=lambda _t: FakeExtractor(RECONCILE_OUTPUT),
    )
    client = application.test_client()
    client.post("/ingest", data={"text": "doc", "type": "requirements"})
    return client


def test_reconcile_page_renders_on_an_empty_working_set(client):
    response = client.get("/reconcile")
    assert response.status_code == 200
    assert b"Nothing to reconcile" in response.data


def test_reconcile_is_in_the_navigation(client):
    assert b'href="/reconcile"' in client.get("/").data


def test_reconcile_page_lists_proposals(reconcile_client):
    response = reconcile_client.get("/reconcile")
    assert response.status_code == 200
    assert b"Payment Processing" in response.data
    assert b"Unified Payment Processing" in response.data
    assert b"resolvable" in response.data
    assert b"no candidate" in response.data


def test_api_reconcile_returns_json(reconcile_client):
    payload = reconcile_client.get("/api/reconcile").get_json()
    assert payload["summary"]["total"] == 3
    assert payload["summary"]["resolvable"] == 2
    assert payload["summary"]["no_candidate"] == 1
    assert {row["predicate"] for row in payload["rows"]} == {
        "supports_capability",
        "implements_requirement",
        "traces_to_goal",
    }


def test_bulk_resolve_binds_everything_above_the_threshold(reconcile_client, load_working):
    before = len(load_working().unresolved_references())
    response = reconcile_client.post(
        "/reconcile/bulk",
        data={"bulk_scope": "threshold", "threshold": "0.75"},
        follow_redirects=True,
    )

    assert b"Resolved 2 of 3" in response.data
    assert b"1 with no candidate of the expected kind" in response.data
    assert len(load_working().unresolved_references()) == before - 2


def test_bulk_resolve_respects_a_stricter_threshold(reconcile_client, load_working):
    """At 1.0 only the document-key match qualifies; wording alone does not."""
    reconcile_client.post(
        "/reconcile/bulk",
        data={"bulk_scope": "threshold", "threshold": "1.0"},
        follow_redirects=True,
    )

    graph = load_working()
    linked = [
        a
        for a in graph.active()
        if a.predicate == "implements_requirement" and a.object == REQUIREMENT_NODE
    ]
    assert linked, "the external-reference match must resolve at any threshold"
    still_open = {a.predicate for a in graph.unresolved_references()}
    assert "supports_capability" in still_open


def test_bulk_resolve_with_no_selection_warns(reconcile_client):
    response = reconcile_client.post(
        "/reconcile/bulk", data={"bulk_scope": "selected"}, follow_redirects=True
    )
    assert b"No references selected" in response.data


def test_manual_resolve_binds_a_chosen_target(reconcile_client, load_working):
    reference = next(
        a for a in load_working().unresolved_references() if a.predicate == "supports_capability"
    )
    reconcile_client.post(
        f"/reconcile/{reference.id}/resolve",
        data={"target_node_id": CAPABILITY_NODE, "actor": "tester", "note": "decided by hand"},
        follow_redirects=True,
    )

    graph = load_working()
    assert graph.assertions[reference.id].status == "SUPERSEDED"
    replacement = graph.assertions[graph.assertions[reference.id].superseded_by]
    assert replacement.object == CAPABILITY_NODE
    assert "decided by hand" in replacement.provenance.correction_note


def test_manual_resolve_refuses_a_wrong_kind_by_default(reconcile_client, load_working):
    reference = next(
        a for a in load_working().unresolved_references() if a.predicate == "supports_capability"
    )
    response = reconcile_client.post(
        f"/reconcile/{reference.id}/resolve",
        data={"target_node_id": REQUIREMENT_NODE, "actor": "tester"},
        follow_redirects=True,
    )

    assert b"is not an expected target" in response.data
    graph = load_working()
    assert graph.assertions[reference.id].is_active, "a refused binding must change nothing"


def test_manual_resolve_allows_a_deliberate_cross_kind_override(reconcile_client, load_working):
    reference = next(
        a for a in load_working().unresolved_references() if a.predicate == "traces_to_goal"
    )
    reconcile_client.post(
        f"/reconcile/{reference.id}/resolve",
        data={
            "target_node_id": REQUIREMENT_NODE,
            "actor": "tester",
            "allow_kind_override": "on",
            "note": "the referent is a function",
        },
        follow_redirects=True,
    )

    graph = load_working()
    replacement = graph.assertions[graph.assertions[reference.id].superseded_by]
    assert replacement.object == REQUIREMENT_NODE
    assert "[kind override]" in replacement.provenance.correction_note


def test_manual_resolve_reports_a_missing_target(reconcile_client, load_working):
    reference = next(iter(load_working().unresolved_references()))
    response = reconcile_client.post(
        f"/reconcile/{reference.id}/resolve",
        data={"target_node_id": "softwaresystem:not_extracted", "actor": "tester"},
        follow_redirects=True,
    )

    assert b"does not exist" in response.data


def test_resolution_appears_in_the_audit_trail(reconcile_client):
    reconcile_client.post(
        "/reconcile/bulk",
        data={"bulk_scope": "threshold", "threshold": "0.75", "note": "sprint 12 pass"},
        follow_redirects=True,
    )

    response = reconcile_client.get("/changes")
    assert b"Resolved" in response.data
    assert b"sprint 12 pass" in response.data
