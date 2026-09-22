"""
Reference resolution — binding an unresolved cross-graph reference to a real node.

WHAT "RESOLVING" MEANS
----------------------
An unresolved reference is an assertion whose predicate points into the other
graph (`implements_requirement`, `traces_to_goal`, …) and whose target was kept
as a literal because ingest refuses to invent a node for it. Resolving binds that
literal to a node that already exists, producing the object-valued assertion the
link always claimed to be.

WHY THIS IS NOT `review.correct()`
----------------------------------
`correct()` deliberately will *not* turn a cross-graph reference into a node —
doing so silently would erase the difference between "resolved" and "referenced
but not yet reconciled", which is the whole reason ingest stores them as
literals. Resolution is the opposite act: a deliberate, recorded decision that
the referent is now known. One is forbidden, the other is a human judgement, so
they must not share a code path.

THE MACHINE PROPOSES, THE HUMAN DECIDES
---------------------------------------
Candidate generation is deterministic and explainable (`exact`, `external_ref`,
`contains`, `tokens`). It never resolves anything by itself. `bulk_resolve`
accepts only candidates above an explicit threshold *and* of the kind the
predicate is supposed to point at, because a wrong traceability link is worse
than a missing one: it makes the downstream audit confidently wrong.

WHY KIND SCOPING IS NOT OPTIONAL
--------------------------------
Measured on the real ARC-G output, unscoped best-match picks
`Concept:'Card Payment Processing'` (0.95) over the correct
`BusinessCapability:'Unified Payment Processing'` (0.93) for
`supports_capability → 'Payment Processing'`. Lexical similarity alone prefers
the wrong kind of thing. Every predicate therefore declares the kinds it may
point at, and a candidate outside them is shown as a near miss that a human must
deliberately override.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

from .model import (
    CROSS_GRAPH_PREDICATES,
    SOURCE_HUMAN_ARCHITECT,
    STATUS_SUPERSEDED,
    STATUS_VERIFIED,
    KnowledgeGraph,
    Provenance,
    utc_now,
)
from .review import ACTION_RESOLVE, Decision, ReviewLog


class ReconcileError(Exception):
    """A resolution that could not be applied (unknown reference, bad target)."""


# Default acceptance threshold. Deliberately conservative: on the real fixture the
# defensible matches score 0.90+ while plausible-but-weak ones sit at 0.43-0.59,
# and accepting a wrong traceability link is worse than leaving it for a human.
DEFAULT_MATCH_THRESHOLD = 0.75


_REQUIREMENT_KINDS: FrozenSet[str] = frozenset(
    {
        "Requirement",
        "BusinessRequirement",
        "FunctionalRequirement",
        "NonFunctionalRequirement",
        "ConstraintRequirement",
        "PlatformExtensibilityRequirement",
        "PlatformMultiTenancyRequirement",
        "PlatformCompatibilityRequirement",
    }
)

# What each cross-graph predicate is allowed to point at. An empty set means
# "unspecified" and disables scoping for that predicate — better to show
# unscoped candidates than to silently find nothing.
EXPECTED_TARGET_KINDS: Dict[str, FrozenSet[str]] = {
    "implements_requirement": _REQUIREMENT_KINDS,
    "implements_functional_requirement": frozenset({"FunctionalRequirement"}),
    "implements_non_functional_requirement": frozenset({"NonFunctionalRequirement"}),
    "traces_to_goal": frozenset({"BusinessGoal"}),
    "traces_to_goals": frozenset({"BusinessGoal"}),
    "addresses_goal": frozenset({"BusinessGoal"}),
    "traces_to_capability": frozenset({"BusinessCapability"}),
    "traces_to_capabilities": frozenset({"BusinessCapability"}),
    "supports_capability": frozenset({"BusinessCapability"}),
    "supports_business_capability": frozenset({"BusinessCapability"}),
    "traces_to_process": frozenset({"BusinessProcess"}),
    "traces_to_processes": frozenset({"BusinessProcess"}),
    "satisfies_quality_attribute": frozenset({"NonFunctionalRequirement", "QualityScenario"}),
    "governed_by_rule": frozenset({"BusinessRule", "ConstraintRequirement"}),
    "governed_by_rules": frozenset({"BusinessRule", "ConstraintRequirement"}),
    "delivers_initiative": frozenset({"Initiative"}),
}


# ============================================================================
# Matching
# ============================================================================

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "of",
        "and",
        "or",
        "for",
        "to",
        "in",
        "on",
        "with",
        "by",
        "at",
        "is",
        "are",
        "be",
        "must",
        "shall",
        "that",
        "this",
        "it",
        "as",
    }
)


def normalise(text: str) -> str:
    return _NON_ALNUM.sub(" ", (text or "").lower()).strip()


def significant_tokens(text: str) -> FrozenSet[str]:
    return frozenset(t for t in normalise(text).split() if len(t) > 1 and t not in _STOPWORDS)


def match_score(
    target_text: str, label: str, external_refs: Sequence[str] = ()
) -> Tuple[float, str]:
    """Score one candidate, with the reason it scored that way.

    Deterministic and explainable on purpose: a reviewer accepting a proposed link
    should be able to see *why* it was proposed.
    """
    t_norm = normalise(target_text)
    l_norm = normalise(label)
    if not t_norm or not l_norm:
        return 0.0, ""

    if t_norm == l_norm:
        return 1.0, "exact"

    refs = {normalise(r) for r in external_refs if r}
    if t_norm in refs:
        # The document's own stable key. This is the signal the whole
        # reconciliation problem wants, which is why requirement IDs matter.
        return 1.0, "external_ref"

    if t_norm in l_norm or l_norm in t_norm:
        shorter, longer = sorted((t_norm, l_norm), key=len)
        return round(0.75 + 0.24 * (len(shorter) / max(len(longer), 1)), 3), "contains"

    a_tokens = significant_tokens(t_norm)
    b_tokens = significant_tokens(l_norm)
    if not a_tokens or not b_tokens:
        return 0.0, ""
    shared = len(a_tokens & b_tokens)
    if not shared:
        return 0.0, ""
    overlap = shared / min(len(a_tokens), len(b_tokens))
    jaccard = shared / len(a_tokens | b_tokens)
    return round(0.7 * overlap + 0.3 * jaccard, 3), "tokens"


@dataclass
class Candidate:
    node_id: str
    label: str
    kind: str
    score: float
    reason: str
    in_expected_kind: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "label": self.label,
            "kind": self.kind,
            "score": self.score,
            "score_pct": int(round(self.score * 100)),
            "reason": self.reason,
            "in_expected_kind": self.in_expected_kind,
        }


@dataclass
class ReferenceCandidates:
    """One unresolved reference and everything that could plausibly satisfy it."""

    assertion_id: str
    predicate: str
    source_id: str
    source_label: str
    source_kind: str
    target_text: str
    confidence: float
    source_text: str
    expected_kinds: List[str]
    candidates: List[Candidate] = field(default_factory=list)  # expected kind only
    near_misses: List[Candidate] = field(default_factory=list)  # other kinds

    @property
    def best(self) -> Optional[Candidate]:
        return self.candidates[0] if self.candidates else None

    @property
    def best_near_miss(self) -> Optional[Candidate]:
        return self.near_misses[0] if self.near_misses else None

    def mislabel_suspected(self, threshold: float = DEFAULT_MATCH_THRESHOLD) -> bool:
        """A strong match in the *wrong* kind usually means the predicate is wrong.

        Only when scoping is what blocks the binding: there is **no acceptable
        candidate of the expected kind**, but there is one elsewhere. A reference
        that already resolves is not suspicious merely because some unrelated node
        happens to score marginally higher — that is what kind scoping is for.

        Measured on the real ARC-G output at the default threshold: two
        `traces_to_goal` references have no BusinessGoal candidate at all but score
        0.90 and 0.86 against FunctionalRequirements. The extraction attached a goal
        predicate to a function name. Saying so is more useful than "no target
        found", because the fix is upstream. Lowering the threshold surfaces more
        of them (four more sit at 0.42-0.62 in the wrong kind).
        """
        near = self.best_near_miss
        if near is None or near.score < threshold:
            return False
        best = self.best
        return best is None or best.score < threshold

    def status(self, threshold: float = DEFAULT_MATCH_THRESHOLD) -> str:
        best = self.best
        if best is None:
            return "no_candidate"
        return "resolvable" if best.score >= threshold else "below_threshold"

    def to_dict(self, threshold: float = DEFAULT_MATCH_THRESHOLD) -> Dict[str, Any]:
        best = self.best
        near = self.best_near_miss
        return {
            "assertion_id": self.assertion_id,
            "predicate": self.predicate,
            "source_id": self.source_id,
            "source_label": self.source_label,
            "source_kind": self.source_kind,
            "target_text": self.target_text,
            "confidence": self.confidence,
            "source_text": self.source_text,
            "expected_kinds": self.expected_kinds,
            "candidates": [c.to_dict() for c in self.candidates],
            "near_misses": [c.to_dict() for c in self.near_misses],
            "best": best.to_dict() if best else None,
            "best_near_miss": near.to_dict() if near else None,
            "best_score": best.score if best else 0.0,
            "status": self.status(threshold),
            "resolvable": self.status(threshold) == "resolvable",
            "mislabel_suspected": self.mislabel_suspected(threshold),
        }


def _node_table(graph: KnowledgeGraph) -> List[Tuple[Any, str, Tuple[str, ...]]]:
    """Pre-normalise every node once.

    Scoring is O(references x nodes); normalising labels and refs inside that loop
    turns an interactive query into a noticeable pause on a large graph.
    """
    return [(n, normalise(n.label), tuple(n.external_refs or [])) for n in graph.nodes.values()]


def reference_candidates(
    graph: KnowledgeGraph,
    assertion_ids: Optional[Iterable[str]] = None,
    limit: int = 3,
    near_miss_limit: int = 3,
) -> List[ReferenceCandidates]:
    """Propose target nodes for each unresolved cross-graph reference."""
    wanted = set(assertion_ids) if assertion_ids is not None else None
    table = _node_table(graph)

    out: List[ReferenceCandidates] = []
    for a in graph.unresolved_references():
        if wanted is not None and a.id not in wanted:
            continue

        source = graph.nodes.get(a.subject)
        expected = EXPECTED_TARGET_KINDS.get(a.predicate, frozenset())

        matches: List[Candidate] = []
        near: List[Candidate] = []
        for node, _norm, refs in table:
            if node.id == a.subject:
                continue  # a thing does not reference itself
            score, reason = match_score(a.target, node.label, refs)
            if score <= 0.0:
                continue
            in_kind = (not expected) or node.kind in expected
            candidate = Candidate(
                node_id=node.id,
                label=node.label,
                kind=node.kind,
                score=score,
                reason=reason,
                in_expected_kind=in_kind,
            )
            (matches if in_kind else near).append(candidate)

        matches.sort(key=lambda c: (-c.score, c.label.lower()))
        near.sort(key=lambda c: (-c.score, c.label.lower()))

        out.append(
            ReferenceCandidates(
                assertion_id=a.id,
                predicate=a.predicate,
                source_id=a.subject,
                source_label=source.label if source else a.subject,
                source_kind=source.kind if source else "?",
                target_text=a.target,
                confidence=round(a.confidence, 3),
                source_text=a.source_text,
                expected_kinds=sorted(expected),
                candidates=matches[:limit],
                near_misses=near[:near_miss_limit],
            )
        )

    out.sort(key=lambda r: (-r.best.score if r.best else 0.0, r.predicate, r.target_text))
    return out


# ============================================================================
# Resolving
# ============================================================================


def _require_reference(graph: KnowledgeGraph, assertion_id: str):
    if not isinstance(assertion_id, str):
        raise ReconcileError(
            "expected an assertion id string, got "
            f"{type(assertion_id).__name__} — pass `.id`, not the assertion"
        )
    a = graph.assertions.get(assertion_id)
    if a is None:
        raise ReconcileError(f"unknown assertion: {assertion_id}")
    if a.predicate not in CROSS_GRAPH_PREDICATES:
        raise ReconcileError(
            f"{a.predicate!r} is not a cross-graph predicate; use review.correct() "
            "to change an ordinary assertion"
        )
    if a.object is not None:
        raise ReconcileError(f"{assertion_id} is already resolved to {a.object}")
    if not a.is_active:
        raise ReconcileError(f"{assertion_id} is {a.status}, not an open reference")
    return a


def resolve_reference(
    graph: KnowledgeGraph,
    log: ReviewLog,
    assertion_id: str,
    target_node_id: str,
    actor: str = "",
    note: str = "",
    allow_kind_override: bool = False,
) -> Decision:
    """Bind one unresolved reference to an existing node.

    Never invents the target: resolution asserts that the referent was already
    extracted and merely not joined. Creating the node would be a different act
    with different consequences, so it is not offered as a side effect here.
    """
    a = _require_reference(graph, assertion_id)

    node = graph.nodes.get(target_node_id)
    if node is None:
        raise ReconcileError(
            f"target node {target_node_id!r} does not exist — resolution binds to "
            "nodes that were already extracted; it never creates them"
        )

    expected = EXPECTED_TARGET_KINDS.get(a.predicate, frozenset())
    if expected and node.kind not in expected and not allow_kind_override:
        raise ReconcileError(
            f"{node.kind} is not an expected target for {a.predicate} "
            f"(expected: {', '.join(sorted(expected))}). Re-submit with an explicit "
            "override to bind across kinds deliberately."
        )

    score, reason = match_score(a.target, node.label, node.external_refs)
    before = {"target": a.target, "object": None, "status": a.status, "scope": a.scope}

    trace = (
        f"resolved reference {a.target!r} -> {node.kind}:{node.label} "
        f"({reason or 'manual'} {score:.2f})"
        + ("" if (not expected or node.kind in expected) else " [kind override]")
        + (f" | {note}" if note else "")
    )

    # `implements_*` bound to a requirement *is* the ontology's
    # RequirementRealization — the REQ<->ARC join, modelled as a first-class
    # resource precisely so this link can carry evidence.
    ontology_class = (
        "RequirementRealization"
        if a.predicate.startswith("implements_") and node.kind in _REQUIREMENT_KINDS
        else a.ontology_class
    )

    resolved = graph.add_assertion(
        a.subject,
        a.predicate,
        obj=node.id,
        value=None,
        # A human established this link, so it is human-authoritative. The lexical
        # evidence lives in the note and the audit entry rather than being smuggled
        # into `confidence`, which means "how sure was the source".
        confidence=1.0,
        source_text=a.source_text,
        ontology_class=ontology_class,
        provenance=Provenance(
            source_type=SOURCE_HUMAN_ARCHITECT,
            run_id=a.provenance.run_id,
            pass_name=a.provenance.pass_name,
            model_id=a.provenance.model_id,
            chunk_label=a.provenance.chunk_label,
            asserted_at=utc_now(),
            asserted_by=actor or "architect",
            derived_from=a.provenance.derived_from,
            correction_note=trace,
        ),
        status=STATUS_VERIFIED,
        scope=a.scope,
        initiative_id=a.initiative_id,
    )

    # The literal reference is kept for lineage: it records what the document
    # actually said before a human bound it to a node.
    a.status = STATUS_SUPERSEDED
    a.superseded_by = resolved.id

    return log.record(
        Decision(
            action=ACTION_RESOLVE,
            assertion_id=a.id,
            actor=actor,
            note=note,
            before=before,
            after={
                "target": resolved.target,
                "object": resolved.object,
                "status": resolved.status,
                "scope": resolved.scope,
            },
            replacement_id=resolved.id,
            promoted_ids=[],
        )
    )


@dataclass
class BulkResolveResult:
    """Outcome of a bulk pass, including everything it declined to do."""

    resolved: List[str] = field(default_factory=list)
    below_threshold: List[Tuple[str, float]] = field(default_factory=list)
    no_candidate: List[str] = field(default_factory=list)
    unknown_ids: List[str] = field(default_factory=list)
    threshold: float = DEFAULT_MATCH_THRESHOLD

    @property
    def considered(self) -> int:
        return len(self.resolved) + len(self.below_threshold) + len(self.no_candidate)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resolved": len(self.resolved),
            "below_threshold": len(self.below_threshold),
            "no_candidate": len(self.no_candidate),
            "unknown_ids": len(self.unknown_ids),
            "considered": self.considered,
            "threshold": self.threshold,
        }


def bulk_resolve(
    graph: KnowledgeGraph,
    log: ReviewLog,
    assertion_ids: Optional[Iterable[str]] = None,
    min_score: float = DEFAULT_MATCH_THRESHOLD,
    actor: str = "",
    note: str = "",
) -> BulkResolveResult:
    """Accept the proposed link for every reference whose best candidate clears
    `min_score`, and report exactly what was left behind.

    Only the *expected-kind* best candidate is eligible, so bulk can never bind a
    capability reference to a component on lexical similarity alone.
    """
    result = BulkResolveResult(threshold=min_score)

    open_ids = {a.id for a in graph.unresolved_references()}
    if assertion_ids is not None:
        requested = list(assertion_ids)
        # A caller may hold a stale page. Anything no longer open is reported
        # rather than silently skipped, so the UI can say why nothing happened.
        result.unknown_ids = [i for i in requested if i not in open_ids]
        targets = [i for i in requested if i in open_ids]
    else:
        targets = sorted(open_ids)

    proposals = {rc.assertion_id: rc for rc in reference_candidates(graph, assertion_ids=targets)}

    for assertion_id in targets:
        rc = proposals.get(assertion_id)
        if rc is None:
            result.no_candidate.append(assertion_id)
            continue
        best = rc.best
        if best is None:
            result.no_candidate.append(assertion_id)
            continue
        if best.score < min_score:
            result.below_threshold.append((assertion_id, best.score))
            continue
        resolve_reference(
            graph,
            log,
            assertion_id,
            best.node_id,
            actor=actor,
            note=note,
            allow_kind_override=False,
        )
        result.resolved.append(assertion_id)

    return result
