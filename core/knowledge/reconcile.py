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
    IDENTITY_BY_CITATION_PREDICATES,
    REQUIREMENT_KINDS as _REQUIREMENT_KINDS,
    IDENTITY_SCOPE_DOCUMENT,
    SOURCE_HUMAN_ARCHITECT,
    STATUS_SUPERSEDED,
    STATUS_VERIFIED,
    ExternalReference,
    KnowledgeGraph,
    Provenance,
    utc_now,
)
from .review import ACTION_RESOLVE, Decision, ReviewLog


class ReconcileError(Exception):
    """A resolution that could not be applied (unknown reference, bad target)."""


# Re-exported: this is the one definition of "what is a requirement", shared by
# the matcher, ingest's classification gate and the realization report.
REQUIREMENT_KINDS = _REQUIREMENT_KINDS

# Default acceptance threshold. Deliberately conservative: on the real fixture the
# defensible matches score 0.90+ while plausible-but-weak ones sit at 0.43-0.59,
# and accepting a wrong traceability link is worse than leaving it for a human.
DEFAULT_MATCH_THRESHOLD = 0.75

# What a cross-graph predicate is allowed to point at, plus the kinds that are
# merely *mechanically possible*. The two differ on purpose:
#
# - `EXPECTED_TARGET_KINDS` gates binding. A candidate outside it can never be
#   bound by bulk and needs an explicit human override.
# - `PLAUSIBLE_TARGET_KINDS` only decides what is worth OFFERING as a near miss.
#   A container is not a requirement, but a reviewer who knows the shape of the
#   data should still be able to override deliberately rather than find the
#   target missing from the list entirely. `Concept` and `DomainConcept` are in
#   it because prose extraction lands architecture referents there when no pass
#   classified them — the same fallback `ingest._resolve` uses.
PLAUSIBLE_TARGET_KINDS: FrozenSet[str] = frozenset(
    {
        "Requirement", "BusinessRequirement", "FunctionalRequirement",
        "NonFunctionalRequirement", "ConstraintRequirement",
        "PlatformExtensibilityRequirement", "PlatformMultiTenancyRequirement",
        "PlatformCompatibilityRequirement",
        "BusinessGoal", "BusinessCapability", "BusinessProcess", "BusinessRule",
        "QualityAttribute", "QualityScenario", "Initiative", "ExternalReference",
        "Constraint", "Regulation", "Policy", "Standard",
        "Concept", "DomainConcept",
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
    # A named pattern a requirement mandates. Scoped to the requirement kinds so a
    # proposal cannot bind a mandate to, say, a technology stack that shares a word.
    "mandated_by": _REQUIREMENT_KINDS,
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


def _as_references(values: Sequence[Any]) -> List[ExternalReference]:
    """Accept either typed references or bare strings.

    Bare strings are read as DOCUMENT-scoped, never as enterprise keys — an
    identifier whose kind we do not know must not be granted match authority.
    """
    out: List[ExternalReference] = []
    for value in values or []:
        if isinstance(value, ExternalReference):
            out.append(value)
        elif isinstance(value, str) and value.strip():
            out.append(ExternalReference(identifier=value.strip(), scope=IDENTITY_SCOPE_DOCUMENT,
                                         reference_type="OTHER"))
    return out


def match_score(
    target_text: str,
    label: str,
    external_refs: Sequence[Any] = (),
    source_document: str = "",
    predicate: str = "",
) -> Tuple[float, str]:
    """Score one candidate, with the reason it scored that way.

    Deterministic and explainable on purpose: a reviewer accepting a proposed link
    should be able to see *why* it was proposed.

    IDENTIFIERS ARE SCOPED, AND ONLY SOME MAY BE MATCHED ON. An earlier version
    returned a definitive 1.0 for any identifier equal to the target. That is
    right for an enterprise key and WRONG for a document-local label: two
    documents may both number a requirement `FR-001`, and a confident wrong join
    is worse than a missing one because it makes the audit wrong rather than
    incomplete.

    `source_document` is the document the ASSERTION making the reference came
    from. A document-local identifier is trusted only when the reference's own
    `system` names that same document — one source stated both, so the label
    genuinely identifies the thing there. Otherwise it is evidence and no more.

    EXCEPT `implements_*`, WHERE CITING THE DOCUMENT'S OWN KEY IS THE CLAIM.
    A requirements document numbers its requirements; an architecture document
    that cites `FR-PM-001` is quoting that key, not inventing one. For those
    predicates a matching document-local identifier IS identity, and treating it
    as mere evidence is what left a verbatim-preserved requirement id unable to
    join across documents — the deepest form of the defect this item was opened
    for. Every other predicate keeps the strictly-scoped reading, because a
    shared heading like "Payment Processing" in two documents means two things.
    """
    t_norm = normalise(target_text)
    l_norm = normalise(label)
    if not t_norm or not l_norm:
        return 0.0, ""

    if t_norm == l_norm:
        return 1.0, "exact"

    # Scan every reference before deciding, and prefer an enterprise key. A
    # document label and a system-of-record key can carry the same identifier
    # (both `FR-PM-001`), so returning on the first match would let list order
    # decide the outcome — the strongest signal would be invisible depending on
    # which reference happened to be recorded first.
    cites_requirement = predicate in IDENTITY_BY_CITATION_PREDICATES
    local_hit = False
    local_same_document = False
    for ref in _as_references(external_refs):
        if normalise(ref.identifier) != t_norm:
            continue
        if ref.is_join_key:
            # Held in a system of record: an exact identifier match, the most
            # reliable join available and the reason references exist at all.
            return 1.0, "external_ref"
        local_hit = True
        if source_document and (ref.system or "").strip() == source_document.strip():
            local_same_document = True

    if local_hit and local_same_document:
        return 1.0, "external_ref"

    if local_hit and cites_requirement:
        return 1.0, "citing_document_key"

    if local_hit:
        # The label matches, but it is local to a different document, so `FR-001`
        # may well mean something else there. Reported as evidence, and
        # deliberately below the resolve threshold.
        return 0.6, "unscoped_ref"

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
    # Both are ORDERING signals, never score changes and never gates: the score
    # still has to clear the threshold on its own merit. They exist because
    # lexical similarity alone cannot tell "the same Initiative" or "the graph
    # this predicate claims to point into" from wording, and both are knowable
    # deterministically — from `delivers_initiative` / `authorised_by_initiative`
    # and from which document the candidate's run read.
    same_initiative: bool = False
    other_side: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "label": self.label,
            "kind": self.kind,
            "score": self.score,
            "score_pct": int(round(self.score * 100)),
            "reason": self.reason,
            "in_expected_kind": self.in_expected_kind,
            "same_initiative": self.same_initiative,
            "other_side": self.other_side,
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
    source_initiative: str = ""
    source_side: str = ""

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
            "source_initiative": self.source_initiative,
            "source_side": self.source_side,
            "same_initiative_candidates": sum(1 for c in self.candidates if c.same_initiative),
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
    # Typed references, not the flat strings: `match_score` must be able to tell
    # an enterprise key from a document label, and the flat list has lost that.
    return [(n, normalise(n.label), tuple(n.external_references or ()))
            for n in graph.nodes.values()]


def _document_of_run(graph: KnowledgeGraph, run_id: str) -> str:
    """Which document a run read. Empty when unknown."""
    run = graph.runs.get(run_id) if run_id else None
    return (run.document_ref or "") if run else ""


# What a run's document says about which side of the reconciliation its nodes
# are on. The values match `ExtractionRun.document_type`.
SIDE_REQUIREMENTS = "requirements"
SIDE_ARCHITECTURE = "architecture"


def node_sides(graph: KnowledgeGraph) -> Dict[str, str]:
    """Which side each node came from — the document that DECLARED it, or failing
    that, the runs that asserted things about it.

    A node's side is not a property of the node: it is where it was read from.
    That is why it is derived from the ingest rather than declared, and why
    `declared_by` is consulted first — a requirement extracted with no triples has
    no assertion to infer from, and it is precisely the requirement whose absence
    of claims the audit is about.

    Disagreement (a node named by runs from both documents, or declared by one and
    asserted by the other) or an unrecorded document yields an empty string —
    "unknown" — and unknown never influences an ordering. Guessing a side would
    re-rank candidates on evidence that is not there.
    """
    from_declaration = {
        nid: side for nid, side in graph.declared_by.items() if (side or "").strip()
    }
    sides: Dict[str, set] = {}
    for a in graph.active():
        run = graph.runs.get(a.provenance.run_id) if a.provenance.run_id else None
        side = (run.document_type if run else "") or ""
        if not side:
            continue
        for nid in (a.subject, a.object):
            if nid:
                sides.setdefault(nid, set()).add(side)

    resolved: Dict[str, str] = {}
    for nid in set(from_declaration) | set(sides):
        declared = from_declaration.get(nid, "")
        asserted = sides.get(nid, set())
        if declared and not asserted:
            resolved[nid] = declared
        elif not declared and len(asserted) == 1:
            resolved[nid] = next(iter(asserted))
        elif declared and asserted == {declared}:
            resolved[nid] = declared
    return resolved


def _initiative_of(graph: KnowledgeGraph) -> Dict[str, str]:
    """Which Initiative each node belongs to, from the scoping edges ingest writes.

    `delivers_initiative` (architecture elements) and `authorised_by_initiative`
    (requirements) are the two directions of the same anchor. The assertion's
    claim scope `initiative_id` is the fallback: a run ingested under an
    Initiative scopes its facts to it even when no `initiative_ref` was
    extracted, and on `data/` that fallback is the only signal that exists.

    One Initiative per node; disagreement resolves to unknown rather than to
    whichever happened to be asserted first.
    """
    found: Dict[str, set] = {}

    def note(node_id: str, initiative: str) -> None:
        text = (initiative or "").strip()
        if node_id and text:
            found.setdefault(node_id, set()).add(text)

    for a in graph.active():
        if a.predicate in ("delivers_initiative", "authorised_by_initiative"):
            target = graph.nodes.get(a.object) if a.object else None
            note(a.subject, target.label if target else (a.value or ""))
        else:
            note(a.subject, a.initiative_id or "")
    # A node's own `initiative_id` is a claim about the node, so it is read off
    # any active assertion naming it — including resolved links, whose claim
    # scope is carried from the reference they replaced.
    return {nid: next(iter(values)) for nid, values in found.items() if len(values) == 1}


def _preference(c: Candidate) -> Tuple[int, int, float, str]:
    """Same Initiative, then the other graph, then score, then label."""
    return (0 if c.same_initiative else 1, 0 if c.other_side else 1, -c.score, c.label.lower())


def _candidate_order(
    candidates: List[Candidate], threshold: float
) -> List[Candidate]:
    """Rank candidates: acceptable ones first, then Initiative, then side, then score.

    THE THRESHOLD IS PART OF THE ORDER, not applied after it. Preferring the same
    Initiative is only useful among candidates that would actually be *bound*: if
    the candidate this element delivers scores 0.5 and another scores 0.9, ranking
    the weak one first would block a defensible link to honour a signal that is
    weaker than the match itself. Grouping by acceptability first means a wrong
    Initiative never displaces a good match, and among equally acceptable ones the
    Initiative decides.

    Within a group:
      - the same Initiative wins, because it is the strongest deterministic anchor
        available when requirement identifiers are missing or paraphrased (YB-005)
      - then the other graph, because a cross-graph predicate *claims* its referent
        lives there, so a candidate from the source's own document is the weaker
        reading of the same words
      - then the score, then the label, so the order is total and stable
    """
    acceptable = [c for c in candidates if c.score >= threshold]
    rest = [c for c in candidates if c.score < threshold]
    return sorted(acceptable, key=_preference) + sorted(rest, key=_preference)


def reference_candidates(
    graph: KnowledgeGraph,
    assertion_ids: Optional[Iterable[str]] = None,
    limit: int = 3,
    near_miss_limit: int = 3,
) -> List[ReferenceCandidates]:
    """Propose target nodes for each unresolved cross-graph reference."""
    wanted = set(assertion_ids) if assertion_ids is not None else None
    table = _node_table(graph)
    sides = node_sides(graph)
    initiatives = _initiative_of(graph)

    out: List[ReferenceCandidates] = []
    for a in graph.unresolved_references():
        if wanted is not None and a.id not in wanted:
            continue

        source = graph.nodes.get(a.subject)
        expected = EXPECTED_TARGET_KINDS.get(a.predicate, frozenset())
        assertion_doc = _document_of_run(graph, a.provenance.run_id)
        source_side = sides.get(a.subject, "")
        source_initiative = initiatives.get(a.subject, "") or (a.initiative_id or "")

        matches: List[Candidate] = []
        near: List[Candidate] = []
        for node, _norm, refs in table:
            if node.id == a.subject:
                continue  # a thing does not reference itself
            score, reason = match_score(a.target, node.label, refs, assertion_doc, a.predicate)
            if score <= 0.0:
                continue
            in_kind = (not expected) or node.kind in expected
            # A candidate of neither the expected kind nor a kind a cross-graph
            # predicate could sensibly point at is lexical noise — a
            # `TechnologyStack` scoring 0.7 against "Performance" is not a
            # proposal, it is an unrelated node that shares a word. Kind scoping
            # filters those out of the option list rather than letting them pad
            # it; a genuinely wrong-kind but plausible node stays reportable.
            plausible = (not expected) or in_kind or node.kind in PLAUSIBLE_TARGET_KINDS
            if not plausible:
                continue
            candidate = Candidate(
                node_id=node.id,
                label=node.label,
                kind=node.kind,
                score=score,
                reason=reason,
                in_expected_kind=in_kind,
                same_initiative=bool(
                    source_initiative and initiatives.get(node.id, "") == source_initiative
                ),
                other_side=bool(source_side and sides.get(node.id, "") not in ("", source_side)),
            )
            (matches if in_kind else near).append(candidate)

        matches = _candidate_order(matches, DEFAULT_MATCH_THRESHOLD)
        near = _candidate_order(near, DEFAULT_MATCH_THRESHOLD)

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
                source_initiative=source_initiative,
                source_side=source_side,
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


# Reasons that ARE identity rather than resemblance. A document's own stable key,
# or wording that matches verbatim, leaves no room for the link to be wrong about
# *which* thing it points at.
_IDENTITY_REASONS = frozenset({"exact", "external_ref", "citing_document_key"})


def _link_confidence(
    proposed: Optional[float], computed: float, reason: str
) -> float:
    """How sure the link is, as a band rather than a constant.

    A resolved link used to arrive at confidence 1.0 unconditionally, which
    claimed more than the evidence supported: the human accepted a *proposal*,
    and the proposal's own strength is what says how much of a risk that was.
    The band keeps that evidence on the link:

      - exact wording or an enterprise identifier -> 1.0 (identity, not similarity)
      - a lexical proposal -> half its distance from a perfect score, so a match
        at the acceptance threshold keeps 0.75 and a near-perfect one keeps ~0.95
      - a target the reviewer picked without a proposal -> 1.0 (their own claim)

    `status=VERIFIED` with human provenance remains the separate authority claim:
    a low-confidence band means "review the reasoning", not "an agent guessed".
    """
    if proposed is None or reason in _IDENTITY_REASONS:
        return 1.0
    return round(0.5 + max(0.0, min(1.0, computed)) / 2, 3)


def resolve_reference(
    graph: KnowledgeGraph,
    log: ReviewLog,
    assertion_id: str,
    target_node_id: str,
    actor: str = "",
    note: str = "",
    allow_kind_override: bool = False,
    proposed_score: Optional[float] = None,
    match_reason: str = "",
) -> Decision:
    """Bind one unresolved reference to an existing node.

    Never invents the target: resolution asserts that the referent was already
    extracted and merely not joined. Creating the node would be a different act
    with different consequences, so it is not offered as a side effect here.

    `proposed_score`/`match_reason` carry WHY this target was proposed, when a
    proposal produced it (`reference_candidates`, `bulk_resolve`). They set the
    link's confidence as a band — see `_link_confidence` — instead of asserting
    1.0 merely because a machine proposed the link. Omitted (a target typed into
    the form by hand), the link is the reviewer's own claim and scores 1.0.
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

    if proposed_score is not None:
        # A proposal already scored this pair under its own predicate; re-scoring
        # here would drop the predicate and could contradict the reason the
        # reviewer was shown (an identifier citation is the case in point: the
        # citation rule makes it identity, and a bare re-score does not know that).
        score, reason = proposed_score, (match_reason or reason)
    else:
        score, reason = match_score(a.target, node.label, node.external_references, "", a.predicate)
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
        confidence=_link_confidence(proposed_score, score, reason),
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
    if resolved is None:
        # Binding the reference to this node would state an irreflexive fact
        # (the reference resolves to its own subject). Refused at the write
        # boundary; the assertion stays unresolved rather than being marked
        # superseded by a replacement the graph does not hold.
        raise ReconcileError(
            f"cannot bind {a.id} to {node.id}: a reflexive fact is refused"
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
    # assertion id -> replacement assertion id, so a caller can look at what the
    # binding actually produced (its confidence band, its target kind) without
    # re-deriving it from the audit log.
    bound: List[Tuple[str, str]] = field(default_factory=list)
    # Requirements still without an architectural answer AFTER the pass. A
    # resolution run that binds links but leaves the requirement side untouched
    # should be able to say so in the same breath — that gap is the thing the
    # whole exercise exists to find.
    unrealized_requirements: int = 0

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
            "unrealized_requirements": self.unrealized_requirements,
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
        decision = resolve_reference(
            graph,
            log,
            assertion_id,
            best.node_id,
            actor=actor,
            note=note,
            allow_kind_override=False,
            proposed_score=best.score,
            match_reason=best.reason,
        )
        result.resolved.append(assertion_id)
        result.bound.append((assertion_id, decision.replacement_id))

    result.unrealized_requirements = _unrealized_count(graph)
    return result


def _unrealized_count(graph: KnowledgeGraph) -> int:
    """Requirements with no bound realization edge. Imported lazily.

    `realization` imports this module for `REQUIREMENT_KINDS` and the node
    helpers, so a top-level import here would be a cycle. Importing inside the
    call keeps the dependency one-way (realization -> reconcile) and costs
    nothing: this runs once per bulk pass, not per candidate.
    """
    from .realization import unrealized_requirements

    return len(unrealized_requirements(graph))
