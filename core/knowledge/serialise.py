"""
Canonical graph <-> JSON.

WHY THIS IS A NAMED MODULE
--------------------------
`docs/architecture-review.md` §3.2 called the transform between extraction output
and storage "the highest-risk component in the system" and required it to be a
named part with tests. `ingest.py` owns one direction (extraction -> graph);
this module owns the other (graph -> JSON -> graph), which the revision store,
the diff engine and the web UI all sit on.

WHAT GOES WRONG WITHOUT IT
--------------------------
A round trip that drops `superseded_by` silently resurrects a fact a human
deleted. One that drops `status` turns a confirmed decision back into an agent
guess. One that drops `provenance` collapses "asserted by an agent" and
"confirmed by a human" — the distinction the entire correction-merge design
rests on. So the tests here assert field-level survival, not merely that a graph
loads.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from .model import (
    SCOPE_DOCUMENT,
    Assertion,
    ExternalReference,
    ExtractionRun,
    KnowledgeGraph,
    Node,
    PassRecord,
    Provenance,
)

# Bumped when the on-disk shape changes incompatibly, so a future loader can
# refuse rather than silently mis-read an older snapshot.
#
# Still 1 after typed identifiers were added, deliberately: the change is
# additive. `external_refs` remains and is still written, so a v1 reader is not
# broken, and `node_from_dict` reads a v1 node (no `external_references`) as
# document-scoped references rather than failing.
SCHEMA_VERSION = 1


# ============================================================================
# Leaf types
# ============================================================================


def provenance_to_dict(p: Provenance) -> Dict[str, Any]:
    return p.to_dict()


def provenance_from_dict(data: Any) -> Provenance:
    data = data or {}
    known = Provenance.__dataclass_fields__
    return Provenance(**{k: v for k, v in data.items() if k in known})


def node_to_dict(n: Node) -> Dict[str, Any]:
    return {
        "id": n.id,
        "kind": n.kind,
        "label": n.label,
        "external_refs": list(n.external_refs or []),
        # Typed identifiers, alongside the flat list rather than replacing it, so
        # an older reader still finds something and the two cannot drift.
        "external_references": [r.to_dict() for r in n.external_references or []],
    }


def node_from_dict(data: Dict[str, Any]) -> Node:
    """Rebuild a node, tolerating revisions written before identifiers were typed.

    A saved revision is immutable, so its nodes carry only `external_refs` as
    bare strings. Those are read back as DOCUMENT-scoped references rather than
    dropped or assumed to be enterprise keys — the conservative reading, and the
    one that keeps an old revision from silently gaining match authority it was
    never granted.
    """
    node = Node(
        id=data["id"],
        kind=data.get("kind") or "",
        label=data.get("label") or "",
        external_refs=list(data.get("external_refs") or []),
    )
    for raw in data.get("external_references") or []:
        if isinstance(raw, dict) and raw.get("identifier"):
            node.external_references.append(
                ExternalReference(
                    identifier=str(raw["identifier"]),
                    system=str(raw.get("system") or ""),
                    reference_type=str(raw.get("reference_type") or "OTHER"),
                    scope=str(raw.get("scope") or SCOPE_DOCUMENT),
                    uri=str(raw.get("uri") or ""),
                    is_authoritative=bool(raw.get("is_authoritative")),
                    attribute_scope=list(raw.get("attribute_scope") or []),
                    notes=str(raw.get("notes") or ""),
                )
            )
        elif isinstance(raw, str) and raw.strip():
            node.external_references.append(
                ExternalReference(identifier=raw.strip(), scope=SCOPE_DOCUMENT,
                                  reference_type="OTHER")
            )

    # A node with flat refs but no typed records is a revision written before
    # identifiers were typed. Re-express them as DOCUMENT-scoped references so
    # the typed axis is populated for every node — without granting match
    # authority the original never carried. No-op for a current revision, where
    # every flat ref already has its typed record.
    if node.external_refs and not node.external_references:
        node.external_references = [
            ExternalReference(identifier=text, scope=SCOPE_DOCUMENT, reference_type="OTHER")
            for text in node.external_refs
            if text
        ]
    return node


def pass_record_to_dict(p: PassRecord) -> Dict[str, Any]:
    return {
        "pass_name": p.pass_name,
        "chunk_label": p.chunk_label,
        "outcome": p.outcome,
        "path": p.path,
        "elapsed": p.elapsed,
        "error": p.error,
        "triples_produced": p.triples_produced,
    }


def pass_record_from_dict(data: Dict[str, Any]) -> PassRecord:
    known = PassRecord.__dataclass_fields__
    return PassRecord(**{k: v for k, v in (data or {}).items() if k in known})


def run_to_dict(r: ExtractionRun) -> Dict[str, Any]:
    return {
        "id": r.id,
        "document_ref": r.document_ref,
        "document_type": r.document_type,
        "document_hash": r.document_hash,
        "document_chars": r.document_chars,
        "model_id": r.model_id,
        "started_at": r.started_at,
        "completed_at": r.completed_at,
        "chunk_count": r.chunk_count,
        "passes": [pass_record_to_dict(p) for p in r.passes],
        "completeness": r.completeness,
    }


def run_from_dict(data: Dict[str, Any]) -> ExtractionRun:
    data = data or {}
    return ExtractionRun(
        id=data["id"],
        document_ref=data.get("document_ref") or "",
        document_type=data.get("document_type") or "",
        document_hash=data.get("document_hash") or "",
        document_chars=int(data.get("document_chars") or 0),
        model_id=data.get("model_id") or "",
        started_at=data.get("started_at") or "",
        completed_at=data.get("completed_at") or "",
        chunk_count=int(data.get("chunk_count") or 0),
        passes=[pass_record_from_dict(p) for p in (data.get("passes") or [])],
        completeness=data.get("completeness") or "UNKNOWN",
    )


def assertion_to_dict(a: Assertion) -> Dict[str, Any]:
    return {
        "id": a.id,
        "subject": a.subject,
        "predicate": a.predicate,
        "object": a.object,
        "value": a.value,
        "confidence": a.confidence,
        "source_text": a.source_text,
        "ontology_class": a.ontology_class,
        "provenance": provenance_to_dict(a.provenance),
        "status": a.status,
        "superseded_by": a.superseded_by,
        "scope": a.scope,
        "initiative_id": a.initiative_id,
    }


def assertion_from_dict(data: Dict[str, Any]) -> Assertion:
    return Assertion(
        id=data["id"],
        subject=data["subject"],
        predicate=data.get("predicate") or "",
        object=data.get("object"),
        value=data.get("value"),
        confidence=float(data.get("confidence") or 0.0),
        source_text=data.get("source_text") or "",
        ontology_class=data.get("ontology_class"),
        provenance=provenance_from_dict(data.get("provenance")),
        status=data.get("status") or "UNVERIFIED",
        superseded_by=data.get("superseded_by"),
        scope=data.get("scope") or "INITIATIVE_PROPOSAL",
        initiative_id=data.get("initiative_id"),
    )


# ============================================================================
# Graph
# ============================================================================


def graph_to_dict(graph: KnowledgeGraph) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "version_id": graph.version_id,
        "parent_version_id": graph.parent_version_id,
        "label": graph.label,
        "nodes": {nid: node_to_dict(n) for nid, n in graph.nodes.items()},
        "declared_by": dict(graph.declared_by),
        "assertions": {aid: assertion_to_dict(a) for aid, a in graph.assertions.items()},
        "runs": {rid: run_to_dict(r) for rid, r in graph.runs.items()},
    }


def graph_from_dict(data: Dict[str, Any]) -> KnowledgeGraph:
    data = data or {}
    version = int(data.get("schema_version") or 0)
    if version > SCHEMA_VERSION:
        raise ValueError(
            f"Snapshot schema_version {version} is newer than this build "
            f"understands ({SCHEMA_VERSION}); refusing to mis-read it."
        )
    graph = KnowledgeGraph(
        version_id=data.get("version_id") or "",
        parent_version_id=data.get("parent_version_id") or "",
        label=data.get("label") or "",
    )
    for nid, nd in (data.get("nodes") or {}).items():
        graph.nodes[nid] = node_from_dict(nd)
    for aid, ad in (data.get("assertions") or {}).items():
        graph.assertions[aid] = assertion_from_dict(ad)
    for rid, rd in (data.get("runs") or {}).items():
        graph.runs[rid] = run_from_dict(rd)
    # Absent in revisions written before the side was recorded; an empty map makes
    # those nodes "unknown side", which is the conservative reading.
    graph.declared_by = {
        str(nid): str(side)
        for nid, side in (data.get("declared_by") or {}).items()
        if nid and side
    }
    return graph


def dumps(graph: KnowledgeGraph, indent: int = 2) -> str:
    return json.dumps(graph_to_dict(graph), indent=indent)


def loads(text: str) -> KnowledgeGraph:
    return graph_from_dict(json.loads(text))
