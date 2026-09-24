"""The review write path — the gate's record of what a human decided."""

import pytest

from core.knowledge import (
    ReviewError,
    ReviewLog,
    apply_decisions,
    bulk_verify,
    promote_to_baseline,
    review_progress,
)
from core.knowledge.model import (
    SCOPE_BASELINE,
    SCOPE_INITIATIVE,
    SOURCE_HUMAN_REVIEWER,
    STATUS_CORRECTED,
    STATUS_DISPUTED,
    STATUS_SUPERSEDED,
    STATUS_UNVERIFIED,
    STATUS_VERIFIED,
)


# Executor bookkeeping, not extracted facts. These edges exist to scope an
# ingest and are written by the ingest itself, so "a fact a reviewer can verify"
# never means one of them.
_BOOKKEEPING = {"authorised_by_initiative", "delivers_initiative"}


def a_fact(graph, predicate=None):
    """The first active assertion a reviewer would actually be looking at.

    Skips human decisions and the executor's own scoping edges, so the choice does
    not depend on insertion order: every caller means "a fact the extractor
    produced", and reordering the ingest passes used to silently change which one
    that was.
    """
    for a in graph.active():
        if predicate is not None and a.predicate != predicate:
            continue
        if predicate is None and (a.is_human or a.predicate in _BOOKKEEPING):
            continue
        return a
    raise AssertionError(f"no active assertion for {predicate!r}")


def test_verify_marks_status_and_flips_provenance_to_human(req_extraction):
    target = a_fact(req_extraction)
    log = ReviewLog()
    decision = apply_decisions(
        req_extraction, log, target.id, "verify", actor="tester", note="matches §3.2"
    )

    a = req_extraction.assertions[target.id]
    assert a.status == STATUS_VERIFIED
    assert a.provenance.source_type == SOURCE_HUMAN_REVIEWER
    assert a.provenance.asserted_by == "tester"
    assert a.is_human
    assert decision.action == "verify"
    assert decision.changed_fields == ["confidence", "status"]
    assert log.entries == [decision]


def test_dispute_keeps_the_claim_visible(req_extraction):
    """Silently deleting a rejected claim is worse than keeping it flagged."""
    target = a_fact(req_extraction)
    log = ReviewLog()
    apply_decisions(
        req_extraction, log, target.id, "dispute", actor="tester", note="not in the doc"
    )

    a = req_extraction.assertions[target.id]
    assert a.status == STATUS_DISPUTED
    assert a.is_active, "a disputed assertion must remain in the graph"
    assert target.id in {x.id for x in req_extraction.active()}
    assert review_progress(req_extraction).disputed == 1


def test_reset_returns_an_assertion_to_the_queue(req_extraction):
    target = a_fact(req_extraction)
    log = ReviewLog()
    apply_decisions(req_extraction, log, target.id, "verify", actor="tester")
    apply_decisions(req_extraction, log, target.id, "reset", actor="tester")

    a = req_extraction.assertions[target.id]
    assert a.status == STATUS_UNVERIFIED
    assert not a.is_human, "reopening must stop attributing the claim to a human"
    assert review_progress(req_extraction).unverified >= 1


def test_correction_supersedes_rather_than_mutating(req_extraction):
    target = a_fact(req_extraction)
    log = ReviewLog()
    decision = apply_decisions(
        req_extraction,
        log,
        target.id,
        "correct",
        actor="tester",
        target="Corrected Target",
        note="doc says so",
    )

    old = req_extraction.assertions[target.id]
    assert old.status == STATUS_SUPERSEDED
    assert old.superseded_by == decision.replacement_id
    assert not old.is_active

    new = req_extraction.assertions[decision.replacement_id]
    assert new.status == STATUS_CORRECTED
    assert new.value == "Corrected Target"
    assert new.is_human
    assert new.provenance.correction_note == "doc says so"
    assert new.confidence == 1.0


def test_correction_to_the_same_target_does_not_supersede_itself(req_extraction):
    target = a_fact(req_extraction)
    log = ReviewLog()
    decision = apply_decisions(
        req_extraction,
        log,
        target.id,
        "correct",
        actor="tester",
        target=target.target,
        note="confirmed as written",
    )

    assert decision.replacement_id == target.id
    a = req_extraction.assertions[target.id]
    assert a.status == STATUS_CORRECTED
    assert a.superseded_by is None, "an assertion must not point at itself"
    assert a.is_active


def test_correction_of_a_cross_graph_reference_stays_a_literal(req_extraction):
    """Turning a reference into a local node would destroy the unresolved/resolved
    distinction that reconciliation depends on."""
    reference = a_fact(req_extraction, "traces_to_goal")
    log = ReviewLog()
    decision = apply_decisions(
        req_extraction,
        log,
        reference.id,
        "correct",
        actor="tester",
        target="Lower Payment Failure Rate",
    )

    new = req_extraction.assertions[decision.replacement_id]
    assert new.value == "Lower Payment Failure Rate"
    assert new.object is None
    assert "concept:lower_payment_failure_rate" not in req_extraction.nodes


def test_correction_can_repoint_to_an_existing_node(req_extraction):
    target = a_fact(req_extraction, "enforces")
    other = req_extraction.nodes["functionalrequirement:payment_acceptance"]
    log = ReviewLog()
    decision = apply_decisions(
        req_extraction, log, target.id, "correct", actor="tester", target=other.label
    )

    new = req_extraction.assertions[decision.replacement_id]
    assert new.object == other.id
    assert new.value is None


def test_unknown_assertion_and_action_are_rejected(req_extraction):
    log = ReviewLog()
    with pytest.raises(ReviewError, match="unknown assertion"):
        apply_decisions(req_extraction, log, "a_does_not_exist", "verify")
    with pytest.raises(ReviewError, match="unknown action"):
        apply_decisions(req_extraction, log, a_fact(req_extraction).id, "explode")


def test_passing_an_assertion_object_instead_of_an_id_fails_clearly(req_extraction):
    with pytest.raises(ReviewError, match="expected an assertion id string"):
        apply_decisions(req_extraction, ReviewLog(), a_fact(req_extraction), "verify")


def test_bulk_verify_by_threshold_only_touches_unverified(req_extraction):
    log = ReviewLog()
    already = a_fact(req_extraction)
    apply_decisions(req_extraction, log, already.id, "verify", actor="tester")

    decisions = bulk_verify(
        req_extraction, log, max_confidence=0.75, actor="tester", note="low-confidence sweep"
    )

    assert decisions, "the fixture has facts below the threshold"
    for d in decisions:
        assert req_extraction.assertions[d.assertion_id].status == STATUS_VERIFIED
    assert req_extraction.assertions[already.id].provenance.asserted_by == "tester"
    assert all(d.note == "low-confidence sweep" for d in decisions)


def test_bulk_verify_ignores_ids_that_are_already_settled(req_extraction):
    log = ReviewLog()
    target = a_fact(req_extraction)
    apply_decisions(req_extraction, log, target.id, "dispute", actor="tester")
    before = len(log.entries)

    decisions = bulk_verify(req_extraction, log, assertion_ids=[target.id], actor="tester")
    assert decisions == []
    assert len(log.entries) == before


def test_bulk_verify_ignores_unknown_ids(req_extraction):
    decisions = bulk_verify(req_extraction, ReviewLog(), assertion_ids=["a_nope"], actor="tester")
    assert decisions == []


def test_progress_reports_an_auditability_gate(req_extraction):
    progress = review_progress(req_extraction)
    assert progress.total == len(list(req_extraction.active()))
    assert progress.unverified == progress.total
    assert not progress.is_auditable
    assert progress.percent_reviewed == 0

    for a in list(req_extraction.active()):
        apply_decisions(req_extraction, ReviewLog(), a.id, "verify", actor="tester")

    settled = review_progress(req_extraction)
    assert settled.outstanding == 0
    assert settled.is_auditable
    assert settled.percent_reviewed == 100


def test_promote_to_baseline_leaves_unchecked_facts_behind(req_extraction):
    log = ReviewLog()
    verified = a_fact(req_extraction, "has_functional_requirement")
    disputed = a_fact(req_extraction, "traces_to_goal")
    apply_decisions(req_extraction, log, verified.id, "verify", actor="tester")
    apply_decisions(req_extraction, log, disputed.id, "dispute", actor="tester")

    result, decision = promote_to_baseline(req_extraction, log, actor="tester")

    assert req_extraction.assertions[verified.id].scope == SCOPE_BASELINE
    assert req_extraction.assertions[disputed.id].scope == SCOPE_INITIATIVE
    assert result.promoted == 1
    assert result.skipped_disputed == 1
    assert result.skipped_unverified >= 1
    assert verified.id in decision.promoted_ids
    assert decision.action == "promote_baseline"
    assert log.entries[-1] is decision


def test_superseded_facts_are_not_promoted(req_extraction):
    log = ReviewLog()
    target = a_fact(req_extraction)
    apply_decisions(req_extraction, log, target.id, "correct", actor="tester", target="New")
    result, _ = promote_to_baseline(req_extraction, log, actor="tester")
    assert req_extraction.assertions[target.id].scope == SCOPE_INITIATIVE
    assert req_extraction.assertions[target.id].status == STATUS_SUPERSEDED
