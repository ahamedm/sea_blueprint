"""
Route-level tests.

These run the real ingest -> merge -> review -> revision paths with a fake
extractor, so they cover the wiring that the projections unit-test cannot: the
handoff from agent result to canonical graph, which is where the MVP was broken.
"""

import json
from pathlib import Path

import pytest

from core.knowledge import RevisionStore, graph_from_extraction
from tests.conftest import FakeExtractor, FakeResult


def store_for(app) -> RevisionStore:
    return RevisionStore(app.config["STORE_ROOT"]).ensure()


# ============================================================================
# Pages render
# ============================================================================


def test_every_page_renders_on_an_empty_working_set(client):
    for path in ["/", "/ingest", "/review", "/reconcile", "/map", "/gaps", "/quality", "/changes"]:
        response = client.get(path)
        assert response.status_code == 200, f"{path} -> {response.status_code}"


def test_the_quality_census_renders_and_serves_json(client):
    """The attribute-shaped audit.

    `requirements_output` classifies no quality attribute and `architecture_output`
    delivers none, so this exercises the page's empty branch; the populated branch
    is covered below and in `test_quality_census.py`.
    """
    response = client.get("/quality")
    assert response.status_code == 200
    assert b"Quality attributes" in response.data

    payload = client.get("/api/quality").get_json()
    assert "summary" in payload
    assert set(payload["summary"]["states"]) == {
        "stated_in_requirements",
        "delivered_by_architecture",
        "realized_by_technique",
        "has_quality_scenario",
    }


def test_the_quality_page_renders_the_states_and_the_merge(tmp_path):
    """The populated branch: four states, the empty one named, and one concern
    carrying two labels rather than counting as two gaps."""
    from app import create_app
    from tests.conftest import FakeExtractor

    output = {
        "entities": [
            {
                "name": "Uptime",
                "ontology_class": "NonFunctionalRequirement",
                "quality_attribute": "Availability",
            }
        ],
        "elements": [
            {
                "name": "Gateway",
                "element_type": "SoftwareSystem",
                "satisfies_attributes": ["High Availability"],
            }
        ],
    }
    root = tmp_path / "quality-sea"
    app = create_app(
        {"TESTING": True, "STORE_ROOT": str(root)},
        store_root=str(root),
        extractor_factory=lambda doc_type: FakeExtractor(output),
    )
    app.test_client().post(
        "/ingest", data={"text": "quality body", "type": "requirements"}
    )

    page = app.test_client().get("/quality")
    assert b"Uptime" in page.data
    assert b"Gateway" in page.data
    assert b"one concern, 2 labels" in page.data

    payload = app.test_client().get("/api/quality").get_json()
    assert payload["summary"]["attributes"] == 1
    assert payload["summary"]["attribute_nodes"] == 2
    assert payload["summary"]["merged_labels"] == 1
    assert payload["summary"]["states"]["stated_in_requirements"] == 1
    assert payload["summary"]["states"]["delivered_by_architecture"] == 1
    assert payload["summary"]["states"]["has_quality_scenario"] == 0
    assert "has_quality_scenario" in payload["summary"]["unpopulated_states"]

    # Collapsible + filterable: every concern row carries the keys the client-side
    # filter reads, and the section is a `<details>` so it can be folded away.
    assert b'id="census-search"' in page.data
    assert b'id="census-coverage"' in page.data
    assert b'id="census-state"' in page.data
    assert b"data-filter-row" in page.data
    assert b"data-filter-group" in page.data
    assert b'<details class="card collapsible" open data-filter-group>' in page.data
    assert b'data-coverage="answered"' in page.data
    assert b"table-wrap sm" in page.data


def test_the_quality_census_filter_rows_carry_their_states(tmp_path):
    """The state filter is only as good as the data attribute behind it."""
    import re

    from app import create_app
    from tests.conftest import FakeExtractor

    output = {
        "entities": [
            {
                "name": "Uptime",
                "ontology_class": "NonFunctionalRequirement",
                "quality_attribute": "Availability",
            }
        ],
        "elements": [
            {
                "name": "Gateway",
                "element_type": "SoftwareSystem",
                "satisfies_attributes": ["High Availability"],
            }
        ],
    }
    root = tmp_path / "quality-states"
    app = create_app(
        {"TESTING": True, "STORE_ROOT": str(root)},
        store_root=str(root),
        extractor_factory=lambda doc_type: FakeExtractor(output),
    )
    app.test_client().post("/ingest", data={"text": "quality body", "type": "requirements"})

    html = app.test_client().get("/quality").data.decode()
    row = re.search(r"<tr data-filter-row[^>]*>", html).group(0)
    assert 'data-coverage="answered"' in row
    assert "stated_in_requirements" in row
    assert "delivered_by_architecture" in row
    # The state names are space-delimited so a substring cannot match by accident.
    assert 'data-states="stated_in_requirements delivered_by_architecture "' in row


def test_the_map_quality_filter_is_a_url_not_a_script(client):
    """The filter has to survive a reload and a shared link, and an unknown value
    has to select nothing rather than fall back to the whole graph."""
    response = client.get("/map?concern=RELIABILITY")
    assert response.status_code == 200
    assert b"No quality attribute matches" in response.data


def test_viewpoint_and_gap_routes_exist(client):
    """The old nav linked to /graph and /gaps with no routes behind them (404)."""
    assert client.get("/map").status_code == 200
    assert client.get("/gaps").status_code == 200


def test_the_retired_view_urls_still_resolve(client):
    """`/c4` named a notation this view is not, and `/graph` named "the graph" while
    drawing only the architecture one. Both are kept as redirects so a bookmark or
    an external link lands on the view that replaced them rather than on a 404."""
    for path in ("/c4", "/graph"):
        response = client.get(f"{path}?lens=architecture")
        assert response.status_code == 301, path
        assert "/map?lens=architecture" in response.headers["Location"], path


def test_map_page_renders_both_sides_of_the_graph(client, load_working):
    """Exercises the populated branch of the map template.

    The requirements fixture is the important half: the view this replaced drew
    *only* architecture elements and came back empty for a requirements document.
    """
    client.post("/ingest", data={"text": "architecture body", "type": "architecture"})

    response = client.get("/map?lens=all")
    assert response.status_code == 200
    assert b'id="map"' in response.data
    assert b"Payment Orchestrator" in response.data


def test_map_page_draws_a_requirements_graph(client):
    """The regression the map exists for.

    The view this replaced returned an empty diagram for a requirements document
    and explained that architecture extraction produces C4 elements — so step 2 of
    the user journey, all of it, had no view at all.
    """
    client.post("/ingest", data={"text": "requirements body", "type": "requirements"})

    response = client.get("/map")
    assert response.status_code == 200
    assert b"Payment Acceptance" in response.data
    assert b"Nothing to draw" not in response.data


def test_map_graph_gets_the_real_estate_and_the_table_is_demoted(client, load_working):
    """The graph is the page; the browse table is a collapsed, filtered panel.

    A fixed 560px canvas wasted a large monitor, and a 200-row table underneath
    pushed everything else off the screen. The controls are asserted here because
    they are what makes the taller canvas usable rather than merely taller.
    """
    client.post("/ingest", data={"text": "architecture body", "type": "architecture"})

    html = client.get("/map?lens=all").data
    assert b'id="graph-resize"' in html, "no drag handle to claim more height"
    assert b'id="graph-full"' in html, "no fullscreen control"
    assert b'id="graph-relayout"' in html
    # `<details>` with no `open` attribute: collapsed by default, so the graph
    # owns the first screen. The marker is HTML, not CSS, and `hidden` would have
    # been invisible to in-page search.
    assert b'<details class="card collapsible" id="concepts-panel">' in html
    assert b"<details class=\"card collapsible\" id=\"concepts-panel\" open>" not in html
    assert b'id="concept-search"' in html
    assert b'id="concept-kind"' in html
    assert b"data-concept-row" in html
    assert b"Payment Orchestrator" in html, "the panel still carries the concepts"


def test_the_map_table_reports_the_total_when_it_truncates(client, load_working, monkeypatch):
    """A filter over a silently truncated list answers "no match" for a concept
    that exists, so the page has to say what it is not showing."""
    import app as app_module

    monkeypatch.setattr(app_module, "MAP_CONCEPT_ROWS", 2)
    client.post("/ingest", data={"text": "architecture body", "type": "architecture"})

    html = client.get("/map").data
    assert b"most-referenced of" in html
    # `<tr data-concept-row`, not the bare attribute: the filter script names the
    # selector too, and counting that would make the cap look one row larger.
    assert html.count(b"<tr data-concept-row") == 2, "the cap is what the hint reports"


def test_gap_report_page_shows_unresolved_references(seeded_client):
    response = seeded_client.get("/gaps")
    assert response.status_code == 200
    assert b"Unresolved cross-graph references" in response.data
    # Extraction is COMPLETE here (the fake reports 3 successful model calls),
    # but nothing has been reviewed, so the audit gate must still be shut.
    assert b"Not auditable yet" in response.data
    assert b"are outstanding" in response.data


def test_gap_report_answers_both_directions(seeded_client):
    """The acceptance criteria of YB-005, as rendered.

    "A requirement with no architecture can be listed" and "an architecture element
    answering no requirement can be listed" are the two halves, and the page has to
    carry both headings whether or not either list is empty — a missing heading
    reads as "no finding" rather than "not reported".
    """
    response = seeded_client.get("/gaps")
    assert b"Requirements with no architectural answer" in response.data
    assert b"Architecture claiming a requirement that never bound" in response.data
    assert b"Unanswered requirements" in response.data


def test_the_two_lists_are_populated_from_the_same_report(seed_both_documents):
    """Both directions on one graph, through the API AND the page.

    A count and a label list are different promises: the earlier fixture produced
    the numbers while the page rendered the empty branch, which is exactly the kind
    of gap a summary-only assertion misses.
    """
    payload = seed_both_documents.get("/api/gaps").get_json()
    realization = payload["realization"]

    assert payload["unresolved_count"] == realization["summary"]["unbound_claims"]
    assert payload["unrealized_count"] == 20
    assert len(realization["unrealized"]) == 20
    assert len(realization["obligations"]) == 13
    assert realization["summary"]["requirements"] == 22

    page = seed_both_documents.get("/gaps").get_data(as_text=True)
    assert "no architecture references it" in page
    assert "AlpineJS UI Framework" in page
    assert "PAN-Card Encryption Service" in page


def test_gap_report_page_lists_an_unanswered_requirement(reconcile_client):
    """Populated branch: a requirement the architecture never answers has to appear
    by name on the page, not merely be counted."""
    response = reconcile_client.get("/gaps")
    assert b"Requirements with no architectural answer" in response.data
    assert b"Settlement Reporting" in response.data
    assert "no architecture references it".encode() in response.data


def test_api_realization_reports_both_directions(reconcile_client):
    payload = reconcile_client.get("/api/realization").get_json()
    summary = payload["summary"]

    # Two requirements in the fixture: one answered by a citation, one untouched.
    assert summary["requirements"] == 2
    assert summary["realized"] == 1
    assert summary["unrealized"] == 1
    assert summary["coverage"]["full"] == 1
    assert summary["coverage"]["none"] == 1
    assert summary["bound_edges"] >= 1
    assert {r["label"] for r in payload["unrealized"]} == {"Settlement Reporting"}
    # The reference naming nothing extracted is the architecture-side finding.
    assert {o["source_label"] for o in payload["obligations"]} == {"Payment Gateway Platform"}
    assert {"unrealized", "obligations", "claims", "requirements"} <= set(payload)


def test_a_requirements_only_graph_realizes_nothing(requirements_only_client):
    """YB-030, pinned — and it has to be pinned here.

    The defect cannot be reproduced from a fixture replay of a two-document run:
    it needs a graph with NO architecture in it, which is the one shape a saved
    architecture fixture can never produce. The requirements document's own
    `System` node claims `implements_requirement` against a requirement whose label
    resolves, so before the guard the requirement read as answered by an
    architecture that does not exist.
    """
    payload = requirements_only_client.get("/api/realization").get_json()
    summary = payload["summary"]

    assert summary["requirements"] == 2
    assert summary["realized"] == 0, (
        "a requirements-side source must never be a realization — that is the "
        "requirements document answering itself"
    )
    assert summary["bound_edges"] == 0
    assert summary["coverage"]["none"] == 2
    # Not deleted, just not a realization: the claim still surfaces from the
    # source side as an obligation, which is the honest reading of a requirements
    # document naming the system that will implement it.
    assert {o["source_label"] for o in payload["obligations"]} == {"Payment Gateway Platform"}


def test_ingest_records_the_document_type_on_the_run(seeded_client, load_working):
    """Which document a run read is what tells the two sides of the audit apart,
    and it cannot be recovered after ingest — so it has to be written down."""
    runs = list(load_working().runs.values())
    assert runs
    assert {r.document_type for r in runs} == {"requirements"}


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
    mapped = seeded_client.get("/api/map").get_json()
    assert {"nodes", "links", "lens", "lens_labels"} <= set(mapped)

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
    assert len(payload["classes"]) == 70
    assert len(payload["enums"]) == 48
    assert payload["overview"]["stats"]["classes"] == 70
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
# by wording, one resolvable only by its identifier, and one with no candidate at
# all.
#
# The identifier is stated as a requirements-TOOLING key (`system: Jira`), which
# is what makes it matchable across documents. A bare `FR-PM-001` with no system
# is read as a label local to the document that stated it, and is deliberately
# refused as a cross-document join — see `test_reconcile.py`.
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
            "external_references": [
                {"identifier": "FR-PM-001", "system": "Jira",
                 "reference_type": "REQUIREMENT_KEY"}
            ],
        },
        # Deliberately never claimed by anything: the requirement-side finding the
        # fixture exists to render.
        {"name": "Settlement Reporting", "ontology_class": "FunctionalRequirement"},
    ],
}

CAPABILITY_NODE = "businesscapability:unified_payment_processing"
REQUIREMENT_NODE = "functionalrequirement:payment_acceptance"

# The architecture side of the same story. It exists because `RECONCILE_OUTPUT`
# alone CANNOT realize anything: it is one requirements document, and the claim it
# carries comes from the document's own `System` node. That is the YB-030 defect —
# a requirements-side source answering its own requirement — and a fixture that
# asserted it as the expected number is what kept the defect invisible. Realization
# needs an architecture document, so the fixture now has one.
RECONCILE_ARCH_OUTPUT = {
    "elements": [
        {"name": "Payment Orchestrator", "element_type": "Container"},
    ],
    "triples": [
        {
            "subject": "Payment Orchestrator",
            "predicate": "implements_requirement",
            "object": "FR-PM-001",
            "confidence": 0.9,
            "source_text": "routes and validates payment requests",
        },
    ],
}


class TwoDocumentExtractor:
    """Answers per document type, the way the real agent stack does."""

    def __call__(self, _doc_type):
        return self

    def run(self, input_data):
        doc_type = input_data.get("document_type") or "requirements"
        output = RECONCILE_ARCH_OUTPUT if doc_type == "architecture" else RECONCILE_OUTPUT
        return FakeResult(output, {"document_type": doc_type, "model_id": "fake"})


@pytest.fixture
def reconcile_client(store_root):
    from app import create_app

    application = create_app(
        {"TESTING": True, "STORE_ROOT": str(store_root), "REVIEWER": "tester"},
        store_root=str(store_root),
        extractor_factory=TwoDocumentExtractor(),
    )
    client = application.test_client()
    client.post("/ingest", data={"text": "doc", "type": "requirements"})
    client.post("/ingest", data={"text": "arch doc", "type": "architecture"})
    return client


@pytest.fixture
def requirements_only_client(store_root):
    """One requirements document, and no architecture whatsoever.

    The graph YB-030 is about: its `System` node claims the requirement, the label
    resolves straight back onto the requirement, and nothing architectural exists.
    """
    from app import create_app

    application = create_app(
        {"TESTING": True, "STORE_ROOT": str(store_root), "REVIEWER": "tester"},
        store_root=str(store_root),
        extractor_factory=lambda _t: FakeExtractor(RECONCILE_OUTPUT),
    )
    client = application.test_client()
    client.post("/ingest", data={"text": "doc", "type": "requirements"})
    return client


@pytest.fixture
def seed_both_documents(store_root):
    """A client holding the saved REQ-G and ARC-G outputs, ingested as two runs.

    The real fixture, not a minimal one: it is the only place both directions of
    the audit are populated at the same time, and it is where the project's own
    numbers come from.
    """
    root = Path(__file__).resolve().parents[1] / "data" / "output"
    req_path, arch_path = root / "test_req_prd.json", root / "test_arch.json"
    if not req_path.exists() or not arch_path.exists():
        pytest.skip("saved extraction fixtures are absent")

    def payload(path):
        blob = json.loads(path.read_text())
        return (
            {k: v for k, v in blob.items() if k not in ("metadata", "case", "input_file")},
            dict(blob.get("metadata", {})),
        )

    documents = {"requirements": payload(req_path), "architecture": payload(arch_path)}

    from app import create_app

    class SavedOutputs:
        """Stands in for the agent stack, answering per document type."""

        def __call__(self, doc_type):
            return self

        def run(self, input_data):
            output, metadata = documents[input_data.get("document_type") or "requirements"]
            return FakeResult(output, metadata)

    application = create_app(
        {"TESTING": True, "STORE_ROOT": str(store_root), "REVIEWER": "tester"},
        store_root=str(store_root),
        extractor_factory=SavedOutputs(),
    )
    client = application.test_client()
    client.post("/ingest", data={"text": "x" * 200, "type": "requirements"})
    client.post("/ingest", data={"text": "y" * 200, "type": "architecture"})
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
    # Three cross-graph references exist in the fixture. The
    # `implements_requirement` one cites `FR-PM-001`, the key the requirements
    # document issued for a requirement it also declares, so it is already a link
    # and never reaches the queue. That leaves the capability (resolvable) and
    # the goal (no candidate of the expected kind).
    assert payload["summary"]["total"] == 2
    assert payload["summary"]["resolvable"] == 1
    assert payload["summary"]["no_candidate"] == 1
    assert {row["predicate"] for row in payload["rows"]} == {
        "supports_capability",
        "traces_to_goal",
    }


def test_bulk_resolve_binds_everything_above_the_threshold(reconcile_client, load_working):
    before = len(load_working().unresolved_references())
    response = reconcile_client.post(
        "/reconcile/bulk",
        data={"bulk_scope": "threshold", "threshold": "0.75"},
        follow_redirects=True,
    )

    assert b"Resolved 1 of 2" in response.data
    assert b"1 with no candidate of the expected kind" in response.data
    assert len(load_working().unresolved_references()) == before - 1


def test_bulk_resolve_respects_a_stricter_threshold(reconcile_client, load_working):
    """At 1.0 only an exact match qualifies; wording alone does not."""
    reconcile_client.post(
        "/reconcile/bulk",
        data={"bulk_scope": "threshold", "threshold": "1.0"},
        follow_redirects=True,
    )

    graph = load_working()
    cited = [
        a
        for a in graph.active()
        if a.predicate == "implements_requirement" and a.value == "FR-PM-001"
    ]
    # Bound before any pass ran: an `implements_*` citation of a requirement key
    # is identity, not a proposal to accept. The literal is kept for lineage, so
    # what proves the binding is that it is not an open reference any more.
    assert cited, "the requirement-key citation must still be in the graph"
    assert all(a not in graph.unresolved_references() for a in cited)
    # Nothing else bound at 1.0, where the lexical capability match scores 0.92.
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
