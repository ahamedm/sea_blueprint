"""
The Design Assistant's page and routes.

THE PROPERTY THAT MATTERS MOST is in here: proposing must not change the graph.
`/changes/discard` empties the whole working set, so a draft merged straight into it
could not be undone on its own — which is why applying is a deliberate second step
and why `test_a_draft_does_not_touch_the_working_set` is the first assertion of the
route rather than a nicety.
"""

from __future__ import annotations

from core.knowledge import SOURCE_DESIGN_ASSISTANT, RevisionStore


def _state(app):
    store = RevisionStore(app.config["STORE_ROOT"]).ensure()
    return store, store.load_working()


def test_the_design_page_renders_and_says_what_it_would_read(app, seeded_client):
    response = app.test_client().get("/design")
    assert response.status_code == 200
    assert b"Design Assistant" in response.data
    assert b"What this run would read" in response.data
    assert b"Propose an architecture" in response.data
    assert b"Baseline ARC-G" in response.data


def test_the_design_page_refuses_an_empty_working_set(client):
    response = client.get("/design")
    assert response.status_code == 200
    assert b"Nothing to design against" in response.data
    assert b"Propose an architecture" not in response.data


def test_the_design_tab_is_in_the_nav(app, seeded_client):
    assert b'href="/design"' in app.test_client().get("/").data


def test_a_draft_does_not_touch_the_working_set(app, seeded_client):
    """The whole reason drafts exist as files."""
    _store, before = _state(app)
    before_assertions = set(before.graph.assertions)
    before_nodes = set(before.graph.nodes)

    response = app.test_client().post("/design/draft", data={})
    assert response.status_code == 200

    _store, after = _state(app)
    assert set(after.graph.assertions) == before_assertions, "the working set changed"
    assert set(after.graph.nodes) == before_nodes, "the working set changed"
    # But the draft is on disk and rendered.
    assert b"Apply to the working set" in response.data


def test_the_preview_shows_the_proposal_its_findings_and_the_degradation(app, seeded_client):
    response = app.test_client().post("/design/draft", data={})
    html = response.data

    assert b"Payment Orchestrator" in html
    assert b"Connection Pooling" in html
    assert b"Circuit Breaker" in html
    # An unresolved pattern is shown as unresolved, not silently matched.
    assert b"Quantum Flux Balancer" in html
    assert b"unresolved" in html
    # Findings are surfaced with their subject.
    assert b"Scheme Switch" in html
    assert b"ungrounded" in html
    # And what the digest had to leave out is stated on the page.
    assert b"What the model was not shown" in html
    assert b"no frozen baseline" in html


def test_the_preview_reports_the_run_pass_by_pass(app, seeded_client):
    """A design run of six passes must not read as one opaque block."""
    html = app.test_client().post("/design/draft", data={}).data
    for pass_name in (b"structure", b"connections", b"techniques", b"patterns",
                      b"scenarios", b"traceability"):
        assert pass_name in html, pass_name


def test_applying_merges_as_proposals_commits_and_consumes_the_draft(app, seeded_client):
    store, before = _state(app)
    revisions_before = len(store.list_revisions())

    app.test_client().post("/design/draft", data={})
    draft = store.__class__(app.config["STORE_ROOT"])  # keep the import honest
    from core.knowledge import DesignDraftStore

    drafts = DesignDraftStore(app.config["STORE_ROOT"])
    assert len(drafts.list()) == 1

    response = app.test_client().post(
        "/design/apply", data={"draft_id": drafts.list()[0].id}, follow_redirects=False
    )
    assert response.status_code == 302

    _store, after = _state(app)
    assert set(after.graph.nodes) - set(before.graph.nodes), "nothing was applied"
    assert len(_store.list_revisions()) == revisions_before + 1
    assert drafts.list() == [], "the draft should be consumed by the apply"

    # Applied facts are PROPOSALS, not extractions, and are unreviewed.
    applied = [a for a in after.graph.active() if a.provenance.source_type == SOURCE_DESIGN_ASSISTANT]
    assert applied, "the applied facts lost their provenance"
    assert not any(a.is_human for a in applied)


def test_applying_makes_the_quality_census_see_scenarios(app, seeded_client):
    """`has_quality_scenario` read 0 on every graph before this profile existed."""
    from core.knowledge import quality_report

    app.test_client().post("/design/draft", data={})
    from core.knowledge import DesignDraftStore

    drafts = DesignDraftStore(app.config["STORE_ROOT"])
    app.test_client().post("/design/apply", data={"draft_id": drafts.list()[0].id})

    _store, after = _state(app)
    assert quality_report(after.graph)["summary"]["states"]["has_quality_scenario"] >= 1


def test_discarding_leaves_the_graph_alone_and_removes_the_draft(app, seeded_client):
    _store, before = _state(app)
    app.test_client().post("/design/draft", data={})

    from core.knowledge import DesignDraftStore

    drafts = DesignDraftStore(app.config["STORE_ROOT"])
    draft_id = drafts.list()[0].id

    response = app.test_client().post(
        "/design/discard", data={"draft_id": draft_id}, follow_redirects=True
    )
    assert b"never touched" in response.data
    assert drafts.list() == []
    _store, after = _state(app)
    assert set(after.graph.nodes) == set(before.graph.nodes)


def test_applying_an_unknown_draft_is_refused(app, seeded_client):
    response = app.test_client().post(
        "/design/apply", data={"draft_id": "draft_nope"}, follow_redirects=True
    )
    assert b"no such design draft" in response.data


def test_the_api_lists_drafts(app, seeded_client):
    empty = app.test_client().get("/api/design").get_json()
    assert empty["drafts"] == [] and empty["latest"] is None

    app.test_client().post("/design/draft", data={})
    payload = app.test_client().get("/api/design").get_json()
    assert len(payload["drafts"]) == 1
    assert payload["latest"]["id"] == payload["drafts"][0]["id"]
    assert payload["latest"]["findings"]


def test_the_domain_pack_picker_is_offered(app, seeded_client):
    html = app.test_client().get("/design").data
    assert b"domain_pack" in html
    assert b"payment_processing" in html
