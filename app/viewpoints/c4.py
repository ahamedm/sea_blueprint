"""
The C4 architecture viewpoint.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
This is **not** the projection layer. `app.projections` reads the canonical graph
and hands back flat records and edges; this module decides what the *C4* view
shows. Concretely, it owns three decisions that are C4's and nobody else's:

1. **Which element kinds appear at which level.** C4's context level shows
   software systems and the people who use them. A `Container` inside a system is
   real and present in the graph, and is deliberately not drawn here. That omission
   is the viewpoint doing its job — not the graph losing data.
2. **What counts as a relationship.** An edge requires a node-valued object.
   `description`, `system_class` and `technology` are *detail* about an element, and
   drawing them as links would be nonsense.
3. **What the renderer receives.** Node/link shapes tuned for the diagram, plus
   `excluded_kinds` so a reader can see what this level is hiding.

LEVELS
------
C4 defines four: context, container, component, code. We model three, with code
elements folded into `component`, because the extracted graphs do not carry enough
code-level structure to justify a separate view. That is a modelling choice worth
revisiting when they do.

Everything below composes `app.projections` primitives rather than re-deriving
assertions — see `app/viewpoints/__init__.py` for why the layers are split.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.projections import edge_records, literal_facts, node_records

# C4 element kinds per level, cumulative: each level adds detail to the one above.
C4_LEVELS: Dict[str, frozenset] = {
    "context": frozenset({"Person", "SoftwareSystem", "ExternalSystem"}),
    "container": frozenset(
        {"Person", "SoftwareSystem", "ExternalSystem", "Container", "DataStore"}
    ),
    "component": frozenset(
        {
            "Person",
            "SoftwareSystem",
            "ExternalSystem",
            "Container",
            "DataStore",
            "Component",
            "CodeElement",
            "Interface",
            "DeploymentNode",
        }
    ),
}

DEFAULT_LEVEL = "context"

# Literal facts worth surfacing on an element. Deliberately a short allow-list:
# a node may carry any number of literal facts, and a diagram is not a table.
ELEMENT_DETAIL_FACTS = ("description", "technology", "technology_stack", "system_class")


def c4_view(graph, level: str = DEFAULT_LEVEL) -> Dict[str, Any]:
    """Select the C4 elements for `level` and shape them for a diagram."""
    resolved_level = level if level in C4_LEVELS else DEFAULT_LEVEL
    kinds = C4_LEVELS[resolved_level]

    records = node_records(graph, kinds=kinds)
    node_ids = {r["id"] for r in records}

    facts = literal_facts(graph, node_ids)
    links = edge_records(graph, node_ids)

    nodes: List[Dict[str, Any]] = []
    for record in sorted(records, key=lambda r: r["id"]):
        detail = facts.get(record["id"], {})
        nodes.append(
            {
                **record,
                # `group` is what D3 colours by; it is the kind, surfaced under the
                # name the renderer expects rather than leaking that choice upward.
                "group": record["kind"],
                "description": detail.get("description", ""),
                "technology": detail.get("technology") or detail.get("technology_stack", ""),
                "system_class": detail.get("system_class", ""),
            }
        )

    return {
        "viewpoint": "c4",
        "level": resolved_level,
        "nodes": nodes,
        "links": links,
        "kind_counts": {kind: sum(1 for n in nodes if n["kind"] == kind) for kind in sorted(kinds)},
        # What this viewpoint is deliberately not showing, so the omission is
        # visible rather than mistaken for absence.
        "excluded_kinds": sorted({n.kind for n in graph.nodes.values() if n.kind not in kinds}),
    }
