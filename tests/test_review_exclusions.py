"""
Hiding noise in the review queue, and removing a fact through the app.

Two related but distinct capabilities, deliberately kept apart in both the model
and the UI:

    exclude_*   hide a predicate or kind from the QUEUE. Cheap, local, changes
                nothing about the graph.
    retire      take the fact out of the graph. Durable, audited, reversible.

The failure this covers is a reviewer facing a page of invented `connects_to`
triples with every filter *except* a way to say "not this" — isolating the noise was
possible, working past it was not.
"""

import pytest

from app.projections import ReviewFilters, project_review_rows, project_review_summary
from core.knowledge.review import ReviewLog
from core.knowledge import RevisionStore, graph_from_extraction
from core.knowledge.model import RETIREMENT_MARK, STATUS_RETIRED

NOISY = {
    "triples": [
        {"subject": "Payment Orchestrator", "predicate": "connects_to",
         "object": "Transaction Store", "confidence": 0.5},
        {"subject": "Payment Orchestrator", "predicate": "connects_to",
         "object": "Payment UI Service", "confidence": 0.5},
        {"subject": "Payment Orchestrator", "predicate": "uses_technology",
         "object": "Valkey", "confidence": 0.9},
    ],
    "elements": [
        {"name": "Payment Orchestrator", "element_type": "Container"},
        {"name": "Transaction Store", "element_type": "DataStore"},
        {"name": "Payment UI Service", "element_type": "Container"},
    ],
}


@pytest.fixture
def noisy_graph():
    graph, _ = graph_from_extraction(
        NOISY, {"document_type": "architecture", "model_id": "m"}, document_ref="arch.md"
    )
    return graph


# ============================================================================
# Exclusions
# ============================================================================


def test_excluding_a_predicate_hides_it_and_keeps_the_rest(noisy_graph):
    everything = project_review_rows(noisy_graph, ReviewLog())
    assert {r["predicate"] for r in everything} >= {"connects_to", "uses_technology"}

    filtered = project_review_rows(
        noisy_graph, ReviewLog(), ReviewFilters(exclude_predicate="connects_to")
    )
    assert filtered, "the rest of the graph must still be reviewable"
    assert all(r["predicate"] != "connects_to" for r in filtered)
    assert any(r["predicate"] == "uses_technology" for r in filtered)


def test_excluding_a_kind_hides_every_fact_about_it(noisy_graph):
    filtered = project_review_rows(noisy_graph, ReviewLog(), ReviewFilters(exclude_kind="DataStore"))
    assert all(r["subject_kind"] != "DataStore" for r in filtered)


def test_an_exclusion_does_not_touch_the_graph(noisy_graph):
    """Hiding is not removing — the whole reason both exist."""
    before = {a.id for a in list(noisy_graph.active())}
    project_review_rows(noisy_graph, ReviewLog(), ReviewFilters(exclude_predicate="connects_to"))
    assert {a.id for a in list(noisy_graph.active())} == before


def test_exclusions_survive_a_round_trip_through_a_query_string(noisy_graph):
    """Filter links are rebuilt from the dataclass, so an exclusion has to be part
    of the query it produces or a chip click would silently drop it."""
    f = ReviewFilters(exclude_predicate="connects_to", exclude_kind="DataStore", run="run_x")
    q = f.to_query()
    assert "exclude_predicate=connects_to" in q
    assert "exclude_kind=DataStore" in q
    assert "run=run_x" in q

    back = ReviewFilters.from_args(
        {k: v for k, v in (part.split("=", 1) for part in q.split("&"))}
    )
    assert back.exclude_predicate == "connects_to"
    assert back.exclude_kind == "DataStore"
    assert back.run == "run_x"
    assert back.has_exclusions is True


def test_no_exclusions_means_an_untouched_queue(noisy_graph):
    assert ReviewFilters().has_exclusions is False
    assert len(project_review_rows(noisy_graph, ReviewLog(), ReviewFilters())) == len(
        list(noisy_graph.active())
    )


def test_the_summary_ranks_predicates_so_the_volume_is_visible(noisy_graph):
    """ "Which predicate is producing the most rows?" is the question that leads to
    an exclusion, and a bare sorted list cannot answer it.

    Ranked by COUNT, not by suspicion — `element_type` legitimately has the most
    instances (one per element), and the ranking is not a claim that it is noise.
    What it gives a reviewer is the shape of the page they are about to read.
    """
    summary = project_review_summary(noisy_graph, ReviewLog())
    ranked = list(summary["by_predicate"].items())
    assert ranked[0] == ("element_type", 3), ranked
    assert summary["by_predicate"]["connects_to"] == 2
    assert summary["by_predicate"]["uses_technology"] == 1
    assert summary["runs"]  # a run id to scope by


def test_scoping_to_a_run_hides_facts_from_other_runs():
    from core.knowledge import merge_graphs

    a, _ = graph_from_extraction(
        {"triples": [{"subject": "X", "predicate": "connects_to", "object": "Y",
                      "confidence": 0.5}],
         "elements": [{"name": "X", "element_type": "Container"}]},
        {"document_type": "architecture"}, document_ref="a.md",
    )
    b, _ = graph_from_extraction(
        {"triples": [{"subject": "P", "predicate": "uses_technology", "object": "Q",
                      "confidence": 0.5}],
         "elements": [{"name": "P", "element_type": "Container"}]},
        {"document_type": "architecture"}, document_ref="b.md",
    )
    merged = merge_graphs(a, b)
    run_a = next(iter(merged.runs))

    rows = project_review_rows(merged, ReviewLog(), ReviewFilters(run=run_a))
    assert rows
    assert all(r["predicate"] != "uses_technology" for r in rows)


# ============================================================================
# Through the app
# ============================================================================


@pytest.fixture
def retire_client(store_root):
    """A client holding the noisy graph, for driving the routes."""
    from app import create_app
    from tests.conftest import FakeResult

    class Extractor:
        def __call__(self, _doc_type):
            return self

        def run(self, _input):
            return FakeResult(NOISY, {"document_type": "architecture", "model_id": "m"})

    application = create_app(
        {"TESTING": True, "STORE_ROOT": str(store_root), "REVIEWER": "tester"},
        store_root=str(store_root),
        extractor_factory=Extractor(),
    )
    client = application.test_client()
    client.post("/ingest", data={"text": "x" * 200, "type": "architecture"})
    return client


def _load(store_root):
    return RevisionStore(store_root).ensure().load_working().graph


def test_removing_a_fact_through_the_route(retire_client, store_root):
    graph = _load(store_root)
    target = next(a for a in graph.active() if a.predicate == "connects_to")

    response = retire_client.post(
        f"/review/{target.id}/decision",
        data={"action": "retire", "actor": "tester", "note": "invented predicate"},
        follow_redirects=True,
    )
    assert response.status_code == 200

    after = _load(store_root)
    a = after.assertions[target.id]
    assert a.status == STATUS_RETIRED
    assert a.superseded_by == RETIREMENT_MARK
    assert target.id not in {x.id for x in list(after.active())}
    # Nothing else was disturbed.
    assert len(list(after.active())) == len(list(graph.active())) - 1


def test_the_removal_is_in_the_audit_trail(retire_client, store_root):
    graph = _load(store_root)
    target = next(a for a in graph.active() if a.predicate == "connects_to")
    retire_client.post(
        f"/review/{target.id}/decision",
        data={"action": "retire", "actor": "tester", "note": "invented predicate"},
    )

    snapshot = RevisionStore(store_root).ensure().load_working()
    entry = next(d for d in snapshot.log.entries if d.assertion_id == target.id)
    assert entry.action == "retire"
    assert entry.label == "Removed"
    assert entry.note == "invented predicate"
    assert entry.changed_fields == ["status", "superseded_by"]


def test_a_removed_fact_can_be_reopened_through_the_route(retire_client, store_root):
    graph = _load(store_root)
    target = next(a for a in graph.active() if a.predicate == "connects_to")
    retire_client.post(f"/review/{target.id}/decision", data={"action": "retire", "actor": "t"})
    retire_client.post(f"/review/{target.id}/decision", data={"action": "reset", "actor": "t"})

    restored = _load(store_root).assertions[target.id]
    assert restored.is_active is True
    assert restored.superseded_by is None


def test_the_review_page_offers_remove_and_the_exclusion_filters(retire_client):
    page = retire_client.get("/review").get_data(as_text=True)
    assert "Remove" in page
    assert 'name="exclude_predicate"' in page
    assert 'name="exclude_kind"' in page
    assert 'name="run"' in page
    assert "Hiding is not removing" in page
    assert "Removed" in page  # the chip


def test_the_removed_chip_lists_what_was_removed(retire_client, store_root):
    graph = _load(store_root)
    target = next(a for a in graph.active() if a.predicate == "connects_to")
    retire_client.post(f"/review/{target.id}/decision", data={"action": "retire", "actor": "t"})

    response = retire_client.get("/review?only=retired")
    assert response.status_code == 200
    assert target.id.encode() in response.data
    # ...and not in the default view, which is what removal has to mean.
    assert target.id.encode() not in retire_client.get("/review").data
