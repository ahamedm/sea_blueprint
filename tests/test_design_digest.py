"""
Graph -> prompt digest.

The digest is the Design Assistant's "document". Three things decide whether it is
usable, and each is a test here:

1. **Deterministic** — the same graph renders the same text, so a diff between two
   runs is a diff between two models, not two renderings.
2. **A document, not a dump** — node ids must not leak (`qualityattribute:time_behaviour`
   is not English), and the identifier a design must cite has to be present.
3. **Honest about its own limits** — completeness travels with it, and a budget cut
   is recorded rather than silently taken.
"""

from __future__ import annotations

from core.knowledge import graph_from_extraction, merge_graphs
from core.knowledge.digest import (
    architecture_digest,
    design_input,
    requirements_digest,
)


def requirement_graph() -> object:
    graph, _run = graph_from_extraction(
        {
            "initiatives": [{"id": "INIT-1", "name": "Payments"}],
            "entities": [
                {
                    "name": "Payment Request Validation",
                    "ontology_class": "FunctionalRequirement",
                    "requirement_id": "FR-PM-001",
                    "source_text": "The platform shall validate every request.",
                },
                {
                    "name": "Payment Authorization Latency",
                    "ontology_class": "NonFunctionalRequirement",
                    "requirement_id": "NFR-PS-001",
                    "quality_attribute": "Time Behaviour",
                    "quality_category": "PERFORMANCE_EFFICIENCY",
                    "subcharacteristic": "TIME_BEHAVIOUR",
                },
                {
                    "name": "Horizontal Scaling",
                    "ontology_class": "ConstraintRequirement",
                    "requirement_id": "NFR-PM-002",
                    "quality_attribute": "Scalability",
                },
            ],
        },
        {"model_id": "fake", "document_type": "requirements"},
        document_ref="req.md",
        document_text="req body",
    )
    return graph


def architecture_graph() -> object:
    graph, _run = graph_from_extraction(
        {
            "elements": [
                {"name": "Payment Gateway Platform", "element_type": "SoftwareSystem"},
                {
                    "name": "Payment Orchestrator",
                    "element_type": "Container",
                    "parent": "Payment Gateway Platform",
                    "responsibilities": ["Acceptance and validation of payment requests"],
                },
            ],
            "design_techniques": [
                {
                    "name": "Redundancy / Replicas",
                    "satisfies_attributes": ["Availability"],
                }
            ],
            "references": [
                {
                    "element": "Payment Orchestrator",
                    "relationship": "implements_requirement",
                    "reference": "FR-PM-001",
                }
            ],
        },
        {"model_id": "fake", "document_type": "architecture"},
        document_ref="arch.md",
        document_text="arch body",
    )
    return graph


def merged() -> object:
    return merge_graphs(requirement_graph(), architecture_graph())


# ============================================================================
# Determinism and shape
# ============================================================================


def test_the_same_graph_renders_the_same_text():
    graph = merged()
    assert requirements_digest(graph).text == requirements_digest(graph).text
    assert design_input(graph).text == design_input(graph).text


def test_the_digest_is_a_document_not_a_database_dump():
    """Node ids are not English; a prompt full of them is a prompt full of noise."""
    text = design_input(merged()).text
    assert "qualityattribute:" not in text
    assert "functionalrequirement:" not in text
    assert "softwaresystem:" not in text


def test_requirement_identifiers_travel_with_the_requirement():
    """They are the join keys a design cites; losing them makes the draft unlinkable."""
    text = requirements_digest(merged()).text
    assert "FR-PM-001" in text
    assert "NFR-PS-001" in text
    assert "NFR-PM-002" in text


def test_the_quality_classification_travels():
    text = requirements_digest(merged()).text
    assert "TIME_BEHAVIOUR" in text
    assert "Time Behaviour" in text


def test_the_document_quotes_the_requirement_where_the_graph_stored_it():
    """Nothing populates requirement source text today, so the line is simply absent.

    Pinned deliberately: if the requirements profile starts carrying the statement
    through, this is where the digest should begin quoting it.
    """
    graph = merged()
    requirement = next(
        n for n in graph.nodes.values() if n.label == "Payment Request Validation"
    )
    assert not any(
        a.source_text for a in graph.active() if a.subject == requirement.id
    )
    text = requirements_digest(graph).text
    assert "source:" not in text


# ============================================================================
# What the design actually needs from the architecture
# ============================================================================


def test_the_digest_says_which_requirements_are_already_answered():
    text = architecture_digest(merged()).text
    assert "already answers" in text
    assert "Payment Request Validation" in text
    assert "Payment Orchestrator" in text


def test_the_digest_lists_the_unanswered_requirements_as_the_worklist():
    text = architecture_digest(merged()).text
    assert "NO architectural answer" in text
    assert "Payment Authorization Latency" in text
    assert "Horizontal Scaling" in text


def test_existing_elements_and_techniques_are_present_to_extend():
    text = architecture_digest(merged()).text
    assert "Payment Orchestrator" in text
    assert "Redundancy / Replicas" in text


# ============================================================================
# Honesty
# ============================================================================


def test_the_completeness_caveat_travels_with_the_input():
    """Absence of a requirement is not evidence until the run says it is."""
    digest = design_input(merged())
    assert any("Extraction" in c for c in digest.caveats), digest.caveats
    assert "Extraction" in digest.text


def test_no_baseline_is_stated_rather_than_assumed():
    digest = design_input(merged())
    assert digest.base_ref == "working (no frozen baseline)"
    assert any("no frozen baseline" in c for c in digest.caveats)


def test_a_baseline_is_used_and_named_when_one_is_given():
    baseline = architecture_graph()
    digest = design_input(requirement_graph(), baseline=baseline, base_ref="rev_baseline")
    assert digest.base_ref == "rev_baseline"
    assert "rev_baseline" in digest.text
    assert "Payment Orchestrator" in digest.text
    assert not any("no frozen baseline" in c for c in digest.caveats)


def test_a_budget_cut_is_recorded_and_ordered():
    """A design that silently saw half the requirements is a wrong design."""
    detail_cut = requirements_digest(merged(), budget_chars=250)
    assert detail_cut.caveats, "a cut must be reported"
    assert "dropped" in detail_cut.caveats[0]
    assert len(detail_cut.text) <= 250

    truncated = requirements_digest(merged(), budget_chars=120)
    assert any("TRUNCATED" in c for c in truncated.caveats)
    assert len(truncated.text) <= 120

    # The architecture degrades in its own order: responsibilities first.
    arch_cut = architecture_digest(merged(), budget_chars=600)
    assert any("responsibilities" in c for c in arch_cut.caveats), arch_cut.caveats
    assert len(arch_cut.text) <= 600


def test_a_generous_budget_cuts_nothing():
    section = requirements_digest(merged(), budget_chars=100000)
    assert section.caveats == []
    assert "FR-PM-001" in section.text


def test_the_quality_section_reports_coverage_not_just_names():
    text = design_input(merged()).text
    assert "Quality attributes and their coverage" in text
    assert "Stated and delivered" in text or "stated" in text.lower()
