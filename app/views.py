"""
Projection Layer: Transforms the Canonical Graph into UI-ready views.

This layer sits between the framework-agnostic Knowledge Graph and the
Flask-based UI. It aggregates granular assertions into structured DTOs
(Data Transfer Objects) optimized for rendering.
"""

from typing import Any, Dict, List
from agents.knowledge.model import KnowledgeGraph, Assertion


def project_review_table(graph: KnowledgeGraph) -> List[Dict[str, Any]]:
    """Project the graph into a flat table for human review.
    
    Each row represents an assertion, aggregated with its subject's metadata.
    """
    rows = []
    for a in graph.active():
        node = graph.nodes.get(a.subject)
        if not node:
            continue
            
        target = ""
        if a.object:
            obj_node = graph.nodes.get(a.object)
            target = f"[{obj_node.kind}] {obj_node.label}" if obj_node else a.object
        elif a.value:
            target = a.value
            
        rows.append({
            "id": a.id,
            "subject_kind": node.kind,
            "subject_label": node.label,
            "predicate": a.predicate,
            "target": target,
            "confidence": a.confidence,
            "status": a.status,
            "scope": a.scope,
            "initiative_id": a.initiative_id,
            "source_text": a.source_text[:100] + "..." if len(a.source_text) > 100 else a.source_text
        })
    return sorted(rows, key=lambda x: (x["subject_label"], x["predicate"]))


def project_c4_context(graph: KnowledgeGraph) -> Dict[str, Any]:
    """Project the graph into a C4 Context view structure for D3.js."""
    nodes = []
    links = []
    
    # Filter for high-level elements (SoftwareSystems, People, External Systems)
    context_kinds = {"SoftwareSystem", "Person", "ExternalSystem"}
    
    for node in graph.nodes.values():
        if node.kind in context_kinds:
            nodes.append({
                "id": node.id,
                "label": node.label,
                "kind": node.kind,
                "group": node.kind
            })
            
    # Find relationships between these elements
    for a in graph.active():
        if a.object and a.predicate in ["connects_to", "uses", "interacts_with"]:
            source = graph.nodes.get(a.subject)
            target = graph.nodes.get(a.object)
            if source and target and source.kind in context_kinds and target.kind in context_kinds:
                links.append({
                    "source": source.id,
                    "target": target.id,
                    "label": a.predicate
                })
                
    return {"nodes": nodes, "links": links}


def project_gap_report(graph: KnowledgeGraph) -> Dict[str, Any]:
    """Project unresolved references into a gap report."""
    return {
        "unresolved_count": len(graph.unresolved_references()),
        "dangling_count": len(graph.dangling_assertions()),
        "completeness": list(graph.runs.values())[0].completeness if graph.runs else "UNKNOWN",
        "references": [
            {
                "source": graph.nodes[a.subject].label if a.subject in graph.nodes else a.subject,
                "predicate": a.predicate,
                "target": a.target
            }
            for a in graph.unresolved_references()
        ]
    }
