#!/usr/bin/env python3
"""
Headless reconciliation — propose and apply cross-graph reference bindings without
the Flask app.

WHY THIS EXISTS. Reconciliation has only ever been reachable through the web
route: open `/reconcile`, read the proposals, press "bulk resolve". That makes a
routine, repeatable pass depend on a browser and a running server, so it cannot
run in CI, on a schedule, or on a machine where the UI is not installed. The
reconciliation itself has never needed the web layer — `reference_candidates` and
`bulk_resolve` live in `core/` — so this is a thin shell over them, and nothing on
this path imports Flask.

DRY RUN IS THE DEFAULT, because binding a traceability link is a judgement call
and the standing rule is that a wrong link is worse than a missing one: it makes
the audit confidently wrong rather than merely incomplete. So the script proposes
and reports, and writes nothing until `--apply` is given. It also reports what it
DECLINED to do — references below the threshold and references with no candidate
of the expected kind are named, never silently dropped.

Usage:
    .venv/bin/python scripts/reconcile.py                 # dry run on data/sea
    .venv/bin/python scripts/reconcile.py --apply         # bind what clears 0.75
    .venv/bin/python scripts/reconcile.py --threshold 0.9 --limit 10
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.knowledge import (
    DEFAULT_MATCH_THRESHOLD,
    RevisionStore,
    bulk_resolve,
    realization_report,
    reference_candidates,
)
from core.knowledge.realization import COVERAGE_ORDER

# The report's words for `ReferenceCandidates.status()`. Kept beside the status
# keys so a new status cannot be added without the CLI showing something for it.
_STATUS_LABELS = {
    "resolvable": "resolvable",
    "below_threshold": "below threshold",
    "no_candidate": "no candidate",
}


# ============================================================================
# Report
# ============================================================================


def _print_coverage(heading: str, graph: Any) -> None:
    """The requirement side of the audit, in the words the report already uses."""
    summary = realization_report(graph)["summary"]
    coverage = summary["coverage"]
    print(f"\n{heading}")
    print(
        f"  requirements {summary['requirements']}   "
        f"realized {summary['realized']}   unrealized {summary['unrealized']}"
    )
    print(
        "  coverage     "
        + "  ".join(f"{state}={coverage[state]}" for state in COVERAGE_ORDER)
    )


def _reference_line(proposal: Any) -> str:
    return (
        f"[{proposal.source_kind}] {proposal.source_label} · "
        f"{proposal.predicate} -> '{proposal.target_text}'"
    )


def _print_proposals(proposals: List[Any], threshold: float, limit: Optional[int]) -> None:
    print(f"\nPROPOSALS — {len(proposals)} open reference(s)")
    if not proposals:
        print("  none: every cross-graph reference already names a node.")
        return

    shown = proposals if limit is None else proposals[: max(0, limit)]
    for proposal in shown:
        status = proposal.status(threshold)
        label = _STATUS_LABELS.get(status, status)
        print(f"  {label:<15} {_reference_line(proposal)}")
        best = proposal.best
        if best is None:
            expected = ", ".join(proposal.expected_kinds) or "any"
            print(f"  {'':<15} no candidate of the expected kind ({expected})")
        else:
            below = "" if best.score >= threshold else f"  < threshold {threshold:g}"
            print(
                f"  {'':<15} best: [{best.kind}] {best.label}  "
                f"({best.reason} {best.score:.2f}){below}"
            )

    hidden = len(proposals) - len(shown)
    if hidden:
        print(f"  … {hidden} more proposal(s) not shown (--limit {limit})")


def _print_bindings(
    result: Any, proposals: Dict[str, Any], graph: Any, applied: bool
) -> None:
    verb = "BOUND" if applied else "WOULD BIND"
    suffix = "" if applied else " (nothing written)"
    print(f"\n{verb} — {len(result.bound)} reference(s){suffix}")
    for assertion_id, replacement_id in result.bound:
        proposal = proposals.get(assertion_id)
        if proposal is not None:
            print(f"  {_reference_line(proposal)}")
        replacement = graph.assertions.get(replacement_id)
        node = graph.nodes.get(replacement.object) if replacement else None
        target = f"[{node.kind}] {node.label}" if node else "?"
        confidence = f"  confidence {replacement.confidence:.2f}" if replacement else ""
        print(f"    -> {target}{confidence}")


def _print_declined(result: Any) -> None:
    """The counts, and the reason they are counts rather than actions.

    The individual references are already in the (capped) proposal section with
    their status; repeating every one here would make `--limit` meaningless on a
    graph where most references are declined, which is the common case.
    """
    if not (result.below_threshold or result.no_candidate):
        return
    print(
        f"\nDECLINED — {len(result.below_threshold)} below threshold, "
        f"{len(result.no_candidate)} with no candidate of the expected kind."
    )
    print(
        "  Nothing was bound on a weak match: a wrong traceability link is worse "
        "than a missing one. Resolve those individually, or lower the threshold "
        "deliberately."
    )


# ============================================================================
# Entry point
# ============================================================================


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--store-root",
        default="data/sea",
        help="working-set root (default: %(default)s)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_MATCH_THRESHOLD,
        help="minimum match score to bind a proposal (default: %(default)s)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="bind the proposals that clear the threshold and persist them; "
        "without this flag the run is a dry run",
    )
    parser.add_argument(
        "--actor",
        default=os.environ.get("SEA_REVIEWER", "architect"),
        help="who is accepting the links (default: SEA_REVIEWER, else architect)",
    )
    parser.add_argument(
        "--note",
        default=None,
        help="audit note recorded on every binding (default: bulk resolve (threshold X))",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="cap how many proposals are printed",
    )
    args = parser.parse_args(argv)

    # Clamped the way the route clamps a form field: an out-of-range threshold is
    # user input, and rejecting every candidate silently is the worse failure.
    threshold = min(1.0, max(0.0, args.threshold))
    note = args.note or f"bulk resolve (threshold {threshold:g})"

    store = RevisionStore(args.store_root).ensure()
    snapshot = store.load_working()

    print(f"store        {args.store_root}")
    print(f"mode         {'APPLY' if args.apply else 'DRY RUN (nothing will be written)'}")
    print(f"threshold    {threshold:g}")
    print(f"actor        {args.actor}")
    print(f"note         {note}")

    if not snapshot.graph.nodes:
        print("\nThe working set is empty — there is nothing to reconcile.")
        return 0

    _print_coverage("COVERAGE BEFORE", snapshot.graph)

    # The proposals the report shows are computed before the pass: `bulk_resolve`
    # runs its own proposal generation internally, and the two must agree, so both
    # come from `reference_candidates`.
    proposals = reference_candidates(snapshot.graph)
    _print_proposals(proposals, threshold, args.limit)
    proposals_by_id = {rc.assertion_id: rc for rc in proposals}

    # The pass always runs in memory, exactly as the route runs it. Whether the
    # result reaches disk is the only thing `--apply` decides.
    result = bulk_resolve(
        snapshot.graph,
        snapshot.log,
        min_score=threshold,
        actor=args.actor,
        note=note,
    )

    print(
        f"\nRESULT — {len(result.resolved)} of {result.considered} reference(s) "
        f"at threshold {threshold:g}"
    )
    print(f"  resolved        {len(result.resolved)}")
    print(f"  below threshold {len(result.below_threshold)}")
    print(f"  no candidate    {len(result.no_candidate)}")
    print(f"  unknown ids     {len(result.unknown_ids)}")

    _print_bindings(result, proposals_by_id, snapshot.graph, applied=args.apply)
    _print_declined(result)

    # The route persists only when something was resolved. A pass that declined
    # everything must not touch the file: a rewritten working set that is byte-wise
    # different for no change is noise in the revision history.
    written = False
    if args.apply and result.resolved:
        store.save_working(snapshot.graph, snapshot.log, snapshot.meta)
        written = True
        print(f"\nWROTE {len(result.resolved)} binding(s) to {store.root}")

    _print_coverage(
        "COVERAGE AFTER"
        + ("" if written else " (what the graph would look like if applied)"),
        snapshot.graph,
    )

    if written:
        print(
            f"\n{result.unrealized_requirements} requirement(s) still have no bound "
            "architectural answer."
        )
    elif args.apply:
        print("\nNothing cleared the threshold; the working set was not written.")
    else:
        print(
            f"\nDRY RUN — nothing was written to {store.root}. "
            "Re-run with --apply to bind."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
