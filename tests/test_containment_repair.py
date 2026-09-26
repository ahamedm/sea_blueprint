"""
Cross-chunk containment repair.

THE DEFECT. Each pass sees one chunk. An element whose container is named in a
different chunk comes back with `parent` empty, and C4 loses the hierarchy —
measured on `test_data/arch/payment_platform_arch.md`, where four elements were
unplaced while every other structural invariant passed.

The repair is deterministic and recorded. These tests pin three properties that
matter more than the happy path:

  1. it REPAIRS what it can and the validator then agrees (the two must not
     disagree — one is the fix, the other is the check),
  2. it DECLINES when the graph offers no unambiguous anchor, leaving the ordinary
     finding in place rather than inventing a parent,
  3. it NEVER removes a fact: a style emitted as an element is reported, not
     deleted, because the element carries real edges.
"""

from __future__ import annotations

from agents.architecture_extraction.repair import (
    merge_style_elements,
    repair_containment,
    style_as_element,
    system_under_design,
)
from agents.extraction.validators import check_containment
from agents.knowledge_extraction.agent import ExtractedTriple


def _elements():
    return [
        {"name": "Payment Gateway Platform", "element_type": "SoftwareSystem",
         "system_class": "BUSINESS_TECHNOLOGY_PLATFORM"},
        {"name": "Settlement and Reconciliation Container", "element_type": "Container",
         "parent": ""},
        {"name": "Database", "element_type": "DataStore", "parent": ""},
        {"name": "Operational Dashboard", "element_type": "Container", "parent": ""},
    ]


# ============================================================================
# 1. The repair, and agreement with the check
# ============================================================================


def test_an_unplaced_container_is_attached_to_the_system_under_design():
    elements, triples, flags = repair_containment(_elements(), [])

    settlement = next(e for e in elements if e["name"].startswith("Settlement"))
    assert settlement["parent"] == "Payment Gateway Platform"
    assert [f.subject for f in flags] == [
        "Settlement and Reconciliation Container", "Database", "Operational Dashboard",
    ]
    assert all(f.kind == "containment_repaired" for f in flags)


def test_the_repair_emits_the_part_of_edge_the_declaration_requires():
    """A declared parent with no edge is itself a finding, so the repair must add both."""
    elements, triples, _ = repair_containment(_elements(), [])

    edges = {(t.subject, t.predicate, t.object) for t in triples}
    assert ("Database", "part_of", "Payment Gateway Platform") in edges
    assert all(isinstance(t, ExtractedTriple) for t in triples)


def test_the_repair_and_the_validator_agree():
    """The crossing check: the fix must silence the check it exists to satisfy."""
    elements, triples, _ = repair_containment(_elements(), [])
    assert check_containment(elements, triples) == []


def test_an_existing_part_of_edge_is_not_duplicated():
    triples = [ExtractedTriple(subject="Database", predicate="part_of",
                               object="Payment Gateway Platform", confidence=1.0)]
    _, out, _ = repair_containment(_elements(), triples)
    assert [t for t in out if t.subject == "Database"] == triples


def test_a_placed_element_is_left_alone():
    elements = [
        {"name": "System", "element_type": "SoftwareSystem"},
        {"name": "Placed", "element_type": "Container", "parent": "System"},
    ]
    out, triples, flags = repair_containment(elements, [])
    assert out[1]["parent"] == "System"
    assert flags == [] and triples == []


# ============================================================================
# 2. Declining — the honest outcome when there is no anchor
# ============================================================================


def test_it_declines_when_the_anchor_is_ambiguous():
    """Two domain platforms and a floating container: no repair, finding stands."""
    elements = [
        {"name": "System A", "element_type": "SoftwareSystem",
         "system_class": "BUSINESS_APPLICATION"},
        {"name": "System B", "element_type": "SoftwareSystem",
         "system_class": "BUSINESS_TECHNOLOGY_PLATFORM"},
        {"name": "Floating", "element_type": "Container", "parent": ""},
    ]
    out, triples, flags = repair_containment(elements, [])

    assert out[2]["parent"] == ""
    assert triples == [] and flags == []
    # Declining must not hide the defect: the validator still reports it.
    assert [f.subject for f in check_containment(out, triples)] == ["Floating"]


def test_a_single_system_of_any_class_is_an_anchor():
    elements = [
        {"name": "Only System", "element_type": "SoftwareSystem", "system_class": ""},
        {"name": "Floating", "element_type": "Container", "parent": ""},
    ]
    out, _, _ = repair_containment(elements, [])
    assert out[1]["parent"] == "Only System"


def test_an_enterprise_platform_is_not_an_anchor():
    """OpenShift is not where a business container lives, so it cannot be the anchor."""
    elements = [
        {"name": "OpenShift", "element_type": "SoftwareSystem",
         "system_class": "ENTERPRISE_TECHNOLOGY_PLATFORM"},
        {"name": "Floating", "element_type": "Container", "parent": ""},
    ]
    assert system_under_design(elements) == ""
    out, triples, flags = repair_containment(elements, [])
    assert out[1]["parent"] == "" and flags == [] and triples == []

    # With a domain system present, that is the one chosen.
    mixed = elements + [
        {"name": "Payments", "element_type": "SoftwareSystem",
         "system_class": "BUSINESS_TECHNOLOGY_PLATFORM"},
    ]
    assert system_under_design(mixed) == "Payments"


# ============================================================================
# 3. Never remove a fact
# ============================================================================


def test_a_style_emitted_as_an_element_is_flagged_not_removed():
    """The element carried 24 real edges in the measured run; deleting it would
    discard every one, so the leak is reported for a reviewer to act on."""
    elements = _elements() + [
        {"name": "Microservices", "element_type": "Container", "parent": ""},
    ]
    flags = style_as_element(elements)

    assert [f.subject for f in flags] == ["Microservices"]
    assert flags[0].kind == "style_as_element"
    assert any(e["name"] == "Microservices" for e in elements)


def test_a_phrase_style_is_recognised_by_its_token():
    """Sources state styles as phrases; that is the same leak, not a different one."""
    flags = style_as_element([
        {"name": "Stateless Modular Microservices", "element_type": "Container"},
        {"name": "Payment Orchestrator", "element_type": "Container"},
    ])
    assert [f.subject for f in flags] == ["Stateless Modular Microservices"]


def test_a_business_name_containing_a_style_word_is_not_flagged():
    """Token matching must not turn every name into a finding."""
    flags = style_as_element([
        {"name": "Event Driven Settlement Service", "element_type": "Container"},
    ])
    # EVENT_DRIVEN is two tokens, so the single-token match does not fire here;
    # the exact-name match is what the style check is for.
    assert flags == []


# ============================================================================
# 4. Merging a style element — remove the node, keep every fact
# ============================================================================


def _style_graph():
    """The measured shape: the style element holds the platform's technology,
    technique and connection edges, and points `follows_style` at itself."""
    elements = [
        {"name": "Payment Gateway Platform", "element_type": "SoftwareSystem",
         "system_class": "BUSINESS_TECHNOLOGY_PLATFORM"},
        {"name": "Microservices", "element_type": "Container",
         "parent": "Payment Gateway Platform"},
        {"name": "Prometheus", "element_type": "ExternalSystem"},
    ]
    triples = [
        ExtractedTriple(subject="Microservices", predicate="part_of",
                        object="Payment Gateway Platform", confidence=1.0),
        ExtractedTriple(subject="Microservices", predicate="uses_technology",
                        object="REST", confidence=0.9),
        ExtractedTriple(subject="Microservices", predicate="connects_to",
                        object="Valkey", confidence=0.9),
        ExtractedTriple(subject="Prometheus", predicate="connects_to",
                        object="Microservices", confidence=0.9),
        ExtractedTriple(subject="Microservices", predicate="follows_style",
                        object="Microservices", confidence=0.9),
    ]
    return elements, triples


def test_a_style_element_is_merged_into_the_system_under_design():
    elements, triples = _style_graph()
    out_elements, styles, out_triples, flags = merge_style_elements(elements, [], triples)

    assert [e["name"] for e in out_elements] == ["Payment Gateway Platform", "Prometheus"]
    edges = {(t.subject, t.predicate, t.object) for t in out_triples}
    assert ("Payment Gateway Platform", "uses_technology", "REST") in edges
    assert ("Payment Gateway Platform", "connects_to", "Valkey") in edges
    assert ("Prometheus", "connects_to", "Payment Gateway Platform") in edges
    # The style element's own containment is dropped, not turned into a self-edge.
    assert ("Payment Gateway Platform", "part_of", "Payment Gateway Platform") not in edges
    assert [f.kind for f in flags] == ["style_element_merged"]
    assert [s["name"] for s in styles] == ["Microservices"]


def test_the_follows_style_object_survives_the_merge():
    """`follows_style` names the style, not the element. Re-pointing its object
    would assert that the platform follows itself."""
    elements, triples = _style_graph()
    _, _, out_triples, _ = merge_style_elements(elements, [], triples)
    edges = {(t.subject, t.predicate, t.object) for t in out_triples}

    assert ("Payment Gateway Platform", "follows_style", "Microservices") in edges
    assert ("Payment Gateway Platform", "follows_style", "Payment Gateway Platform") not in edges


def test_the_style_element_is_not_merged_without_an_anchor():
    """Two domain platforms: no unambiguous anchor, so it declines and reports."""
    elements, triples = _style_graph()
    elements.append({"name": "Second Platform", "element_type": "SoftwareSystem",
                     "system_class": "BUSINESS_APPLICATION"})
    out_elements, _, _, flags = merge_style_elements(elements, [], triples)

    assert any(e["name"] == "Microservices" for e in out_elements)
    assert flags == []
    assert [f.subject for f in style_as_element(out_elements)] == ["Microservices"]


def test_children_of_a_merged_element_move_to_the_anchor():
    elements = [
        {"name": "Platform", "element_type": "SoftwareSystem",
         "system_class": "BUSINESS_TECHNOLOGY_PLATFORM"},
        {"name": "Microservices", "element_type": "Container", "parent": "Platform"},
        {"name": "Payments API", "element_type": "Component", "parent": "Microservices"},
    ]
    out, _, _, _ = merge_style_elements(elements, [], [])

    assert not any(e["name"] == "Microservices" for e in out)
    assert next(e for e in out if e["name"] == "Payments API")["parent"] == "Platform"


def test_no_style_remains_an_element_after_the_merge():
    """The crossing check: merge first, then the leak detector must come back empty."""
    elements, triples = _style_graph()
    out_elements, _, _, _ = merge_style_elements(elements, [], triples)
    assert style_as_element(out_elements) == []


def test_the_merge_does_not_duplicate_an_edge_the_anchor_already_has():
    elements, triples = _style_graph()
    triples.append(ExtractedTriple(subject="Payment Gateway Platform",
                                   predicate="uses_technology", object="REST",
                                   confidence=0.9))
    _, _, out_triples, _ = merge_style_elements(elements, [], triples)
    matches = [t for t in out_triples
               if t.predicate == "uses_technology" and t.object == "REST"]
    assert len(matches) == 1
