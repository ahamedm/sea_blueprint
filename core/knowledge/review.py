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
    Assertion,
    KnowledgeGraph,
    Provenance,
    is_initiative_scope,
    reflexive_violation,
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
    """A human vouches for an agent's assertion. Provenance flips to human.

    A reflexive fact on an irreflexive predicate cannot be verified, and the
    refusal is raised rather than returned: verifying is the act that turns a
    wrong extraction into human authority, and four `X part_of X` facts acquired
    exactly that status through a bulk verify (YB-052). The repository may still
    hold such facts from an older revision, so the refusal has to be here, not
    only at the point where they are written.
    """
    a = _require(graph, assertion_id)
    reason = reflexive_violation(a.subject, a.predicate, a.object)
    if reason:
        raise ReviewError(f"cannot verify a reflexive assertion: {reason}")
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
    if replacement is None:
        # The correction would state something impossible (`X part_of X`). Refused
        # at the write boundary like any other reflexive fact, and reported rather
        # than recording a replacement that the graph does not hold.
        raise ReviewError(
            f"cannot correct {assertion_id} to a reflexive fact: "
            + reflexive_violation(a.subject, predicate, obj)
        )
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
) -> "BulkResult":
    """Verify many assertions at once.

    Selection is re-derived from the graph rather than trusted from the form, so
    a stale checkbox list cannot act on assertions the reviewer never saw.

    Returns what it decided AND what it declined, because "3 of 5" without a
    reason reads as a bug to the reviewer who selected five. A reflexive fact is
    refused with its reason (YB-052); a settled one is reported as settled.
    """
    result = BulkResult()
    if assertion_ids is not None:
        targets = []
        for aid in assertion_ids:
            a = graph.assertions.get(aid)
            if a is None:
                # The page in front of the reviewer is older than the graph. That
                # is normal, and it is reported rather than silently absorbed into
                # a smaller count — YB-052 asked for the reasons, not a number.
                result.skipped.append(BulkSkip(str(aid), "not found"))
            elif not a.is_active:
                result.skipped.append(BulkSkip(a.id, _ALREADY_SETTLED))
            else:
                targets.append(a)
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

    for a in targets:
        reason = _skip_reason(a, ACTION_VERIFY)
        if reason:
            result.skipped.append(BulkSkip(a.id, reason))
            continue
        result.decisions.append(log.record(verify(graph, a.id, actor, note)))
    return result


BULK_ACTIONS = (ACTION_VERIFY, ACTION_DISPUTE, ACTION_RETIRE, ACTION_RESET)
"""The actions a reviewer may apply to a whole selection.

`correct` is deliberately absent: it needs a replacement target per assertion, so
"correct these five" has no single meaning.
"""


@dataclass
class BulkSkip:
    """One selected assertion a bulk action did not act on, and why."""

    assertion_id: str
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.assertion_id, "reason": self.reason}


_ALREADY_SETTLED = "already in that state"
"""The no-op skip reason. Distinct from a refusal so a page can say which is which."""


@dataclass
class BulkResult:
    """What a bulk action did, and what it declined to do.

    A bare count cannot tell "already in that state" from "refused because the
    fact is impossible", and the second is exactly what the reviewer has to be
    told: four `X part_of X` facts became VERIFIED through a bulk verify that
    reported only a number (YB-052). The type is list-compatible on purpose —
    `len()` and truthiness read the decisions — so a caller that only counts
    keeps working while the page can show the reasons.
    """

    decisions: List[Decision] = field(default_factory=list)
    skipped: List[BulkSkip] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.decisions)

    def __bool__(self) -> bool:
        return bool(self.decisions)

    def __iter__(self):
        return iter(self.decisions)

    @property
    def refused(self) -> List[BulkSkip]:
        """Skips that are refusals rather than no-ops — what the reviewer must see."""
        return [s for s in self.skipped if s.reason != _ALREADY_SETTLED]


def _skip_reason(assertion: "Assertion", action: str) -> str:
    """Why this action would not apply — "" when it would.

    Skipped rather than applied blindly, so the count a page reports is the number of
    decisions actually recorded. "Verify selected" over a mixed selection should say
    three, not five, or the reviewer learns to distrust the number.

    Refusal is a different outcome from a no-op and carries its own reason: an
    irreflexive fact cannot be verified by anyone, so verifying it is not a state
    the graph declines to enter twice — it is a claim no reviewer can intend.
    """
    if action == ACTION_VERIFY:
        if assertion.status != STATUS_UNVERIFIED:
            return _ALREADY_SETTLED
        return reflexive_violation(assertion.subject, assertion.predicate,
                                   assertion.object)
    if action == ACTION_DISPUTE:
        # A dispute on a removed or superseded fact is meaningless: there is nothing
        # left in play to disagree with.
        if not assertion.is_active or assertion.status == STATUS_DISPUTED:
            return _ALREADY_SETTLED
        return ""
    if action == ACTION_RESET:
        # Reopening is the RESTORE path, so a retired assertion is eligible even
        # though its status may already read UNVERIFIED — `is_active` is what says
        # whether there is anything to undo.
        if assertion.status == STATUS_UNVERIFIED and assertion.is_active:
            return _ALREADY_SETTLED
        return ""
    if action == ACTION_RETIRE:
        return "" if assertion.is_active else _ALREADY_SETTLED
    return f"unknown action: {action!r}"


def bulk_apply(
    graph: KnowledgeGraph,
    log: ReviewLog,
    assertion_ids: Iterable[str],
    action: str,
    actor: str = "",
    note: str = "",
) -> BulkResult:
    """Apply one action to many selected assertions.

    Selection is re-derived from the graph exactly as `bulk_verify` does it: a stale
    checkbox list cannot act on an assertion the reviewer never saw, and an id that
    has since disappeared is skipped rather than raising — the page in front of them
    is older than the graph, which is normal rather than exceptional.

    Every action goes through `apply_decisions`, so a bulk decision and a single one
    cannot diverge in what they record.
    """
    if action not in BULK_ACTIONS:
        raise ReviewError(
            f"unknown bulk action: {action!r} (one of {', '.join(BULK_ACTIONS)})"
        )
    result = BulkResult()
    for assertion_id in assertion_ids or ():
        assertion = graph.assertions.get(assertion_id)
        if assertion is None:
            result.skipped.append(BulkSkip(str(assertion_id), "not found"))
            continue
        reason = _skip_reason(assertion, action)
        if reason:
            result.skipped.append(BulkSkip(assertion.id, reason))
            continue
        result.decisions.append(
            apply_decisions(graph, log, assertion_id, action, actor=actor, note=note)
        )
    return result


def promote_to_baseline(
    graph: KnowledgeGraph, log: ReviewLog, actor: str = "", note: str = ""
) -> Tuple["PromotionResult", Decision]:
    """Promote verified initiative facts into the system baseline.

    This is the "merge an Initiative into the Baseline" step of
    `docs/living-system-architecture.md`. Unverified and disputed facts are
    deliberately left behind — a baseline that absorbs unchecked extraction is
    not a baseline.

    EVERY fact is classified into exactly one bucket. That is the point of
    `unrecognised_scope`: this loop used to skip anything whose scope it did not
    recognise, counting nothing, so a fact that could never be promoted was
    indistinguishable from there being nothing to promote. The scope collision in
    `model.py` made exactly that happen — a fact restored through `serialise`'s load
    default carried `"INITIATIVE_PROPOSAL"` while the constant in force said
    `"INITIATIVE"`, and the merge reported "0 promoted, 0 left behind".
    """
    result = PromotionResult()
    promoted: List[str] = []
    for a in list(graph.assertions.values()):
        if a.scope == SCOPE_BASELINE:
            result.already_baseline += 1
            continue
        if not is_initiative_scope(a.scope):
            # Neither baseline nor an initiative proposal. Only an inactive fact or
            # an unrecognised scope reaches here, and both are worth counting: the
            # first because a retired fact is not a proposal to merge, the second
            # because it is a vocabulary disagreement we cannot see otherwise.
            if a.is_active:
                result.unrecognised_scope += 1
            continue
        if not a.is_active:
            result.skipped_inactive += 1
        elif a.status in REVIEWED_STATUSES:
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
    """What a promotion did, with every input fact accounted for.

    The four skip counters are not decoration: "promoted 0" is only informative
    next to what it passed over, and a bucket that does not exist is a fact that
    vanishes without being reported.
    """

    promoted: int = 0
    skipped_unverified: int = 0
    skipped_disputed: int = 0
    skipped_inactive: int = 0
    already_baseline: int = 0
    unrecognised_scope: int = 0

    @property
    def considered(self) -> int:
        """Every fact the promotion classified."""
        return (self.promoted + self.skipped_unverified + self.skipped_disputed
                + self.skipped_inactive + self.already_baseline
                + self.unrecognised_scope)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "promoted": self.promoted,
            "skipped_unverified": self.skipped_unverified,
            "skipped_disputed": self.skipped_disputed,
            "skipped_inactive": self.skipped_inactive,
            "already_baseline": self.already_baseline,
            "unrecognised_scope": self.unrecognised_scope,
        }
