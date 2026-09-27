"""
The merged knowledge-graph viewpoint — requirement and architecture concepts, and
the links between them.

WHAT THIS REPLACES, AND WHY
---------------------------
This package previously held one viewpoint: C4. It drew **only architecture
elements**, which quietly equated "the graph" with "the architecture graph" — the
fusion `app/viewpoints/__init__.py` and `docs/ui-review-workflow.md` §8 both warn
about, arriving from the other direction. A requirements-only graph had no view at
all: `/review` and `/gaps` listed its assertions, and nothing ever drew the graph
those assertions form. Measured on the requirements fixture, the old view returned
`nodes == []`.

The evidence is the sort of thing a page should not have to say about itself:

    "This graph has no nodes at the C4 element kinds. Architecture extraction
     produces these; requirements extraction produces requirement and
     business-context nodes instead."

The platform's core job is to show an architecture answering its requirements, and
half of that walk had no visual representation. So this viewpoint draws the whole
graph — both sides and the cross-graph references that join them — and **C4 is not
this view**. A force layout is not a specification either, so rendering C4 *as C4*
is its own item (YB-025).

WHAT "C4" WAS ACTUALLY DOING HERE
---------------------------------
The old view was not really C4: a canonical C4 diagram is a notation with layout
conventions and a stable artefact. What C4 contributed was a **level** concept — a
deliberate reduction, reported via `excluded_kinds` so a reader cannot mistake "not
at this level" for "not in the graph". That idea is worth keeping and is kept here
as a **lens**, which is the honest name for it now: a filter over one map, rather
than a claim that the graph has four altitudes.

LENSES, NOT LEVELS
------------------
    all             every node kind
    traceability    requirements, architecture, and the references between them
    business        the context that authorises requirements
    requirements    the requirement hierarchy and its business context
    architecture    the elements that answer requirements

The default is `all`: the view exists so that no side of the graph is invisible by
default, and a reduction nobody asked for is exactly the failure being repaired.

Everything here composes `app.projections` primitives rather than re-deriving
assertions — see `app/viewpoints/__init__.py` for why the layers are split.
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, List, Optional

from app.projections import edge_records, literal_facts, node_records
from core.knowledge.model import CROSS_GRAPH_PREDICATES, reference_targets_a_node
from core.knowledge.quality import quality_state
from core.quality import CHARACTERISTIC_ORDER, enum_name, humanise

# ============================================================================
# Which kinds belong to which layer
# ============================================================================
#
# Grouped by what a kind IS, not by which ontology layer declares it — the two
# differ where extraction fell back. `Concept` and `ExternalReference` are not
# ontology classes at all (`ingest._resolve` creates the first for a referent no
# pass classified; the requirements profile's extra collections produce the
# second), and leaving them out of every lens would make a node visible in the
# graph and invisible in every view of it — the failure this module exists to fix.

BUSINESS_KINDS: FrozenSet[str] = frozenset(
    {
        "Initiative",
        "BusinessGoal",
        "BusinessCapability",
        "BusinessProcess",
        "ProcessActivity",
        "Stakeholder",
        "Regulation",
        "Standard",
        "BusinessRule",
        "Assumption",
        "GlossaryTerm",
        "UseCase",
        "UseCaseStep",
        "Actor",
        "AlternativeFlow",
        "RequirementSpecification",
    }
)

ARCHITECTURE_KINDS: FrozenSet[str] = frozenset(
    {
        "SoftwareSystem",
        "ExternalSystem",
        "Person",
        "Container",
        "Component",
        "DataStore",
        "Interface",
        # A Connection is reified as a node (its protocol and style have nowhere else
        # to live), so it is an architecture kind a view includes deliberately rather
        # than an unclassified node it stumbles over.
        "Connection",
        "CodeElement",
        "DeploymentNode",
        "Application",
        "Platform",
        "PlatformContract",
        "Product",
        "SubProduct",
        "System",
        "ArchitectureElement",
        "ArchitecturePattern",
        "ArchitectureStyle",
        "ArchitectureDecision",
        "ArchitectureView",
        "ArchitectureSpecification",
        "Connection",
        "Responsibility",
        "DesignTechnique",
        "EngineeringConvention",
        "TechnologyStack",
        "RequirementRealization",
        "SubProductScope",
        "SystemCapabilityBinding",
    }
)

# The requirement hierarchy and the things that only make sense beside it. This is
# the requirement-side catch-all, which is why the extraction fallbacks land here:
# they are created where a referent was never classified on either side.
REQUIREMENT_KINDS: FrozenSet[str] = frozenset(
    {
        "Requirement",
        "BusinessRequirement",
        "FunctionalRequirement",
        "NonFunctionalRequirement",
        "ConstraintRequirement",
        "PlatformExtensibilityRequirement",
        "PlatformMultiTenancyRequirement",
        "PlatformCompatibilityRequirement",
        "QualityAttribute",
        "QualityScenario",
        "DomainConcept",
        "ConceptAttribute",
        "ConceptRelationship",
        "Concept",
        "ExternalReference",
    }
)

# layer name -> the kinds in it. Used by `layer_of`, which is what the renderer
# colours by.
LAYER_KINDS: Dict[str, FrozenSet[str]] = {
    "business": BUSINESS_KINDS,
    "architecture": ARCHITECTURE_KINDS,
    "requirements": REQUIREMENT_KINDS,
}

# lens name -> the kinds it shows, or None for "no restriction".
#
# `all` is None rather than the union of the three on purpose: a node kind added
# tomorrow must appear in the default view without anyone remembering to register
# it here. The narrower lenses are where a new kind has to be classified, and
# `unclassified_kinds` reports any that were not.
LENSES: Dict[str, Optional[FrozenSet[str]]] = {
    "all": None,
    "traceability": REQUIREMENT_KINDS | ARCHITECTURE_KINDS,
    "business": BUSINESS_KINDS,
    "requirements": REQUIREMENT_KINDS,
    "architecture": ARCHITECTURE_KINDS,
}

LENS_LABELS: Dict[str, str] = {
    "all": "All concepts",
    "traceability": "Traceability",
    "business": "Business context",
    "requirements": "Requirements",
    "architecture": "Architecture",
}

DEFAULT_LENS = "all"

# Layers in wiring order: the legend and the counts read business -> requirement ->
# architecture, so the map is legible as a chain rather than alphabetically.
LAYER_ORDER = ("business", "requirements", "architecture")

# Literal facts worth surfacing in a tooltip. Deliberately a short allow-list: a
# node may carry any number of literal facts, and a map is not a table.
NODE_DETAIL_FACTS = ("description", "technology", "technology_stack", "system_class")


def layer_of(kind: str) -> str:
    """Which layer a node kind belongs to, for grouping and colour.

    An unrecognised kind reports as `requirements` rather than being invented as a
    fourth layer. The honest alternative — an "other" bucket — would make a
    genuinely new kind look like a deliberate category; instead the view reports
    `unclassified_kinds`, so vocabulary drift is visible rather than quietly filed.
    """
    for layer, kinds in LAYER_KINDS.items():
        if kind in kinds:
            return layer
    return "requirements"


def _reference_edges(graph, node_ids: set) -> List[Dict[str, Any]]:
    """Cross-graph references — the edges `edge_records` cannot see.

    THE POINT OF THIS VIEW. `implements_requirement`, `traces_to_goal` and friends
    are stored by ingest as literals, deliberately, so the difference between
    "resolved" and "referenced but not yet reconciled" survives. `edge_records`
    flattens only assertions with a node-valued object, so every *unresolved*
    reference — and every reference bound by citation of a requirement's own key,
    which names a node without holding its id — would be invisible here.

    ONLY `CROSS_GRAPH_PREDICATES`. That restriction is not decoration: without it
    every literal fact whose text happens to equal a node's label becomes an edge,
    so `element_type: Container` and a `part_of` naming a parent would be drawn as
    relationships. Those are properties of a node — `edge_records` handles the ones
    that are genuinely node-to-node relationships — and drawing them here would
    duplicate exactly what the projection layer already did.

    An unresolved reference is drawn as a dangling edge to a synthetic
    `ref:<text>` endpoint: the assertion exists in the graph, and the whole point
    of the audit is that it must be visible rather than inferred. A reference whose
    referent is real but hidden by the lens is dropped instead — the lens already
    reports it as an excluded kind, and a dashed line to a node that is not on the
    page is worse than the count.

    Walking `graph.active()` here is a deliberate exception to "a viewpoint only
    composes projections": there is no projection primitive for *reference* edges,
    because until now nothing but the reconcile table needed one.
    """
    edges: List[Dict[str, Any]] = []
    seen = set()
    for a in graph.active():
        if a.predicate not in CROSS_GRAPH_PREDICATES:
            continue  # a literal fact, or a link `edge_records` already drew
        if a.subject not in node_ids:
            continue
        target = reference_targets_a_node(graph, a)
        if target is not None and target.id == a.subject:
            continue  # a node does not reference itself
        if target is None:
            endpoint = f"ref:{a.target}"
            key = (a.subject, endpoint, a.predicate)
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                {
                    "id": f"{a.subject}~{endpoint}:{a.predicate}",
                    "source": a.subject,
                    "target": endpoint,
                    "predicate": a.predicate,
                    "label": a.predicate.replace("_", " "),
                    "confidence": round(a.confidence, 2),
                    "reference": True,
                    "open": True,
                    "target_label": a.target,
                }
            )
            continue
        if target.id not in node_ids:
            continue  # real referent, hidden by this lens
        key = (a.subject, target.id, a.predicate)
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            {
                "id": f"{a.subject}~{target.id}:{a.predicate}",
                "source": a.subject,
                "target": target.id,
                "predicate": a.predicate,
                "label": a.predicate.replace("_", " "),
                "confidence": round(a.confidence, 2),
                "reference": True,
                "open": False,
                "target_label": target.label,
            }
        )
    return edges


def _dedupe(edges: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One edge per `(source, target, predicate)`.

    A relationship said twice is one relationship — the same rule `edge_records`
    already applies within its own set, extended across the two sources. The first
    occurrence wins, and `edge_records` is passed first, so a link the graph holds
    as a real node-to-node edge is the shape that survives over a text reference
    naming the same node.
    """
    out: List[Dict[str, Any]] = []
    seen = set()
    for edge in edges:
        key = (edge["source"], edge["target"], edge["predicate"])
        if key in seen:
            continue
        seen.add(key)
        out.append(edge)
    return out


# ============================================================================
# Quality-attribute focus — the architect's filter
# ============================================================================
#
# `lens` selects by DOCUMENT LAYER (business / requirements / architecture),
# which is a fact about how the graph was extracted. The architect's question is
# not "show me the architecture layer" — it is "show me Security", and that
# crosses the layers: the requirements that state the concern, the elements and
# techniques that deliver it, and the attribute node where the two meet.

FOCUS_CHARACTERISTIC = "characteristic"
FOCUS_ATTRIBUTE = "attribute"


def _char_rank(category: str) -> int:
    try:
        return CHARACTERISTIC_ORDER.index(category)
    except ValueError:
        return len(CHARACTERISTIC_ORDER)


def _concern_node_ids(entry: Dict[str, Any]) -> set:
    """Every node a concern's row points at: the attribute, and its claims."""
    ids = set(entry.get("node_ids") or [])
    for bucket in ("stated", "delivered", "scenarios"):
        for claim in entry.get(bucket) or []:
            if claim.get("source_id"):
                ids.add(claim["source_id"])
    return ids


def quality_focus(graph, concern: str) -> Optional[Dict[str, Any]]:
    """Resolve a focus value and the node neighbourhood it selects.

    Accepts an ISO characteristic (`RELIABILITY`), a sub-characteristic
    (`AVAILABILITY`) or a label as the graph spells it (`High Availability`),
    because a filtered URL should not require the reader to know which of the
    three they are naming. Returns None when nothing matches, so an unknown value
    reads as "this filter selected nothing" rather than silently showing the whole
    graph — a filter that fails open is worse than one that fails visibly.
    """
    wanted = enum_name(concern)
    if not wanted:
        return None

    matched: List[Any] = []
    kind = ""
    label = concern
    for entry in quality_state(graph):
        if enum_name(entry.category) == wanted:
            matched.append(entry)
            kind = kind or FOCUS_CHARACTERISTIC
            label = humanise(entry.category)
        elif (
            entry.key == wanted
            or enum_name(entry.display) == wanted
            or any(enum_name(alias) == wanted for alias in entry.labels)
        ):
            matched.append(entry)
            if not kind:
                kind, label = FOCUS_ATTRIBUTE, entry.display

    if not matched:
        return None

    concerns = [e.to_dict() for e in matched]
    node_ids: set = set()
    for entry in concerns:
        node_ids |= _concern_node_ids(entry)

    return {
        "value": concern,
        "kind": kind,
        "label": label,
        "concerns": concerns,
        # A list, not a set: this payload is handed to the template's `tojson`
        # filter for the D3 view, and a set is not serialisable.
        "node_ids": sorted(node_ids),
        "counts": {
            "concerns": len(concerns),
            "stated": sum(1 for c in concerns if c["stated"]),
            "delivered": sum(1 for c in concerns if c["delivered"]),
            "answered": sum(1 for c in concerns if c["coverage"] == "answered"),
            "gaps": sum(
                1 for c in concerns if c["coverage"] == "architecture_gap"
            ),
            "unasked": sum(1 for c in concerns if c["coverage"] == "unasked"),
        },
    }


def quality_focus_options(graph) -> Dict[str, Any]:
    """The focus values this graph can be filtered to, with counts.

    Only concerns the graph actually HAS. An option that selects nothing is a dead
    end dressed as a filter, and the whole point of the census is that it reports
    what is absent rather than offering it as if it were present.
    """
    state = [e.to_dict() for e in quality_state(graph)]

    by_category: Dict[str, List[Dict[str, Any]]] = {}
    for entry in state:
        by_category.setdefault(entry["category"] or "", []).append(entry)

    return {
        "characteristics": [
            {
                "value": category,
                "label": humanise(category) if category else "Unclassified",
                "concerns": len(entries),
                "answered": sum(1 for e in entries if e["coverage"] == "answered"),
                "gaps": sum(1 for e in entries if e["coverage"] == "architecture_gap"),
                "unasked": sum(1 for e in entries if e["coverage"] == "unasked"),
            }
            for category, entries in sorted(by_category.items(), key=lambda kv: _char_rank(kv[0]))
        ],
        "attributes": [
            {
                "value": entry["key"],
                "label": entry["display"],
                "category": entry["category"],
                "category_label": entry["category_label"],
                "coverage": entry["coverage"],
                "coverage_label": entry["coverage_label"],
            }
            for entry in state
        ],
    }


def merged_view(graph, lens: str = DEFAULT_LENS, concern: str = "") -> Dict[str, Any]:
    """Select the nodes a lens shows and shape them for the map.

    Node shape is what `node_records` and `literal_facts` already produce, plus
    `group` (the layer the renderer colours by) and the tooltip's detail facts.
    Inventing a second node shape would mean two definitions of "a node for a
    diagram", which is how the projection/viewpoint split gets undone.

    `concern` is the quality filter — an ISO characteristic (`RELIABILITY`) or a
    sub-characteristic (`AVAILABILITY`). It narrows the map to the attribute
    neighbourhood: the attribute nodes themselves, the requirements that state
    them and the elements and techniques that deliver them. The layer `lens` still
    applies, and both are reported with what they hide, because a filter that
    silently drops nodes is the failure every other lens here exists to avoid.
    """
    resolved_lens = lens if lens in LENSES else DEFAULT_LENS
    kinds = LENSES[resolved_lens]

    requested = (concern or "").strip()
    focus = quality_focus(graph, requested) if requested else None
    if requested and focus is None:
        # A filter that matches nothing selects nothing. Falling back to the whole
        # graph would make a typo look like a working filter over a graph with no
        # quality attributes in it, which is a different and much more alarming
        # finding.
        focus = {
            "value": requested,
            "kind": "unknown",
            "label": requested,
            "concerns": [],
            "node_ids": [],
            "counts": {
                "concerns": 0, "stated": 0, "delivered": 0,
                "answered": 0, "gaps": 0, "unasked": 0,
            },
        }
    focus_unknown = bool(requested) and focus["kind"] == "unknown"

    records = node_records(graph, kinds=kinds)
    if focus is not None:
        hidden_by_focus = [r for r in records if r["id"] not in focus["node_ids"]]
        records = [r for r in records if r["id"] in focus["node_ids"]]
    else:
        hidden_by_focus = []
    node_ids = {r["id"] for r in records}

    facts = literal_facts(graph, node_ids)
    # `edge_records` first, then the reference edges it cannot see, then one pass
    # that drops a duplicate `(source, target, predicate)`. The two sources overlap
    # legitimately: an assertion whose object is a node id is already an edge, and a
    # second assertion that names the same node by *text* is the same relationship
    # said twice. Drawing both would double every link a citation agrees with, and
    # picking by "which came first" would make the choice depend on dict order.
    links = _dedupe(edge_records(graph, node_ids) + _reference_edges(graph, node_ids))
    links.sort(key=lambda e: (e["source"], e["target"], e["predicate"]))

    nodes: List[Dict[str, Any]] = []
    for record in sorted(records, key=lambda r: r["id"]):
        detail = facts.get(record["id"], {})
        nodes.append(
            {
                **record,
                "group": layer_of(record["kind"]),
                "description": detail.get("description", ""),
                "technology": detail.get("technology") or detail.get("technology_stack", ""),
                "system_class": detail.get("system_class", ""),
                # Whether anything links to or from this node as a *concept*. The
                # alternative — leaving the reader to trace edges by eye — is what
                # makes an unanswered requirement invisible.
                "referenced": any(
                    e.get("reference") and (e["source"] == record["id"] or e["target"] == record["id"])
                    for e in links
                ),
            }
        )

    counts: Dict[str, int] = {}
    for node in nodes:
        counts[node["kind"]] = counts.get(node["kind"], 0) + 1

    groups: Dict[str, int] = {layer: 0 for layer in LAYER_ORDER}
    for node in nodes:
        groups[node["group"]] = groups.get(node["group"], 0) + 1

    all_kinds = {n.kind for n in graph.nodes.values()}
    shown_kinds = {n["kind"] for n in nodes}
    classified = BUSINESS_KINDS | REQUIREMENT_KINDS | ARCHITECTURE_KINDS

    return {
        "viewpoint": "merged",
        "lens": resolved_lens,
        "lens_label": LENS_LABELS[resolved_lens],
        "lens_labels": LENS_LABELS,
        # The quality filter, when one is applied, and what it hid. Both are
        # reported so a narrowed map is never mistaken for a narrowed graph.
        "focus": focus,
        "focus_requested": concern,
        "focus_unknown": focus_unknown,
        "focus_options": quality_focus_options(graph),
        "focus_hidden_nodes": len(hidden_by_focus),
        "focus_hidden_kinds": sorted({r["kind"] for r in hidden_by_focus}),
        "nodes": nodes,
        "links": links,
        "kind_counts": dict(sorted(counts.items())),
        "group_counts": groups,
        # What the lens is deliberately not showing, so the omission stays legible.
        "excluded_kinds": sorted(all_kinds - shown_kinds),
        # Cross-graph references by their two states. An open reference is the
        # traceability gap; a bound one is the link the platform exists to show.
        "reference_count": sum(1 for e in links if e.get("reference")),
        "open_reference_count": sum(1 for e in links if e.get("open")),
        # A kind no lens names. Empty on the current schema; non-empty means the
        # vocabulary moved and this viewpoint has not been told where it belongs.
        "unclassified_kinds": sorted(all_kinds - classified),
    }
