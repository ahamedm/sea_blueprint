"""
The 2026-09-26 semantic-accuracy pass: YB-030, YB-031, YB-032.

What these three have in common is that the graph said something the document did
not support, and nothing reported it:

- **YB-031** — the prompt taught the schema's plural spellings while the router knew
  the singular, so an edge could be written, drawn by the map, and invisible to every
  consumer that routes on the cross-graph set.
- **YB-030** — with label resolution as generous as it is, the requirements
  document's own `System` node answered its own requirements, so a graph with no
  architecture in it reported eleven of twelve realized.
- **YB-032** — a scalar enum field arrived comma-joined, holding one token that
  matched no enum member.

Each is pinned below at the layer that was wrong.
"""

from __future__ import annotations

import pytest

from core.knowledge import graph_from_extraction
from core.knowledge.model import CROSS_GRAPH_PREDICATES
from core.ontology import canonical_predicate

# ============================================================================
# YB-031 — taught plurals, routed singulars
# ============================================================================


def _architecture_graph(triples):
    graph, _run = graph_from_extraction(
        {"elements": [{"name": "Payment Orchestrator", "element_type": "Container"}],
         "triples": triples},
        {"document_type": "architecture", "model_id": "t"},
        document_ref="arch.md",
    )
    return graph


def _claims(graph, *predicates):
    """Only the assertions under test — the graph also carries structural edges."""
    return [a for a in graph.active() if a.predicate in predicates]


def test_a_plural_predicate_routes_as_a_cross_graph_reference():
    """The acceptance criterion: a predicate the prompt teaches must route.

    `implements_requirements` is the schema's own spelling and therefore the one the
    model is shown, and it had no alias — so it fell to the local-edge branch and
    reconciliation could never offer it.
    """
    graph = _architecture_graph([
        {"subject": "Payment Orchestrator", "predicate": "implements_requirements",
         "object": "FR-PM-001", "confidence": 0.9},
    ])
    claim = _claims(graph, "implements_requirement", "implements_requirements")[0]

    assert claim.predicate in CROSS_GRAPH_PREDICATES
    assert claim.predicate == "implements_requirement"
    # Kept as a reference rather than resolved to a node, which is what "cross-graph"
    # means and what reconciliation reads.
    assert claim.value == "FR-PM-001"
    assert not claim.object


def test_one_relationship_is_written_under_one_name():
    """Acceptance: one relationship, one routed name, whatever the model writes.

    Both spellings routed in some cases (the plural is a separate entry in
    `CROSS_GRAPH_PREDICATES`), which left the graph holding two names for one
    relationship — reconciliation debt on top of the lost edge.
    """
    graph = _architecture_graph([
        {"subject": "Payment Orchestrator", "predicate": "traces_to_goals",
         "object": "Reduce Failure Rate", "confidence": 0.8},
        {"subject": "Payment Orchestrator", "predicate": "traces_to_goal",
         "object": "Reduce Failure Rate", "confidence": 0.8},
    ])
    written = {a.predicate for a in _claims(graph, "traces_to_goal", "traces_to_goals")}

    assert written == {"traces_to_goal"}


def test_a_routed_predicate_is_never_rewritten():
    """Precision is kept where the router already distinguishes.

    `implements_functional_requirement` routes with the target
    `FunctionalRequirement`; collapsing it to the general `implements_requirement`
    would lose that for nothing. Canonicalisation rescues unrouted spellings, it does
    not normalise the vocabulary.
    """
    graph = _architecture_graph([
        {"subject": "Payment Orchestrator", "predicate": "implements_functional_requirement",
         "object": "FR-PM-001", "confidence": 0.9},
    ])
    claim = _claims(
        graph, "implements_functional_requirement", "implements_requirement"
    )[0]

    assert claim.predicate == "implements_functional_requirement"


def test_canonical_predicate_leaves_local_predicates_alone():
    for local in ("part_of", "uses_technology", "connects_to"):
        assert canonical_predicate(local) == local


# ============================================================================
# YB-030 — prevention: the requirements profile is not taught the join
# ============================================================================


def _vocabulary_for(agent_name: str) -> str:
    from agents.base_agent import AgentConfig
    from agents.design_assistant import DesignAssistantAgent
    from agents.knowledge_extraction import create_knowledge_extraction_agent
    from config import get_default_agent_config

    if agent_name == "knowledge_extraction":
        agent = create_knowledge_extraction_agent()
    else:
        agent = DesignAssistantAgent(AgentConfig(**get_default_agent_config(agent_name)))
    return agent._format_ontology_context()


def test_the_requirements_profile_is_not_taught_the_architecture_join():
    """The honest fix from YB-030 decision 1: scope the vocabulary by profile.

    Loading the ontology ROOT handed every profile the architecture layer's names,
    which is how a requirements run came to write `implements_requirement` at all.
    `architecture_base` imports the layers beneath it, so the architecture profile
    still sees everything.
    """
    req = _vocabulary_for("knowledge_extraction")
    arc = _vocabulary_for("architecture_extraction")

    assert "implements_requirement" not in req
    assert "satisfies_quality_attributes" not in req
    assert "implements_requirement" in arc
    assert "satisfies_quality_attributes" in arc
    # ...and the requirements layer's own traceability axes are still there, so
    # scoping removed nothing the profile legitimately needs.
    assert "traces_to_goals" in req
    assert "realizes_attribute" in req


def test_visible_layers_follow_imports():
    from core.ontology import load_ontology, visible_layer_keys

    model = load_ontology("ontology")
    assert visible_layer_keys(model, "ontology/requirements_base.yaml") == frozenset(
        {"common", "enterprise", "requirements"}
    )
    assert visible_layer_keys(model, "ontology/architecture_base.yaml") == frozenset(
        {"common", "enterprise", "requirements", "architecture"}
    )
    # Governance is a peer concern, not a layer beneath architecture: the
    # architecture schema does not import it, so an architecture run is not handed
    # the policy/control vocabulary. `governance_base` imports requirements.
    assert visible_layer_keys(model, "ontology/governance_base.yaml") == frozenset(
        {"common", "enterprise", "requirements", "governance"}
    )
    # No entry schema named means UNSCOPED, not "no vocabulary" — an agent whose
    # `ontology_path` is unset must keep working.
    assert visible_layer_keys(model, None) == frozenset()


# ============================================================================
# YB-032 — a scalar field written as a list
# ============================================================================


def _element_graph(**element):
    record = {"name": "Payment Gateway Platform", "element_type": "SoftwareSystem"}
    record.update(element)
    graph, _run = graph_from_extraction(
        {"elements": [record]},
        {"document_type": "architecture", "model_id": "t"},
        document_ref="arch.md",
    )
    return graph


def _values(graph, predicate):
    return sorted(
        str(a.value) for a in graph.active()
        if a.predicate == predicate and a.value
    )


def test_a_comma_joined_enum_becomes_one_assertion_per_value():
    """The measured shape, from `data/sea`."""
    graph = _element_graph(
        quality_category="RELIABILITY, PERFORMANCE_EFFICIENCY, FLEXIBILITY",
        subcharacteristic="AVAILABILITY, TIME_BEHAVIOUR, SCALABILITY",
    )

    assert _values(graph, "quality_category") == [
        "FLEXIBILITY", "PERFORMANCE_EFFICIENCY", "RELIABILITY",
    ]
    assert _values(graph, "subcharacteristic") == [
        "AVAILABILITY", "SCALABILITY", "TIME_BEHAVIOUR",
    ]
    # Acceptance: a comma-joined token must never reach the graph.
    assert not [
        a for a in graph.active()
        if a.predicate in ("quality_category", "subcharacteristic")
        and a.value and "," in str(a.value)
    ]


def test_a_clean_single_value_is_untouched():
    """Sixteen technique nodes carry one clean token; splitting must not disturb them."""
    graph = _element_graph(quality_category="SECURITY", subcharacteristic="CONFIDENTIALITY")

    assert _values(graph, "quality_category") == ["SECURITY"]
    assert _values(graph, "subcharacteristic") == ["CONFIDENTIALITY"]


def test_an_empty_enum_field_writes_nothing():
    graph = _element_graph(quality_category="", subcharacteristic="")
    assert _values(graph, "quality_category") == []
    assert _values(graph, "subcharacteristic") == []


@pytest.mark.parametrize("raw,expected", [
    ("A, B , C", ["A", "B", "C"]),
    ("A,,B", ["A", "B"]),
    ("  A  ", ["A"]),
    ("", []),
    (None, []),
])
def test_the_split_is_forgiving_about_whitespace_and_empties(raw, expected):
    from core.knowledge.ingest import _enum_values

    assert _enum_values(raw) == expected
