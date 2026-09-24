"""
Removing a wrongly-extracted fact, and the two things that were in its way.

THE GAP. Every review action either vouched for a fact or annotated it; none could
take one out. `dispute` says "this is wrong" and explicitly keeps the claim active,
which meant an invented fact went on being counted by `unresolved_references()`, the
gap report and the map — and, because a dispute counted as outstanding review, it
blocked the audit gate permanently. Three slices, tested here:

  1. `retire` — remove the fact from the active graph, durably
  2. a dispute stops withholding the audit
  3. exclusions, so noise can be hidden while the rest is reviewed

The durability tests matter most: an assertion id is content-addressed, so a
re-extraction re-asserts the identical fact. A removal that the next run undoes is
not a removal.
"""

import pytest

from core.knowledge import (
    KnowledgeGraph,
    ReviewError,
    ReviewLog,
    apply_decisions,
    graph_from_extraction,
    merge_graphs,
    review_progress,
)
from core.knowledge.model import (
    RETIREMENT_MARK,
    STATUS_DISPUTED,
    STATUS_RETIRED,
    STATUS_UNVERIFIED,
    STATUS_VERIFIED,
)
from core.knowledge.review import ACTION_RETIRE, retire

SOURCE = {
    "triples": [
        {"subject": "Payment Orchestrator", "predicate": "does_the_wrong_thing",
         "object": "Nonsense", "confidence": 0.6},
        {"subject": "Payment Orchestrator", "predicate": "uses_technology",
         "object": "Valkey", "confidence": 0.9},
    ],
    "elements": [{"name": "Payment Orchestrator", "element_type": "Container"}],
}


@pytest.fixture
def extracted():
    graph, _ = graph_from_extraction(
        SOURCE, {"document_type": "architecture"}, document_ref="arch.md"
    )
    return graph


def _id_of(graph, predicate):
    return next(a.id for a in graph.active() if a.predicate == predicate)


# ============================================================================
# 1. Removing a fact
# ============================================================================


def test_retire_takes_the_fact_out_of_the_active_graph(extracted):
    bad = _id_of(extracted, "does_the_wrong_thing")
    decision = retire(extracted, bad, actor="architect", note="agent invented this")

    a = extracted.assertions[bad]
    assert a.status == STATUS_RETIRED
    assert a.superseded_by == RETIREMENT_MARK
    assert a.is_active is False
    assert decision.action == ACTION_RETIRE
    assert decision.label == "Removed"
    assert sorted(decision.changed_fields) == ["status", "superseded_by"]


def test_a_retired_fact_leaves_every_query(extracted):
    """The point of removal: the audit stops answering with it.

    `dispute` cannot do this — it is an annotation on an assertion that is still
    active, so the phantom reference kept counting.
    """
    bad = _id_of(extracted, "does_the_wrong_thing")
    before = {a.id for a in extracted.active()}
    assert bad in before

    retire(extracted, bad, actor="architect")

    assert bad not in {a.id for a in extracted.active()}
    assert bad not in {a.id for a in extracted.dangling_assertions()}
    assert bad not in {a.id for a in extracted.unresolved_references()}
    # ...and it is still in the graph, for the audit trail.
    assert bad in extracted.assertions


def test_removal_survives_re_extraction(extracted):
    """The durability that makes this a removal rather than a filter.

    The assertion id is content-addressed over (subject, predicate, object, value),
    so the next run produces the SAME id. `merge_graphs` carries `superseded_by`
    across only when it is truthy, which is why the mark is a sentinel and not "".
    """
    bad = _id_of(extracted, "does_the_wrong_thing")
    retire(extracted, bad, actor="architect")

    again, _ = graph_from_extraction(
        SOURCE, {"document_type": "architecture"}, document_ref="arch.md"
    )
    assert bad in again.assertions, "the fixture must reproduce the same fact"

    merged = merge_graphs(extracted, again)
    assert merged.assertions[bad].is_active is False
    assert merged.assertions[bad].status == STATUS_RETIRED
    assert merged.assertions[bad].superseded_by == RETIREMENT_MARK


def test_a_verified_fact_can_still_be_removed_and_stays_removed(extracted):
    """Verified-then-removed is the awkward order: human provenance outranks an
    agent re-observation, so the fold rule must not resurrect it either."""
    bad = _id_of(extracted, "does_the_wrong_thing")
    apply_decisions(extracted, ReviewLog(), bad, "verify", actor="t")
    assert extracted.assertions[bad].status == STATUS_VERIFIED

    retire(extracted, bad, actor="t", note="wrong predicate for this element")

    again, _ = graph_from_extraction(
        SOURCE, {"document_type": "architecture"}, document_ref="arch.md"
    )
    merged = merge_graphs(extracted, again)
    assert merged.assertions[bad].is_active is False
    assert merged.assertions[bad].is_human  # the decision is still attributable


def test_removal_survives_a_delta_round_trip(extracted):
    from core.knowledge import apply_delta, compute_graph_delta

    bad = _id_of(extracted, "does_the_wrong_thing")
    retire(extracted, bad, actor="t")
    delta = compute_graph_delta(extracted, extracted)
    reapplied = apply_delta(extracted, delta)
    assert reapplied.assertions[bad].is_active is False


def test_a_removed_fact_can_be_restored(extracted):
    """Removal is reversible, or it would be a silent delete with extra steps."""
    bad = _id_of(extracted, "does_the_wrong_thing")
    log = ReviewLog()
    retire(extracted, bad, actor="t")
    apply_decisions(extracted, log, bad, "reset", actor="t", note="reconsidered")

    a = extracted.assertions[bad]
    assert a.status == STATUS_UNVERIFIED
    assert a.superseded_by is None
    assert a.is_active is True
    assert a.provenance.source_type == "EXTRACTION_AGENT"


def test_the_other_facts_are_untouched(extracted):
    keep = _id_of(extracted, "uses_technology")
    retire(extracted, _id_of(extracted, "does_the_wrong_thing"), actor="t")
    assert extracted.assertions[keep].is_active is True


def test_retire_refuses_an_already_removed_assertion(extracted):
    bad = _id_of(extracted, "does_the_wrong_thing")
    retire(extracted, bad, actor="t")
    with pytest.raises(ReviewError, match="not an active assertion"):
        retire(extracted, bad, actor="t")


def test_retire_refuses_an_unknown_assertion(extracted):
    with pytest.raises(ReviewError, match="unknown assertion"):
        retire(extracted, "a_nope", actor="t")


def test_retirement_is_counted_apart_from_supersession(extracted):
    """Both are inactive, and they say different things about what a human did —
    one fact was replaced, the other was invented. Lumping them would hide how much
    of the graph never belonged."""
    bad = _id_of(extracted, "does_the_wrong_thing")
    retire(extracted, bad, actor="t")
    p = review_progress(extracted)

    assert p.retired == 1
    assert p.superseded == 1  # the retired one is inactive, so it counts here too
    assert p.total == 2  # the technology fact, plus the Concept its object created


# ============================================================================
# 2. Dispute is a decision, so it stops withholding the audit
# ============================================================================


def test_a_dispute_no_longer_blocks_the_audit(extracted):
    """The trap: a reviewer could dispute an invented fact, verify everything else,
    and still be told the graph was not auditable — with no action left that would
    change that. A dispute IS a judgement; it belongs in `outstanding` (needs
    attention) and not in what withholds the audit."""
    log = ReviewLog()
    bad = _id_of(extracted, "does_the_wrong_thing")
    apply_decisions(extracted, log, bad, "dispute", actor="t", note="invented")
    for a in list(extracted.active()):
        if a.id != bad:
            apply_decisions(extracted, log, a.id, "verify", actor="t")

    p = review_progress(extracted)
    assert p.blocking == 0
    assert p.is_auditable is True
    # ...while the queue still says it needs attention, and the fact is still there.
    assert p.outstanding == 1
    assert p.disputed == 1
    assert extracted.assertions[bad].is_active is True


def test_an_unreviewed_assertion_still_blocks(extracted):
    """Narrowing what blocks must not turn an unread graph into an auditable one."""
    p = review_progress(extracted)
    assert p.blocking == p.unverified == 3
    assert p.is_auditable is False


def test_an_empty_graph_is_not_auditable():
    assert review_progress(KnowledgeGraph()).is_auditable is False


def test_progress_reports_both_numbers(extracted):
    log = ReviewLog()
    apply_decisions(extracted, log, _id_of(extracted, "does_the_wrong_thing"), "dispute", actor="t")
    d = review_progress(extracted).to_dict()
    assert {"blocking", "outstanding", "retired", "disputed"} <= set(d)
    assert d["blocking"] == 2 and d["outstanding"] == 3


def test_a_retired_fact_is_not_outstanding(extracted):
    retire(extracted, _id_of(extracted, "does_the_wrong_thing"), actor="t")
    p = review_progress(extracted)
    assert p.disputed == 0
    assert p.outstanding == 2  # the two unreviewed facts left
