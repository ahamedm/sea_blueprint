"""Re-extraction must not destroy human work.

This is the "sleeper" problem from docs/architecture-review.md §3.3: the review
gate edits extracted knowledge, then someone re-runs extraction with a better
prompt. If extraction replaces the graph, the human work is silently destroyed.
"""

import copy

from agents.knowledge import KnowledgeGraph, Provenance, ReviewLog, apply_decisions, merge_graphs
from agents.knowledge.model import SOURCE_EXTRACTION


def test_human_correction_survives_re_extraction(req_extraction):
    target = next(a for a in req_extraction.active() if a.predicate == "enforces")
    log = ReviewLog()
    decision = apply_decisions(
        req_extraction,
        log,
        target.id,
        "correct",
        actor="tester",
        target="Least Privilege Access Control",
    )

    # The agent re-observes its original, wrong fact.
    incoming = KnowledgeGraph()
    incoming.add_assertion(
        target.subject,
        "enforces",
        obj=target.object,
        value=target.value,
        confidence=0.95,
        provenance=Provenance(source_type=SOURCE_EXTRACTION, run_id="run_2"),
    )
    merged = merge_graphs(req_extraction, incoming)

    facts = merged.find(subject=target.subject, predicate="enforces")
    assert any(
        a.value == "Least Privilege Access Control" and a.is_human for a in facts
    ), "the human correction was destroyed by re-extraction"
    assert all(
        a.value != target.target for a in facts
    ), "the superseded agent fact was resurrected alongside the correction"
    assert len(facts) == 1, "re-observation must fold, not add a parallel fact"
    assert merged.assertions[decision.replacement_id].status == "CORRECTED"


def test_re_observation_raises_confidence_without_duplicating(req_extraction):
    before = len(req_extraction.assertions)
    target = next(
        a
        for a in req_extraction.active()
        if a.confidence < 0.9 and a.predicate not in ("traces_to_goal",)
    )

    incoming = KnowledgeGraph()
    incoming.add_assertion(
        target.subject,
        target.predicate,
        obj=target.object,
        value=target.value,
        confidence=0.99,
        source_text="a considerably longer source text than the original",
        provenance=Provenance(source_type=SOURCE_EXTRACTION, run_id="run_2"),
    )

    merged = merge_graphs(req_extraction, incoming)
    assert len(merged.assertions) == before, "the same fact must fold, not duplicate"

    folded = merged.assertions[target.id]
    assert folded.confidence == 0.99
    assert folded.source_text == "a considerably longer source text than the original"


def test_merge_does_not_mutate_the_input_graph(req_extraction):
    snapshot = copy.deepcopy(req_extraction)
    incoming = KnowledgeGraph()
    incoming.add_node("SoftwareSystem", "Brand New Element")
    incoming.add_assertion("softwaresystem:brand_new_element", "description", value="new")

    merged = merge_graphs(req_extraction, incoming)

    assert req_extraction.nodes.keys() == snapshot.nodes.keys()
    assert req_extraction.assertions.keys() == snapshot.assertions.keys()
    assert "softwaresystem:brand_new_element" in merged.nodes


def test_merge_accumulates_runs_so_each_stays_attributable(req_extraction):
    incoming = KnowledgeGraph()
    incoming.add_node("Concept", "x")
    from agents.knowledge.model import ExtractionRun, Provenance

    incoming.runs["run_second"] = ExtractionRun(id="run_second", document_ref="second.md")
    incoming.add_assertion(
        "concept:x",
        "description",
        value="y",
        provenance=Provenance(source_type=SOURCE_EXTRACTION, run_id="run_second"),
    )

    merged = merge_graphs(req_extraction, incoming)
    assert "run_second" in merged.runs
    assert len(merged.runs) == len(req_extraction.runs) + 1


def test_merge_of_human_assertion_keeps_human_authority(req_extraction):
    target = next(iter(req_extraction.active()))
    log = ReviewLog()
    apply_decisions(req_extraction, log, target.id, "verify", actor="tester")

    incoming = KnowledgeGraph()
    incoming.add_assertion(
        target.subject,
        target.predicate,
        obj=target.object,
        value=target.value,
        confidence=1.0,
        provenance=Provenance(source_type=SOURCE_EXTRACTION, run_id="run_2"),
    )
    merged = merge_graphs(req_extraction, incoming)
    assert merged.assertions[target.id].is_human
    assert merged.assertions[target.id].status == "VERIFIED"
