"""
Ontology reference view — presenting the LinkML schemas so they can be understood.

WHAT THIS IS NOT
----------------
Not `app.projections`: that reads the *instance* graph and answers "what does our
architecture knowledge contain". Not `app.viewpoints`: that describes architecture
in a notation (C4). This module presents the *schema* — which concepts exist and how
they relate — and it is the only one of the three that never reads an assertion.

The one place the schema and the instance graph meet is `instances`: a labelled
cross-reference counting how many nodes of each class exist in the working set. It
is deliberately a *join done here*, not something the loader knows about, because
`core.ontology` must stay usable by agents that have no graph.

WHY POSITIONS ARE COMPUTED HERE
-------------------------------
The class neighbourhood is laid out in **semantic rows**, not by a force
simulation: supertypes above the focus, subtypes below, relationships and referrers
further out. Position then *means* something — a hairball of 58 classes teaches
nothing, and a physics layout teaches less because the arrangement is arbitrary.
Computing the rows server-side also makes the layout testable without a browser.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.ontology import LAYER_LABELS, ClassSpec, OntologyModel

# How many incoming references one neighbourhood drawing will show before it stops
# being readable. The remainder is counted and reported rather than dropped.
MAX_REFERRERS = 14
MAX_ROW_NODES = 12


# ============================================================================
# Helpers
# ============================================================================


def _instance_counts(graph) -> Dict[str, int]:
    """How many nodes of each ontology class exist in the working set.

    The only schema-to-instance join in this module. A schema class with zero
    instances is often the most informative thing on the page: it explains why a
    view is empty without implying the schema is incomplete.
    """
    counts: Dict[str, int] = {}
    if graph is None:
        return counts
    for node in graph.nodes.values():
        counts[node.kind] = counts.get(node.kind, 0) + 1
    return counts


def _match_class(spec: ClassSpec, needle: str) -> bool:
    """Search names, descriptions and slot names.

    Slot names matter: "which class declares `quality_category`?" is a real
    question when reading a schema, and the answer should not require grepping
    YAML.
    """
    if not needle:
        return True
    needle = needle.lower()
    if needle in spec.name.lower() or needle in spec.description.lower():
        return True
    return any(needle in slot.name.lower() for slot in spec.attributes)


def _node_ref(model: OntologyModel, name: str) -> Dict[str, Any]:
    """One graph node for the neighbourhood drawing, classified."""
    if name in model.classes:
        spec = model.classes[name]
        return {
            "name": name,
            "kind": "class",
            "layer": spec.layer,
            "layer_label": LAYER_LABELS.get(spec.layer, spec.layer),
            "abstract": spec.abstract,
            "mixin": spec.mixin,
        }
    if name in model.enums:
        return {
            "name": name,
            "kind": "enum",
            "layer": model.enums[name].layer,
            "layer_label": LAYER_LABELS.get(model.enums[name].layer, model.enums[name].layer),
            "abstract": False,
            "mixin": False,
        }
    return {
        "name": name,
        "kind": "primitive",
        "layer": "",
        "layer_label": "",
        "abstract": False,
        "mixin": False,
    }


# ============================================================================
# Overview
# ============================================================================


def layer_chain(model: OntologyModel, graph=None) -> List[Dict[str, Any]]:
    """The base layers in dependency order, with their imports resolved to labels.

    This is the ontology's own architecture and the first thing a reader needs:
    the layers exist because of a one-way import rule, and each one adds a
    vocabulary the layer above may reference.
    """
    counts = _instance_counts(graph)
    chain = []
    for index, layer in enumerate(model.layers):
        layer_class_names = set(layer.classes)
        chain.append(
            {
                **layer.to_dict(),
                "index": index,
                "imports_labeled": [
                    {"key": key, "label": LAYER_LABELS.get(key, key)} for key in layer.imports
                ],
                "instances": sum(counts.get(name, 0) for name in layer_class_names),
                "examples": [
                    c.name for c in model.classes_in(layer.key) if not c.abstract and not c.mixin
                ][:6],
            }
        )
    return chain


def ontology_overview(model: OntologyModel, graph=None, pack=None) -> Dict[str, Any]:
    """The whole reference page's overview.

    `pack` is the domain overlay in force for the graph being viewed, if any. It is
    reported SEPARATELY from `layers` and never merged into them: the base
    layers are fixed and present for every Initiative, while a pack is conditional
    and swappable per Initiative. Drawing a pack as another layer would say the base
    ontology depends on one domain, which is the opposite of the design.
    """
    stats = model.stats()
    return {
        "stats": stats,
        "layers": layer_chain(model, graph),
        "pack": pack.to_dict() if pack is not None else None,
        "pack_instances": _pack_instance_counts(pack, graph) if pack is not None else {},
        "diagnostics": {
            "unresolved_parents": model.unresolved_parents,
            "unresolved_ranges": model.unresolved_ranges,
            "duplicate_names": model.duplicate_names,
            "is_clean": not (
                model.unresolved_parents or model.unresolved_ranges or model.duplicate_names
            ),
        },
        "mixins": [c.name for c in model.classes.values() if c.mixin],
        "abstract": [c.name for c in model.classes.values() if c.abstract],
        "binding_classes": [
            c.name
            for c in model.classes.values()
            if any(
                binding.lower() in c.description.lower()
                for binding in ("binding class", "binding ")
            )
        ],
    }


def _pack_instance_counts(pack, graph) -> Dict[str, int]:
    """How many nodes the working set holds per domain class.

    The coverage census, in its smallest useful form. A pack class showing 0 is the
    finding this whole layer exists to make askable — `Chargeback` with no instances
    means the requirement set never mentions chargebacks, which is a coverage gap
    no REQ-G/ARC-G traceability check can express.
    """
    counts: Dict[str, int] = {}
    if graph is None or pack is None:
        return counts
    for node in graph.nodes.values():
        if node.kind in pack.classes:
            counts[node.kind] = counts.get(node.kind, 0) + 1
    return counts


# ============================================================================
# Browser
# ============================================================================


def class_rows(
    model: OntologyModel, graph=None, layer: str = "", q: str = "", only: str = ""
) -> List[Dict[str, Any]]:
    """The class browser: one row per class, filterable and searchable."""
    counts = _instance_counts(graph)
    rows: List[Dict[str, Any]] = []

    for spec in model.classes_in(layer or None):
        if not _match_class(spec, q):
            continue
        if only == "abstract" and not spec.abstract:
            continue
        if only == "mixin" and not spec.mixin:
            continue
        if only == "relationship" and not any(s.is_relationship for s in spec.attributes):
            continue
        if only == "unused" and counts.get(spec.name, 0) > 0:
            continue

        effective = model.effective_attributes(spec.name)
        rows.append(
            {
                "name": spec.name,
                "layer": spec.layer,
                "layer_label": LAYER_LABELS.get(spec.layer, spec.layer),
                "description": spec.description,
                "is_a": spec.is_a,
                "mixins": list(spec.mixins),
                "abstract": spec.abstract,
                "mixin": spec.mixin,
                "own_attributes": len(spec.attributes),
                "effective_attributes": len(effective),
                "inherited_attributes": len(effective) - len(spec.attributes),
                "relationships": sum(1 for s in effective if s.is_relationship),
                "instances": counts.get(spec.name, 0),
                "subsets": list(spec.subsets),
            }
        )
    return rows


def enum_rows(model: OntologyModel, layer: str = "", q: str = "") -> List[Dict[str, Any]]:
    needle = (q or "").lower()
    rows = []
    for spec in model.enums_in(layer or None):
        if needle and needle not in spec.name.lower() and needle not in spec.description.lower():
            continue
        rows.append(spec.to_dict())
    return rows


# ============================================================================
# Focus
# ============================================================================


def class_neighbourhood(model: OntologyModel, name: str) -> Dict[str, Any]:
    """Semantic rows for the focus class, ready to draw.

    Rows are ordered outward from the class itself, and each row's *meaning* is its
    position, so the drawing explains the schema rather than merely depicting it.
    """
    spec = model.classes.get(name)
    if spec is None:
        return {"focus": name, "rows": [], "links": [], "hidden": {}, "found": False}

    supertypes = [p for p in ([spec.is_a] if spec.is_a else []) if p in model.classes]
    mixins = [m for m in spec.mixins if m in model.classes]
    subtypes = model.children(name)

    ranges: List[str] = []
    range_links: List[Dict[str, Any]] = []
    for slot, target in model.relationships(name):
        if target not in ranges:
            ranges.append(target)
        range_links.append({"source": name, "target": target, "kind": "range", "label": slot.name})

    incoming_links: List[Dict[str, Any]] = []
    for owner, slot_name in model.referenced_by(name):
        incoming_links.append(
            {"source": owner, "target": name, "kind": "incoming", "label": slot_name}
        )

    # Sort before truncating, not after: otherwise which nodes survive depends on
    # discovery order and the same class draws differently from run to run.
    shown_referrers = sorted({link["source"] for link in incoming_links})[:MAX_REFERRERS]
    shown_subtypes = sorted(subtypes)[:MAX_ROW_NODES]
    shown_ranges = sorted(ranges)[:MAX_ROW_NODES]

    # Emit only the range edges whose target is actually DRAWN.
    #
    # This was a real defect, not a precaution: with more ranges than
    # MAX_ROW_NODES the view emitted an edge to a node that no row contained, so
    # the drawing carried a line to nowhere and `hidden["links"]` counted it as
    # if it had been rendered. It surfaced only when `realizes_attribute` pushed
    # NonFunctionalRequirement's range count past the cap. The general rule the
    # original comment states — never leave the client to drop dangling links —
    # has to hold for rows truncated by a cap too.
    drawn_range_set = set(shown_ranges)
    emitted_range_links = [link for link in range_links if link["target"] in drawn_range_set]

    rows = [
        {
            "key": "referenced_by",
            "label": "Pointed at by",
            "hint": "classes with a slot whose range is this class",
            "nodes": [_node_ref(model, n) for n in shown_referrers],
        },
        {
            "key": "supertypes",
            "label": "Is a / mixes in",
            "hint": "is_a is the taxonomy; mixins are cross-cutting aspects",
            "nodes": [_node_ref(model, n) for n in supertypes + mixins],
        },
        {
            "key": "focus",
            "label": "This class",
            "hint": "",
            "nodes": [{**_node_ref(model, name), "is_focus": True}],
        },
        {
            "key": "subtypes",
            "label": "Specialised by",
            "hint": "these inherit the slots above",
            "nodes": [_node_ref(model, n) for n in shown_subtypes],
        },
        {
            "key": "ranges",
            "label": "Points at",
            "hint": "slots whose range is another class — the relationships, not the hierarchy",
            "nodes": [_node_ref(model, n) for n in shown_ranges],
        },
    ]

    included = {node["name"] for row in rows for node in row["nodes"]}
    candidates = (
        [{"source": child, "target": name, "kind": "is_a", "label": "is a"} for child in subtypes]
        + [
            {"source": name, "target": parent, "kind": "is_a", "label": "is a"}
            for parent in supertypes
        ]
        + [
            {"source": name, "target": mixin, "kind": "mixin", "label": "mixes in"}
            for mixin in mixins
        ]
        + emitted_range_links
        + incoming_links
    )
    # Emit only edges between nodes this view actually shows. Leaving the client to
    # drop dangling links would make a deliberate omission indistinguishable from a
    # bug — and untestable, which is how it went unnoticed the first time. The
    # `included` guard is now a second line of defence behind `emitted_range_links`,
    # covering the case where a row is capped rather than absent.
    links = [
        link for link in candidates if link["source"] in included and link["target"] in included
    ]
    # Edges dropped by the cap, so `hidden` reports what the reader is not seeing.
    capped_range_edges = len(range_links) - len(emitted_range_links)

    return {
        "focus": name,
        "found": True,
        "rows": rows,
        "links": links,
        "hidden": {
            "referenced_by": max(
                0, len({link["source"] for link in incoming_links}) - MAX_REFERRERS
            ),
            "subtypes": max(0, len(subtypes) - MAX_ROW_NODES),
            "ranges": max(0, len(ranges) - MAX_ROW_NODES),
            "links": len(candidates) - len(links),
            "capped_range_edges": capped_range_edges,
        },
    }


def class_detail(model: OntologyModel, name: str, graph=None) -> Optional[Dict[str, Any]]:
    """Everything the focus panel shows about one class."""
    spec = model.classes.get(name)
    if spec is None:
        return None

    counts = _instance_counts(graph)
    own = model.own_attributes(name)
    inherited = model.inherited_attributes(name)

    return {
        **spec.to_dict(),
        "layer_label": LAYER_LABELS.get(spec.layer, spec.layer),
        "ancestors": model.ancestors(name),
        "ancestor_chain": model.ancestors(name)[1:],
        "children": model.children(name),
        "supertype_names": model.parents(name),
        "own_slots": [s.to_dict() for s in own],
        "inherited_slots": [{**slot.to_dict(), "declared_by": owner} for owner, slot in inherited],
        "effective_count": len(model.effective_attributes(name)),
        "relationships": [
            {
                "slot": slot.name,
                "target": target,
                "target_layer": model.classes[target].layer if target in model.classes else "",
            }
            for slot, target in model.relationships(name)
        ],
        "referenced_by": [
            {"owner": owner, "slot": slot_name} for owner, slot_name in model.referenced_by(name)
        ],
        "instances": counts.get(name, 0),
        "neighbourhood": class_neighbourhood(model, name),
    }


# ============================================================================
# Machine-readable
# ============================================================================


def ontology_payload(model: OntologyModel, graph=None) -> Dict[str, Any]:
    """The whole model as JSON — for the API, and for anything that wants the
    schema without re-parsing five YAML files."""
    return {
        "overview": ontology_overview(model, graph),
        "classes": [
            {
                **spec.to_dict(),
                "effective_count": len(model.effective_attributes(spec.name)),
                "ancestors": model.ancestors(spec.name),
                "children": model.children(spec.name),
            }
            for spec in model.classes_in()
        ],
        "enums": [spec.to_dict() for spec in model.enums_in()],
        "subsets": [
            {
                "name": s.name,
                "layer": s.layer,
                "layer_label": LAYER_LABELS.get(s.layer, s.layer),
                "description": s.description,
            }
            for s in sorted(model.subsets.values(), key=lambda s: (s.layer, s.name))
        ],
    }
