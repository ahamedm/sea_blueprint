"""
Ingest for the two collections the Design Assistant adds.

`architecture_patterns` and `quality_scenarios` are declared in the ontology and
had no ingest path at all, so neither could reach the graph. These tests pin the
things that make them worth ingesting rather than merely storing:

- a pattern's mandate is a ROUTED cross-graph reference, because a mandate nothing
  can bind is a claim the auditor cannot verify;
- a scenario's attribute link makes the quality census's `has_quality_scenario`
  state non-zero — the one state that has been empty since ADR-0016;
- a scenario is a scenario, not a requirement stating an attribute, even though it
  uses the same `realizes_attribute` edge;
- a design PROPOSAL is distinguishable from an extraction in the audit trail.
"""

from __future__ import annotations

from core.knowledge import (
    SOURCE_DESIGN_ASSISTANT,
    graph_from_extraction,
    quality_report,
    realization_report,
)
from core.knowledge.model import CROSS_GRAPH_PREDICATES


def build(output: dict, **kwargs):
    graph, run = graph_from_extraction(
        output,
        {"model_id": "fake-model", "model_calls": 1},
        document_ref=kwargs.pop("document_ref", "design:INIT-1@working"),
        document_text=kwargs.pop("document_text", "digest body"),
        **kwargs,
    )
    return graph, run


def pattern_output() -> dict:
    return {
        "architecture_patterns": [
            {
                "name": "Circuit Breaker",
                "pattern_category": "RESILIENCE",
                "mechanism": "Fail fast once an error threshold is crossed.",
                "rationale": "A flaky PGSP must not cascade.",
                "trade_offs": ["A state machine per dependency", "Thresholds need tuning"],
                "applies_to": ["Payment Orchestrator"],
                "realizes_quality_attributes": ["NFR-PS-001"],
                "mandated_by": ["CON-ARC-001"],
            }
        ],
        "elements": [
            {"name": "Payment Orchestrator", "element_type": "Container"},
        ],
    }


def scenario_output() -> dict:
    return {
        "entities": [
            {
                "name": "Payment Authorization Latency",
                "ontology_class": "NonFunctionalRequirement",
                "quality_attribute": "Time Behaviour",
            }
        ],
        "quality_scenarios": [
            {
                "name": "Peak-hour authorization",
                "attribute": "Time Behaviour",
                "stimulus_source": "cardholder",
                "stimulus": "submits an authorization at peak load",
                "environment": "1000 TPS sustained",
                "artifact": "Payment Gateway Platform",
                "response": "the authorization is decided",
                "response_measure": "p95 latency < 500ms",
            }
        ],
    }


# ============================================================================
# Patterns
# ============================================================================


def test_a_pattern_becomes_its_own_node_kind():
    graph, _run = build(pattern_output())
    kinds = {n.kind for n in graph.nodes.values()}
    assert "ArchitecturePattern" in kinds
    node = next(n for n in graph.nodes.values() if n.kind == "ArchitecturePattern")
    assert node.label == "Circuit Breaker"

    facts = {
        (a.predicate, a.value)
        for a in graph.active()
        if a.subject == node.id and a.object is None
    }
    assert ("pattern_category", "RESILIENCE") in facts
    assert any(p == "mechanism" for p, _ in facts)
    # `trade_offs` are structured TradeOff nodes linked via `has_trade_off`, not
    # literal `trade_off` assertions on the pattern.
    trade_offs = {
        graph.nodes[a.object].label
        for a in graph.active()
        if a.subject == node.id and a.predicate == "has_trade_off"
    }
    assert trade_offs == {"A state machine per dependency", "Thresholds need tuning"}


def test_a_pattern_links_to_the_element_it_governs():
    graph, _run = build(pattern_output())
    pattern = next(n for n in graph.nodes.values() if n.kind == "ArchitecturePattern")
    element = next(n for n in graph.nodes.values() if n.label == "Payment Orchestrator")
    assert any(
        a.subject == element.id and a.predicate == "applies_pattern" and a.object == pattern.id
        for a in graph.active()
    )


def test_a_patterns_mandate_is_a_routed_cross_graph_reference():
    """A mandate nothing can bind is a claim the auditor cannot verify."""
    from core.knowledge import reference_candidates

    graph, _run = build(pattern_output())
    pattern = next(n for n in graph.nodes.values() if n.kind == "ArchitecturePattern")
    mandate = next(
        a for a in graph.active() if a.subject == pattern.id and a.predicate == "mandated_by"
    )
    assert "mandated_by" in CROSS_GRAPH_PREDICATES
    assert mandate.object is None, "a cross-graph target is kept literal"
    assert mandate.target == "CON-ARC-001"
    assert mandate in graph.unresolved_references()

    # Reconciliation offers it, scoped to requirement kinds.
    proposals = reference_candidates(graph)
    assert any(rc.assertion_id == mandate.id for rc in proposals)

    # But it is NOT a realization claim: "the requirement mandates this pattern"
    # runs the other way from "this answers the requirement". Adding it to
    # REALIZATION_PREDICATES would report every mandated pattern as an answer.
    report = realization_report(graph)
    assert not any(c["predicate"] == "mandated_by" for c in report["claims"])


def test_a_patterns_quality_link_is_literal_and_joinable():
    graph, _run = build(pattern_output())
    pattern = next(n for n in graph.nodes.values() if n.kind == "ArchitecturePattern")
    link = next(
        a
        for a in graph.active()
        if a.subject == pattern.id and a.predicate == "realizes_quality_attribute"
    )
    assert link.target == "NFR-PS-001"


# ============================================================================
# Scenarios
# ============================================================================


def test_a_scenario_carries_the_atam_fields():
    graph, _run = build(scenario_output())
    scenario = next(n for n in graph.nodes.values() if n.kind == "QualityScenario")
    facts = {
        a.predicate: a.value
        for a in graph.active()
        if a.subject == scenario.id and a.object is None
    }
    assert facts["stimulus_source"] == "cardholder"
    assert facts["environment"] == "1000 TPS sustained"
    assert facts["response_measure"] == "p95 latency < 500ms"


def test_a_scenario_operationalises_its_attribute():
    graph, _run = build(scenario_output())
    scenario = next(n for n in graph.nodes.values() if n.kind == "QualityScenario")
    attribute = next(n for n in graph.nodes.values() if n.kind == "QualityAttribute")
    assert attribute.label == "Time Behaviour"
    assert any(
        a.subject == scenario.id and a.predicate == "realizes_attribute" and a.object == attribute.id
        for a in graph.active()
    )


def test_a_scenario_populates_the_census_state_that_was_always_empty():
    """ADR-0016's `has_quality_scenario` read 0 on every graph. This is the change."""
    graph, _run = build(scenario_output())
    summary = quality_report(graph)["summary"]
    assert summary["states"]["has_quality_scenario"] == 1
    assert "has_quality_scenario" not in summary["unpopulated_states"]


def test_a_scenario_is_not_counted_as_a_requirement_stating_the_attribute():
    """The two edges share a predicate; the SOURCE kind is what separates them.

    A scenario linking to an attribute must not read as "a requirement is about
    this attribute", or the census's stated/delivered distinction collapses.
    """
    graph, _run = build(scenario_output())
    summary = quality_report(graph)["summary"]
    assert summary["states"]["stated_in_requirements"] == 1  # the NFR only
    assert summary["states"]["has_quality_scenario"] == 1    # the scenario, separately


# ============================================================================
# Provenance
# ============================================================================


def test_a_proposal_is_distinguishable_from_an_extraction():
    graph, _run = build(pattern_output(), source_type=SOURCE_DESIGN_ASSISTANT)
    assert graph.active()
    assert all(a.provenance.source_type == SOURCE_DESIGN_ASSISTANT for a in graph.active())
    assert not any(a.is_human for a in graph.active()), "a proposal still needs review"


def test_the_default_source_type_is_still_extraction():
    """The new keyword must not change what every existing caller records."""
    graph, _run = build(pattern_output())
    assert all(a.provenance.source_type == "EXTRACTION_AGENT" for a in graph.active())
