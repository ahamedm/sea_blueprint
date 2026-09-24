"""
Realization reporting — which requirements have an architectural answer, and which
architecture claims one it never bound.

WHAT THIS ANSWERS
-----------------
The Semantic Auditor's core question is bidirectional, and for a long time neither
direction was answerable:

    requirement  ->  architecture   "does anything answer this requirement?"
    architecture ->  requirement    "is this claim actually a link?"

`reconcile` binds the second; this module reports both. It is a QUERY layer — it
never mutates, resolves or invents anything. Binding is a human judgement (see
`reconcile`), so it stays there; deciding what the resulting state means belongs
here, where it can be read without touching the graph.

THE FOUR STATES, AND WHY "NONE" IS NOT "UNMET"
----------------------------------------------
A requirement is not simply realized or not. It can be:

    none        no architecture claims it at all — the widest gap
    unresolved  something claims it, but no claim has been bound yet
    partial     at least one bound link, and at least one still unbound
    full        every claim on it is bound

Collapsing these loses the difference that decides what to DO about it. `none` is
probably an extraction or coverage problem — the architecture document never
mentioned it. `unresolved` is a reconciliation problem: the reference exists and
is waiting for a reviewer. Reporting them as one number would make the smaller,
fixable case indistinguishable from the larger one.

WHAT COUNTS AS A CLAIM
----------------------
Any active assertion whose predicate asserts that an architecture thing answers a
requirement-ish thing (`REALIZATION_PREDICATES`), whether its target is a node (a
bound link) or a literal (an unresolved reference). Ordinary structural edges are
not claims: `part_of` and `uses_technology` say nothing about requirements, and
counting them would report every deployment node as "answering nothing".

ABSENCE IS NOT EVIDENCE
-----------------------
A requirement listed as `none` on a PARTIAL or UNKNOWN run may simply not have
been extracted. That is why the caller renders this next to completeness, and why
this module reports the run states it saw rather than a bare verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Tuple

from .model import (
    REQUIREMENT_KINDS,
    Assertion,
    KnowledgeGraph,
    Node,
    reference_targets_a_node,
)
from .reconcile import reference_candidates

# Predicates that assert a requirement-ish thing is answered by something. A
# superset of `CROSS_GRAPH_PREDICATES`, deliberately: `satisfies_attribute` and
# `realizes_attribute` point at QualityAttribute nodes *within* one graph, and
# they are still how an NFR gets an answer when no cross-graph link exists. The
# question "is this requirement answered?" must not depend on which side of the
# join the answer happens to live.
REALIZATION_PREDICATES: FrozenSet[str] = frozenset(
    {
        "implements_requirement",
        "implements_functional_requirement",
        "implements_non_functional_requirement",
        "realizes_quality_attribute",
        "realizes_quality_attributes",
        "satisfies_quality_attribute",
        "satisfies_quality_attributes",
        "supports_capability",
        "supports_business_capability",
        "supports_capabilities",
        "addresses_goal",
        "addresses_goals",
        "traces_to_goal",
        "traces_to_goals",
        "traces_to_capability",
        "traces_to_capabilities",
        "traces_to_process",
        "traces_to_processes",
        "governed_by_rule",
        "governed_by_rules",
    }
)

COVERAGE_NONE = "none"
COVERAGE_UNRESOLVED = "unresolved"
COVERAGE_PARTIAL = "partial"
COVERAGE_FULL = "full"

COVERAGE_ORDER = (COVERAGE_NONE, COVERAGE_UNRESOLVED, COVERAGE_PARTIAL, COVERAGE_FULL)


# ============================================================================
# Queries over one graph
# ============================================================================


def realization_edges(graph: KnowledgeGraph) -> List[Tuple[Any, Node]]:
    """Bound realization links: the assertion and the node it points at.

    "Bound" means `reference_targets_a_node` finds the node — either the
    assertion holds a node id (what reconciliation writes) or a node with exactly
    that label already exists. A reference nothing answers by either route is a
    claim, not a link, and is reported as one.
    """
    out: List[Tuple[Any, Node]] = []
    for a in graph.active():
        if a.predicate not in REALIZATION_PREDICATES:
            continue
        node = reference_targets_a_node(graph, a)
        if node is not None:
            out.append((a, node))
    return out


def unbound_claims(graph: KnowledgeGraph) -> List[Any]:
    """Active realization claims that name no node in this graph.

    These are `Graph.unresolved_references()` restricted to requirement-ish
    predicates — the same assertions `/reconcile` offers for binding, named from
    the requirement side so the two views cannot drift apart.
    """
    return [a for a in graph.unresolved_references() if a.predicate in REALIZATION_PREDICATES]


def _requirement_type_of(graph: KnowledgeGraph, node_id: str) -> str:
    """The requirement's own classification, from the node's `requirement_type`."""
    for a in graph.active():
        if a.subject == node_id and a.predicate == "requirement_type" and a.value:
            return str(a.value)
    return ""


def _claim_record(a: Any, graph: KnowledgeGraph) -> Dict[str, Any]:
    """One claim, as a row — whether it is bound or still a reference."""
    source = graph.nodes.get(a.subject)
    # The node the claim names, by whichever of the three routes it names one:
    # node id, exact label, or (for `implements_*`) a cited external identifier.
    # Using the raw `a.target` here would render a bound link as `FR-PM-002`
    # instead of the requirement it points at.
    target = reference_targets_a_node(graph, a)
    return {
        "assertion_id": a.id,
        "source_id": a.subject,
        "source_label": source.label if source else a.subject,
        "source_kind": source.kind if source else "?",
        "predicate": a.predicate,
        "target_id": target.id if target else "",
        "target_label": target.label if target else a.target,
        "target_kind": target.kind if target else "reference",
        "bound": target is not None,
        "confidence": round(a.confidence, 2),
        "status": a.status,
        "source_text": a.source_text,
        "is_human": a.is_human,
    }


@dataclass
class RequirementRealization:
    """One requirement and everything that claims to answer it."""

    node_id: str
    label: str
    kind: str
    external_refs: List[str] = field(default_factory=list)
    requirement_type: str = ""
    bound: List[Dict[str, Any]] = field(default_factory=list)
    unbound: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def coverage(self) -> str:
        if self.bound and self.unbound:
            return COVERAGE_PARTIAL
        if self.bound:
            return COVERAGE_FULL
        if self.unbound:
            return COVERAGE_UNRESOLVED
        return COVERAGE_NONE

    @property
    def is_realized(self) -> bool:
        """Whether *any* claim on it has been bound.

        Not the same as `coverage == COVERAGE_FULL`: a requirement with one bound
        answer and one dangling guess is realized, and only a human can say
        whether the dangling guess matters.
        """
        return bool(self.bound)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "label": self.label,
            "kind": self.kind,
            "external_refs": list(self.external_refs),
            "requirement_type": self.requirement_type,
            "coverage": self.coverage,
            "realized": self.is_realized,
            "bound": list(self.bound),
            "unbound": list(self.unbound),
        }


def requirement_nodes(graph: KnowledgeGraph) -> List[Node]:
    """Nodes whose kind IS a requirement, in a stable display order."""
    return sorted(
        (n for n in graph.nodes.values() if n.kind in REQUIREMENT_KINDS),
        key=lambda n: (n.kind, n.label.lower()),
    )


def realization_state(graph: KnowledgeGraph) -> List[RequirementRealization]:
    """Every requirement with its claims, bound and unbound. One pass.

    This is the primitive the report, the coverage counters and any future view
    build on, so the three cannot disagree about what a claim is.
    """
    records: Dict[str, RequirementRealization] = {
        node.id: RequirementRealization(
            node_id=node.id,
            label=node.label,
            kind=node.kind,
            external_refs=list(node.external_refs),
            requirement_type=_requirement_type_of(graph, node.id),
        )
        for node in requirement_nodes(graph)
    }
    if not records:
        return []

    # One walk over every realization claim. It lands in a requirement's `bound`
    # list when `reference_targets_a_node` finds the node it names, and in
    # `unbound` otherwise — which is also where a claim naming nothing extracted
    # stays visible instead of disappearing.
    for a in graph.active():
        if a.predicate not in REALIZATION_PREDICATES:
            continue
        node = reference_targets_a_node(graph, a)
        if node is not None:
            record = records.get(node.id)
            if record is not None:
                record.bound.append(_claim_record(a, graph))
            continue
        record = records.get(a.subject)
        if record is not None:
            # A requirement claiming another requirement — an NFR realizing an
            # attribute requirement, say. The claim is on the SOURCE, because
            # nothing tells us which requirement the text meant.
            record.unbound.append(_claim_record(a, graph))

    return [records[n.id] for n in requirement_nodes(graph)]


def unrealized_requirements(
    graph: KnowledgeGraph, include_unbound: bool = True
) -> List[RequirementRealization]:
    """Requirements no bound architecture element answers.

    `include_unbound=True` (default) reports requirements whose claims exist but
    have not been bound — they are *still* unanswered, and hiding them would make
    the number move when a reviewer binds a link without changing the architecture
    at all. `False` narrows it to requirements nothing even claims.
    """
    out = []
    for record in realization_state(graph):
        if record.is_realized:
            continue
        if not include_unbound and record.unbound:
            continue
        out.append(record)
    return out


def unmet_obligations(graph: KnowledgeGraph) -> List[Dict[str, Any]]:
    """Architecture elements claiming a requirement they never bound.

    This is the other direction of the audit. An element that says it implements
    something and has no bound link is either a paraphrase reconciliation could
    not resolve, or an architecture element answering a requirement that does not
    exist — both findings, and neither visible from the requirement side alone.
    """
    rows = []
    for a in unbound_claims(graph):
        source = graph.nodes.get(a.subject)
        row = _claim_record(a, graph)
        row["source_id"] = a.subject
        row["source_kind"] = source.kind if source else "?"
        rows.append(row)
    rows.sort(key=lambda r: (r["source_label"].lower(), r["predicate"], r["target_label"]))
    return rows


def realization_report(graph: KnowledgeGraph) -> Dict[str, Any]:
    """Both directions of the audit, plus the numbers a page needs.

    `claims` is everything that did not bind — the literal references kept at
    ingest. It is returned even when no requirement matches, because an
    architecture citing a requirement the requirements document never stated is
    itself the finding the auditor exists to surface.
    """
    state = realization_state(graph)
    bound = realization_edges(graph)
    claims = unbound_claims(graph)
    obligations = unmet_obligations(graph)

    coverage = {state_name: 0 for state_name in COVERAGE_ORDER}
    for record in state:
        coverage[record.coverage] += 1

    # An unbound claim either has a proposed target a reviewer can accept, or it
    # does not. Splitting the number there is what separates "waiting for a
    # decision" from "the matcher cannot see it at all" — the second is the
    # semantic-matching gap, and reporting both as `unbound_claims` would hide it.
    proposed = {
        rc.assertion_id
        for rc in reference_candidates(graph, assertion_ids=[a.id for a in claims])
        if rc.best is not None
    }

    return {
        "summary": {
            "requirements": len(state),
            "realized": sum(1 for r in state if r.is_realized),
            "unrealized": sum(1 for r in state if not r.is_realized),
            "coverage": coverage,
            "bound_edges": len(bound),
            "unbound_claims": len(claims),
            "proposed_claims": len(proposed),
            "unproposed_claims": len(claims) - len(proposed),
            "obliged_elements": len({a.subject for a in claims}),
            "requirement_kinds": sorted({n.kind for n in requirement_nodes(graph)}),
        },
        # Requirements an architecture claims but does not (yet) answer, or that
        # nothing claims at all. Sorted worst-first: `none` before `unresolved`
        # before `partial`, then by label.
        "unrealized": [
            r.to_dict()
            for r in sorted(
                unrealized_requirements(graph),
                key=lambda r: (COVERAGE_ORDER.index(r.coverage), r.label.lower()),
            )
        ],
        "requirements": [r.to_dict() for r in state],
        "obligations": obligations,
        "claims": [_claim_record(a, graph) for a in claims],
    }


def realization_coverage(graph: KnowledgeGraph) -> Dict[str, Any]:
    """Just the counters, for a header strip that should not build whole rows."""
    return realization_report(graph)["summary"]
