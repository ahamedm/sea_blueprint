"""
Reflexive facts are refused at the write boundary, and the refusal is reported (YB-052).

WHY REFUSAL AND NOT A WARNING. `X part_of X` is not a borderline extraction: nothing
can be part of itself, so no reviewer judgement is being overridden by refusing it.
A local model emitted four of them across two runs on one document, the C4 view
reported them, and then a bulk verify made all four VERIFIED — a review gate that
accepted something no reviewer could have intended. Two things follow, and each
needs its own guard:

  1. **They cannot enter.** `KnowledgeGraph.add_assertion` is where every extracted
     fact (and every human correction) crosses, so the rule lives there rather than
     in a prompt the model may ignore.
  2. **An old revision cannot launder them.** A graph written before the guard can
     still hold one, so `verify` refuses it too — singly and in bulk — and says so,
     because a bulk action that reports only a count cannot tell "already decided"
     from "refused as impossible".
"""

from __future__ import annotations

import pytest

from core.knowledge import (
    IRREFLEXIVE_PREDICATES,
    KnowledgeGraph,
    ReviewError,
    ReviewLog,
    bulk_apply,
    graph_from_extraction,
    verify,
)
from core.knowledge.model import (
    STATUS_UNVERIFIED,
    STATUS_VERIFIED,
    Assertion,
    Provenance,
    make_assertion_id,
)


def _graph_with_two_elements():
    graph = KnowledgeGraph()
    system = graph.add_node("SoftwareSystem", "Payments")
    container = graph.add_node("Container", "Orchestrator")
    return graph, system, container


def _inject_an_old_reflexive_fact(graph, node_id):
    """A reflexive assertion, written straight into the graph.

    The only way one exists now is that a revision was saved before the guard, so a
    test of "can this be verified" has to reproduce that state rather than ask the
    write boundary to produce it.
    """
    aid = make_assertion_id(node_id, "part_of", node_id, None)
    graph.assertions[aid] = Assertion(
        id=aid, subject=node_id, predicate="part_of", object=node_id,
        confidence=1.0, provenance=Provenance(),
        source_text="Payments is part of Payments",
    )
    return aid


# ============================================================================
# 1. The refusal at the write boundary
# ============================================================================


def test_a_reflexive_fact_is_refused_and_recorded_not_dropped():
    graph, system, _container = _graph_with_two_elements()

    returned = graph.add_assertion(
        system, "part_of", obj=system, confidence=1.0,
        source_text="Payments is part of Payments",
    )

    assert returned is None, "a refused assertion must not be returned as stored"
    assert graph.assertions == {}, "the fact was refused, so nothing is in the graph"
    assert len(graph.refusals) == 1
    refusal = graph.refusals[0]
    assert refusal.predicate == "part_of"
    assert refusal.subject == system
    assert "irreflexive" in refusal.reason
    assert refusal.source_text == "Payments is part of Payments"


def test_a_real_containment_is_still_stored():
    graph, system, container = _graph_with_two_elements()

    stored = graph.add_assertion(container, "part_of", obj=system, confidence=1.0)

    assert stored is not None
    assert stored.predicate == "part_of"
    assert graph.refusals == []


@pytest.mark.parametrize("predicate", sorted(IRREFLEXIVE_PREDICATES))
def test_every_declared_irreflexive_predicate_is_refused(predicate):
    graph, system, _container = _graph_with_two_elements()

    assert graph.add_assertion(system, predicate, obj=system) is None
    assert graph.refusals[-1].predicate == predicate


def test_a_self_reference_on_an_ordinary_predicate_is_allowed():
    """The rule is about specific predicates, not a blanket ban on self-reference."""
    graph, system, _container = _graph_with_two_elements()

    stored = graph.add_assertion(system, "related_to", obj=system)

    assert stored is not None
    assert graph.refusals == []


def test_a_literal_assertion_is_never_refused():
    """`description` has no object, so there is nothing for the rule to compare."""
    graph, system, _container = _graph_with_two_elements()

    assert graph.add_assertion(system, "description", value="Payments") is not None
    assert graph.add_assertion(system, "hosts", value="") is not None
    assert graph.refusals == []


# ============================================================================
# 2. The refusal reaches the run record
# ============================================================================


def test_ingest_refuses_the_triple_and_reports_it_on_the_run():
    output = {
        "elements": [{"name": "Payments", "element_type": "SoftwareSystem"}],
        "triples": [
            {"subject": "Payments", "predicate": "part_of", "object": "Payments",
             "source_text": "Payments is part of Payments"},
            {"subject": "Payments", "predicate": "uses_technology", "object": "PostgreSQL"},
        ],
    }

    graph, run = graph_from_extraction(
        output, {"model_id": "stub"}, document_ref="arch.md",
        document_text="Payments is part of Payments and uses PostgreSQL.",
    )

    assert not [a for a in graph.active() if a.predicate == "part_of"]
    # The sibling fact in the same output is untouched: one bad triple does not
    # cost the run its other facts.
    assert [a for a in graph.active() if a.predicate == "uses_technology"]
    assert [r["predicate"] for r in run.refusals] == ["part_of"]
    assert "irreflexive" in run.refusals[0]["reason"]


def test_an_ordinary_run_records_no_refusals():
    output = {
        "elements": [
            {"name": "Payments", "element_type": "SoftwareSystem"},
            {"name": "Orchestrator", "element_type": "Container", "parent": "Payments"},
        ],
    }

    _graph, run = graph_from_extraction(output, {"model_id": "stub"},
                                        document_ref="arch.md", document_text="...")

    assert run.refusals == []


# ============================================================================
# 3. The review gate cannot launder an old one
# ============================================================================


def test_a_single_verify_refuses_a_reflexive_fact():
    graph, system, _container = _graph_with_two_elements()
    aid = _inject_an_old_reflexive_fact(graph, system)

    with pytest.raises(ReviewError, match="reflexive"):
        verify(graph, aid, actor="tester")

    assert graph.assertions[aid].status == STATUS_UNVERIFIED


def test_a_bulk_verify_refuses_it_and_names_the_reason():
    graph, system, _container = _graph_with_two_elements()
    aid = _inject_an_old_reflexive_fact(graph, system)
    log = ReviewLog()

    result = bulk_apply(graph, log, [aid], "verify", actor="tester")

    assert result.decisions == []
    assert len(result.skipped) == 1
    assert "irreflexive" in result.skipped[0].reason
    assert [s.assertion_id for s in result.refused] == [aid]
    assert graph.assertions[aid].status == STATUS_UNVERIFIED
    assert log.entries == []


def test_a_bulk_verify_still_verifies_the_ordinary_selection():
    """The refusal is per-assertion: one impossible fact does not block the batch."""
    graph, system, container = _graph_with_two_elements()
    aid = _inject_an_old_reflexive_fact(graph, system)
    ordinary = graph.add_assertion(container, "part_of", obj=system, confidence=1.0)

    result = bulk_apply(graph, ReviewLog(), [aid, ordinary.id], "verify", actor="t")

    assert [d.assertion_id for d in result.decisions] == [ordinary.id]
    assert graph.assertions[ordinary.id].status == STATUS_VERIFIED
    assert graph.assertions[aid].status == STATUS_UNVERIFIED


def test_a_correction_to_a_reflexive_form_is_refused():
    """Correcting an assertion is another write of the same impossible fact."""
    from core.knowledge import correct

    graph, system, container = _graph_with_two_elements()
    aid = graph.add_assertion(container, "part_of", obj=system, confidence=1.0).id

    with pytest.raises(ReviewError, match="reflexive"):
        # Correcting the target to the assertion's OWN subject: `Orchestrator
        # part_of Orchestrator`, which is the impossible form by another route.
        correct(graph, aid, corrected_target=container, actor="reviewer")

    # And the original is not superseded by a replacement the graph does not hold.
    assert graph.assertions[aid].status == STATUS_UNVERIFIED
