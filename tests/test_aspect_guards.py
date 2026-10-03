"""
Aspect guards — Container, Component and Integration.

WHY THIS FILE EXISTS. An audit of the five core aspects of an architecture
(Container, Component, Design techniques, Integration to external systems, Domain
data elements) asked the same question of each: what holds it in shape — the
ontology, the pass schema, the prompt, a deterministic validator, or refusal at the
write boundary? Four had answers and three of those had holes:

  * **Container** — `container_type` is `required: true` in the ontology and was
    produced by nothing. Not in the pass schema, not in ingest, not in any check.
    An absent field and an unasked question are indistinguishable, so a required
    slot the pipeline never emitted was invisible.
  * **Component** — the ontology says `belongs_to_container` ranges over
    `Container`, and `check_containment` only proved a parent EXISTED. A Component
    attached to a SoftwareSystem passed every check, giving a graph that reported a
    C4 hierarchy and was in fact flat.
  * **Integration** — the connections prompt forbids a non-element endpoint in its
    strongest terms, and that rule had no deterministic backstop at all; and the
    `IntegrationStyle` / `IntegrationProtocol` enums were read by no Python, so the
    schema's hand-copied `Literal`s could drift from the ontology unnoticed.

The tests below pin the guards, and — as important — pin what they must NOT flag.
A validator that reports a correct graph is worse than none, which is why the
false-positive cases (a Component inside a DataStore, a reused endpoint, a
repair-attached parent) are asserted here rather than left to the harness.

The vocabularies are read from the ontology, not restated, so these tests also fail
if the ontology loses the range or the enum a guard depends on.
"""

from __future__ import annotations

from agents.architecture_extraction.passes import ConnectionRecord, ElementRecord
from agents.architecture_extraction.repair import repair_containment
from agents.knowledge_extraction.agent import ExtractedTriple
from agents.extraction import (
    CONNECTION_FIELD_ENUMS,
    allowed_parent_kinds,
    check_attribution_endpoints,
    check_connection_endpoints,
    check_containment,
    check_containment_kinds,
    check_enum_membership,
    check_nonempty_field,
    check_reference_kinds,
    check_schema_consistency,
    ontology_enum,
    ontology_slot_range,
    ontology_subclasses,
)
from core.knowledge import graph_from_extraction


def _ingest(output):
    graph, run = graph_from_extraction(
        output,
        metadata={"run_id": "run_aspect_test", "document_type": "architecture"},
        document_ref="arch.md",
        document_text="the document body",
    )
    return graph, run


def _facts(graph, subject, predicate):
    return [a for a in graph.active()
            if a.subject == subject and a.predicate == predicate]


# A well-formed little C4 tree, reused by several tests.
def _tree():
    return [
        {"name": "Platform", "element_type": "SoftwareSystem"},
        {"name": "Orchestrator", "element_type": "Container",
         "parent": "Platform", "container_type": "API_SERVICE"},
        {"name": "Store", "element_type": "DataStore",
         "parent": "Platform", "container_type": "DATABASE"},
        {"name": "Router", "element_type": "Component", "parent": "Orchestrator"},
        {"name": "Writer", "element_type": "Component", "parent": "Store"},
        {"name": "RouteTable", "element_type": "CodeElement", "parent": "Router"},
    ]


# ============================================================================
# 1. Container — the required slot the pipeline never produced
# ============================================================================


def test_the_ontology_still_requires_container_type_on_a_container():
    """The guard below is only right while the ontology says so. If this fails, the
    `required: true` was removed and `check_nonempty_field` should be un-wired rather
    than left flagging a slot the ontology no longer demands."""
    assert ontology_slot_range("Container", "container_type") == "ContainerType"


def test_the_ontology_still_defines_the_container_type_enum():
    values = ontology_enum("ContainerType")
    assert {"API_SERVICE", "WORKER", "DATABASE", "CACHE", "MESSAGE_BROKER"} <= values


def test_a_container_type_outside_the_ontology_enum_is_flagged():
    """The model guesses at this vocabulary ("SERVICE"), which is exactly why the
    schema keeps it a plain `str` and a validator judges it — a strict `Literal`
    would fail at the schema level and send the pass into a retry loop."""
    elements = _tree() + [{"name": "Legacy", "element_type": "Container",
                           "parent": "Platform", "container_type": "SERVICE"}]

    flags = [f for f in check_enum_membership(elements) if f.subject == "Legacy"]

    assert len(flags) == 1
    assert "container_type='SERVICE' not in ContainerType" in flags[0].reasons[0]


def test_every_ontology_container_type_is_accepted():
    elements = [
        {"name": f"C{index}", "element_type": "Container", "parent": "Platform",
         "container_type": value}
        for index, value in enumerate(sorted(ontology_enum("ContainerType")))
    ]

    assert check_enum_membership(elements) == []


def test_a_container_without_a_container_type_is_reported_as_incomplete():
    elements = _tree() + [{"name": "Untyped", "element_type": "Container",
                           "parent": "Platform", "container_type": ""}]

    flags = check_nonempty_field(elements, "container_type",
                                 applies_to=("Container", "DataStore"))

    assert [f.subject for f in flags] == ["Untyped"]
    assert "container_type is empty" in flags[0].reasons[0]


def test_the_required_slot_does_not_apply_to_every_element_type():
    """A SoftwareSystem or Component has no `container_type`, and demanding one
    would report a correct graph — the failure mode this whole area is careful
    about."""
    elements = _tree() + [{"name": "External", "element_type": "ExternalSystem"}]

    assert check_nonempty_field(elements, "container_type",
                                applies_to=("Container", "DataStore")) == []


def test_the_schema_carries_container_type_and_treats_absent_as_empty():
    """`None` means "not stated", which is what `""` already means — the same
    posture the `parent` coercer takes, so a null does not fail the call."""
    record = ElementRecord.model_validate(
        {"name": "Orchestrator", "element_type": "Container", "container_type": None})

    assert record.container_type == ""
    assert ElementRecord.model_validate(
        {"name": "X", "element_type": "Container",
         "container_type": "  API_SERVICE  "}).container_type == "API_SERVICE"


def test_container_type_reaches_the_graph(architecture_output):
    """The schema is only half of it: a field the pass emits and ingest never reads
    is YB-051 exactly — the connections pass, paid for and discarded."""
    output = {
        **architecture_output,
        "elements": [
            {**e, "container_type": "API_SERVICE"}
            if e["name"] == "Payment Orchestrator" else e
            for e in architecture_output["elements"]
        ],
    }

    graph, _run = _ingest(output)
    orchestrator = next(n for n in graph.nodes.values()
                        if n.label == "Payment Orchestrator")

    assert _facts(graph, orchestrator.id, "container_type")[0].value == "API_SERVICE"


# ============================================================================
# 2. Component — containment by KIND, not merely by existence
# ============================================================================


def test_the_parent_ranges_are_read_from_the_ontology():
    """These four lines are the whole rule. They are asserted rather than restated
    in code so a changed range cannot leave a table asserting the old one."""
    assert ontology_slot_range("Component", "belongs_to_container") == "Container"
    assert ontology_slot_range("CodeElement", "belongs_to_component") == "Component"
    # Inherited: DataStore declares no parent_system of its own.
    assert ontology_slot_range("DataStore", "parent_system") == "SoftwareSystem"
    assert ontology_slot_range("Container", "parent_system") == "SoftwareSystem"


def test_a_range_permits_its_own_subclasses():
    """`belongs_to_container` ranges over `Container`, and a DataStore IS a Container
    — so a Component inside a DataStore is legitimate, and a set built from the range
    alone would call it a violation."""
    assert "DataStore" in ontology_subclasses("Container")
    assert allowed_parent_kinds("Component") == {"Container", "DataStore"}
    assert allowed_parent_kinds("Container") == {"SoftwareSystem"}
    assert allowed_parent_kinds("CodeElement") == {"Component"}


def test_an_element_type_with_no_declared_parent_has_no_rule():
    """Empty means "nothing to check", not "no parent allowed" — otherwise every
    top-level type would be reported the moment somebody passed it in."""
    assert allowed_parent_kinds("SoftwareSystem") == frozenset()


def test_a_component_inside_a_software_system_is_flagged():
    """The defect: a flat graph that reports a C4 hierarchy. The component sits at
    context level and every C4 reduction then has to guess."""
    elements = _tree() + [{"name": "Floating", "element_type": "Component",
                           "parent": "Platform"}]

    flags = [f for f in check_containment_kinds(elements) if f.subject == "Floating"]

    assert len(flags) == 1
    assert flags[0].kind == "containment_kind"
    assert "contained by a SoftwareSystem" in flags[0].reasons[0]
    assert "allows Container/DataStore" in flags[0].reasons[0]


def test_a_code_element_inside_a_container_is_flagged():
    elements = _tree() + [{"name": "Loose", "element_type": "CodeElement",
                           "parent": "Orchestrator"}]

    assert [f.subject for f in check_containment_kinds(elements)] == ["Loose"]


def test_a_well_formed_tree_is_not_flagged():
    """The half that matters: `_tree()` is correct, and a guard that reports it is
    worse than no guard."""
    assert check_containment_kinds(_tree()) == []


def test_a_component_inside_a_datastore_is_allowed():
    """Subclass closure, asserted as behaviour and not only as a set."""
    assert check_containment_kinds(_tree()) == []
    writer = next(e for e in _tree() if e["name"] == "Writer")
    assert writer["parent"] == "Store"


def test_an_undeclared_parent_is_left_to_check_containment():
    """One cause, one finding. `check_containment` already reports a parent that is
    not a declared element; reporting it here too is the double-counting the C4
    scorecard was caught doing, and it makes one defect look like two."""
    elements = [{"name": "Orphan", "element_type": "Component", "parent": "Nowhere"}]

    assert [f.kind for f in check_containment(elements, [])] == ["containment"]
    assert check_containment_kinds(elements) == []


def test_a_repair_attached_parent_is_excluded():
    """`repair_containment` attaches an unplaced element to the system under design
    and reports it as `containment_repaired`. The kind check must not also report it:
    the document gap would then read as two defects, and the repair's own fix would
    be described as a mistake it created."""
    elements = [
        {"name": "Platform", "element_type": "SoftwareSystem"},
        {"name": "Orchestrator", "element_type": "Container", "parent": "Platform"},
        {"name": "Floating", "element_type": "Component", "parent": ""},
    ]
    triples = [ExtractedTriple(subject="Orchestrator", predicate="part_of",
                               object="Platform", confidence=1.0)]

    repaired, part_of, repair_flags = repair_containment(elements, triples)

    # The repair did its job according to `check_containment`...
    assert check_containment(repaired, part_of) == []
    # ...and it is exactly the case the kind check would otherwise flag.
    assert "Floating" in [f.subject for f in check_containment_kinds(repaired)]

    inferred = [f.subject for f in repair_flags if f.kind == "containment_repaired"]
    assert inferred == ["Floating"]
    assert check_containment_kinds(repaired, inferred_parents=inferred) == []


# ============================================================================
# 3. Integration — the endpoint rule gets a backstop
# ============================================================================


def test_a_connection_to_something_undeclared_is_flagged():
    """The prompt calls this out at length — a style, a quality attribute, a
    technique, a category word, a group of things "cannot be drawn" — and until now
    nothing checked it. "External Services" is the measured live example."""
    connections = [{"source": "Orchestrator", "target": "External Services"}]

    flags = check_connection_endpoints(connections, _tree())

    assert len(flags) == 1
    assert flags[0].kind == "connection_endpoint"
    assert "'External Services' is not an element this run declared" in flags[0].reasons[0]


def test_an_attribution_list_naming_an_undeclared_element_is_flagged():
    """The channel no guard reached, and how a category word became a node.

    `used_by` / `adopted_by` / `applies_to` name elements by hand, the prompt invites
    a group label outright ("statelessness and redundancy are platform-wide
    decisions"), and ingest resolves the name through `_resolve`, whose fallback kind
    is `Concept`. On a run that reports COMPLETE, that is one node per phrasing of one
    idea — `All Microservices`, `Microservices`, `Microservice Communications` (ISS-1).
    """
    collections = {"design_techniques": [
        {"name": "Statelessness", "applies_to": ["Orchestrator", "All Microservices"]},
    ]}

    flags = check_attribution_endpoints(collections, _tree())

    assert len(flags) == 1, [f.to_dict() for f in flags]
    assert flags[0].kind == "attribution_endpoint"
    assert flags[0].subject == "Statelessness"
    assert flags[0].object == "All Microservices"
    assert "is not an element this run declared" in flags[0].reasons[0]


def test_every_attribution_field_is_covered():
    """One list checked and the others not is how this gap opened in the first place."""
    collections = {
        "technology_stacks": [{"name": "Docker", "used_by": ["Nope"]}],
        "architecture_styles": [{"name": "Microservices", "adopted_by": ["Nope"]}],
        "design_techniques": [{"name": "Statelessness", "applies_to": ["Nope"]}],
        "engineering_conventions": [{"name": "Naming", "applies_to": ["Nope"]}],
    }

    flagged = {f.predicate for f in check_attribution_endpoints(collections, _tree())}

    assert flagged == {"used_by", "adopted_by", "applies_to"}


def test_a_declared_attribution_is_not_flagged():
    collections = {"technology_stacks": [{"name": "Docker", "used_by": ["Orchestrator"]}]}

    assert check_attribution_endpoints(collections, _tree()) == []


def test_attribution_endpoints_are_not_judged_without_declarations():
    """Same posture as the connection check: with nothing declared, every value would
    be flagged, which measures the run rather than the graph."""
    collections = {"design_techniques": [{"name": "S", "applies_to": ["Anything"]}]}

    assert check_attribution_endpoints(collections, []) == []


def test_a_reference_target_outside_the_declared_range_is_flagged():
    """`DeploymentNode.serves` ranges over SoftwareSystem, and nothing checked it.

    Containment is range-checked by `check_containment_kinds`; every other reference
    slot was not. So an edge to a Container passed every guard — and the extraction
    field's own wording invited "or products", which is the case the schema has not
    decided and the one this catches.
    """
    elements = _tree() + [
        {"name": "Cluster", "element_type": "DeploymentNode",
         "serves": ["Orchestrator"]},                       # a Container
    ]

    flags = check_reference_kinds(elements)

    assert [f.kind for f in flags] == ["reference_target_kind"]
    assert "declares range SoftwareSystem" in flags[0].reasons[0]
    assert flags[0].object == "Orchestrator"


def test_a_serves_target_no_run_declared_is_flagged():
    """The other half: ingest mints a `Concept` placeholder rather than refusing, so
    the graph gains a node whose only purpose is to be the far end of an edge."""
    elements = _tree() + [
        {"name": "Cluster", "element_type": "DeploymentNode",
         "serves": ["Payment Suite"]},
    ]

    flags = check_reference_kinds(elements)

    assert [f.kind for f in flags] == ["reference_target_undeclared"]
    assert "placeholder node with no kind" in flags[0].reasons[0]


def test_an_in_range_serves_target_is_not_flagged():
    """A guard that fires on correct output is worse than none — `Platform` IS a
    SoftwareSystem in `_tree()`."""
    elements = _tree() + [
        {"name": "Cluster", "element_type": "DeploymentNode", "serves": ["Platform"]},
    ]

    assert check_reference_kinds(elements) == []


def test_a_connection_with_a_missing_end_is_flagged():
    """The schema marks both required, but the text-fallback path does not, and
    ingest silently skips such a record — so nothing anywhere reported it."""
    connections = [
        {"source": "", "target": "Store"},
        {"source": "Orchestrator", "target": ""},
    ]

    flags = check_connection_endpoints(connections, _tree())

    assert len(flags) == 2
    assert "source is empty — a connection needs two ends" in flags[0].reasons[0]
    assert "target is empty — a connection needs two ends" in flags[1].reasons[0]


def test_declared_endpoints_are_not_flagged():
    connections = [{"source": "Orchestrator", "target": "Store"},
                   {"source": "Router", "target": "RouteTable"}]

    assert check_connection_endpoints(connections, _tree()) == []


def test_a_known_element_outside_the_run_is_not_flagged():
    """The Design Assistant is told to REUSE an existing element by name rather than
    re-propose it, so a reused endpoint appears in no proposed element record.
    Without `known_labels` every correct reuse would be reported as undeclared."""
    connections = [{"source": "Orchestrator", "target": "Legacy Ledger"}]

    assert check_connection_endpoints(
        connections, _tree(), known_labels=["legacy ledger"]) == []


def test_with_no_declared_elements_there_is_nothing_to_judge():
    """Flagging every endpoint against an empty set would measure the run, not the
    graph. Same posture as `check_element_types` with no ontology."""
    assert check_connection_endpoints([{"source": "a", "target": "b"}], []) == []


def test_the_endpoint_check_accepts_pass_objects_as_well_as_dicts():
    """A pass result is a pydantic model on the structured path and a dict on the
    text path; both reach these validators."""
    connections = [ConnectionRecord(source="Orchestrator", target="Nowhere")]

    flags = check_connection_endpoints(connections, _tree())

    assert [f.kind for f in flags] == ["connection_endpoint"]


# ============================================================================
# 4. The integration vocabularies must not drift
# ============================================================================


def test_the_connection_enums_are_read_from_the_ontology():
    assert "SYNCHRONOUS_REQUEST_RESPONSE" in ontology_enum("IntegrationStyle")
    assert "SHARED_DATABASE" in ontology_enum("IntegrationStyle")
    assert "REST" in ontology_enum("IntegrationProtocol")
    assert "SFTP_FILE" in ontology_enum("IntegrationProtocol")


def test_the_live_connection_schema_agrees_with_the_ontology():
    """`IntegrationStyle` and `IntegrationProtocol` are `Literal`s hand-copied from
    enums that no Python read. This is the assertion that makes the copy safe."""
    assert check_schema_consistency(ConnectionRecord, CONNECTION_FIELD_ENUMS) == []


def test_the_live_element_schema_agrees_with_the_ontology():
    """Still clean with `container_type` added, which is the point of keeping it a
    `str`: a `Literal` would need a `C4ElementType` enum the ontology does not have."""
    assert check_schema_consistency(ElementRecord) == []


def test_the_drift_check_has_teeth():
    """A guard asserted only on a clean input is untested. This builds the drift the
    check exists to find, in both directions."""
    from typing import Literal

    from pydantic import BaseModel

    class Drifted(BaseModel):
        style: Literal["", "SYNCHRONOUS_REQUEST_RESPONSE", "MADE_UP_STYLE"] = ""
        protocol: Literal["", "REST"] = ""

    flags = check_schema_consistency(Drifted, CONNECTION_FIELD_ENUMS)
    reasons = " ".join(r for f in flags for r in f.reasons)

    assert "MADE_UP_STYLE" in reasons                      # schema invented a value
    assert "KAFKA" in reasons or "GRPC" in reasons          # schema dropped ontology ones
    assert {f.subject for f in flags} == {"style", "protocol"}


# ============================================================================
# 5. The integration mechanism reaches the graph
# ============================================================================


def test_sensitive_data_and_failure_handling_reach_the_graph(architecture_output):
    """Declared on `Connection` and reachable from nowhere: the pass schema did not
    ask, so "which links carry regulated data?" — the PCI-scope question — had no
    graph answer."""
    output = {
        **architecture_output,
        "connections": [
            {"source": "Payment Orchestrator", "target": "Transaction Store",
             "description": "persists", "protocol": "JDBC", "style": "SHARED_DATABASE",
             "carries_sensitive_data": True, "failure_handling": "retry x3 then DLQ"},
        ],
    }

    graph, _run = _ingest(output)
    connection = next(n for n in graph.nodes.values() if n.kind == "Connection")

    assert _facts(graph, connection.id, "carries_sensitive_data")[0].value == "true"
    assert _facts(graph, connection.id, "failure_handling")[0].value == "retry x3 then DLQ"


def test_an_unstated_sensitive_data_flag_asserts_nothing(architecture_output):
    """Only `True` is emitted. Recording `false` for every quiet link would assert a
    negative the document never made — and a reviewer cannot tell that from a
    reviewed answer."""
    graph, _run = _ingest(architecture_output)
    connection = next(n for n in graph.nodes.values() if n.kind == "Connection")

    assert _facts(graph, connection.id, "carries_sensitive_data") == []
    assert _facts(graph, connection.id, "failure_handling") == []
