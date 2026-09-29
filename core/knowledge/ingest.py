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

import logging

from ..ontology import canonical_predicate
from .model import (
    CROSS_GRAPH_PREDICATES,
    MANAGED_REFERENCE_TYPES,
    REQUIREMENT_KINDS,
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
    ontology_class_for_predicate,
    utc_now,
)

# Predicates where the object names the containing element (structural).
CONTAINMENT_PREDICATES = frozenset({"part_of", "belongs_to", "composed_of"})

_logger = logging.getLogger(__name__)


# The extraction-output keys this function reads. Data rather than a comment
# because the defect it guards is a key a profile EMITS and ingest never reads:
# the architecture profile produced `connections` records on every run and they
# were discarded for the life of the pass (YB-051), so the C4 diagram had boxes
# and no arrows. The per-pass `triples_produced` counter could not show it —
# that pass emitted triples as well — which is why the guard is per KEY.
INGESTED_OUTPUT_KEYS = frozenset({
    "elements", "connections", "triples",
    "technology_stacks", "architecture_styles", "design_techniques",
    "engineering_conventions", "quality_scenarios", "architecture_patterns",
    "references", "entities", "initiatives",
})

# Keys a consumer downstream of ingest reads. `findings` is rendered by the run
# pipeline and the run page; listing it keeps the accounting from reporting a
# loss where there is a reader.
DOWNSTREAM_OUTPUT_KEYS = frozenset({"findings"})

# Keys a profile may emit that NO consumer routes, each with the reason that is
# safe. `ExtractedRelationship` is a predicate vocabulary with no endpoints — the
# triples carry the same claim — so reading it here would state one fact twice
# (YB-051 checked this and found no loss). Kept as data so the accounting can
# distinguish "explained" from "unaccounted" rather than warning on every
# requirements run or hiding every unrouted key alike.
UNROUTED_OUTPUT_KEYS = {
    "relationships": "a predicate vocabulary with no endpoints; the triples carry it",
}

ROUTED_OUTPUT_KEYS = INGESTED_OUTPUT_KEYS | DOWNSTREAM_OUTPUT_KEYS


def _run_id(document_ref: str, document_text: str, model_id: str) -> str:
    """A run id, unique per call and not merely per second.

    `utc_now()` has SECOND resolution, so hashing it made two runs started within the
    same second share an identifier. That is not cosmetic: `graph.runs` is keyed by
    run id, so ingesting two documents in the same second made the second run's
    `ExtractionRun` silently overwrite the first — losing its completeness verdict and
    its per-pass outcomes, which is what `/c4`'s `run-completeness` gap and the run
    page both read. The live journal prefixes every event with the same value, so two
    concurrent runs also interleaved into one stream.

    `new_revision_id` next door already carried a `uuid4` suffix with the docstring
    "does not collide within a second". This is the same fix for the same reason; the
    time component is kept because a readable creation stamp in a log is worth having,
    and the uuid is what actually makes it unique.
    """
    import hashlib
    import uuid

    raw = f"{document_ref}|{len(document_text)}|{model_id}|{utc_now()}|{uuid.uuid4().hex}"
    return "run_" + hashlib.sha1(raw.encode()).hexdigest()[:12]


def new_run_id() -> str:
    """Mint a run id BEFORE the work starts.

    A live progress journal and the `ExtractionRun` it eventually becomes must
    share one identity, and `_run_id` mixes in the current time — so the id is
    unpredictable and cannot be recovered afterwards. A caller that wants to
    publish progress while extraction runs mints the id here, passes it as
    `metadata["run_id"]`, and journals under the same value.
    """
    return _run_id("", "", "")


def _document_hash(text: str) -> str:
    import hashlib
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _document_type(metadata: Dict[str, Any]) -> str:
    """The kind of document a run read, normalised to `requirements`/`architecture`.

    The extraction output carries `document_type` (the extractor is told which
    profile to run), so this is the one place that knows which SIDE of the
    reconciliation a run's nodes belong to. Anything else — a missing field, an
    unexpected value — is recorded verbatim but classifies as neither side:
    guessing that a document is an architecture one is how a requirement ends up
    ranked as if it were an answer to itself.
    """
    return str(metadata.get("document_type") or "").strip().lower()


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
    began emitting them (YB-005, root cause 1).

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
            # Typed `REQUIREMENT_KEY` even though it stays DOCUMENT-scoped, and
            # the type is load-bearing: it is what distinguishes the requirements
            # document's own key from any other label it happens to contain. An
            # `implements_*` citation of this identifier is identity rather than
            # resemblance (see `reference_targets_a_node`), while a shared heading
            # typed `OTHER` remains evidence a human must accept.
            ref = document_reference(str(value), document_ref)
            ref.reference_type = "REQUIREMENT_KEY"
            refs.append(ref)

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


def _ref_texts(raw: Any) -> List[str]:
    """Reference values as plain strings, from either shape a profile emits.

    A reference list may arrive as bare strings, as typed reference dicts, or as
    nested lists. Flattening it here keeps the ingest loops from each growing
    their own slightly-different idea of what a reference looks like.
    """
    values: List[str] = []

    def walk(value: Any) -> None:
        if value is None:
            return
        if isinstance(value, str):
            text = value.strip()
            if text:
                values.append(text)
            return
        if isinstance(value, dict):
            text = str(value.get("identifier") or value.get("id") or value.get("reference") or "").strip()
            if text:
                values.append(text)
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                walk(item)

    walk(raw)
    return values


def _collect_declared_nodes(
    graph: KnowledgeGraph,
    output: Dict[str, Any],
    document_ref: str = "",
    document_type: str = "",
) -> Dict[str, str]:
    """Create nodes from explicit collections. Returns label -> node id.

    Every declared node id is also recorded on the graph as having come from THIS
    document (`declared_by`). Which side of the reconciliation a node is on is a
    property of where it was read from, and a declared-but-never-described node —
    a requirement extracted with no triples at all — has no assertion to infer
    that from. Recording it at declaration is what makes the side knowable for
    exactly the nodes whose absence of claims the audit cares about.
    """
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
        graph.declared_by[nid] = document_type

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

    for p in output.get("architecture_patterns", []) or []:
        if isinstance(p, dict):
            declare("ArchitecturePattern", p.get("name") or "")

    for s in output.get("quality_scenarios", []) or []:
        if isinstance(s, dict):
            declare("QualityScenario", s.get("name") or "")

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
    source_type: str = SOURCE_EXTRACTION,  # Where these facts came from
) -> Tuple[KnowledgeGraph, ExtractionRun]:
    """Build a canonical graph from one extraction result.

    If `initiative_id` is provided, all assertions are scoped as INITIATIVE_PROPOSAL.
    Otherwise, they are treated as SYSTEM_BASELINE or DOMAIN_TRUTH depending on context.

    `domain_pack` names the domain vocabulary the extraction ran under. It defaults
    to whatever the metadata carries, so callers that pass it through the extraction
    output need not pass it twice; supplying it here wins.

    `source_type` is the provenance origin stamped on every assertion this run
    produces. It defaults to `SOURCE_EXTRACTION` — reading a document — and exists
    because a design PROPOSAL is not an extraction even though it travels the same
    path: the difference is exactly what a reviewer has to be able to see.
    """
    metadata = metadata or {}
    graph = KnowledgeGraph()

    model_id = str(metadata.get("model_id") or metadata.get("model") or "")
    run = ExtractionRun(
        # A caller may mint the id and pass it in, which is what lets a live progress
        # journal carry the SAME id as the run record it eventually becomes. Without
        # it, events published during extraction could not be correlated with the run
        # afterwards — and the id cannot be predicted, because `_run_id` includes the
        # current time.
        id=str(metadata.get("run_id") or "") or _run_id(document_ref, document_text, model_id),
        document_ref=document_ref,
        document_type=_document_type(metadata),
        document_hash=_document_hash(document_text) if document_text else "",
        document_chars=int(metadata.get("document_chars") or len(document_text)),
        model_id=model_id,
        started_at=metadata.get("started_at") or utc_now(),
        completed_at=utc_now(),
        chunk_count=int(metadata.get("chunks") or 1),
        passes=list(pass_records or _passes_from_metadata(metadata)),
        # Token usage, when the profile reported it. Absent is recorded as absent
        # rather than as zero — a run predating this, or one whose provider
        # returned no usage, is not a free run.
        usage=_usage_from_metadata(metadata),
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
            source_type=source_type,
            run_id=run.id,
            pass_name=pass_name,
            model_id=model_id,
            chunk_label=chunk_label,
            asserted_at=run.completed_at,
            derived_from=document_ref,
            domain_pack=domain_pack,
        )

    by_label = _collect_declared_nodes(
        graph, output, document_ref, run.document_type
    )

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
                     "deployment_model", "container_type"):
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
            for part in _enum_values(e.get(attr)):
                graph.add_assertion(nid, attr, value=part,
                                    confidence=1.0, provenance=p, scope=scope, initiative_id=init_id)

    # ---- connections: the edges a C4 diagram is made of ----
    #
    # THE PASS WAS RUNNING AND ITS OUTPUT WAS DISCARDED. The profile emits these
    # records under their own key, and this function read eleven keys without this
    # one — so every run paid for a model call per chunk and put nothing in the
    # graph (YB-051). The ontology already declares `Connection` and describes it as
    # "the arrow in a C4 diagram — and the edge the auditor walks to find coupling
    # and single points of failure"; only the plumbing was missing.
    #
    # Reified as a NODE rather than a bare element->element edge, because protocol,
    # style, `via_interface` and failure handling have nowhere to live on an
    # assertion, which is (subject, predicate, object|value). Same move the quality
    # attributes already make above.
    for c in output.get("connections", []) or []:
        if not isinstance(c, dict):
            continue
        source_label = str(c.get("source") or "").strip()
        target_label = str(c.get("target") or "").strip()
        if not (source_label and target_label):
            continue

        p = prov("connections")
        scope = default_scope
        init_id = initiative_id or c.get("initiative_id")
        cid = graph.add_node("Connection", f"{source_label} → {target_label}")
        # An endpoint that names nothing declared becomes a placeholder through
        # `_resolve`, exactly as every other undeclared referent does: the connection
        # stays visible and surfaces as an unresolved reference instead of vanishing.
        source_id = _resolve(graph, source_label, by_label)
        target_id = _resolve(graph, target_label, by_label)
        graph.add_assertion(cid, "source", obj=source_id,
                            confidence=1.0, provenance=p, scope=scope, initiative_id=init_id)
        graph.add_assertion(cid, "target", obj=target_id,
                            confidence=1.0, provenance=p, scope=scope, initiative_id=init_id)
        if c.get("protocol"):
            graph.add_assertion(cid, "protocol", value=str(c["protocol"]),
                                confidence=1.0, provenance=p, scope=scope, initiative_id=init_id)
        if c.get("style"):
            graph.add_assertion(cid, "style", value=str(c["style"]),
                                confidence=1.0, provenance=p, scope=scope, initiative_id=init_id)
        if c.get("description"):
            graph.add_assertion(cid, "description_text", value=str(c["description"]),
                                confidence=1.0, provenance=p, scope=scope, initiative_id=init_id)
        # Declared on `Connection` in the ontology and reachable from nowhere: the
        # pass schema did not ask, so the graph could not answer "which links carry
        # regulated data?" — the PCI-scope question the payment domain exists to ask.
        # Emitted only when true, so an unstated link is silent rather than asserting
        # a false negative the document never made.
        if c.get("carries_sensitive_data"):
            graph.add_assertion(cid, "carries_sensitive_data", value="true",
                                confidence=1.0, provenance=p, scope=scope, initiative_id=init_id)
        if c.get("failure_handling"):
            graph.add_assertion(cid, "failure_handling", value=str(c["failure_handling"]),
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
        for attr in ("quality_category", "subcharacteristic"):
            for part in _enum_values(d.get(attr)):
                graph.add_assertion(did, attr, value=part,
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

    # ---- architecture patterns: the named solutions the design adopts ----
    #
    # Distinct from a technique: a pattern is adopted from a published catalogue
    # and judged by "is the published pattern present?", a technique is a mechanism
    # the architecture exhibits and is judged by whether the NFR it claims is met
    # (ontology/README.md). Both carry the quality link, and nothing else.
    for pattern in output.get("architecture_patterns", []) or []:
        if not isinstance(pattern, dict):
            continue
        pid = _resolve(graph, pattern.get("name") or "", by_label, "ArchitecturePattern")
        p = prov("patterns")
        for slot in ("pattern_category", "mechanism", "rationale"):
            if pattern.get(slot):
                graph.add_assertion(pid, slot, value=str(pattern[slot]),
                                    confidence=1.0, provenance=p)
        for trade_off in pattern.get("trade_offs") or []:
            if str(trade_off).strip():
                graph.add_assertion(pid, "trade_off", value=str(trade_off).strip(),
                                    confidence=1.0, provenance=p)
        # The attribute a pattern targets, as a node — the same shape the technique
        # block writes, so the quality census reads both without a special case.
        for attribute in pattern.get("satisfies_attributes") or []:
            name = str(attribute).strip()
            if not name:
                continue
            aid = _resolve(graph, name, by_label, "QualityAttribute")
            graph.add_assertion(pid, "satisfies_attribute", obj=aid,
                                confidence=1.0, provenance=p)
        for target in pattern.get("applies_to") or []:
            eid = _resolve(graph, target, by_label)
            graph.add_assertion(eid, "applies_pattern", obj=pid,
                                confidence=1.0, provenance=p)
        for nfr in pattern.get("realizes_quality_attributes") or []:
            if str(nfr).strip():
                graph.add_assertion(pid, "realizes_quality_attribute", value=str(nfr).strip(),
                                    confidence=1.0, provenance=p)
        # The requirement that MANDATES this pattern, kept literal for the same
        # reason and routed so reconciliation can bind it. An unrouted mandate is
        # a claim nothing can verify, which is what the slot exists to avoid.
        for requirement in pattern.get("mandated_by") or []:
            if str(requirement).strip():
                graph.add_assertion(pid, "mandated_by", value=str(requirement).strip(),
                                    ontology_class=ontology_class_for_predicate("mandated_by"),
                                    confidence=float(pattern.get("confidence") or 0.8),
                                    provenance=p)

    # ---- quality scenarios: the ATAM operationalisation of an attribute ----
    #
    # A scenario is what makes a quality requirement falsifiable (stimulus →
    # environment → response → measure). `realizes_attribute` links it to the
    # QualityAttribute it operationalises — the same predicate an NFR uses, because
    # both point at the same node and `core.knowledge.quality` reads scenarios by
    # the SOURCE kind, not by the predicate name.
    for scenario in output.get("quality_scenarios", []) or []:
        if not isinstance(scenario, dict):
            continue
        sid = _resolve(graph, scenario.get("name") or "", by_label, "QualityScenario")
        p = prov("scenarios")
        for slot in ("stimulus_source", "stimulus", "environment", "artifact",
                     "response", "response_measure"):
            if scenario.get(slot):
                graph.add_assertion(sid, slot, value=str(scenario[slot]),
                                    confidence=1.0, provenance=p)
        if scenario.get("description"):
            graph.add_assertion(sid, "description", value=str(scenario["description"]),
                                confidence=1.0, provenance=p)
        attribute = str(scenario.get("attribute") or "").strip()
        if attribute:
            aid = _resolve(graph, attribute, by_label, "QualityAttribute")
            graph.add_assertion(sid, "realizes_attribute", obj=aid,
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

    # ---- traceability references (architecture profile) ----
    for r in output.get("references", []) or []:
        if not isinstance(r, dict):
            continue
        eid = _resolve(graph, r.get("element") or "", by_label)
        predicate = (r.get("relationship") or "implements_requirement").strip()
        p = prov("traceability")
        graph.add_assertion(
            eid, predicate,
            value=(r.get("reference") or "").strip(),
            confidence=float(r.get("confidence") or 0.8),
            source_text=r.get("source_text") or "",
            ontology_class=ontology_class_for_predicate(predicate),
            provenance=p, scope=default_scope, initiative_id=initiative_id,
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
            graph.add_assertion(nid, "delivers_initiative", obj=iid, confidence=1.0,
                                ontology_class="InitiativeDelivery",
                                provenance=p, scope=default_scope,
                                initiative_id=initiative_id)

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
            graph.add_assertion(nid, "authorised_by_initiative", obj=iid, confidence=1.0,
                                ontology_class="RequirementAuthorization",
                                provenance=p, scope=default_scope,
                                initiative_id=initiative_id)

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
            for part in _enum_values(e.get(attr)):
                p = prov("triples")
                graph.add_assertion(nid, attr, value=part,
                                    confidence=1.0, provenance=p,
                                    scope=default_scope, initiative_id=initiative_id)

        # ---- requirement classification (BUSINESS / FUNCTIONAL / …) ----
        #
        # Extracted since the schema existed and dropped here, which is why
        # `requirement_type` was null on every requirement in REQ-G as well as on
        # every architecture triple. Gated on the node being a Requirement: a
        # `DomainConcept` or `Stakeholder` record carries the requirement's
        # classification as document context, not as a property of itself, and
        # asserting it on the node would state something the document never said.
        node = graph.nodes.get(nid)
        if e.get("requirement_type") and node is not None and node.kind in REQUIREMENT_KINDS:
            p = prov("triples")
            graph.add_assertion(nid, "requirement_type", value=str(e["requirement_type"]),
                                confidence=1.0, provenance=p,
                                scope=default_scope, initiative_id=initiative_id)

        # ---- cross-graph references the requirements side makes ----
        #
        # The counterpart of the architecture profile's `references`: a
        # requirement document may cite the capabilities or systems it belongs
        # to. Kept literal for the same reason as every other cross-graph edge,
        # and emitted only when the record carries one — the current extraction
        # profile does not, so this changes nothing on today's output while
        # removing the assumption that only architecture ever references outward.
        p = None
        for key in ("requirement_refs", "implements_requirements"):
            for ref in _ref_texts(e.get(key)):
                p = p or prov("triples")
                graph.add_assertion(nid, "implements_requirement", value=ref,
                                    confidence=float(e.get("confidence") or 0.8),
                                    source_text=str(e.get("source_text") or ""),
                                    ontology_class="RequirementRealization",
                                    provenance=p, scope=default_scope,
                                    initiative_id=initiative_id)

    # ---- triples ----
    #
    # LAST, deliberately. A triple's cross-graph target is kept as a literal, and
    # whether that literal already names a node can only be decided once every
    # declared node exists — including the requirement nodes the entity loop above
    # creates. Running this section earlier is why an `implements_requirement`
    # reference to a requirement's own `requirement_id` looked unresolved at
    # ingest and had to be reconciled by hand. `_collect_declared_nodes` declares
    # the entities, but only the loop above gives them their identifiers and
    # `requirement_type`, so this order is the one that leaves every fact present.
    for t in output.get("triples", []) or []:
        if not isinstance(t, dict):
            continue
        subject = (t.get("subject") or "").strip()
        predicate = canonical_predicate(t.get("predicate") or "")
        obj = (t.get("object") or "").strip()
        if not (subject and predicate):
            continue

        sid = _resolve(graph, subject, by_label)
        p = prov("triples")
        p.asserted_at = run.completed_at
        confidence = float(t.get("confidence") or 0.0)
        source_text = t.get("source_text") or ""
        ontology_class = t.get("ontology_class") or ontology_class_for_predicate(predicate)

        if predicate in CROSS_GRAPH_PREDICATES:
            # A reference into another graph — keep it literal so reconciliation
            # can find it. Do NOT invent a local node.
            #
            # Scope is left at the model default (an Initiative proposal) rather
            # than forced to this run's default: a triple is the document's own
            # claim, and `promote_to_baseline` promotes exactly the
            # Initiative-scoped facts a reviewer has vetted. Stamping the run's
            # scope here would silently mark unreviewed triples as baseline.
            graph.add_assertion(sid, predicate, value=obj, confidence=confidence,
                                source_text=source_text, ontology_class=ontology_class,
                                provenance=p, initiative_id=initiative_id)
        else:
            oid = _resolve(graph, obj, by_label)
            graph.add_assertion(sid, predicate, obj=oid, confidence=confidence,
                                source_text=source_text, ontology_class=ontology_class,
                                provenance=p, initiative_id=initiative_id)

        # `requirement_type` qualifies the SUBJECT when the subject is a
        # requirement, and nothing else. On an architecture triple it names the
        # requirements document's own vocabulary, not a property of the container
        # the triple is about — which is why leaving it out is preferable to
        # asserting it broadly "because the field was there".
        subj_node = graph.nodes.get(sid)
        if (
            t.get("requirement_type")
            and subj_node is not None
            and subj_node.kind in REQUIREMENT_KINDS
        ):
            graph.add_assertion(sid, "requirement_type", value=str(t["requirement_type"]),
                                confidence=confidence, source_text=source_text,
                                provenance=p, initiative_id=initiative_id)

    # ---- what this run did with the output that produced it (YB-051) ----
    #
    # `emitted` is what the profile put under each collection key; `consumed` says
    # whether any consumer in the pipeline reads it. An emitted, unconsumed key is
    # the exact shape of the connections defect, and it is reported here rather
    # than asserted, because a profile is allowed to carry its own extra data — it
    # is not allowed to carry it invisibly.
    collected = {
        key: value for key, value in (output or {}).items()
        if isinstance(value, list) and value
        and all(isinstance(item, dict) for item in value)
    }
    run.output_counts = {
        key: {"emitted": len(records),
              "consumed": 1 if key in ROUTED_OUTPUT_KEYS else 0}
        for key, records in collected.items()
    }
    run.unconsumed_keys = sorted(
        key for key, counts in run.output_counts.items() if not counts["consumed"]
    )
    run.stored_facts = len(graph.assertions)

    unaccounted = [k for k in run.unconsumed_keys if k not in UNROUTED_OUTPUT_KEYS]
    if unaccounted:
        _logger.warning(
            "ingest read none of the %d record(s) emitted under %s — "
            "an output key with no consumer is how the C4 connections were lost",
            sum(run.output_counts[k]["emitted"] for k in unaccounted),
            ", ".join(repr(k) for k in unaccounted),
        )

    # Facts the graph declined, copied onto the run that produced them. The graph
    # itself does not persist them (they are diagnostics), so this is the only
    # place a refusal survives a save.
    run.refusals = [refusal.to_dict() for refusal in graph.refusals]
    if run.refusals:
        _logger.warning(
            "refused %d impossible fact(s) from %s: %s",
            len(run.refusals), document_ref or "the document",
            "; ".join(r.get("reason", "") for r in run.refusals[:3]),
        )

    return graph, run


def _enum_values(value: Any) -> List[str]:
    """One declared enum value, or the several the model joined into one string.

    THE FIELD IS DECLARED SCALAR AND THE MODEL WRITES A LIST. `quality_category`
    and `subcharacteristic` hold a single ISO 25010 member each, and two
    assertions in `data/sea` arrived as
    `'RELIABILITY, PERFORMANCE_EFFICIENCY, FLEXIBILITY'` — one token that matches
    no enum member, which a consumer grouping by the axis reads as unclassified,
    and which the census would report as a class of its own (YB-032).

    Splitting is lossless: every value the model did emit survives. That is the
    posture this repo takes everywhere else with vocabulary it does not recognise
    — keep it and let a validator report it, rather than drop it here. Whether a
    part is a real enum member is `check_enum_membership`'s question, not this
    function's; this one only refuses to let a LIST hide behind a scalar.

    A value with no separator comes back as the single element it always was, so
    the sixteen technique nodes that were already clean are untouched — and a
    value that legitimately contains a comma is not a case this field has.
    """
    text = str(value or "").strip()
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


def _usage_from_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Token usage as the profile reported it, or empty.

    Passed through rather than normalised: the keys are the profile's, the numbers
    are the provider's, and a layer between them that invented a shape would be
    the place a wrong total goes unnoticed. Empty means NOT RECORDED.
    """
    raw = metadata.get("usage")
    if not isinstance(raw, dict) or not raw:
        return {}
    return {str(k): v for k, v in raw.items()}


def _passes_from_metadata(metadata: Dict[str, Any]) -> List[PassRecord]:
    """The run's pass records, from the richest source the metadata carries.

    THREE SHAPES, IN ORDER OF FIDELITY:

    1. **`passes`** — the per-attempt records themselves. What the profiles emit
       now, and the only shape that preserves which attempt failed and why.
    2. **Counters** — `model_calls` / `failed_calls` / `empty_calls`, plus
       `text_fallback_calls`. The architecture profile has always emitted these;
       an older payload or a hand-written one may.
    3. **Nothing** — an empty list, which reads as `UNKNOWN`. Coarse is better
       than absent, but absent is honestly absent: an empty list is why
       `compute_completeness` returns UNKNOWN rather than pretending.

    WHY `text_fallback_calls` IS COUNTED AS `empty`. A text-fallback call came
    back with something, so it is not a failure — but the text parser has no
    reliable parser for several collections, so a run that used it is genuinely
    incomplete. Reading the counter as `ok` (which is what subtracting only
    `failed` and `empty` did) reported COMPLETE for a run that fell back, which is
    the one direction of error this field exists to prevent.
    """
    records = _pass_records_from_metadata(metadata.get("passes"))
    if records:
        return records

    failed = int(metadata.get("failed_calls") or 0)
    empty = int(metadata.get("empty_calls") or 0)
    text_fallback = int(metadata.get("text_fallback_calls") or 0)
    total = int(metadata.get("model_calls") or 0)
    ok = max(0, total - failed - empty - text_fallback)

    out: List[PassRecord] = []
    for _ in range(ok):
        out.append(PassRecord(pass_name="(unspecified)", chunk_label="",
                              outcome="ok"))
    for _ in range(empty):
        out.append(PassRecord(pass_name="(unspecified)", chunk_label="",
                              outcome="empty"))
    for _ in range(text_fallback):
        out.append(PassRecord(pass_name="text_fallback", chunk_label="",
                              outcome="empty", path="text"))
    for _ in range(failed):
        out.append(PassRecord(pass_name="(unspecified)", chunk_label="",
                              outcome="failed"))
    return out


def _pass_records_from_metadata(raw: Any) -> List[PassRecord]:
    """Per-attempt records from the `passes` metadata key, if present.

    Accepts either shape a caller might hand over — a `PassRecord` or the plain
    dict the agent serialises it into — because the extraction output crosses a
    JSON boundary in between. An unusable entry is skipped rather than raising:
    a malformed pass list must not take the whole ingest down, and a missing
    record degrades to the counter path, which is the honest outcome.
    """
    if not isinstance(raw, (list, tuple)):
        return []

    out: List[PassRecord] = []
    for item in raw:
        if isinstance(item, PassRecord):
            out.append(item)
            continue
        if not isinstance(item, dict):
            continue
        outcome = str(item.get("outcome") or "").strip().lower()
        if outcome not in ("ok", "empty", "failed"):
            # An unknown outcome is not silently mapped to `ok`: that is the one
            # reading which could open the audit gate on evidence we cannot read.
            continue
        out.append(
            PassRecord(
                pass_name=str(item.get("pass_name") or "(unspecified)"),
                chunk_label=str(item.get("chunk_label") or ""),
                outcome=outcome,
                path=str(item.get("path") or ""),
                elapsed=float(item.get("elapsed") or 0.0),
                error=str(item.get("error") or ""),
                triples_produced=int(item.get("triples_produced") or 0),
                temperature=_optional_temperature(item.get("temperature")),
            )
        )
    return out


def _optional_temperature(value: Any) -> Optional[float]:
    """A per-pass temperature, or None when the record does not carry one.

    Tolerant on purpose. Older runs predate the field, and a stored run is
    historical data — a malformed number must degrade to "not recorded" rather
    than make an otherwise readable revision unloadable.
    """
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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

    # Which document declared a node is a fact about the knowledge, not about the
    # snapshot it arrived in: dropping it on merge would make a merged graph's
    # cross-graph ordering depend on whether it had been re-saved.
    for nid, side in incoming.declared_by.items():
        merged.declared_by.setdefault(nid, side)

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
        # `None` is a refusal (a reflexive fact on an irreflexive predicate) —
        # nothing to fold lineage onto, and the source graph's own refusal is
        # already recorded, so the fact is not silently resurrected here.
        if folded is not None and a.superseded_by and not folded.superseded_by:
            folded.superseded_by = a.superseded_by

    return merged
