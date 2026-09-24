"""
Human review decisions — the write path behind the review gate.

WHY THIS IS A MODULE AND NOT ROUTE CODE
---------------------------------------
A review decision is a knowledge-layer operation, not a UI event. The CLI
reviewer (`scripts/review_assertions.py`) and the web gate must produce identical
graphs from identical decisions, or "verified" means different things depending
on which door you came in through. `docs/user-journey.md` gap 2 is exactly this:
*the gate has nowhere to record a decision.*

WHAT A DECISION IS
------------------
Every action is recorded as a `Decision` carrying what changed (`before` /
`after`), who did it and when. That log is the answer to "why does the graph look
like this?", which is the question an auditor actually asks — the graph alone
only shows the current state, not the reasoning.

CORRECTION IS SUPERSESSION, NOT MUTATION
----------------------------------------
Assertion identity is content-addressed (`make_assertion_id`), so changing an
object changes the assertion's id. Correcting therefore writes a *new* assertion
and marks the old one `SUPERSEDED` with `superseded_by` pointing at the
replacement. Mutating in place would erase the fact that a human changed their
mind, and would break the re-extraction merge: the agent would re-assert the old
fact and, with no superseded record, it would fold back in alongside the
correction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .model import (
    CROSS_GRAPH_PREDICATES,
    RETIREMENT_MARK,
    SCOPE_BASELINE,
    SCOPE_INITIATIVE,
    SOURCE_HUMAN_REVIEWER,
    STATUS_CORRECTED,
    STATUS_DISPUTED,
    STATUS_RETIRED,
    STATUS_SUPERSEDED,
    STATUS_UNVERIFIED,
    STATUS_VERIFIED,
    KnowledgeGraph,
    Provenance,
    utc_now,
)

# Actions a reviewer can take on one assertion.
ACTION_VERIFY = "verify"
ACTION_CORRECT = "correct"
ACTION_DISPUTE = "dispute"
ACTION_RESET = "reset"
# Remove a fact the extractor should not have produced. Distinct from `dispute`,
# which keeps it: see `retire` for why both are needed.
ACTION_RETIRE = "retire"
# Bulk action: promote verified initiative facts into the system baseline.
ACTION_PROMOTE = "promote_baseline"
# Reconciliation: bind an unresolved cross-graph reference to an existing node.
# Recorded here because it belongs to the same audit trail as every other human
# decision, even though it is applied by `reconcile.resolve_reference`.
ACTION_RESOLVE = "resolve"

REVIEWED_STATUSES = frozenset({STATUS_VERIFIED, STATUS_CORRECTED})


class ReviewError(Exception):
    """A decision that could not be applied (unknown assertion, no change made)."""


# ============================================================================
# Decision log
# ============================================================================


@dataclass
class Decision:
    """One recorded act of human judgement."""

    action: str
    assertion_id: str = ""
    actor: str = ""
    at: str = ""
    note: str = ""
    before: Dict[str, Any] = field(default_factory=dict)
    after: Dict[str, Any] = field(default_factory=dict)
    replacement_id: str = ""
    promoted_ids: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return {
            ACTION_VERIFY: "Verified",
            ACTION_CORRECT: "Corrected",
            ACTION_DISPUTE: "Disputed",
            ACTION_RETIRE: "Removed",
            ACTION_RESET: "Reopened",
            ACTION_PROMOTE: "Baselined",
            ACTION_RESOLVE: "Resolved",
        }.get(self.action, self.action)

    @property
    def changed_fields(self) -> List[str]:
        """Which fields actually moved — the diff a reviewer wants to see."""
        keys = set(self.before) | set(self.after)
        return sorted(k for k in keys if self.before.get(k) != self.after.get(k))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "assertion_id": self.assertion_id,
            "actor": self.actor,
            "at": self.at,
            "note": self.note,
            "before": self.before,
            "after": self.after,
            "replacement_id": self.replacement_id,
            "promoted_ids": list(self.promoted_ids),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Decision":
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


@dataclass
class ReviewLog:
    """Append-only audit trail of review decisions."""

    entries: List[Decision] = field(default_factory=list)

    def record(self, decision: Decision) -> Decision:
        if not decision.at:
            decision.at = utc_now()
        self.entries.append(decision)
        return decision

    def for_assertion(self, assertion_id: str) -> List[Decision]:
        return [
            d
            for d in self.entries
            if d.assertion_id == assertion_id or d.replacement_id == assertion_id
        ]

    def by_actor(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for d in self.entries:
            key = d.actor or "(unattributed)"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for d in self.entries:
            counts[d.action] = counts.get(d.action, 0) + 1
        return dict(sorted(counts.items()))

    def to_dict(self) -> Dict[str, Any]:
        return {"entries": [d.to_dict() for d in self.entries]}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReviewLog":
        return cls(entries=[Decision.from_dict(d) for d in (data or {}).get("entries", [])])


# ============================================================================
# Progress — the gate's view of the graph
# ============================================================================


@dataclass
class ReviewProgress:
    """How far the graph is from being auditable.

    `docs/user-journey.md` §4: step 5 (audit) refuses to run on an unverified
    graph, because an audit over unverified extraction reports extraction
    artifacts as architecture gaps — confidently wrong, which is worse than no
    audit. This is the number that gate reads.
    """

    total: int = 0
    unverified: int = 0
    verified: int = 0
    corrected: int = 0
    disputed: int = 0
    retired: int = 0
    superseded: int = 0
    human: int = 0

    @property
    def reviewed(self) -> int:
        return self.verified + self.corrected

    @property
    def outstanding(self) -> int:
        """Assertions a human has not yet decided on.

        Kept as `unverified + disputed` and deliberately NOT narrowed. It is the
        "needs attention" count the review queue shows, and a disputed fact is one
        of those — someone rejected it and nobody has said what happens next.
        """
        return self.unverified + self.disputed

    @property
    def blocking(self) -> int:
        """What actually withholds the audit, which is narrower than `outstanding`.

        Only assertions NOBODY has judged block. A disputed claim is a human
        decision, so counting it as an outstanding *review* was backwards: a
        reviewer could dispute an invented fact, verify every other assertion in the
        graph, and still be told the graph was not auditable — with no action left
        that would make it so. That is the trap this property removes.

        It does not make the disputed fact go away. `is_active` is unchanged by a
        dispute, so the claim is still in the graph, still in the audit, and still
        on the map; clearing one out is `retire`.
        """
        return self.unverified

    @property
    def is_auditable(self) -> bool:
        return self.blocking == 0 and self.total > 0

    @property
    def percent_reviewed(self) -> int:
        return int(round(100 * self.reviewed / self.total)) if self.total else 0

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "total": self.total,
            "unverified": self.unverified,
            "verified": self.verified,
            "corrected": self.corrected,
            "disputed": self.disputed,
            "retired": self.retired,
            "superseded": self.superseded,
            "human": self.human,
            "reviewed": self.reviewed,
            "outstanding": self.outstanding,
            "blocking": self.blocking,
            "is_auditable": self.is_auditable,
            "percent_reviewed": self.percent_reviewed,
        }
        return d


def review_progress(graph: KnowledgeGraph) -> ReviewProgress:
    p = ReviewProgress()
    for a in graph.assertions.values():
        if not a.is_active:
            p.superseded += 1
            # Retirement is a supersession with nothing in its place, so it is
            # counted separately — the two say different things about what a human
            # did, and lumping them would hide how much of the graph was invented.
            if a.status == STATUS_RETIRED:
                p.retired += 1
            continue
        p.total += 1
        if a.is_human:
            p.human += 1
        if a.status == STATUS_VERIFIED:
            p.verified += 1
        elif a.status == STATUS_CORRECTED:
            p.corrected += 1
        elif a.status == STATUS_DISPUTED:
            p.disputed += 1
        else:
            p.unverified += 1
    return p


# ============================================================================
# Applying decisions
# ============================================================================


def _require(graph: KnowledgeGraph, assertion_id: str):
    # Callers are web forms and CLI arguments, so a non-string here means a
    # programming error upstream. Say so plainly: `dict.get` would fail with
    # "unhashable type" and send the reader hunting in the wrong place.
    if not isinstance(assertion_id, str):
        raise ReviewError(
            "expected an assertion id string, got "
            f"{type(assertion_id).__name__} — pass `.id`, not the assertion"
        )
    a = graph.assertions.get(assertion_id)
    if a is None:
        raise ReviewError(f"unknown assertion: {assertion_id}")
    return a


def _snapshot(a) -> Dict[str, Any]:
    return {
        "status": a.status,
        "confidence": a.confidence,
        "object": a.object,
        "value": a.value,
        "scope": a.scope,
        "superseded_by": a.superseded_by,
    }


def _human_provenance(a, actor: str, note: str) -> Provenance:
    """A human decision inherits the agent's traceability, not its authority."""
    return Provenance(
        source_type=SOURCE_HUMAN_REVIEWER,
        run_id=a.provenance.run_id,
        pass_name=a.provenance.pass_name,
        model_id=a.provenance.model_id,
        chunk_label=a.provenance.chunk_label,
        asserted_at=utc_now(),
        asserted_by=actor or "reviewer",
        derived_from=a.provenance.derived_from,
        correction_note=note,
    )


def _resolve_target(graph: KnowledgeGraph, predicate: str, target: str) -> Optional[str]:
    """Decide whether a corrected target names a node or is a literal.

    Cross-graph predicates deliberately keep literals: turning
    `implements_requirement: "FR-PM-001"` into a local node would invent an
    entity and destroy the unresolved/resolved distinction that reconciliation
    depends on.
    """
    if predicate in CROSS_GRAPH_PREDICATES:
        return None
    if target in graph.nodes:
        return target
    wanted = target.strip().lower()
    for node in graph.nodes.values():
        if node.label.strip().lower() == wanted:
            return node.id
    return None


def verify(graph: KnowledgeGraph, assertion_id: str, actor: str = "", note: str = "") -> Decision:
    """A human vouches for an agent's assertion. Provenance flips to human."""
    a = _require(graph, assertion_id)
    before = _snapshot(a)
    a.status = STATUS_VERIFIED
    a.provenance = _human_provenance(a, actor, note)
    a.confidence = 1.0
    return Decision(
        action=ACTION_VERIFY,
        assertion_id=assertion_id,
        actor=actor,
        note=note,
        before=before,
        after=_snapshot(a),
    )


def dispute(graph: KnowledgeGraph, assertion_id: str, actor: str = "", note: str = "") -> Decision:
    """A human rejects the claim but leaves it in the active graph.

    NOT a deletion, and the distinction is the point. `dispute` records a verdict
    on a fact that is still in play — it may be referred to, argued about, or
    resolved later by verifying or correcting it — so the assertion stays active
    and every query still sees it. `retire` is the other act: the fact should never
    have been extracted, and the graph should stop answering with it.

    Because it stays active, a dispute does NOT remove the fact from the gap report
    or the map. Someone who wants it gone wants `retire`.
    """
    a = _require(graph, assertion_id)
    before = _snapshot(a)
    a.status = STATUS_DISPUTED
    a.provenance = _human_provenance(a, actor, note)
    return Decision(
        action=ACTION_DISPUTE,
        assertion_id=assertion_id,
        actor=actor,
        note=note,
        before=before,
        after=_snapshot(a),
    )


def retire(graph: KnowledgeGraph, assertion_id: str, actor: str = "", note: str = "") -> Decision:
    """Remove a fact from the active graph, without putting anything in its place.

    WHY THIS IS NOT `dispute`. A dispute keeps the claim in play and blocks the
    audit gate until someone resolves it; it is an annotation on a fact that is
    still there. An assertion the extractor invented — a predicate no part of the
    architecture exhibits, a reference to something that does not exist — has no
    resolution: the only correct end state is for the graph to stop asserting it.
    Without this action there was no way to reach that state, and the fact went on
    polluting `active()`, the gap report and the map while carrying a flag.

    HOW IT IS REMOVED, AND WHY THAT WAY. Supersession is this model's deletion:
    `is_active` is false once `superseded_by` is set, and every query, audit and
    view filters on it. Retirement is that mechanism with `RETIREMENT_MARK` in
    place of a replacement id, so the lineage still says what happened — the
    difference between "replaced by this" and "removed, nothing took its place" is
    legible from the assertion alone.

    The mark is a non-empty sentinel rather than `""` for a concrete reason:
    `merge_graphs` carries `superseded_by` across a re-extraction only when it is
    truthy, so an empty one would let the next run fold the identical assertion back
    in as active. The removal would silently undo itself, which is the failure mode
    a durable deletion exists to avoid.

    WHAT IT DOES NOT DO:
      - It does not delete the assertion. It stays in the graph, in the audit trail,
        and in the `Removed` filter, and `reset` restores it.
      - It does not delete NODES. A node's identity is deliberately durable so
        re-extraction converges and corrections have something to attach to;
        removing the node would have the next run recreate it. Retire the
        assertions about a spurious node instead, and the orphan it leaves is
        reported by `dangling_assertions()` rather than hidden.
      - It does not retract a fact from a FROZEN baseline revision. Revision
        snapshots are immutable by design, so a retraction has to be expressed as a
        change relative to them (YB-009's territory).
    """
    a = _require(graph, assertion_id)
    if not a.is_active:
        raise ReviewError(f"{assertion_id} is {a.status}, not an active assertion")
    before = _snapshot(a)
    a.status = STATUS_RETIRED
    a.superseded_by = RETIREMENT_MARK
    a.provenance = _human_provenance(a, actor, note)
    return Decision(
        action=ACTION_RETIRE,
        assertion_id=assertion_id,
        actor=actor,
        note=note,
        before=before,
        after=_snapshot(a),
    )


def reset(graph: KnowledgeGraph, assertion_id: str, actor: str = "", note: str = "") -> Decision:
    """Undo a decision — back to the queue, attributed to the agent again.

    Also the RESTORE path for a removed fact, and that part needed fixing: setting
    the status back to UNVERIFIED is not enough on its own, because `is_active`
    reads the lineage too. An assertion retired earlier kept `superseded_by ==
    RETIREMENT_MARK`, so "reopening" it left it inactive and invisible — a restore
    that silently did nothing.

    The two cases are told apart by what the lineage points at. `RETIREMENT_MARK`
    means nothing replaced it, so reopening is complete once the mark is cleared.
    Any other value is a real replacement held in the graph, and the link to it is
    left alone: reopening a superseded assertion means "resume reviewing this", not
    "orphan its correction", which would break the lineage invariant.
    """
    a = _require(graph, assertion_id)
    before = _snapshot(a)
    a.status = STATUS_UNVERIFIED
    if a.superseded_by == RETIREMENT_MARK:
        a.superseded_by = None
    a.provenance = Provenance(
        source_type=a.provenance.source_type if not a.provenance.is_human else "EXTRACTION_AGENT",
        run_id=a.provenance.run_id,
        pass_name=a.provenance.pass_name,
        model_id=a.provenance.model_id,
        chunk_label=a.provenance.chunk_label,
        asserted_at=a.provenance.asserted_at,
        derived_from=a.provenance.derived_from,
    )
    return Decision(
        action=ACTION_RESET,
        assertion_id=assertion_id,
        actor=actor,
        note=note,
        before=before,
        after=_snapshot(a),
    )


def correct(
    graph: KnowledgeGraph,
    assertion_id: str,
    corrected_target: Optional[str] = None,
    corrected_predicate: Optional[str] = None,
    corrected_confidence: Optional[float] = None,
    actor: str = "",
    note: str = "",
) -> Decision:
    """Replace a claim with the human's version, keeping the original for lineage.

    Returns the recorded decision. `replacement_id` names the surviving
    assertion — which is what the UI should highlight afterwards.
    """
    a = _require(graph, assertion_id)
    before = _snapshot(a)

    predicate = (corrected_predicate or a.predicate).strip() or a.predicate
    target = a.target if corrected_target is None else str(corrected_target).strip()

    obj = _resolve_target(graph, predicate, target)
    value = None if obj is not None else target
    confidence = 1.0 if corrected_confidence is None else float(corrected_confidence)

    replacement = graph.add_assertion(
        a.subject,
        predicate,
        obj=obj,
        value=value,
        confidence=confidence,
        source_text=a.source_text,
        ontology_class=a.ontology_class,
        provenance=_human_provenance(a, actor, note),
        status=STATUS_CORRECTED,
        scope=a.scope,
        initiative_id=a.initiative_id,
    )

    # Nothing actually moved: `add_assertion` folded the human provenance onto
    # the original assertion in place. Superseding it would point the record at
    # itself and erase the fact from the active set.
    if replacement.id == a.id:
        return Decision(
            action=ACTION_CORRECT,
            assertion_id=assertion_id,
            actor=actor,
            note=note,
            before=before,
            after=_snapshot(a),
            replacement_id=replacement.id,
        )

    replacement.status = STATUS_CORRECTED
    a.status = STATUS_SUPERSEDED
    a.superseded_by = replacement.id
    return Decision(
        action=ACTION_CORRECT,
        assertion_id=assertion_id,
        actor=actor,
        note=note,
        before=before,
        after=_snapshot(replacement),
        replacement_id=replacement.id,
    )


def apply_decisions(
    graph: KnowledgeGraph,
    log: ReviewLog,
    assertion_id: str,
    action: str,
    actor: str = "",
    note: str = "",
    target: Optional[str] = None,
    predicate: Optional[str] = None,
) -> Decision:
    """Single entry point used by the web layer and the CLI, so both agree."""
    if action == ACTION_VERIFY:
        decision = verify(graph, assertion_id, actor, note)
    elif action == ACTION_DISPUTE:
        decision = dispute(graph, assertion_id, actor, note)
    elif action == ACTION_RETIRE:
        decision = retire(graph, assertion_id, actor, note)
    elif action == ACTION_RESET:
        decision = reset(graph, assertion_id, actor, note)
    elif action == ACTION_CORRECT:
        decision = correct(
            graph,
            assertion_id,
            corrected_target=target,
            corrected_predicate=predicate,
            actor=actor,
            note=note,
        )
    else:
        raise ReviewError(f"unknown action: {action}")
    return log.record(decision)


# ============================================================================
# Bulk operations
# ============================================================================


def bulk_verify(
    graph: KnowledgeGraph,
    log: ReviewLog,
    assertion_ids: Optional[Iterable[str]] = None,
    max_confidence: Optional[float] = None,
    subject: Optional[str] = None,
    actor: str = "",
    note: str = "",
) -> List[Decision]:
    """Verify many assertions at once.

    Selection is re-derived from the graph rather than trusted from the form, so
    a stale checkbox list cannot act on assertions the reviewer never saw.
    """
    if assertion_ids is not None:
        targets = [graph.assertions.get(aid) for aid in assertion_ids]
        targets = [a for a in targets if a is not None and a.is_active]
    else:
        targets = []
        for a in graph.active():
            if subject and a.subject != subject:
                continue
            if max_confidence is not None and a.confidence > max_confidence:
                continue
            if a.status != STATUS_UNVERIFIED:
                continue
            targets.append(a)

    decisions = []
    for a in targets:
        if a.status != STATUS_UNVERIFIED:
            continue
        decisions.append(log.record(verify(graph, a.id, actor, note)))
    return decisions


def promote_to_baseline(
    graph: KnowledgeGraph, log: ReviewLog, actor: str = "", note: str = ""
) -> Tuple["PromotionResult", Decision]:
    """Promote verified initiative facts into the system baseline.

    This is the "merge an Initiative into the Baseline" step of
    `docs/living-system-architecture.md`. Unverified and disputed facts are
    deliberately left behind — a baseline that absorbs unchecked extraction is
    not a baseline.
    """
    result = PromotionResult()
    promoted: List[str] = []
    for a in list(graph.assertions.values()):
        if a.scope == SCOPE_BASELINE:
            result.already_baseline += 1
            continue
        if a.scope != SCOPE_INITIATIVE or not a.is_active:
            continue
        if a.status in REVIEWED_STATUSES:
            a.scope = SCOPE_BASELINE
            result.promoted += 1
            promoted.append(a.id)
        elif a.status == STATUS_DISPUTED:
            result.skipped_disputed += 1
        else:
            result.skipped_unverified += 1

    decision = log.record(
        Decision(
            action=ACTION_PROMOTE,
            actor=actor,
            note=note,
            promoted_ids=promoted,
            after={"promoted": result.promoted, "scope": SCOPE_BASELINE},
        )
    )
    return result, decision


@dataclass
class PromotionResult:
    promoted: int = 0
    skipped_unverified: int = 0
    skipped_disputed: int = 0
    already_baseline: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "promoted": self.promoted,
            "skipped_unverified": self.skipped_unverified,
            "skipped_disputed": self.skipped_disputed,
            "already_baseline": self.already_baseline,
        }
