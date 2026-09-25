"""
The quality-attribute census.

WHAT THIS PINS. Three things the item this closes (YB-029) named as the reason a
census over the old fixtures would have been meaningless:

1. **Grouping is by canonical concern, not by label.** The architecture profile
   writes `High Availability` and the requirements classifier writes
   `Availability`; a census that grouped on the label would report one concern as
   two gaps. The first test is the one that would have caught that.
2. **An attribute the architecture delivers and no requirement states is listed.**
   That is the direction the gap report cannot express.
3. **A state nothing populates is reported as empty rather than omitted.** With no
   `QualityScenario` in the graph, "no scenario" must read as a finding, not
   disappear.
"""

from __future__ import annotations

from core.knowledge import graph_from_extraction, quality_report, quality_state
from core.quality import canonical_quality_concern


def build(output: dict):
    graph, _run = graph_from_extraction(
        output,
        {"model_id": "fake-model", "model_calls": 1},
        document_ref="doc.md",
        document_text="document body",
    )
    return graph


def requirement(name: str, quality_attribute: str, category: str = "", sub: str = "") -> dict:
    entity = {
        "name": name,
        "ontology_class": "NonFunctionalRequirement",
        "quality_attribute": quality_attribute,
    }
    if category:
        entity["quality_category"] = category
    if sub:
        entity["subcharacteristic"] = sub
    return entity


def element(name: str, satisfies: list, element_type: str = "Container") -> dict:
    return {"name": name, "element_type": element_type, "satisfies_attributes": list(satisfies)}


def technique(name: str, satisfies: list) -> dict:
    return {"name": name, "technique_category": "reliability", "satisfies_attributes": list(satisfies)}


# ============================================================================
# The join: one concern, several labels
# ============================================================================


def test_two_labels_for_one_attribute_are_one_concern():
    """`Availability` stated and `High Availability` delivered must meet.

    This is the whole premise of the item: `QualityAttribute` is a better
    converging join than `implements_requirement` precisely because the taxonomy
    can collapse the two spellings.
    """
    graph = build(
        {
            "entities": [requirement("Uptime", "Availability")],
            "elements": [element("Gateway", ["High Availability"])],
        }
    )
    state = quality_state(graph)
    assert len(state) == 1
    entry = state[0]
    assert entry.key == "AVAILABILITY"
    assert sorted(entry.labels) == ["Availability", "High Availability"]
    # One node per spelling, one concern — the merge is real, not a rename.
    assert len(entry.node_ids) == 2
    assert entry.stated and entry.delivered
    assert entry.coverage == "answered"


def test_a_requirement_label_and_a_free_text_synonym_converge():
    """`Time Behaviour`, `Low Latency` and `Throughput` are one concern."""
    graph = build(
        {
            "entities": [requirement("Latency", "Time Behaviour")],
            "elements": [element("Cache", ["Low Latency"]), element("Bus", ["Throughput"])],
        }
    )
    state = quality_state(graph)
    assert [e.key for e in state] == ["TIME_BEHAVIOUR"]
    assert state[0].category == "PERFORMANCE_EFFICIENCY"
    assert len(state[0].delivered) == 2


def test_characteristics_group_under_their_iso_parent():
    graph = build(
        {
            "entities": [requirement("Uptime", "Availability")],
            "elements": [element("Gateway", ["Scalability"])],
        }
    )
    report = quality_report(graph)
    by_label = {row["label"]: row for row in report["characteristics"]}
    assert "Reliability" in by_label
    assert [c["display"] for c in by_label["Reliability"]["concerns"]] == ["Availability"]
    assert "Flexibility" in by_label


# ============================================================================
# Four states, and the empty one
# ============================================================================


def test_all_four_states_are_reported_even_when_empty():
    """Acceptance: a state nothing populates is reported as such.

    The graph has no `QualityScenario`, so `has_quality_scenario` must be present
    and zero — a census that dropped the column it could not fill is how "no
    scenarios anywhere" would read as "scenarios are fine".
    """
    graph = build({"entities": [requirement("Uptime", "Availability")]})
    summary = quality_report(graph)["summary"]
    assert set(summary["states"]) == {
        "stated_in_requirements",
        "delivered_by_architecture",
        "realized_by_technique",
        "has_quality_scenario",
    }
    assert summary["states"]["has_quality_scenario"] == 0
    assert "has_quality_scenario" in summary["unpopulated_states"]
    assert summary["states"]["stated_in_requirements"] == 1


def test_a_design_technique_satisfies_the_technique_state():
    graph = build(
        {
            "entities": [requirement("Uptime", "Availability")],
            "design_techniques": [technique("Replication", ["Availability"])],
        }
    )
    entry = quality_state(graph)[0]
    assert entry.states["delivered_by_architecture"] is True
    assert entry.states["realized_by_technique"] is True
    assert [t["source_label"] for t in entry.techniques] == ["Replication"]


def test_a_scenario_populates_the_scenario_state():
    """The branch exists even though nothing emits a scenario yet.

    `QualityScenario` is declared in the ontology and populated by nothing, so the
    state reads empty on every real graph. This pins that the query is ready the
    day one is emitted, rather than silently unable to see it.
    """
    graph = build({"entities": [requirement("Uptime", "Availability")]})
    attribute = next(n for n in graph.nodes.values() if n.kind == "QualityAttribute")
    scenario_id = graph.add_node("QualityScenario", "Peak-hour failover")
    graph.add_assertion(scenario_id, "realizes_attribute", obj=attribute.id, confidence=1.0)

    entry = quality_state(graph)[0]
    assert entry.states["has_quality_scenario"] is True
    assert quality_report(graph)["summary"]["states"]["has_quality_scenario"] == 1


# ============================================================================
# The two inverses
# ============================================================================


def test_delivered_but_not_stated_is_listed():
    """The direction `/gaps` cannot express: architecture providing an unasked quality."""
    graph = build({"elements": [element("Gateway", ["Scalability"])]})
    report = quality_report(graph)
    assert [c["display"] for c in report["unasked"]] == ["Scalability"]
    assert report["unasked"][0]["coverage"] == "unasked"
    assert report["architecture_gaps"] == []


def test_stated_but_not_delivered_is_an_architecture_gap():
    # The requirement is not NAMED "Capacity": `_resolve` reuses any node carrying
    # a label, so a requirement with the attribute's own name would bind the
    # attribute edge to the requirement instead of materialising the attribute.
    graph = build({"entities": [requirement("Peak Capacity", "Capacity")]})
    report = quality_report(graph)
    assert [c["display"] for c in report["architecture_gaps"]] == ["Capacity"]
    assert report["architecture_gaps"][0]["coverage"] == "architecture_gap"
    assert report["unasked"] == []


def test_stated_and_delivered_is_answered():
    graph = build(
        {
            "entities": [requirement("Uptime", "Availability")],
            "elements": [element("Gateway", ["Availability"])],
        }
    )
    report = quality_report(graph)
    assert report["summary"]["coverage"]["answered"] == 1
    assert report["unasked"] == [] and report["architecture_gaps"] == []


# ============================================================================
# Unresolved labels
# ============================================================================


def test_an_unrecognised_label_is_its_own_concern_and_reported():
    """Never folded into a neighbour to make a number look tidier."""
    graph = build({"elements": [element("Platform", ["Green IT"])]})
    entry = quality_state(graph)[0]
    assert entry.resolved is False
    assert entry.key == "UNRESOLVED:GREEN_IT"
    report = quality_report(graph)
    assert report["summary"]["unresolved"] == 1
    assert [c["label"] for c in report["unresolved"]] == ["Green IT"]


# ============================================================================
# The canonical resolver itself
# ============================================================================


def test_taxonomy_names_win_over_keyword_guesses():
    """An exact name is not passed through the keyword scorer.

    `Performance` is a characteristic name, and it must resolve to the
    characteristic rather than to whichever sub-characteristic a keyword happened
    to hit.
    """
    cases = {
        "Availability": ("AVAILABILITY", "RELIABILITY"),
        "Time Behaviour": ("TIME_BEHAVIOUR", "PERFORMANCE_EFFICIENCY"),
        "Scalability": ("SCALABILITY", "FLEXIBILITY"),
        "Reliability": ("RELIABILITY", "RELIABILITY"),
        "Performance": ("PERFORMANCE_EFFICIENCY", "PERFORMANCE_EFFICIENCY"),
        "High Availability": ("AVAILABILITY", "RELIABILITY"),
        "Low Latency": ("TIME_BEHAVIOUR", "PERFORMANCE_EFFICIENCY"),
    }
    for label, (key, category) in cases.items():
        concern = canonical_quality_concern(label)
        assert concern.key == key, label
        assert concern.category == category, label
        assert concern.resolved is True, label


def test_renamed_characteristics_resolve_to_their_2023_names():
    """Usability became Interaction Capability, Portability became Flexibility."""
    assert canonical_quality_concern("Usability").category == "INTERACTION_CAPABILITY"
    assert canonical_quality_concern("Portability").category == "FLEXIBILITY"


def test_an_empty_label_does_not_invent_a_concern():
    concern = canonical_quality_concern("   ")
    assert concern.resolved is False
    assert concern.key == "UNNAMED"


# ============================================================================
# Honesty about the extraction
# ============================================================================


def test_the_report_carries_the_completeness_caveat():
    """Absence of a requirement is not evidence until the run says it is.

    A PARTIAL run that shows an attribute as "unasked" may simply not have read
    the NFR that states it; the caveat has to travel with the number.
    """
    graph = build({"elements": [element("Gateway", ["Scalability"])]})
    report = quality_report(graph)
    assert "Extraction" in report["completeness_note"]
    assert report["summary"]["unpopulated_states"] is not None
