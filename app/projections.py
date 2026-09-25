"""
Graph projection: canonical graph -> flat, UI-ready records.

TWO KINDS OF "PROJECTION" — DO NOT FUSE THEM
--------------------------------------------
This package deliberately separates two things that are easy to conflate, and
were conflated until they were split:

1. **Graph projection** (this module). Notation-agnostic. Turns the canonical
   graph into rows, counters, edges and deltas that any consumer can render. It
   knows about assertions, confidence, provenance and filters. It knows nothing
   about architecture notation.

       project_review_rows, project_gap_report, project_delta,
       project_decisions, project_reconciliation, project_dashboard

2. **Architecture viewpoints** (`app.viewpoints`). Notation-specific. Decides
   what a recognised view shows — for the merged knowledge map, which node kinds
   each lens draws and what counts as a relationship versus an element's detail.

       app.viewpoints.merged.merged_view

They have different reasons to change, so they live in different modules: a new
assertion shape or confidence band changes (1); a change in what the map draws, or
a new notation, changes (2). A viewpoint *composes* this module's primitives
(`node_records`, `edge_records`, `literal_facts`) rather than reimplementing them,
so the dependency runs one way: viewpoints -> projections.

WHY THERE IS A PROJECTION LAYER AT ALL
--------------------------------------
`docs/architecture-review.md` §2.4: depiction is a view concern, not a storage
concern. Every assertion carries metadata — `confidence`, `source_text`,
provenance — so a naive renderer shows unlabelled edges. Storage stays optimised
for query and inference; views are materialised on top. Every fact read here is
still an assertion underneath.

WHY IT IS SEPARATE FROM THE ROUTES
----------------------------------
`docs/architecture-review.md` §3.9: the view projection layer is the first
blocking gap, because "nothing to look at". Keeping projections pure functions
of `(graph, log, filters)` means they are testable without a browser or a server,
and the same projections can back a JSON API, a CLI table or a PDF export without
the UI logic being duplicated three ways.

THE ONE RULE
------------
Projections never mutate. A view that changes the graph makes the review gate
unauditable — you could no longer tell a decision from a rendering.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from core.knowledge import completeness_note, review_progress
from core.knowledge.model import (
    CROSS_GRAPH_PREDICATES,
    STATUS_CORRECTED,
    STATUS_DISPUTED,
    STATUS_RETIRED,
    STATUS_SUPERSEDED,
    STATUS_UNVERIFIED,
    STATUS_VERIFIED,
)
from core.knowledge.realization import realization_report
from core.knowledge.quality import quality_report
from core.knowledge.reconcile import DEFAULT_MATCH_THRESHOLD, reference_candidates
from core.knowledge.review import ReviewLog

# Confidence below this is what the review gate wants a human to look at first.
# 0.7 is a product decision, not a model one: it is the point below which the
# extractor is visibly unsure, so queueing by it puts real doubt at the top.
LOW_CONFIDENCE = 0.7

STATUS_ORDER = [
    STATUS_UNVERIFIED,
    STATUS_DISPUTED,
    STATUS_CORRECTED,
    STATUS_VERIFIED,
    STATUS_SUPERSEDED,
]


# ============================================================================
# Filters
# ============================================================================


@dataclass
class ReviewFilters:
    """The review queue's query. Parsed from request args, usable without Flask."""

    status: str = ""
    scope: str = ""
    kind: str = ""
    predicate: str = ""
    q: str = ""
    only: str = ""
    min_confidence: Optional[float] = None
    max_confidence: Optional[float] = None
    sort: str = "confidence"

    # NEGATIVE FILTERS. The queue had every filter except a way to say "not this":
    # a reviewer facing 200 invented `connects_to` triples could isolate them but
    # not exclude them, so working through the rest of the graph meant reading past
    # the noise on every page. Exclusion is the cheap answer to that, and it is
    # deliberately separate from `retire` — hiding a fact from a queue does not
    # remove it from the graph, and the two should not be confused by the UI either.
    exclude_predicate: str = ""
    exclude_kind: str = ""
    # Scoping to one extraction run. Related to YB-018 (review batches), which is
    # the fuller version: this only says WHICH run a fact came from.
    run: str = ""

    SORTS = {
        "confidence": "Lowest confidence first",
        "subject": "Subject",
        "predicate": "Predicate",
        "recent": "Recently changed",
    }

    @classmethod
    def from_args(cls, args: Any) -> "ReviewFilters":
        def num(name: str) -> Optional[float]:
            raw = (args.get(name) or "").strip()
            if not raw:
                return None
            try:
                return float(raw)
            except ValueError:
                return None

        return cls(
            status=(args.get("status") or "").strip(),
            scope=(args.get("scope") or "").strip(),
            kind=(args.get("kind") or "").strip(),
            predicate=(args.get("predicate") or "").strip(),
            q=(args.get("q") or "").strip(),
            only=(args.get("only") or "").strip(),
            min_confidence=num("min_confidence"),
            max_confidence=num("max_confidence"),
            sort=(args.get("sort") or "confidence").strip(),
            exclude_predicate=(args.get("exclude_predicate") or "").strip(),
            exclude_kind=(args.get("exclude_kind") or "").strip(),
            run=(args.get("run") or "").strip(),
        )

    @property
    def has_exclusions(self) -> bool:
        return bool(self.exclude_predicate or self.exclude_kind)

    @property
    def is_default(self) -> bool:
        return not any(
            [
                self.status,
                self.scope,
                self.kind,
                self.predicate,
                self.q,
                self.only,
                self.min_confidence,
                self.max_confidence,
                self.exclude_predicate,
                self.exclude_kind,
                self.run,
            ]
        )

    def to_query(self, **overrides: Any) -> str:
        """Rebuild a query string, so filter links are not hand-written in HTML."""
        values = {
            "status": self.status,
            "scope": self.scope,
            "kind": self.kind,
            "predicate": self.predicate,
            "q": self.q,
            "only": self.only,
            "min_confidence": "" if self.min_confidence is None else self.min_confidence,
            "max_confidence": "" if self.max_confidence is None else self.max_confidence,
            "sort": self.sort,
            "exclude_predicate": self.exclude_predicate,
            "exclude_kind": self.exclude_kind,
            "run": self.run,
        }
        values.update(overrides)
        parts = [f"{k}={v}" for k, v in values.items() if v not in ("", None)]
        return "&".join(parts)


# ============================================================================
# Assertion-level projection
# ============================================================================


def confidence_band(confidence: float) -> str:
    if confidence >= 0.85:
        return "high"
    if confidence >= LOW_CONFIDENCE:
        return "medium"
    return "low"


def _status_slug(status: str) -> str:
    return status.strip().lower()


def project_assertion(
    graph, log: ReviewLog, assertion_id: str, include_superseded: bool = True
) -> Dict[str, Any]:
    """One assertion, fully expanded — the detail panel behind a review row."""
    a = graph.assertions.get(assertion_id)
    if a is None:
        return {}
    node = graph.nodes.get(a.subject)
    obj_node = graph.nodes.get(a.object) if a.object else None

    target_kind = (
        "reference"
        if a.predicate in CROSS_GRAPH_PREDICATES
        else ("node" if a.object else "literal")
    )

    decisions = log.for_assertion(assertion_id)
    return {
        "id": a.id,
        "subject_id": a.subject,
        "subject_kind": node.kind if node else "?",
        "subject_label": node.label if node else a.subject,
        "predicate": a.predicate,
        "target": a.target,
        "target_kind": target_kind,
        "target_label": obj_node.label if obj_node else a.target,
        "object": a.object,
        "value": a.value,
        "confidence": round(a.confidence, 3),
        "confidence_pct": int(round(a.confidence * 100)),
        "confidence_band": confidence_band(a.confidence),
        "status": a.status,
        "status_slug": _status_slug(a.status),
        "scope": a.scope,
        "initiative_id": a.initiative_id or "",
        "ontology_class": a.ontology_class or "",
        "source_text": a.source_text,
        "source_type": a.provenance.source_type,
        "is_human": a.is_human,
        "asserted_by": a.provenance.asserted_by,
        "asserted_at": a.provenance.asserted_at,
        "run_id": a.provenance.run_id,
        "pass_name": a.provenance.pass_name,
        "model_id": a.provenance.model_id,
        "correction_note": a.provenance.correction_note,
        "derived_from": a.provenance.derived_from,
        "superseded_by": a.superseded_by,
        "is_active": a.is_active,
        "is_retired": a.status == STATUS_RETIRED,
        "needs_review": a.is_active and a.status in (STATUS_UNVERIFIED, STATUS_DISPUTED),
        "is_low_confidence": a.is_active and a.confidence < LOW_CONFIDENCE,
        "is_unresolved": a in graph.unresolved_references(),
        "decision_count": len(decisions),
        "last_decision": decisions[-1].label if decisions else "",
        "decisions": [
            d.to_dict() | {"label": d.label, "changed_fields": d.changed_fields} for d in decisions
        ],
    }


def _matches_filters(a, graph, f: ReviewFilters, unresolved_ids: set, dangling_ids: set) -> bool:
    if f.status and a.status != f.status.upper():
        return False
    if f.scope and a.scope != f.scope.upper():
        return False
    if f.predicate and a.predicate != f.predicate:
        return False
    subject = graph.nodes.get(a.subject)
    if f.kind and (subject.kind if subject else "?") != f.kind:
        return False
    if f.min_confidence is not None and a.confidence < f.min_confidence:
        return False
    if f.max_confidence is not None and a.confidence > f.max_confidence:
        return False

    if f.only == "pending" and a.status not in (STATUS_UNVERIFIED, STATUS_DISPUTED):
        return False
    if f.only == "human" and not a.is_human:
        return False
    if f.only == "low_confidence" and a.confidence >= LOW_CONFIDENCE:
        return False
    if f.only == "unresolved" and a.id not in unresolved_ids:
        return False
    if f.only == "dangling" and a.id not in dangling_ids:
        return False
    if f.only == "superseded" and a.status != STATUS_SUPERSEDED:
        return False
    if f.only == "retired" and a.status != STATUS_RETIRED:
        return False

    # The exclusions come after the positives, so a chip and an exclusion that
    # contradict each other yield nothing rather than silently preferring one.
    if f.exclude_predicate and a.predicate == f.exclude_predicate:
        return False
    if f.exclude_kind and (subject.kind if subject else "?") == f.exclude_kind:
        return False
    if f.run and a.provenance.run_id != f.run:
        return False

    if f.q:
        needle = f.q.lower()
        haystack = " ".join(
            [
                subject.label if subject else "",
                a.predicate,
                a.target,
                a.source_text,
                a.ontology_class or "",
                graph.nodes[a.object].label if a.object in graph.nodes else "",
            ]
        ).lower()
        if needle not in haystack:
            return False
    return True


def project_review_rows(
    graph,
    log: ReviewLog,
    filters: Optional[ReviewFilters] = None,
    include_superseded: bool = False,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """The review queue: assertions as flat rows, filtered and ordered.

    Ordering defaults to *lowest confidence first* rather than alphabetical,
    because the reviewer's scarce resource is attention and the extractor has
    already told us where it is least sure.
    """
    f = filters or ReviewFilters()
    unresolved_ids = {a.id for a in graph.unresolved_references()}
    dangling_ids = {a.id for a in graph.dangling_assertions()}

    # A retired assertion is inactive, so asking for it has to widen the pool —
    # otherwise the chip is a link to an empty page.
    include_inactive = include_superseded or f.only == "retired" or f.status == STATUS_RETIRED
    pool = graph.assertions.values() if include_inactive else graph.active()
    selected = [a for a in pool if _matches_filters(a, graph, f, unresolved_ids, dangling_ids)]

    if f.sort == "confidence":
        selected.sort(key=lambda a: (a.confidence, a.subject, a.predicate))
    elif f.sort == "subject":
        selected.sort(
            key=lambda a: (
                (graph.nodes[a.subject].label.lower() if a.subject in graph.nodes else a.subject),
                a.predicate,
                a.target,
            )
        )
    elif f.sort == "predicate":
        selected.sort(key=lambda a: (a.predicate, a.subject, a.target))
    elif f.sort == "recent":
        selected.sort(key=lambda a: a.provenance.asserted_at, reverse=True)

    if limit is not None:
        selected = selected[:limit]

    rows = []
    for a in selected:
        node = graph.nodes.get(a.subject)
        obj_node = graph.nodes.get(a.object) if a.object else None
        decisions = log.for_assertion(a.id)
        rows.append(
            {
                "id": a.id,
                "subject_id": a.subject,
                "subject_kind": node.kind if node else "?",
                "subject_label": node.label if node else a.subject,
                "predicate": a.predicate,
                "target": a.target,
                "target_kind": (
                    "reference"
                    if a.predicate in CROSS_GRAPH_PREDICATES
                    else ("node" if a.object else "literal")
                ),
                "target_label": obj_node.label if obj_node else a.target,
                "confidence": round(a.confidence, 2),
                "confidence_pct": int(round(a.confidence * 100)),
                "confidence_band": confidence_band(a.confidence),
                "status": a.status,
                "status_slug": _status_slug(a.status),
                "scope": (
                    "Baseline"
                    if a.scope == "SYSTEM_BASELINE"
                    else ("Domain" if a.scope == "DOMAIN_TRUTH" else "Initiative")
                ),
                "scope_raw": a.scope,
                "initiative_id": a.initiative_id or "",
                "ontology_class": a.ontology_class or "",
                "source_type": a.provenance.source_type,
                "is_human": a.is_human,
                "source_text": a.source_text,
                "needs_review": a.is_active and a.status in (STATUS_UNVERIFIED, STATUS_DISPUTED),
                "is_low_confidence": a.is_active and a.confidence < LOW_CONFIDENCE,
                "is_unresolved": a.id in unresolved_ids,
                # Active vs removed is what decides whether the row offers Verify
                # and Remove or Reopen, so it is projected rather than left to the
                # template to re-derive from three fields.
                "is_active": a.is_active,
                "is_retired": a.status == STATUS_RETIRED,
                "decision_count": len(decisions),
                "last_decision": decisions[-1].label if decisions else "",
            }
        )
    return rows


def project_review_summary(graph, log: ReviewLog) -> Dict[str, Any]:
    """Counts that drive the queue's header and its one-click filter chips."""
    progress = review_progress(graph)
    by_status: Dict[str, int] = {}
    by_band: Dict[str, int] = {"low": 0, "medium": 0, "high": 0}
    for a in graph.active():
        by_status[a.status] = by_status.get(a.status, 0) + 1
        by_band[confidence_band(a.confidence)] += 1

    kinds: Dict[str, int] = {}
    for a in graph.active():
        node = graph.nodes.get(a.subject)
        k = node.kind if node else "?"
        kinds[k] = kinds.get(k, 0) + 1

    # Counted over ACTIVE assertions, so the numbers match what the exclusion
    # filters can actually hide. A predicate the extractor invented is exactly what
    # someone wants excluded, and it is the predicate with the highest count — which
    # is the signal that says so.
    predicates: Dict[str, int] = {}
    for a in graph.active():
        predicates[a.predicate] = predicates.get(a.predicate, 0) + 1

    runs: Dict[str, int] = {}
    for a in graph.active():
        if a.provenance.run_id:
            runs[a.provenance.run_id] = runs.get(a.provenance.run_id, 0) + 1

    return {
        "progress": progress.to_dict(),
        "by_status": by_status,
        "by_band": by_band,
        "by_kind": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
        "predicates": sorted(graph.predicates()),
        # Ranked, and with counts: "which predicate is producing the noise?" is the
        # question that leads to an exclusion, and a bare sorted list cannot answer
        # it.
        "by_predicate": dict(sorted(predicates.items(), key=lambda kv: (-kv[1], kv[0]))),
        "runs": dict(sorted(runs.items())),
        "unresolved": len(graph.unresolved_references()),
        "dangling": len(graph.dangling_assertions()),
        "low_confidence": by_band["low"],
        "decisions": len(log.entries),
        "needs_review": progress.outstanding,
    }


# ============================================================================
# Graph-level projections
# ============================================================================


def project_node_index(graph, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    """Elements with their fact counts — the 'what is in this graph' view."""
    counts: Dict[str, int] = {}
    for a in graph.active():
        counts[a.subject] = counts.get(a.subject, 0) + 1

    rows = []
    for n in graph.nodes.values():
        if kind and n.kind != kind:
            continue
        rows.append(
            {
                "id": n.id,
                "kind": n.kind,
                "label": n.label,
                "facts": counts.get(n.id, 0),
                "external_refs": list(n.external_refs),
                # Typed identifiers, so the view can show WHICH system holds an
                # identifier and how far its authority reaches. A CMDB CI and a
                # heading in a markdown file look identical as bare strings, and
                # that difference is the whole reason references are typed.
                "external_references": [
                    {
                        "identifier": r.identifier,
                        "system": r.system,
                        "reference_type": r.reference_type,
                        "scope": r.scope,
                        "is_join_key": r.is_join_key,
                    }
                    for r in (n.external_references or [])
                ],
            }
        )
    return sorted(rows, key=lambda r: (-r["facts"], r["label"].lower()))


# ============================================================================
# Primitives every viewpoint composes
# ============================================================================
#
# These are notation-agnostic: they know an assertion when they see one and
# nothing about how a view should be drawn. Keeping them here rather than inside
# a viewpoint is what stops each new notation from reimplementing assertion
# flattening — and reimplementing it slightly differently.


def node_records(graph, kinds: Optional[Iterable[str]] = None) -> List[Dict[str, Any]]:
    """Nodes as flat records, optionally restricted to a set of kinds."""
    wanted = None if kinds is None else set(kinds)
    return [
        {
            "id": n.id,
            "label": n.label,
            "kind": n.kind,
            "external_refs": list(n.external_refs),
        }
        for n in graph.nodes.values()
        if wanted is None or n.kind in wanted
    ]


def edge_records(graph, node_ids: Iterable[str]) -> List[Dict[str, Any]]:
    """Assertions between two selected nodes, flattened and de-duplicated.

    Flattening is the whole reason this layer exists: assertions are reified, so a
    naive renderer would draw unlabelled edges. Parallel edges collapse — two runs
    asserting the same link is one relationship, not two.
    """
    wanted = set(node_ids)
    seen = set()
    edges: List[Dict[str, Any]] = []
    for a in graph.active():
        if not a.object or a.subject not in wanted or a.object not in wanted:
            continue
        key = (a.subject, a.object, a.predicate)
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            {
                "id": f"{a.subject}->{a.object}:{a.predicate}",
                "source": a.subject,
                "target": a.object,
                "predicate": a.predicate,
                "label": a.predicate.replace("_", " "),
                "confidence": round(a.confidence, 2),
            }
        )
    return edges


def literal_facts(graph, node_ids: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """Facts whose object is a value rather than a node, keyed by subject.

    These are properties of an element — a description, a technology, a system
    class — not relationships. No viewpoint should ever draw one as an edge, so
    they are separated from `edge_records` structurally rather than by convention.
    """
    wanted = set(node_ids)
    out: Dict[str, Dict[str, Any]] = {}
    for a in graph.active():
        if a.subject in wanted and a.object is None:
            out.setdefault(a.subject, {})[a.predicate] = a.value
    return out


def project_gap_report(graph) -> Dict[str, Any]:
    """The structural half of the audit: what the graph cannot yet account for.

    Deliberately explicit about *why* a gap may not be a real gap. A graph from a
    PARTIAL or UNKNOWN run cannot support absence claims, and saying so on the
    report is the difference between a useful finding and a confident falsehood.

    `realization` is the other half and the point of the report: unresolved
    references are only the architecture->requirement direction. Requirements with
    no answer at all — including the ones nothing ever cited — are a different
    finding with a different fix, and a reader who sees only one number will
    assume it covers both.
    """
    progress = review_progress(graph)
    unresolved = graph.unresolved_references()
    dangling = graph.dangling_assertions()
    realization = realization_report(graph)

    by_predicate: Dict[str, int] = {}
    for a in unresolved:
        by_predicate[a.predicate] = by_predicate.get(a.predicate, 0) + 1

    runs = []
    for run in graph.runs.values():
        runs.append(
            {
                "id": run.id,
                "document": run.document_ref,
                "completeness": run.completeness,
                "passes": len(run.passes),
                "failed": len(run.failed_passes),
                "empty": sum(1 for p in run.passes if p.outcome == "empty"),
                "model_id": run.model_id,
                "completed_at": run.completed_at,
                "chars": run.document_chars,
            }
        )

    worst = "COMPLETE"
    if any(r["completeness"] == "FAILED" for r in runs):
        worst = "FAILED"
    elif any(r["completeness"] == "PARTIAL" for r in runs):
        worst = "PARTIAL"
    elif not runs or any(r["completeness"] == "UNKNOWN" for r in runs):
        worst = "UNKNOWN"

    auditability = {
        "COMPLETE": "Absence of a fact may be meaningful.",
        "PARTIAL": "Absence of a fact is NOT evidence of its absence.",
        "FAILED": "The graph is not usable for auditing.",
        "UNKNOWN": "Absence of a fact is NOT evidence of its absence.",
    }[worst]

    return {
        "unresolved_count": len(unresolved),
        "dangling_count": len(dangling),
        "realization": realization,
        # Flat counters so a stat tile does not have to reach two levels deep.
        "unrealized_count": realization["summary"]["unrealized"],
        "bound_edges": realization["summary"]["bound_edges"],
        "unmet_obligation_count": len(realization["obligations"]),
        "completeness": worst,
        "completeness_note": completeness_note(graph),
        "auditability": auditability,
        "is_auditable": progress.is_auditable and worst == "COMPLETE",
        "progress": progress.to_dict(),
        "unresolved_by_predicate": dict(sorted(by_predicate.items(), key=lambda kv: -kv[1])),
        "references": [
            {
                "id": a.id,
                "source_id": a.subject,
                "source": graph.nodes[a.subject].label if a.subject in graph.nodes else a.subject,
                "predicate": a.predicate,
                "target": a.target,
                "confidence": round(a.confidence, 2),
            }
            for a in unresolved
        ],
        "dangling": [
            {
                "id": a.id,
                "subject": a.subject,
                "predicate": a.predicate,
                "target": a.target,
            }
            for a in dangling
        ],
        "runs": runs,
        "elements_without_facts": [
            {"id": n.id, "kind": n.kind, "label": n.label}
            for n in graph.nodes.values()
            if not any(a.subject == n.id for a in graph.active())
        ],
    }


def project_delta(delta, old_graph, new_graph) -> Dict[str, Any]:
    """A GraphDelta rendered as changes a human can read."""
    lookup = {**old_graph.nodes, **new_graph.nodes}

    def label(node_id: str) -> str:
        node = lookup.get(node_id)
        return node.label if node else node_id

    def fact(a) -> Dict[str, Any]:
        return {
            "id": a.id,
            "subject": label(a.subject),
            "predicate": a.predicate,
            "target": label(a.object) if a.object else a.target,
            "confidence": round(a.confidence, 2),
            "status": a.status,
            "status_slug": _status_slug(a.status),
            "is_human": a.is_human,
        }

    changed = []
    for old_a, new_a in delta.changed_assertions:
        fields = [
            f
            for f in ("status", "confidence", "scope", "superseded_by")
            if getattr(old_a, f) != getattr(new_a, f)
        ]
        changed.append(
            {
                "id": new_a.id,
                "subject": label(new_a.subject),
                "predicate": new_a.predicate,
                "target": label(new_a.object) if new_a.object else new_a.target,
                "fields": fields,
                "before": {f: getattr(old_a, f) for f in fields},
                "after": {f: getattr(new_a, f) for f in fields},
            }
        )

    return {
        "added_nodes": [{"id": n.id, "kind": n.kind, "label": n.label} for n in delta.added_nodes],
        "removed_nodes": [
            {"id": n.id, "kind": n.kind, "label": n.label} for n in delta.removed_nodes
        ],
        "added_assertions": [fact(a) for a in delta.added_assertions],
        "removed_assertions": [fact(a) for a in delta.removed_assertions],
        "changed_assertions": changed,
        "totals": {
            "nodes_added": len(delta.added_nodes),
            "nodes_removed": len(delta.removed_nodes),
            "facts_added": len(delta.added_assertions),
            "facts_removed": len(delta.removed_assertions),
            "facts_changed": len(delta.changed_assertions),
        },
        "is_empty": not any(
            [
                delta.added_nodes,
                delta.removed_nodes,
                delta.added_assertions,
                delta.removed_assertions,
                delta.changed_assertions,
            ]
        ),
    }


# Badge class per decision action. Kept here rather than as a chain of ternaries
# in the templates, where adding an action silently mis-renders the existing ones.
_ACTION_BADGE = {
    "verify": "verified",
    "correct": "corrected",
    "dispute": "disputed",
    "reset": "unverified",
    "promote_baseline": "baseline",
    "resolve": "resolved",
    "retire": "removed",
}


def project_decisions(graph, log: ReviewLog, limit: Optional[int] = 200) -> List[Dict[str, Any]]:
    """The audit trail, newest first — 'why does the graph look like this?'"""
    entries = list(reversed(log.entries))
    if limit:
        entries = entries[:limit]

    rows = []
    for d in entries:
        a = graph.assertions.get(d.assertion_id) or graph.assertions.get(d.replacement_id)
        node = graph.nodes.get(a.subject) if a else None
        rows.append(
            {
                "action": d.action,
                "label": d.label,
                "badge": _ACTION_BADGE.get(d.action, "superseded"),
                "actor": d.actor or "(unattributed)",
                "at": d.at,
                "note": d.note,
                "assertion_id": d.assertion_id,
                "replacement_id": d.replacement_id,
                "changed_fields": d.changed_fields,
                "before": d.before,
                "after": d.after,
                "promoted": len(d.promoted_ids),
                "subject": node.label if node else "",
                "predicate": a.predicate if a else "",
                "target": a.target if a else "",
                "subject_kind": node.kind if node else "",
            }
        )
    return rows


# ============================================================================
# Reconciliation
# ============================================================================

# How many target options one row offers in its manual select: enough to cover the
# expected kind plus the notable near misses, small enough that a graph with
# hundreds of references still renders a page and not a megabyte.
MAX_ROW_OPTIONS = 12


def project_reconciliation(graph, threshold: float = DEFAULT_MATCH_THRESHOLD) -> Dict[str, Any]:
    """Unresolved references with their proposed targets, ready for bulk accept.

    The projection reports what it *declined* to do as prominently as what it did.
    `below_threshold` and `no_candidate` are the honest part; `mislabel_suspected`
    is the useful part — a strong match in the wrong kind usually means the
    predicate is wrong, not that the target is missing.
    """
    references = reference_candidates(graph)
    rows: List[Dict[str, Any]] = []

    for rc in references:
        row = rc.to_dict(threshold)

        # One merged, de-duplicated option list for the row's manual select, so a
        # reviewer can accept a near miss deliberately instead of hunting for it.
        options: List[Dict[str, Any]] = []
        seen = set()
        for candidate in list(rc.candidates) + list(rc.near_misses):
            if candidate.node_id in seen:
                continue
            seen.add(candidate.node_id)
            options.append(candidate.to_dict())
        options.sort(key=lambda c: (-c["score"], c["label"].lower()))
        row["options"] = options[:MAX_ROW_OPTIONS]
        row["hidden_options"] = max(0, len(options) - len(row["options"]))
        rows.append(row)

    by_predicate: Dict[str, int] = {}
    for row in rows:
        by_predicate[row["predicate"]] = by_predicate.get(row["predicate"], 0) + 1

    def count(status: str) -> int:
        return sum(1 for r in rows if r["status"] == status)

    summary = {
        "total": len(rows),
        "resolvable": count("resolvable"),
        "below_threshold": count("below_threshold"),
        "no_candidate": count("no_candidate"),
        "mislabel_suspected": sum(1 for r in rows if r["mislabel_suspected"]),
        "by_predicate": dict(sorted(by_predicate.items(), key=lambda kv: -kv[1])),
        "expected_kinds": sorted({k for r in rows for k in r["expected_kinds"]}),
        "threshold": threshold,
        "average_best_score": (
            round(sum(r["best_score"] for r in rows) / len(rows), 3) if rows else 0.0
        ),
    }
    return {"rows": rows, "summary": summary, "threshold": threshold}


def project_realization(graph) -> Dict[str, Any]:
    """Realization coverage, shaped for the reconcile page's header strip.

    A thin wrapper rather than a second implementation: the report is the
    knowledge-layer query, and this layer only decides what a page needs to show.
    Splitting them any further would let the page's numbers drift from the gap
    report's.
    """
    report = realization_report(graph)
    summary = report["summary"]
    return {
        # The whole report rather than a subset: `/api/realization` exists to be
        # read by something that is not this page, and a caller should not have to
        # know which keys the view happens to use today.
        "report": report,
        "summary": summary,
        "coverage_pct": (
            int(round(100 * summary["realized"] / summary["requirements"]))
            if summary["requirements"]
            else 0
        ),
        # Worst-first, so a page leads with what is entirely unanswered rather than
        # with requirements that already have an answer plus a stray reference.
        "unrealized": report["unrealized"],
        "obligations": report["obligations"],
        "requirements": report["requirements"],
        "claims": report["claims"],
    }


def project_quality_report(graph) -> Dict[str, Any]:
    """The quality-attribute census, shaped for a page.

    Attribute-shaped rather than requirement-shaped: this is the other face of the
    audit behind `project_gap_report`. Kept a thin wrapper for the same reason as
    `project_realization` — the census is a knowledge-layer query, and a page that
    recomputed it would eventually disagree with the API that serves it.
    """
    report = quality_report(graph)
    summary = report["summary"]
    return {
        "report": report,
        "summary": summary,
        "characteristics": report["characteristics"],
        "attributes": report["attributes"],
        # The direction the gap report cannot express, and the one a reader is
        # least likely to have thought of: architecture delivering a quality
        # nobody asked for.
        "unasked": report["unasked"],
        "architecture_gaps": report["architecture_gaps"],
        "unresolved": report["unresolved"],
        "completeness_note": report["completeness_note"],
        "state_labels": report["state_labels"],
        "coverage_labels": report["coverage_labels"],
    }


def project_dashboard(
    graph,
    log: ReviewLog,
    revisions: Sequence[Any] = (),
    working_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Everything the landing page needs, in one projection."""
    progress = review_progress(graph)
    stats = graph.stats()
    gaps = project_gap_report(graph)

    top_predicates: Dict[str, int] = {}
    for a in graph.active():
        top_predicates[a.predicate] = top_predicates.get(a.predicate, 0) + 1

    return {
        "stats": stats,
        "progress": progress.to_dict(),
        "completeness": gaps["completeness"],
        "auditability": gaps["auditability"],
        "is_auditable": gaps["is_auditable"],
        "unresolved": gaps["unresolved_count"],
        "dangling": gaps["dangling_count"],
        "top_predicates": dict(sorted(top_predicates.items(), key=lambda kv: -kv[1])[:10]),
        "top_kinds": stats["node_kinds"],
        "documents": sorted({r.document_ref for r in graph.runs.values() if r.document_ref}),
        "runs": len(graph.runs),
        "revisions": len(revisions),
        "baselines": [r.label for r in revisions if getattr(r, "is_baseline", False)],
        "latest_revision": revisions[0] if revisions else None,
        "working_meta": working_meta or {},
        "recent_decisions": project_decisions(graph, log, limit=8),
        "low_confidence": sum(1 for a in graph.active() if a.confidence < LOW_CONFIDENCE),
        "empty": not graph.nodes and not graph.assertions,
    }
