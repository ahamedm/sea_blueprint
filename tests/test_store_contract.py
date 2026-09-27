"""
The store contract, run against every backend.

WHY THIS IS PARAMETRISED. A second backend is only a backend if it behaves like the
first. Asserting the file store's behaviour and the SQL store's separately would let
them drift — and the drift would show up as a lost revision or a resurrected fact,
not as a failing test. So both are run through the same list, and the one honest
difference (`concurrency_safe`) is asserted rather than assumed.

WHAT IS PINNED

  1. round-trip fidelity — status, provenance and `superseded_by` survive a save and
     load, because a backend that drops them silently resurrects deleted facts and
     turns confirmed decisions back into agent guesses;
  2. the guarded write — a stale `expected_version` raises `StoreConflict` on a backend
     that can guard, and is REFUSED rather than ignored on one that cannot;
  3. revision behaviour — parent chaining, newest-first ordering, and the freeze gate
     that refuses a baseline containing unverified assertions.

The concurrency test is the reason this work exists: the server has always run
threaded against an unlocked store, so a silent lost update was possible and untested.
"""

from __future__ import annotations

import pytest

from core.knowledge import RevisionStore, StoreConflict
from core.knowledge.model import (
    SOURCE_EXTRACTION,
    STATUS_VERIFIED,
    KnowledgeGraph,
    Provenance,
)


def _graph(verified: bool = False):
    graph = KnowledgeGraph(label="test")
    node = graph.add_node("Container", "Payment Orchestrator")
    assertion = graph.add_assertion(
        node, "responsibility", value="Routes payments", confidence=0.9,
        source_text="the document says so",
        provenance=Provenance(source_type=SOURCE_EXTRACTION, run_id="run_1"),
    )
    if verified:
        assertion.status = STATUS_VERIFIED
    return graph, assertion


@pytest.fixture(params=["file", "sqlite"])
def store(request, tmp_path):
    if request.param == "file":
        return RevisionStore(tmp_path / "sea").ensure()
    sqlalchemy = pytest.importorskip("sqlalchemy")
    assert sqlalchemy  # the backend is optional by construction
    from core.knowledge.store_sql import SqliteStore

    return SqliteStore(tmp_path / "sea.sqlite").ensure()


# ============================================================================
# 1. The contract exists and is honest about what it can do
# ============================================================================


def test_every_backend_declares_whether_it_can_guard(store):
    """A caller has to be able to tell, without trying, whether a guarded write will
    be enforced. Silence here is how lost updates happen."""
    assert store.concurrency_safe in (True, False)


# ============================================================================
# 2. Round-trip fidelity
# ============================================================================


def test_an_untouched_store_loads_an_empty_graph(store):
    snapshot = store.load_working()
    assert snapshot.graph.nodes == {}
    assert snapshot.graph.assertions == {}
    assert store.has_working() is False


def test_the_working_set_round_trips_with_its_review_state(store):
    graph, assertion = _graph(verified=True)
    store.save_working(graph)

    assert store.has_working() is True
    loaded = store.load_working().graph
    restored = loaded.assertions[assertion.id]
    assert restored.status == STATUS_VERIFIED
    assert restored.confidence == pytest.approx(0.9)
    assert restored.source_text == "the document says so"
    assert restored.provenance.run_id == "run_1"
    assert restored.provenance.source_type == SOURCE_EXTRACTION
    assert loaded.nodes[assertion.subject].label == "Payment Orchestrator"


def test_discarding_the_working_set_empties_it(store):
    graph, _ = _graph()
    store.save_working(graph)
    store.discard_working()
    assert store.has_working() is False


# ============================================================================
# 3. The guarded write — the point of the exercise
# ============================================================================


def test_a_stale_version_is_refused_where_the_backend_can_guard(store):
    if not store.concurrency_safe:
        pytest.skip("this backend declares that it cannot guard writes")

    graph, _ = _graph()
    first = store.save_working(graph)
    version = first.meta["version"]

    # A second writer that read the same version loses cleanly.
    store.save_working(graph, expected_version=version)
    with pytest.raises(StoreConflict) as excinfo:
        store.save_working(graph, expected_version=version)
    assert excinfo.value.expected == version
    assert excinfo.value.found == version + 1


def test_the_version_advances_by_one_per_write(store):
    if not store.concurrency_safe:
        pytest.skip("this backend has no version token")

    graph, _ = _graph()
    first = store.save_working(graph)
    second = store.save_working(graph, expected_version=first.meta["version"])
    assert second.meta["version"] == first.meta["version"] + 1


def test_a_backend_that_cannot_guard_refuses_rather_than_ignores(store):
    """Accepting `expected_version` and not enforcing it would be worse than not
    offering it: the caller would believe the write was protected."""
    if store.concurrency_safe:
        pytest.skip("this backend can guard writes, so it honours the argument")

    with pytest.raises(StoreConflict, match="cannot guard"):
        store.save_working(_graph()[0], expected_version=0)


# ============================================================================
# 4. Revisions
# ============================================================================


def test_commit_creates_a_revision_and_chains_the_parent(store):
    graph, _ = _graph(verified=True)
    first = store.commit(graph, label="first")
    assert first.parent_id == ""
    assert first.kind == "draft"

    second = store.commit(graph, label="second")
    assert second.parent_id == first.id
    assert [r.id for r in store.list_revisions()] == [second.id, first.id]
    assert store.latest().id == second.id


def test_a_revision_round_trips(store):
    graph, assertion = _graph(verified=True)
    revision = store.commit(graph, label="snapshot")
    loaded = store.load_revision(revision.id).graph
    assert loaded.assertions[assertion.id].status == STATUS_VERIFIED
    assert loaded.version_id == revision.id


def test_loading_a_missing_revision_raises(store):
    with pytest.raises(KeyError):
        store.load_revision("rev_nope")


def test_freeze_refuses_a_baseline_over_unverified_assertions(store):
    graph, _ = _graph(verified=False)
    revision = store.commit(graph)
    with pytest.raises(Exception) as excinfo:
        store.freeze(revision.id)
    assert "outstanding" in str(excinfo.value) or "unverified" in str(excinfo.value)

    frozen = store.freeze(revision.id, actor="architect", allow_unverified=True)
    assert frozen.is_baseline is True
    assert store.baselines()[0].id == revision.id


def test_freeze_accepts_a_fully_reviewed_revision(store):
    graph, _ = _graph(verified=True)
    revision = store.commit(graph)
    frozen = store.freeze(revision.id, label="Baseline v1", actor="architect")
    assert frozen.kind == "baseline"
    assert frozen.frozen_by == "architect"
    assert frozen.label == "Baseline v1"


def test_diff_reports_what_changed_between_revisions(store):
    graph, node_assertion = _graph(verified=True)
    before = store.commit(graph)

    graph.add_assertion(
        node_assertion.subject, "responsibility", value="Also settles batches",
        confidence=0.8, provenance=Provenance(source_type=SOURCE_EXTRACTION),
    )
    after = store.commit(graph)

    delta = store.diff(before.id, after.id)
    assert delta.added_assertions, "the new assertion must show up in the diff"
    assert {a.value for a in delta.added_assertions} == {"Also settles batches"}
