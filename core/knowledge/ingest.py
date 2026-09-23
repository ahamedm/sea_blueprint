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
    MANAGED_REFERENCE_TYPES,
    RUN_COMPLETE,
    RUN_FAILED,
    RUN_PARTIAL,
    RUN_UNKNOWN,
    SCOPE_BASELINE,
    SCOPE_DOCUMENT,
    SCOPE_ENTERPRISE,
    SCOPE_INITIATIVE,
    SOURCE_EXTRACTION,
    ExternalReference,
    ExtractionRun,
    KnowledgeGraph,
    PassRecord,
    Provenance,
    document_reference,
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
    """Every stable external identifier a record carries, as bare strings.

    Kept for callers that only need the display form. Prefer
    `_typed_external_refs`, which preserves the kind — that is what decides
    whether an identifier may be matched on.
    """
    return [ref.identifier for ref in _typed_external_refs(record)]


def _typed_external_refs(
    record: Dict[str, Any], document_ref: str = ""
) -> List[ExternalReference]:
    """Identifiers from a record, WITH their kind and scope.

    The two profiles name this differently and one is easy to miss: the
    architecture profile emits `external_references`, while the requirements
    profile carries the document's own key as `requirement_id`. Reading only the
    former is why requirement IDs stayed out of the graph even after the extractor
    began emitting them (TODO item 5, root cause 1).

    WHAT CHANGED, AND WHY IT MATTERS. Identifiers used to arrive as flat strings,
    so an enterprise CMDB key and a heading like `NFR-PS-001` were
    indistinguishable at the point of use — and reconciliation, unable to tell
    them apart, treated both as definitive matches. A `NFR-PS-001` read out of a
    markdown file is a real label worth keeping, but it is DOCUMENT-scoped: two
    documents may both number a requirement `FR-001`. Only an identifier a system
    of record holds is granted match authority.
    """
    refs: List[ExternalReference] = []

    raw = record.get("external_references") or record.get("external_refs") or []
    for r in raw:
        if isinstance(r, dict):
            value = str(r.get("identifier") or r.get("id") or "").strip()
            if not value:
                continue
            system = str(r.get("system") or "").strip()
            ref_type = str(r.get("reference_type") or r.get("type") or "").strip().upper()
            declared_scope = str(r.get("scope") or "").strip().upper()
            # A reference that names a system of record is enterprise-scoped even
            # if the scope was not stated; without a system it is a document
            # label, and claiming more would be inventing authority.
            if not declared_scope:
                declared_scope = (
                    SCOPE_ENTERPRISE
                    if system and ref_type in MANAGED_REFERENCE_TYPES
                    else SCOPE_DOCUMENT
                )
            refs.append(
                ExternalReference(
                    identifier=value,
                    system=system,
                    reference_type=ref_type or "OTHER",
                    scope=declared_scope,
                    uri=str(r.get("uri") or ""),
                    is_authoritative=bool(r.get("is_authoritative")),
                    attribute_scope=list(r.get("attribute_scope") or []),
                )
            )
        elif isinstance(r, str) and r.strip():
            refs.append(document_reference(r, document_ref))

    for key in ("requirement_id", "external_id"):
        value = record.get(key)
        if value:
            refs.append(document_reference(str(value), document_ref))

    deduped: List[ExternalReference] = []
    for ref in refs:
        if not ref.identifier:
            continue
        if not any(
            existing.identifier == ref.identifier and existing.system == ref.system
            for existing in deduped
        ):
            deduped.append(ref)
    return deduped


def _collect_declared_nodes(
    graph: KnowledgeGraph, output: Dict[str, Any], document_ref: str = ""
) -> Dict[str, str]:
    """Create nodes from explicit collections. Returns label -> node id."""
    by_label: Dict[str, str] = {}

    def declare(
        kind: str,
        label: str,
        external_refs: Optional[List[str]] = None,
        external_references: Optional[List[ExternalReference]] = None,
    ) -> None:
        label = (label or "").strip()
        if not label:
            return
        nid = graph.add_node(kind, label, external_refs, external_references)
        by_label.setdefault(label.lower(), nid)

    # Architecture profile
    for e in output.get("elements", []) or []:
        if not isinstance(e, dict):
            continue
        kind = (e.get("element_type") or e.get("ontology_class") or "Concept").strip()
        declare(kind, e.get("name") or "",
                external_references=_typed_external_refs(e, document_ref))

    for t in output.get("technology_stacks", []) or []:
        if isinstance(t, dict):
            declare("TechnologyStack", t.get("name") or "")

    for s in output.get("architecture_styles", []) or []:
        if isinstance(s, dict):
            declare("ArchitectureStyle", s.get("name") or "")

    for d in output.get("design_techniques", []) or []:
        if isinstance(d, dict):
            declare("DesignTechnique", d.get("name") or "")

    for c in output.get("engineering_conventions", []) or []:
        if isinstance(c, dict):
            declare("EngineeringConvention", c.get("name") or "")

    # Requirements profile
    for e in output.get("entities", []) or []:
        if not isinstance(e, dict):
            continue
        kind = (e.get("ontology_class") or e.get("entity_type") or "Concept").strip()
        declare(kind, e.get("name") or "",
                external_references=_typed_external_refs(e, document_ref))

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
    domain_pack: str = "",                # Vocabulary in force, recorded in provenance
) -> Tuple[KnowledgeGraph, ExtractionRun]:
    """Build a canonical graph from one extraction result.

    If `initiative_id` is provided, all assertions are scoped as INITIATIVE_PROPOSAL.
    Otherwise, they are treated as SYSTEM_BASELINE or DOMAIN_TRUTH depending on context.

    `domain_pack` names the domain vocabulary the extraction ran under. It defaults
    to whatever the metadata carries, so callers that pass it through the extraction
    output need not pass it twice; supplying it here wins.
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

    # The domain vocabulary in force for this run, recorded on every assertion it
    # produces. The explicit argument wins over metadata so a caller can correct a
    # stale value; defaulting to metadata means callers that already carry it
    # through the extraction output need not pass it twice.
    domain_pack = str(domain_pack or metadata.get("domain_pack") or "")

    def prov(pass_name: str = "", chunk_label: str = "") -> Provenance:
        return Provenance(
            source_type=SOURCE_EXTRACTION,
            run_id=run.id,
            pass_name=pass_name,
            model_id=model_id,
            chunk_label=chunk_label,
            asserted_at=run.completed_at,
            derived_from=document_ref,
            domain_pack=domain_pack,
        )

    by_label = _collect_declared_nodes(graph, output, document_ref)

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

        # ---- quality attributes this element delivers ----
        #
        # Materialised as a QualityAttribute node and linked with `satisfies_attribute`,
        # so the element points at the ATTRIBUTE rather than at a requirement that
        # happens to state it. That is what lets "which elements deliver
        # Availability?" be answered when no availability NFR exists.
        for attr in e.get("satisfies_attributes") or []:
            name = str(attr).strip()
            if not name:
                continue
            aid = _resolve(graph, name, by_label, "QualityAttribute")
            graph.add_assertion(nid, "satisfies_attribute", obj=aid,
                                confidence=1.0, provenance=p, scope=scope, initiative_id=init_id)
        for attr in ("quality_category", "subcharacteristic"):
            if e.get(attr):
                graph.add_assertion(nid, attr, value=str(e[attr]),
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

    # ---- design techniques: the mechanism that realizes a quality attribute ----
    #
    # The technique→NFR edge is the reason this collection exists. Without it the
    # graph can hold "High Availability is required" and "the platform is
    # replicated" as two unrelated facts, which is exactly the gap it closes.
    for d in output.get("design_techniques", []) or []:
        if not isinstance(d, dict):
            continue
        did = _resolve(graph, d.get("name") or "", by_label, "DesignTechnique")
        p = prov("technology")
        if d.get("technique_category"):
            graph.add_assertion(did, "technique_category", value=str(d["technique_category"]),
                                confidence=1.0, provenance=p)
        if d.get("mechanism"):
            graph.add_assertion(did, "mechanism", value=str(d["mechanism"]),
                                confidence=1.0, provenance=p)
        if d.get("quality_category"):
            graph.add_assertion(did, "quality_category", value=str(d["quality_category"]),
                                confidence=1.0, provenance=p)
        if d.get("subcharacteristic"):
            graph.add_assertion(did, "subcharacteristic", value=str(d["subcharacteristic"]),
                                confidence=1.0, provenance=p)
        # The attribute a technique delivers, as a node. Together with the NFR
        # literal link below this gives both directions: "what does this technique
        # deliver?" (here) and "which techniques answer this stated NFR?"
        # (realizes_quality_attribute). The attribute node is what makes the
        # question answerable when no NFR states the attribute at all.
        for attribute in d.get("satisfies_attributes") or []:
            name = str(attribute).strip()
            if not name:
                continue
            aid = _resolve(graph, name, by_label, "QualityAttribute")
            graph.add_assertion(did, "satisfies_attribute", obj=aid,
                                confidence=1.0, provenance=p)
        for target in d.get("applies_to") or []:
            eid = _resolve(graph, target, by_label)
            graph.add_assertion(eid, "applies_technique", obj=did,
                                confidence=1.0, provenance=p)
        # Kept as a literal reference, like every other cross-graph link: the NFR
        # lives in REQ-G and resolving it is reconciliation's job, not ingest's.
        for nfr in d.get("realizes_quality_attributes") or []:
            if str(nfr).strip():
                graph.add_assertion(did, "realizes_quality_attribute", value=str(nfr).strip(),
                                    confidence=1.0, provenance=p)

    # ---- engineering conventions: the organisation's own rules ----
    for c in output.get("engineering_conventions", []) or []:
        if not isinstance(c, dict):
            continue
        cid = _resolve(graph, c.get("name") or "", by_label, "EngineeringConvention")
        p = prov("technology")
        for slot, key in (
            ("convention_type", "convention_type"),
            ("pattern", "pattern"),
            ("enforcement", "enforcement"),
            ("rationale", "rationale"),
        ):
            if c.get(key):
                graph.add_assertion(cid, slot, value=str(c[key]),
                                    confidence=1.0, provenance=p)
        for example in c.get("examples") or []:
            if str(example).strip():
                graph.add_assertion(cid, "example", value=str(example).strip(),
                                    confidence=1.0, provenance=p)
        for governed in c.get("applies_to") or []:
            gid = _resolve(graph, governed, by_label)
            graph.add_assertion(gid, "conforms_to", obj=cid, confidence=1.0, provenance=p)

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

        # ---- quality classification of an NFR ----
        #
        # `quality_category` used to be dropped here entirely: extraction had no
        # field for it, so the ISO 25010 classification never reached the graph
        # and every NFR was an untyped blob. `realizes_attribute` additionally
        # materialises the attribute as a node, which is what makes "which
        # techniques address Availability?" answerable across BOTH graphs.
        if e.get("quality_attribute"):
            name = str(e["quality_attribute"]).strip()
            if name:
                aid = _resolve(graph, name, by_label, "QualityAttribute")
                p = prov("triples")
                graph.add_assertion(nid, "realizes_attribute", obj=aid,
                                    confidence=1.0, provenance=p,
                                    scope=default_scope, initiative_id=initiative_id)
        for attr in ("quality_category", "subcharacteristic"):
            if e.get(attr):
                p = prov("triples")
                graph.add_assertion(nid, attr, value=str(e[attr]),
                                    confidence=1.0, provenance=p,
                                    scope=default_scope, initiative_id=initiative_id)

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
        # Both forms. Passing only `node.external_refs` silently discarded the
        # typed records on every merge — the identifier survived as a string and
        # its scope, type and system did not, so a freshly ingested enterprise key
        # became indistinguishable from a document label the moment it merged.
        merged.add_node(
            node.kind,
            node.label,
            node.external_refs,
            node.external_references,
        )

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
