"""Revision store: working set vs immutable revision vs frozen baseline."""

import pytest

from core.knowledge import (
    BaselineNotReady,
    KnowledgeGraph,
    ReviewLog,
    RevisionStore,
    apply_decisions,
)


@pytest.fixture
def store(tmp_path):
    return RevisionStore(tmp_path / "store").ensure()


def _settled(graph):
    """Verify everything, so the freeze gate is satisfied."""
    log = ReviewLog()
    for a in list(graph.active()):
        apply_decisions(graph, log, a.id, "verify", actor="tester")
    return log


def test_working_set_is_empty_before_anything_is_saved(store):
    snapshot = store.load_working()
    assert snapshot.graph.nodes == {}
    assert snapshot.log.entries == []
    assert not store.has_working()


def test_working_set_round_trips_with_meta_and_log(store, req_extraction):
    log = ReviewLog()
    apply_decisions(
        req_extraction, log, next(iter(req_extraction.active())).id, "verify", actor="tester"
    )
    store.save_working(req_extraction, log, {"initiative_id": "INIT-1"})

    assert store.has_working()
    reloaded = store.load_working()
    assert set(reloaded.graph.assertions) == set(req_extraction.assertions)
    assert len(reloaded.log.entries) == 1
    assert reloaded.meta["initiative_id"] == "INIT-1"
    assert "saved_at" in reloaded.meta


def test_discard_working_keeps_revisions(store, req_extraction):
    store.commit(req_extraction, ReviewLog(), label="keep me")
    store.save_working(req_extraction, ReviewLog())
    store.discard_working()

    assert not store.has_working()
    assert len(store.list_revisions()) == 1


def test_commit_creates_an_immutable_revision(store, req_extraction):
    revision = store.commit(req_extraction, ReviewLog(), label="draft 1", actor="tester")

    assert store.get_revision(revision.id) is not None
    snapshot = store.load_revision(revision.id)
    assert set(snapshot.graph.assertions) == set(req_extraction.assertions)
    assert revision.stats["nodes"] == len(req_extraction.nodes)
    assert revision.label == "draft 1"
    assert revision.kind == "draft"
    assert not revision.is_baseline


def test_commit_stamps_version_and_parent(store, req_extraction):
    first = store.commit(req_extraction, ReviewLog(), label="one")
    second = store.commit(req_extraction, ReviewLog(), label="two")

    assert store.load_revision(first.id).graph.version_id == first.id
    assert store.load_revision(second.id).graph.parent_version_id == first.id
    assert first.parent_id == ""


def test_latest_is_the_most_recently_committed_even_within_one_second(store, req_extraction):
    """`created_at` has second precision; ordering must not depend on it."""
    first = store.commit(req_extraction, ReviewLog(), label="one")
    second = store.commit(req_extraction, ReviewLog(), label="two")
    third = store.commit(req_extraction, ReviewLog(), label="three")

    assert [r.id for r in store.list_revisions()] == [third.id, second.id, first.id]
    assert store.latest().id == third.id
    assert third.parent_id == second.id


def test_commit_records_documents_completeness_and_progress(store, req_extraction):
    log = _settled(req_extraction)
    revision = store.commit(req_extraction, log, label="reviewed")

    assert revision.documents == ["req.md"]
    assert revision.completeness == ["UNKNOWN"]  # no pass records in the fixture
    assert revision.review_summary.get("verify") == len(log.entries)
    assert revision.progress["is_auditable"] is True


def test_freeze_refuses_a_revision_with_outstanding_assertions(store, req_extraction):
    revision = store.commit(req_extraction, ReviewLog(), label="unreviewed")

    with pytest.raises(BaselineNotReady) as excinfo:
        store.freeze(revision.id, actor="tester")

    assert excinfo.value.progress.outstanding > 0
    assert excinfo.value.revision_id == revision.id
    assert not store.get_revision(revision.id).is_baseline


def test_freeze_can_be_overridden_deliberately(store, req_extraction):
    revision = store.commit(req_extraction, ReviewLog(), label="unreviewed")
    frozen = store.freeze(revision.id, label="Baseline v0", actor="tester", allow_unverified=True)

    assert frozen.is_baseline
    assert frozen.frozen_by == "tester"
    assert frozen.label == "Baseline v0"
    assert store.get_revision(revision.id).is_baseline
    assert [b.id for b in store.baselines()] == [revision.id]


def test_freeze_succeeds_on_a_fully_reviewed_revision(store, req_extraction):
    log = _settled(req_extraction)
    revision = store.commit(req_extraction, log, label="reviewed")
    frozen = store.freeze(revision.id, actor="tester")
    assert frozen.is_baseline


def test_freeze_without_revisions_is_an_error(store):
    with pytest.raises(KeyError, match="no revisions"):
        store.freeze()


def test_freeze_defaults_to_the_latest_revision(store, req_extraction):
    store.commit(req_extraction, ReviewLog(), label="one")
    log = _settled(req_extraction)
    latest = store.commit(req_extraction, log, label="two")
    assert store.freeze(actor="tester").id == latest.id


def test_diff_between_two_revisions(store, req_extraction):
    log = ReviewLog()
    first = store.commit(req_extraction, log, label="one")

    added = KnowledgeGraph()
    added.add_node("SoftwareSystem", "New Element")
    from core.knowledge import merge_graphs

    extended = merge_graphs(req_extraction, added)
    second = store.commit(extended, log, label="two")

    delta = store.diff(first.id, second.id)
    assert [n.label for n in delta.added_nodes] == ["New Element"]
    assert delta.removed_nodes == []


def test_diff_against_working_set(store, req_extraction):
    store.save_working(req_extraction, ReviewLog())
    store.commit(req_extraction, ReviewLog(), label="committed")

    changed = KnowledgeGraph()
    changed.add_node("SoftwareSystem", "Only In Working")
    from core.knowledge import merge_graphs

    store.save_working(merge_graphs(req_extraction, changed), ReviewLog())

    delta = store.diff_against_working(store.load_revision(store.latest().id).graph)
    assert delta.removed_nodes and delta.removed_nodes[0].label == "Only In Working"


def test_loading_an_unknown_revision_is_an_error(store):
    with pytest.raises(KeyError, match="no such revision"):
        store.load_revision("rev_nope")


def test_revisions_are_independent_snapshots(store, req_extraction):
    """Mutating the live graph must not rewrite history."""
    first = store.commit(req_extraction, ReviewLog(), label="one")
    before = len(store.load_revision(first.id).graph.assertions)

    req_extraction.add_node("SoftwareSystem", "Added Later")

    assert len(store.load_revision(first.id).graph.assertions) == before
    assert "softwaresystem:added_later" not in store.load_revision(first.id).graph.nodes
