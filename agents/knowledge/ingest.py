"""
Ingest: extraction output -> canonical knowledge graph.

This is the `???` between extraction and storage. It is the only place that
turns pass results into a graph, which means it is also the only place that can
guarantee the result is well-formed.

TWO RULES WORTH KNOWING
-----------------------
**Declared vs inferred nodes.** Nodes come from the explicit collections
(elements, entities, technology stacks). Triple endpoints that were never
declared become nodes of kind `Concept` — they are real referents but the
extraction never classified them, which is a quality signal worth keeping
queryable rather than hiding.

**Cross-graph predicates produce references, not nodes.** `traces_to_goal`,
`implements_requirement` and friends point at things that may live in the
*other* graph. Creating a local node for them would invent a node that has no
kind and no provenance in this graph, and would erase the distinction between
"resolved" and "referenced but not yet reconciled". So they become literal
values, and `unresolved_references()` finds them.
"""

from typing import Any, Dict, List, Optional, Tuple

from .model import (
    CROSS_GRAPH_PREDICATES,
    RUN_COMPLETE,
    RUN_FAILED,
    RUN_PARTIAL,
    RUN_UNKNOWN,
    SCOPE_BASELINE,
    SCOPE_INITIATIVE,
    SOURCE_EXTRACTION,
    ExtractionRun,
    KnowledgeGraph,
    PassRecord,
    Provenance,
    utc_now,
)

# Predicates where the object names the containing element (structural).
CONTAINMENT_PREDICATES = frozenset({"part_of", "belongs_to", "composed_of"})


def _run_id(document_ref: str, document_text: str, model_id: str) -> str:
    import hashlib
    raw = f"{document_ref}|{len(document_text)}|{model_id}|{utc_now()}"
    return "run_" + hashlib.sha1(raw.encode()).hexdigest()[:12]


def _document_hash(text: str) -> str:
    import hashlib
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


# ============================================================================
# Node collection
# ============================================================================

def _external_refs(record: Dict[str, Any]) -> List[str]:
    """Every stable external identifier a record carries.

    The two profiles name this differently and one is easy to miss: the
    architecture profile emits `external_references`, while the requirements
    profile carries the document's own key as `requirement_id`. Reading only the
    former is why requirement IDs stayed out of the graph even after the extractor
    began emitting them — the primary cause of ARC-G <-> REQ-G being unjoinable
    (TODO item 5, root cause 1). An identifier the graph does not record cannot be
    matched on, so reconciliation's strongest signal was unreachable.
    """
    refs: List[str] = []

    raw = record.get("external_references") or record.get("external_refs") or []
    for r in raw:
        value = (r.get("identifier") or r.get("id") or "") if isinstance(r, dict) else r
        if value:
            refs.append(str(value).strip())

    for key in ("requirement_id", "external_id"):
        value = record.get(key)
        if value:
            refs.append(str(value).strip())

    return [r for r in refs if r]


def _collect_declared_nodes(graph: KnowledgeGraph, output: Dict[str, Any]) -> Dict[str, str]:
    """Create nodes from explicit collections. Returns label -> node id."""
    by_label: Dict[str, str] = {}

    def declare(kind: str, label: str, external_refs: Optional[List[str]] = None) -> None:
        label = (label or "").strip()
        if not label:
            return
        nid = graph.add_node(kind, label, external_refs)
        by_label.setdefault(label.lower(), nid)

    # Architecture profile
    for e in output.get("elements", []) or []:
        if not isinstance(e, dict):
            continue
        kind = (e.get("element_type") or e.get("ontology_class") or "Concept").strip()
        declare(kind, e.get("name") or "", _external_refs(e))

    for t in output.get("technology_stacks", []) or []:
        if isinstance(t, dict):
            declare("TechnologyStack", t.get("name") or "")

    for s in output.get("architecture_styles", []) or []:
        if isinstance(s, dict):
            declare("ArchitectureStyle", s.get("name") or "")

    # Requirements profile
    for e in output.get("entities", []) or []:
        if not isinstance(e, dict):
            continue
        kind = (e.get("ontology_class") or e.get("entity_type") or "Concept").strip()
        declare(kind, e.get("name") or "", _external_refs(e))

        # Collect Initiative nodes from entity initiative_refs
        for init_ref in e.get("initiative_refs") or []:
            if isinstance(init_ref, str) and init_ref.strip():
                declare("Initiative", init_ref.strip(), external_refs=[init_ref.strip()])

    # Initiative scoping — the stable anchor across REQ-G and ARC-G
    for init in output.get("initiatives", []) or []:
        if not isinstance(init, dict):
            continue
        declare("Initiative", init.get("id") or init.get("name") or "",
                external_refs=[init.get("id")] if init.get("id") else None)

    return by_label


def _resolve(
    graph: KnowledgeGraph,
    label: str,
    by_label: Dict[str, str],
    fallback_kind: str = "Concept",
) -> str:
    """Resolve a label to a node id, creating a node if it was never declared.

    An undeclared referent becomes a `Concept` node rather than being dropped:
    the fact that extraction used a term without classifying it is information.
    """
    key = (label or "").strip().lower()
    if key in by_label:
        return by_label[key]
    nid = graph.add_node(fallback_kind, label or "")
    by_label[key] = nid
    return nid


# ============================================================================
# Ingest
# ============================================================================

def graph_from_extraction(
    output: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
    document_ref: str = "",
    document_text: str = "",
    pass_records: Optional[List[PassRecord]] = None,
    initiative_id: Optional[str] = None,  # The "Living System" scoping key
) -> Tuple[KnowledgeGraph, ExtractionRun]:
    """Build a canonical graph from one extraction result.

    If `initiative_id` is provided, all assertions are scoped as INITIATIVE_PROPOSAL.
    Otherwise, they are treated as SYSTEM_BASELINE or DOMAIN_TRUTH depending on context.
    """
    metadata = metadata or {}
    graph = KnowledgeGraph()

    model_id = str(metadata.get("model_id") or metadata.get("model") or "")
    run = ExtractionRun(
        id=_run_id(document_ref, document_text, model_id),
        document_ref=document_ref,
        document_hash=_document_hash(document_text) if document_text else "",
        document_chars=int(metadata.get("document_chars") or len(document_text)),
        model_id=model_id,
        started_at=metadata.get("started_at") or utc_now(),
        completed_at=utc_now(),
        chunk_count=int(metadata.get("chunks") or 1),
        passes=list(pass_records or _passes_from_metadata(metadata)),
    )
    run.completeness = run.compute_completeness()
    graph.runs[run.id] = run

    # Determine default scope for this run
    default_scope = SCOPE_INITIATIVE if initiative_id else SCOPE_BASELINE

    def prov(pass_name: str = "", chunk_label: str = "") -> Provenance:
        return Provenance(
            source_type=SOURCE_EXTRACTION,
            run_id=run.id,
            pass_name=pass_name,
            model_id=model_id,
            chunk_label=chunk_label,
            asserted_at=run.completed_at,
            derived_from=document_ref,
        )

    by_label = _collect_declared_nodes(graph, output)

    # ---- structural facts: description, classification, responsibilities ----
    for e in output.get("elements", []) or []:
        if not isinstance(e, dict):
            continue
        nid = _resolve(graph, e.get("name") or "", by_label,
                       (e.get("element_type") or "Concept"))
        p = prov("structure")
        scope = default_scope
        init_id = initiative_id or e.get("initiative_id")

        if e.get("description"):
            graph.add_assertion(nid, "description", value=e["description"],
                                confidence=1.0, provenance=p, scope=scope, initiative_id=init_id)
        for attr in ("element_type", "c4_level", "system_class", "origin",
                     "deployment_model"):
            if e.get(attr):
                graph.add_assertion(nid, attr, value=str(e[attr]),
                                    confidence=1.0, provenance=p, scope=scope, initiative_id=init_id)
        if e.get("parent"):
            pid = _resolve(graph, e["parent"], by_label)
            graph.add_assertion(nid, "part_of", obj=pid, confidence=1.0, provenance=p, scope=scope, initiative_id=init_id)
        for r in e.get("responsibilities") or []:
            text = str(r).strip()
            if text:
                graph.add_assertion(nid, "responsibility", value=text,
                                    confidence=1.0, provenance=p, scope=scope, initiative_id=init_id)

    # ---- technology / style usage ----
    for t in output.get("technology_stacks", []) or []:
        if not isinstance(t, dict):
            continue
        tid = _resolve(graph, t.get("name") or "", by_label, "TechnologyStack")
        p = prov("technology")
        if t.get("category"):
            graph.add_assertion(tid, "technology_category", value=str(t["category"]),
                                confidence=1.0, provenance=p)
        for user in t.get("used_by") or []:
            uid = _resolve(graph, user, by_label)
            graph.add_assertion(uid, "uses_technology", obj=tid, confidence=1.0, provenance=p)

    for s in output.get("architecture_styles", []) or []:
        if not isinstance(s, dict):
            continue
        sid = _resolve(graph, s.get("name") or "", by_label, "ArchitectureStyle")
        p = prov("technology")
        if s.get("style"):
            graph.add_assertion(sid, "style", value=str(s["style"]),
                                confidence=1.0, provenance=p)
        for adopter in s.get("adopted_by") or []:
            aid = _resolve(graph, adopter, by_label)
            graph.add_assertion(aid, "follows_style", obj=sid, confidence=1.0, provenance=p)

    # ---- triples ----
    for t in output.get("triples", []) or []:
        if not isinstance(t, dict):
            continue
        subject = (t.get("subject") or "").strip()
        predicate = (t.get("predicate") or "").strip()
        obj = (t.get("object") or "").strip()
        if not (subject and predicate):
            continue

        sid = _resolve(graph, subject, by_label)
        p = prov("triples")
        p.asserted_at = run.completed_at
        confidence = float(t.get("confidence") or 0.0)
        source_text = t.get("source_text") or ""
        ontology_class = t.get("ontology_class")

        if predicate in CROSS_GRAPH_PREDICATES:
            # A reference into another graph — keep it literal so reconciliation
            # can find it. Do NOT invent a local node.
            graph.add_assertion(sid, predicate, value=obj, confidence=confidence,
                                source_text=source_text, ontology_class=ontology_class,
                                provenance=p)
        else:
            oid = _resolve(graph, obj, by_label)
            graph.add_assertion(sid, predicate, obj=oid, confidence=confidence,
                                source_text=source_text, ontology_class=ontology_class,
                                provenance=p)

    # ---- traceability references (architecture profile) ----
    for r in output.get("references", []) or []:
        if not isinstance(r, dict):
            continue
        eid = _resolve(graph, r.get("element") or "", by_label)
        p = prov("traceability")
        graph.add_assertion(
            eid, (r.get("relationship") or "implements_requirement").strip(),
            value=(r.get("reference") or "").strip(),
            confidence=float(r.get("confidence") or 0.8),
            source_text=r.get("source_text") or "",
            provenance=p,
        )

    # ---- initiative scoping links ----
    # Link architecture elements and requirements to their authorising Initiative.
    # This provides a stable cross-graph join key that doesn't depend on document names.
    for e in output.get("elements", []) or []:
        if not isinstance(e, dict):
            continue
        nid = _resolve(graph, e.get("name") or "", by_label)
        init_id = e.get("initiative_id") or e.get("delivers_initiative")
        if init_id:
            iid = _resolve(graph, init_id, by_label, "Initiative")
            p = prov("structure")
            graph.add_assertion(nid, "delivers_initiative", obj=iid, confidence=1.0, provenance=p, scope=default_scope, initiative_id=initiative_id)

    for e in output.get("entities", []) or []:
        if not isinstance(e, dict):
            continue
        nid = _resolve(graph, e.get("name") or "", by_label)

        # Handle both single initiative_id and list of initiative_refs
        init_ids = []
        if e.get("initiative_id"):
            init_ids.append(e["initiative_id"])
        if e.get("authorised_by_initiative"):
            init_ids.append(e["authorised_by_initiative"])
        for init_ref in e.get("initiative_refs") or []:
            if isinstance(init_ref, str) and init_ref.strip():
                init_ids.append(init_ref.strip())

        for init_id in init_ids:
            iid = _resolve(graph, init_id, by_label, "Initiative")
            p = prov("triples")
            graph.add_assertion(nid, "authorised_by_initiative", obj=iid, confidence=1.0, provenance=p, scope=default_scope, initiative_id=initiative_id)

    return graph, run


def _passes_from_metadata(metadata: Dict[str, Any]) -> List[PassRecord]:
    """Reconstruct a coarse run record when per-pass detail is unavailable.

    Coarse is better than absent: recording that *something* failed is what stops
    a partial run being read as complete. The agent emits per-pass records where
    it can; this is the fallback for older saved output.
    """
    records: List[PassRecord] = []
    failed = int(metadata.get("failed_calls") or 0)
    empty = int(metadata.get("empty_calls") or 0)
    total = int(metadata.get("model_calls") or 0)
    ok = max(0, total - failed - empty)

    for _ in range(ok):
        records.append(PassRecord(pass_name="(unspecified)", chunk_label="",
                                  outcome="ok"))
    for _ in range(empty):
        records.append(PassRecord(pass_name="(unspecified)", chunk_label="",
                                  outcome="empty"))
    for _ in range(failed):
        records.append(PassRecord(pass_name="(unspecified)", chunk_label="",
                                  outcome="failed"))
    return records


def completeness_note(graph: KnowledgeGraph) -> str:
    """Human-readable completeness summary, for reports and the UI.

    Any consumer that presents extraction results should show this. A graph from
    a PARTIAL run is not evidence of absence.
    """
    if not graph.runs:
        return "no extraction runs recorded"
    worst = RUN_COMPLETE
    lines = []
    for run in graph.runs.values():
        if run.completeness == RUN_FAILED:
            worst = RUN_FAILED
        elif run.completeness == RUN_PARTIAL and worst in (RUN_COMPLETE, RUN_UNKNOWN):
            worst = RUN_PARTIAL
        elif run.completeness == RUN_UNKNOWN and worst == RUN_COMPLETE:
            worst = RUN_UNKNOWN
        failed = run.failed_passes
        detail = f"{len(run.passes)} pass(es)"
        if failed:
            detail += f", {len(failed)} failed: " + ", ".join(
                f"{p.pass_name}@{p.chunk_label}" for p in failed[:4])
        lines.append(f"  {run.id}: {run.completeness} ({detail})")

    header = {
        RUN_COMPLETE: "Extraction complete — absence of a fact may be meaningful.",
        RUN_PARTIAL: "Extraction PARTIAL — some content was not extracted. "
                     "Absence of a fact is NOT evidence of its absence.",
        RUN_FAILED: "Extraction FAILED — the graph is not usable for auditing.",
        RUN_UNKNOWN: "Extraction completeness UNKNOWN — no pass-level detail was "
                     "recorded. Absence of a fact is NOT evidence of its absence.",
    }[worst]
    return header + "\n" + "\n".join(lines)


# ============================================================================
# Merge — graph -> graph
# ============================================================================

def merge_graphs(base: KnowledgeGraph, incoming: KnowledgeGraph) -> KnowledgeGraph:
    """Fold a freshly extracted graph into an existing one.

    Re-extraction must NOT replace the graph. If it did, every human decision
    made in the review gate would be destroyed by the next run — the "sleeper"
    problem in `docs/architecture-review.md` §3.3, and the reason `Provenance`
    and `VerificationStatus` exist at all.

    Precedence is not reimplemented here: `add_assertion` already encodes the
    fold rule (a human assertion outranks an agent re-observation, and
    re-observing the same fact raises confidence instead of duplicating). Merge
    is therefore just routing every incoming fact through the one place that
    knows the rule.

    Returns a NEW graph. The caller diffs it against `base` to get
    diff-and-review rather than blind replacement.
    """
    import copy

    merged = copy.deepcopy(base)

    for node in incoming.nodes.values():
        merged.add_node(node.kind, node.label, node.external_refs)

    # Runs accumulate: each extraction stays attributable after a later one lands.
    for run in incoming.runs.values():
        merged.runs[run.id] = run

    for a in incoming.assertions.values():
        folded = merged.add_assertion(
            a.subject,
            a.predicate,
            obj=a.object,
            value=a.value,
            confidence=a.confidence,
            source_text=a.source_text,
            ontology_class=a.ontology_class,
            provenance=a.provenance,
            status=a.status,
            scope=a.scope,
            initiative_id=a.initiative_id,
        )
        # Lineage fields are not part of the fold rule; carry them explicitly so
        # a snapshot that is itself the product of a merge round-trips faithfully.
        if a.superseded_by and not folded.superseded_by:
            folded.superseded_by = a.superseded_by

    return merged
