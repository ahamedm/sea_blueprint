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

from typing import Any, Dict, FrozenSet, List

from app.projections import edge_records, literal_facts, node_records
from core.knowledge.model import CROSS_GRAPH_PREDICATES, reference_targets_a_node

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


def merged_view(graph, lens: str = DEFAULT_LENS) -> Dict[str, Any]:
    """Select the nodes a lens shows and shape them for the map.

    Node shape is what `node_records` and `literal_facts` already produce, plus
    `group` (the layer the renderer colours by) and the tooltip's detail facts.
    Inventing a second node shape would mean two definitions of "a node for a
    diagram", which is how the projection/viewpoint split gets undone.
    """
    resolved_lens = lens if lens in LENSES else DEFAULT_LENS
    kinds = LENSES[resolved_lens]

    records = node_records(graph, kinds=kinds)
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
