"""
Quality-attribute coverage — the audit read from the attribute side.

WHY THIS IS A SEPARATE QUERY FROM `realization`
-----------------------------------------------
`realization_report` answers a requirement-shaped question: "is this requirement
answered?" It walks requirement nodes and asks what claims them. The architect's
question is attribute-shaped — "where are our quality gaps?" — and the two are not
the same query:

* a requirement that no architecture claims is a requirement gap;
* an ATTRIBUTE that architecture delivers and no requirement states is not a gap
  at all, it is the opposite: the architecture provides a quality nobody asked
  for. `realization` cannot see it, because nothing on the requirement side is
  involved.

THE JOIN THAT CONVERGES
-----------------------
`QualityAttribute` nodes are the one join point between the two graphs that does
not depend on wording matching at the requirement level. An NFR that is about
availability and a container that delivers availability meet at the attribute
node; neither document had to name the other. Measured on the real working set,
`implements_requirement` leaves most requirements unrealized because the
architecture paraphrases them, while `realizes_attribute` and
`satisfies_attribute` land on the same nodes — which is why this census is worth
having, and why YB-029 is the architect's lens rather than another gap list.

FOUR STATES, NOT ONE
--------------------
Per attribute, four things are independently true or false, and they fail
independently:

    stated_in_requirements   an NFR `realizes_attribute` it
    delivered_by_architecture an element `satisfies_attribute` it
    realized_by_technique    a `DesignTechnique` is among the deliverers
    has_quality_scenario     a `QualityScenario` operationalises it

Collapsing them loses the fix. "Stated but not delivered" is an architecture gap;
"delivered but not stated" is an unasked-for quality; "no technique" means the
quality is asserted without a mechanism; "no scenario" means it is not testable.
So each is reported on its own, and a state that nothing in the graph populates
is reported as EMPTY rather than omitted — a census that quietly drops the state
it cannot fill is how "no scenarios anywhere" reads as "scenarios are fine".

GROUPING IS BY CANONICAL CONCERN, NOT BY LABEL
----------------------------------------------
The architecture profile writes whatever the model called the attribute
(`High Availability`), while the requirements classifier writes the taxonomy's own
name (`Availability`). Grouping on the label would report one concern as two gaps.
`core.quality.canonical_quality_concern` resolves both onto the taxonomy, and
this module groups on that key, keeping every original label as an alias so the
merge is visible rather than silent.

ABSENCE IS NOT EVIDENCE
-----------------------
An attribute with no requirement behind it on a PARTIAL or UNKNOWN extraction is
not necessarily unasked-for — the NFR may simply not have been read. The report
carries the run states it saw, like `realization_report` does, so the caller
renders the caveat beside the number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional

from core.quality import (
    CHARACTERISTIC_ORDER,
    ISO_CHARACTERISTICS,
    QualityConcern,
    canonical_quality_concern,
    humanise,
)

from .ingest import completeness_note
from .model import KnowledgeGraph, Node, REQUIREMENT_KINDS, reference_targets_a_node

# The class the census is about. Named here rather than spelled inline so a
# rename lands in one place.
QUALITY_ATTRIBUTE_KIND = "QualityAttribute"
QUALITY_SCENARIO_KIND = "QualityScenario"
DESIGN_TECHNIQUE_KIND = "DesignTechnique"

# The predicates that reach an attribute node. Only these two: every other
# quality-ish predicate in the vocabulary (`satisfies_quality_attribute`,
# `realizes_quality_attribute`) points at an NFR, not at an attribute, and reading
# them here would double-count the same delivery under a literal name.
STATED_PREDICATES: FrozenSet[str] = frozenset({"realizes_attribute"})
DELIVERED_PREDICATES: FrozenSet[str] = frozenset({"satisfies_attribute"})

# The four states, in the order the fix is cheapest to most structural. Every
# report carries all four keys even when empty.
STATES = (
    "stated_in_requirements",
    "delivered_by_architecture",
    "realized_by_technique",
    "has_quality_scenario",
)

STATE_LABELS = {
    "stated_in_requirements": "Stated in requirements",
    "delivered_by_architecture": "Delivered by architecture",
    "realized_by_technique": "Has a realizing technique",
    "has_quality_scenario": "Has a quality scenario",
}

# The single verdict a reader wants first, derived from the two real directions.
# Deliberately derived, never stored on the node: the ontology's `covered` slot
# invites a writer to keep a second copy of this and let it drift.
COVERAGE_ANSWERED = "answered"
COVERAGE_ARCHITECTURE_GAP = "architecture_gap"
COVERAGE_UNASKED = "unasked"
COVERAGE_UNADDRESSED = "unaddressed"

COVERAGE_LABELS = {
    COVERAGE_ANSWERED: "Stated and delivered",
    COVERAGE_ARCHITECTURE_GAP: "Stated, nothing delivers it",
    COVERAGE_UNASKED: "Delivered, no requirement states it",
    COVERAGE_UNADDRESSED: "Neither stated nor delivered",
}


def _claim(graph: KnowledgeGraph, a: Any) -> Dict[str, Any]:
    """One edge reaching an attribute, as a row the view can render."""
    source = graph.nodes.get(a.subject)
    return {
        "assertion_id": a.id,
        "source_id": a.subject,
        "source_label": source.label if source else a.subject,
        "source_kind": source.kind if source else "?",
        "predicate": a.predicate,
        "confidence": round(a.confidence, 2),
        "status": a.status,
        "is_human": a.is_human,
        "source_text": a.source_text,
        "document": a.provenance.derived_from if a.provenance else "",
    }


@dataclass
class AttributeCoverage:
    """One canonical quality concern and everything the graph says about it."""

    key: str
    display: str
    category: str = ""
    subcharacteristic: str = ""
    resolved: bool = False
    resolution: str = ""
    # Every label that folded into this concern, in first-seen order. More than
    # one is the collision this module exists to make visible.
    labels: List[str] = field(default_factory=list)
    node_ids: List[str] = field(default_factory=list)
    stated: List[Dict[str, Any]] = field(default_factory=list)
    delivered: List[Dict[str, Any]] = field(default_factory=list)
    scenarios: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def techniques(self) -> List[Dict[str, Any]]:
        return [c for c in self.delivered if c["source_kind"] == DESIGN_TECHNIQUE_KIND]

    @property
    def coverage(self) -> str:
        if self.stated and self.delivered:
            return COVERAGE_ANSWERED
        if self.stated:
            return COVERAGE_ARCHITECTURE_GAP
        if self.delivered:
            return COVERAGE_UNASKED
        return COVERAGE_UNADDRESSED

    def state(self, name: str) -> List[Dict[str, Any]]:
        return {
            "stated_in_requirements": self.stated,
            "delivered_by_architecture": self.delivered,
            "realized_by_technique": self.techniques,
            "has_quality_scenario": self.scenarios,
        }[name]

    @property
    def states(self) -> Dict[str, bool]:
        return {name: bool(self.state(name)) for name in STATES}

    @property
    def label(self) -> str:
        """The label to show when one is needed. First-seen wins."""
        return self.labels[0] if self.labels else self.display

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "display": self.display,
            "category": self.category,
            "category_label": humanise(self.category) if self.category else "Unclassified",
            "subcharacteristic": self.subcharacteristic,
            "resolved": self.resolved,
            "resolution": self.resolution,
            "aliases": list(self.labels),
            "node_ids": list(self.node_ids),
            "coverage": self.coverage,
            "coverage_label": COVERAGE_LABELS[self.coverage],
            "states": self.states,
            "stated": list(self.stated),
            "delivered": list(self.delivered),
            "techniques": list(self.techniques),
            "scenarios": list(self.scenarios),
            # The four states keyed by the state NAMES, so a renderer can loop the
            # state list rather than hard-code which claim list is which.
            "state_claims": {name: list(self.state(name)) for name in STATES},
            "counts": {
                "stated": len(self.stated),
                "delivered": len(self.delivered),
                "techniques": len(self.techniques),
                "scenarios": len(self.scenarios),
            },
        }


def attribute_nodes(graph: KnowledgeGraph) -> List[Node]:
    """Every `QualityAttribute` node, in a stable display order."""
    return sorted(
        (n for n in graph.nodes.values() if n.kind == QUALITY_ATTRIBUTE_KIND),
        key=lambda n: n.label.lower(),
    )


def quality_state(graph: KnowledgeGraph) -> List[AttributeCoverage]:
    """Every canonical concern, with every edge the graph makes to it.

    One walk over the active graph. The primitive the report, the counters and any
    future view build on, so they cannot disagree about what a delivery is.
    """
    groups: Dict[str, AttributeCoverage] = {}
    node_to_key: Dict[str, str] = {}

    def bucket(concern: QualityConcern, node: Node) -> AttributeCoverage:
        entry = groups.get(concern.key)
        if entry is None:
            entry = AttributeCoverage(
                key=concern.key,
                display=concern.display,
                category=concern.category,
                subcharacteristic=concern.subcharacteristic,
                resolved=concern.resolved,
                resolution=concern.method,
            )
            groups[concern.key] = entry
        # A concern resolved by different routes in different places keeps the
        # strongest resolution, so an exact taxonomy hit is not demoted to
        # "keyword" by a synonym that folded in beside it.
        if concern.resolved and not entry.resolved:
            entry.resolved = True
            entry.resolution = concern.method
        if concern.label not in entry.labels:
            entry.labels.append(concern.label)
        if node.id not in entry.node_ids:
            entry.node_ids.append(node.id)
        node_to_key[node.id] = concern.key
        return entry

    for node in attribute_nodes(graph):
        bucket(canonical_quality_concern(node.label), node)

    scenario_nodes = {n.id for n in graph.nodes.values() if n.kind == QUALITY_SCENARIO_KIND}

    for a in graph.active():
        # Scenarios FIRST. A `QualityScenario` may link to its attribute with the
        # same predicate an NFR uses, and the source kind is what says which of the
        # four states it populates — reading it as "stated in requirements" would
        # let a scenario stand in for a requirement that does not exist.
        if a.subject in scenario_nodes:
            target = reference_targets_a_node(graph, a)
            if target is not None and target.id in node_to_key:
                groups[node_to_key[target.id]].scenarios.append(_claim(graph, a))
                continue
        if a.object in scenario_nodes and a.subject in node_to_key:
            # The ontology's own direction: `QualityAttribute.scenarios`.
            groups[node_to_key[a.subject]].scenarios.append(_claim(graph, a))
            continue

        if a.predicate in STATED_PREDICATES or a.predicate in DELIVERED_PREDICATES:
            # `reference_targets_a_node` rather than `a.object`: it also binds a
            # reference whose TEXT is the attribute's label, which is how a
            # requirement that names "Availability" without a node id still lands.
            target = reference_targets_a_node(graph, a)
            if target is None or target.id not in node_to_key:
                continue
            entry = groups[node_to_key[target.id]]
            if a.predicate in STATED_PREDICATES:
                entry.stated.append(_claim(graph, a))
            else:
                entry.delivered.append(_claim(graph, a))

    return sorted(
        groups.values(),
        key=lambda e: (
            _category_rank(e.category),
            e.subcharacteristic or "",
            e.display.lower(),
        ),
    )


def _category_rank(category: str) -> int:
    """Stable ordering by the taxonomy's own sequence; unknown sorts last."""
    try:
        return CHARACTERISTIC_ORDER.index(category)
    except ValueError:
        return len(CHARACTERISTIC_ORDER)


def characteristics(graph: KnowledgeGraph, state: Optional[List[AttributeCoverage]] = None):
    """Concerns grouped under their ISO characteristic, for a census page.

    A concern resolved only to a characteristic (`Performance`) sits in that
    characteristic's group with an empty sub-characteristic — it is a real claim
    about the characteristic, and inventing a sub-characteristic for it would be
    the guessing `core.quality` refuses to do.
    """
    state = quality_state(graph) if state is None else state
    grouped: Dict[str, List[AttributeCoverage]] = {}
    for entry in state:
        grouped.setdefault(entry.category or "", []).append(entry)

    rows = []
    for category in sorted(grouped, key=_category_rank):
        entries = grouped[category]
        rows.append(
            {
                "category": category,
                "label": humanise(category) if category else "Unclassified",
                "iso": category in ISO_CHARACTERISTICS,
                "concerns": [e.to_dict() for e in entries],
                "counts": {
                    "concerns": len(entries),
                    "stated": sum(1 for e in entries if e.stated),
                    "delivered": sum(1 for e in entries if e.delivered),
                    "gaps": sum(1 for e in entries if e.coverage == COVERAGE_ARCHITECTURE_GAP),
                    "unasked": sum(1 for e in entries if e.coverage == COVERAGE_UNASKED),
                },
            }
        )
    return rows


def quality_report(graph: KnowledgeGraph) -> Dict[str, Any]:
    """The attribute census, both inverses, and the caveat that qualifies it."""
    state = quality_state(graph)

    coverage = {
        COVERAGE_ANSWERED: 0,
        COVERAGE_ARCHITECTURE_GAP: 0,
        COVERAGE_UNASKED: 0,
        COVERAGE_UNADDRESSED: 0,
    }
    for entry in state:
        coverage[entry.coverage] += 1

    # Every state present, including the empty ones. This is the acceptance
    # criterion, and it is why `populated` is computed rather than left to the
    # template to notice.
    state_counts = {name: sum(1 for e in state if e.state(name)) for name in STATES}

    unresolved = [e.to_dict() for e in state if not e.resolved]

    return {
        "summary": {
            "attributes": len(state),
            "attribute_nodes": len(attribute_nodes(graph)),
            # More nodes than concerns means labels merged — the collision this
            # grouping exists to neutralise, reported rather than hidden.
            "merged_labels": len(attribute_nodes(graph)) - len(state),
            "resolved": sum(1 for e in state if e.resolved),
            "unresolved": len(unresolved),
            "coverage": coverage,
            "states": state_counts,
            "unpopulated_states": [name for name in STATES if state_counts[name] == 0],
            "characteristics": len({e.category for e in state if e.category}),
            "stated": sum(1 for e in state if e.stated),
            "delivered": sum(1 for e in state if e.delivered),
            "unasked": sum(1 for e in state if e.coverage == COVERAGE_UNASKED),
            "architecture_gaps": sum(
                1 for e in state if e.coverage == COVERAGE_ARCHITECTURE_GAP
            ),
        },
        "state_labels": dict(STATE_LABELS),
        "coverage_labels": dict(COVERAGE_LABELS),
        "characteristics": characteristics(graph, state),
        "attributes": [e.to_dict() for e in state],
        # The direction `project_gap_report` cannot express: the architecture
        # provides a quality no requirement asked for.
        "unasked": [e.to_dict() for e in state if e.coverage == COVERAGE_UNASKED],
        "architecture_gaps": [
            e.to_dict() for e in state if e.coverage == COVERAGE_ARCHITECTURE_GAP
        ],
        "unresolved": unresolved,
        "completeness_note": completeness_note(graph),
    }
