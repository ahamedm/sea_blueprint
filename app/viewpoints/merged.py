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

from functools import lru_cache
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple

from app.projections import (
    HIERARCHY_DIRECTIONS,
    edge_records,
    hierarchy_records,
    literal_facts,
    node_records,
)
from app.viewpoints.c4 import LEVEL_FROM_FACT, LEVEL_FROM_KIND
from core.knowledge.model import CROSS_GRAPH_PREDICATES, reference_targets_a_node
from core.knowledge.quality import quality_state
from core.ontology import load_ontology
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


# ============================================================================
# Colour: three axes over the same nodes, one at a time
# ============================================================================
#
# The map used to paint a whole LAYER one colour, which was legible while the graph
# was small and stopped being legible the moment it was not: measured on the live
# payments scope, 30 distinct kinds and 176 nodes were drawn in three colours, and
# `Concept` alone — the graph's own unclassified bucket — was 28 of them.
#
# The fix is two channels rather than one, because one cannot carry it: COLOUR is the
# family (a bounded set), SHAPE is the C4 level (so a container is still separable
# from a component inside one colour). And because "which distinction do I want right
# now?" is a real question with two good answers, the axis is a control rather than a
# decision baked into the page (ISSUES.md ISS-11).

#: The axes a reader may colour by. `family` is the default: bounded, ontology-derived,
#: and more informative than the layer without needing 30 colours.
COLOUR_AXES: Tuple[str, ...] = ("family", "layer", "kind")

DEFAULT_COLOUR = "family"

COLOUR_LABELS: Dict[str, str] = {
    "family": "Family",
    "layer": "Layer",
    "kind": "Kind",
}

#: Ontology subsets, in precedence order, become the families. Precedence exists
#: because a class may declare SEVERAL subsets — `DesignTechnique` is in both
#: `ArchitectureStructure` and `ArchitectureRationale`, and `NonFunctionalRequirement`
#: in both `Requirements` and `QualityAttributes` — so "the family" needs a rule rather
#: than a guess. First match wins, and the order below is the one that separates the
#: things a reader is actually trying to tell apart: rationale from the structure it
#: runs on, and requirements from the quality attributes they are about.
FAMILY_ORDER: Tuple[str, ...] = (
    "BusinessContext", "Requirements", "QualityAttributes", "C4Model",
    "ArchitectureRationale", "ArchitectureStructure", "EnterpriseStructure",
    "PlatformModel", "Traceability", "ArchitectureTraceability", "Specification",
)

#: The family for a kind the vocabulary does not declare — `Concept`, the graph's own
#: fallback, and every domain-pack class, because a pack is an overlay this view is not
#: given. Deliberately named for what it is: colouring those nodes as something they
#: are not would hide the one signal the bucket exists to give.
UNCLASSIFIED_FAMILY = "unclassified"

FAMILY_LABELS: Dict[str, str] = {
    "BusinessContext": "Business context",
    "Requirements": "Requirements",
    "QualityAttributes": "Quality attributes",
    "C4Model": "C4 elements",
    "ArchitectureRationale": "Architecture rationale",
    "ArchitectureStructure": "Architecture structure",
    "EnterpriseStructure": "Enterprise structure",
    "PlatformModel": "Platform model",
    "Traceability": "Traceability",
    "ArchitectureTraceability": "Architecture traceability",
    "Specification": "Specification",
    UNCLASSIFIED_FAMILY: "Unclassified",
}

#: The second channel. Shape carries the C4 level where the node has one — the
#: distinction colour cannot make inside a family — and a plain dot otherwise, so
#: "shape means something" is true rather than decorative.
SHAPE_FROM_LEVEL: Dict[str, str] = {
    "context": "system",
    "container": "container",
    "component": "component",
    "code": "code",
}

DEFAULT_SHAPE = "point"

SHAPE_LABELS: Dict[str, str] = {
    "system": "System",
    "container": "Container",
    "component": "Component",
    "code": "Code",
    DEFAULT_SHAPE: "Everything else",
}


@lru_cache(maxsize=None)
def family_map(ontology_dir: str) -> Dict[str, str]:
    """kind -> family, compiled from the ontology's own subsets.

    Empty when the ontology cannot be read at all, which `family_of` handles by
    degrading to the layer rather than by inventing a family.
    """
    try:
        model = load_ontology(ontology_dir)
    except Exception:  # noqa: BLE001 - a map without the vocabulary still draws
        return {}
    out: Dict[str, str] = {}
    for name, spec in model.classes.items():
        for subset in FAMILY_ORDER:
            if subset in (spec.subsets or ()):
                out[name] = subset
                break
    return out


def family_of(kind: str, ontology_dir: str = "ontology") -> str:
    """The family a kind belongs to, for colour.

    Read from the ontology rather than restated here, because the subsets are already
    the project's own grouping of its vocabulary — a second hand-written list would be
    the drift `validators.py` warns about, one layer over. A kind the base layers do
    not declare reports `unclassified`: that is the pack case (`Cardholder`) and the
    fallback case (`Concept`), and both are things the page already reports by name.
    """
    families = family_map(ontology_dir)
    if not families:
        return layer_of(kind)
    return families.get(kind, UNCLASSIFIED_FAMILY)


def shape_of(kind: str, facts: Dict[str, str]) -> str:
    """The shape a node is drawn as: its C4 level, when it states one.

    The level vocabulary lives in `c4.py` for the same reason the families live in the
    ontology: the C4 view owns "what level is this", and a second copy here would be a
    second answer. This is the map borrowing the vocabulary, not the projection.
    """
    level = LEVEL_FROM_FACT.get(str(facts.get("c4_level") or "").upper())
    if level is None:
        level = LEVEL_FROM_KIND.get(kind)
    return SHAPE_FROM_LEVEL.get(level or "", DEFAULT_SHAPE)


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


def _colour_key(node: Dict[str, Any], colour: str) -> str:
    """The value the renderer paints this node by, for the chosen axis."""
    if colour == "kind":
        return node["kind"]
    if colour == "layer":
        return node["group"]
    return node["family"]


def _colour_legend(nodes: List[Dict[str, Any]], colour: str) -> List[Dict[str, Any]]:
    """The swatches the renderer must draw, in a stable order, with their counts.

    Ordered by the axis's own vocabulary rather than by count, so a colour does not
    move to a different family because the graph grew: `family` follows
    `FAMILY_ORDER` with the unclassified bucket last, `layer` follows `LAYER_ORDER`,
    and `kind` is alphabetical. Counts are on the entry because "which of these am I
    actually looking at" is the question a legend is asked.
    """
    seen: Dict[str, int] = {}
    for node in nodes:
        key = _colour_key(node, colour)
        seen[key] = seen.get(key, 0) + 1

    if colour == "family":
        order = [f for f in FAMILY_ORDER if f in seen]
        order += [UNCLASSIFIED_FAMILY] if UNCLASSIFIED_FAMILY in seen else []
        # Anything `family_of` fell back to (the layer names, when the ontology could
        # not be read) still gets a swatch rather than being drawn in a colour the
        # legend does not name.
        order += [k for k in sorted(seen) if k not in order]
        label = lambda k: FAMILY_LABELS.get(k, k)
    elif colour == "layer":
        order = [l for l in LAYER_ORDER if l in seen]
        order += [k for k in sorted(seen) if k not in order]
        label = lambda k: k
    else:
        order = sorted(seen)
        label = lambda k: k

    return [{"key": k, "label": label(k), "count": seen[k]} for k in order]


def _shape_legend(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The shapes actually drawn, so the legend never claims an absent one."""
    seen: Dict[str, int] = {}
    for node in nodes:
        seen[node["shape"]] = seen.get(node["shape"], 0) + 1
    order = [s for s in ("system", "container", "component", "code", DEFAULT_SHAPE)
             if s in seen]
    return [{"key": s, "label": SHAPE_LABELS.get(s, s), "count": seen[s]} for s in order]


#: The predicates a tidy tree may draw as a PARENT link, with the direction each one
#: runs. Kept as data because the direction is NOT uniform and guessing it inverts a
#: tree: `part_of` names the child first (`Container part_of SoftwareSystem`), while
#: `contains` names the parent first (`DomainConcept contains DomainConcept`).
#:
#: Only edges the graph actually holds. A parent invented from nothing is the failure
#: ADR-0029 was written about, and this list is the whole of the invention budget.
_TREE_CHILD_TO_PARENT: Tuple[str, ...] = (
    "part_of", "belongs_to", "composed_of", "concept", "parent_goal",
)
_TREE_PARENT_TO_CHILD: Tuple[str, ...] = (
    "contains", "sub_capabilities",
)

#: Spacing for the computed layout, in viewBox units. The server lays the tree out
#: rather than handing a hierarchy to the client so that "same input, same picture"
#: is assertable in a test instead of merely likely — the acceptance criterion this
#: entry sets, and the one thing a force layout can never offer.
_TREE_X_STEP = 132
_TREE_Y_STEP = 96
_TREE_MARGIN = 60
#: A grouped family wraps into a block of at most this many columns, and the blocks
#: themselves wrap at roughly this canvas width. Both exist for the same reason: 183
#: unparented nodes in one row is a canvas nobody can read at any zoom.
_TREE_GROUP_COLS = 6
_TREE_GROUP_WRAP = 8 * _TREE_X_STEP

#: The id prefix for a placeholder that exists only to group unparented nodes. It is
#: a GROUP, not a parent the graph claims: `parent_of` never assigns a real node under
#: one as if an edge existed, and the group is drawn as a placeholder.
_TREE_GROUP_PREFIX = "group:family:"


def map_tree(
    nodes: List[Dict[str, Any]],
    links: List[Dict[str, Any]],
    hierarchy: List[Dict[str, Any]],
    *,
    ontology_dir: str = "ontology",
) -> Dict[str, Any]:
    """A forest over the drawn nodes, laid out, with what could not be a tree edge.

    WHY THIS IS NOT `d3.hierarchy` ON THE LINKS

    A tidy tree needs one parent per node and this graph does not promise that, and
    more importantly it is NOT CONNECTED — on the live scope it is 27 components. So
    there is no single root to hand a tree layout, and two things follow:

      - it is a FOREST, laid out side by side, because the components genuinely are
        not related and a synthetic single root would claim they are;
      - the nodes in no hierarchy edge at all are GROUPED by the ontology's own
        family (`family_of`, the same axis the map colours by) and drawn under a
        placeholder. Grouping by a declared classification is not inventing a parent:
        `_REFERENCE_COLOUR`-style placeholders are already how this view admits a
        node it cannot place.

    WHAT IT REPORTS RATHER THAN DROPS

    Every edge that is not drawn as a parent/child link is counted, and the reasons
    are separated, because they are different claims:

      - `cross_links` — edges between two drawn nodes that the tree does not use;
      - `alternates` — a second parent a node had, which a tree cannot draw (this is
        the ISS-10 duplicate-identity shape: one container `part_of` two systems);
      - `cycles_broken` — a containment cycle, which the layout must break to exist.

    Dropping any of those silently would hide exactly the traceability this platform
    exists to expose, which is the failure this entry's acceptance calls out.
    """
    drawn = [str(n["id"]) for n in nodes]
    drawn_set = set(drawn)
    labels = {str(n["id"]): str(n.get("label", n["id"])) for n in nodes}
    kinds = {str(n["id"]): str(n.get("kind", "")) for n in nodes}

    # -- 1. the tree edges, by preference -----------------------------------
    # A node keeps the FIRST predicate that offers it a parent; within one predicate
    # the parent with the smallest id wins, so the forest does not depend on
    # assertion order (`graph.active()` is stable, but merge order is not a promise).
    parent_of: Dict[str, str] = {}
    alternates: List[Tuple[str, str, str]] = []
    # `hierarchy` arrives normalised (child, parent) by `projections.hierarchy_records`
    # and in predicate-preference order, so the first parent a node is offered is the
    # one it keeps. Within one predicate the smallest parent id wins, which keeps the
    # forest from depending on assertion or merge order.
    order = {pred: i for i, (pred, _) in enumerate(HIERARCHY_DIRECTIONS)}
    ranked = sorted(hierarchy, key=lambda h: (order.get(h["predicate"], 99),
                                              str(h["parent"]), str(h["child"])))
    for record in ranked:
        child, parent = str(record["child"]), str(record["parent"])
        if child not in drawn_set or parent not in drawn_set or child == parent:
            continue
        existing = parent_of.get(child)
        if existing is None:
            parent_of[child] = parent
        elif existing != parent:
            alternates.append((child, parent, str(record["predicate"])))

    # -- 2. break cycles ----------------------------------------------------
    # Walk each node to its root. Revisiting a node on the way up means the chain is a
    # cycle; the node where it closes gives up its parent. Sorted order keeps the
    # choice deterministic rather than dependent on dict iteration.
    cycles_broken: List[str] = []
    for start in sorted(drawn_set):
        seen: List[str] = []
        node = start
        while node in parent_of and len(seen) <= len(drawn_set):
            if node in seen:
                parent_of.pop(node, None)
                cycles_broken.append(node)
                break
            seen.append(node)
            node = parent_of[node]

    # -- 3. group what no hierarchy edge reaches ----------------------------
    children: Dict[str, List[str]] = {}
    for child, parent in parent_of.items():
        children.setdefault(parent, []).append(child)
    for kids in children.values():
        kids.sort(key=lambda n: (labels.get(n, n).lower(), n))

    in_hierarchy = set(parent_of) | set(children)
    grouped: Dict[str, List[str]] = {}
    for node in drawn:
        if node in in_hierarchy:
            continue
        family = family_of(kinds.get(node, ""), ontology_dir)
        grouped.setdefault(family, []).append(node)

    # -- 4. lay it out ------------------------------------------------------
    # Slots are handed out to LEAVES in order and an internal node takes the mean of
    # its children, which cannot overlap because every leaf owns a distinct slot. It
    # is a layered drawing, not a beauty contest: deterministic and readable first.
    place: Dict[str, Tuple[float, int]] = {}
    next_slot = [0]

    def layout(node: str, depth: int) -> float:
        kids = children.get(node, [])
        if not kids:
            x = float(next_slot[0])
            next_slot[0] += 1
        else:
            xs = [layout(k, depth + 1) for k in kids]
            x = sum(xs) / len(xs)
        place[node] = (_TREE_MARGIN + x * _TREE_X_STEP,
                       _TREE_MARGIN + depth * _TREE_Y_STEP)
        return x

    # Real roots first: that is the hierarchy the graph actually holds, and it is
    # what a reader came for. Alphabetical, so two runs of one graph agree.
    roots = sorted(
        (n for n in in_hierarchy if n not in parent_of),
        key=lambda n: (labels.get(n, n).lower(), n),
    )
    for root in roots:
        layout(root, 0)

    # The grouped families go BELOW the forest as bounded blocks, not as another
    # row of leaves. On the live scope 183 of 201 nodes are in no hierarchy edge at
    # all, so one leaf per slot would be a 26,000-unit canvas — technically tidy and
    # unreadable at any zoom. A block wraps at `_TREE_GROUP_COLS` and the blocks
    # themselves wrap, which keeps the picture bounded and legible.
    group_ids: List[str] = []
    forest_bottom = max((y for _, y in place.values()), default=0.0)
    cursor_x = _TREE_MARGIN
    cursor_y = forest_bottom + _TREE_Y_STEP if place else _TREE_MARGIN
    row_bottom = cursor_y
    for family in sorted(grouped):
        members = grouped[family]
        cols = min(_TREE_GROUP_COLS, max(1, len(members)))
        rows = -(-len(members) // cols)
        block_w = (cols - 1) * _TREE_X_STEP
        if cursor_x > _TREE_MARGIN and cursor_x + block_w > _TREE_GROUP_WRAP:
            cursor_x = _TREE_MARGIN
            cursor_y = row_bottom + _TREE_Y_STEP
        gid = f"{_TREE_GROUP_PREFIX}{family}"
        group_ids.append(gid)
        children[gid] = members
        place[gid] = (cursor_x + block_w / 2, cursor_y)
        for i, member in enumerate(members):
            place[member] = (cursor_x + (i % cols) * _TREE_X_STEP,
                             cursor_y + _TREE_Y_STEP * (1 + i // cols))
            # The placeholder is the member's parent in the DRAWING, so the edge is
            # recorded. It is not a graph edge and is not counted as one below.
            parent_of[member] = gid
        cursor_x += block_w + _TREE_X_STEP * 2
        row_bottom = max(row_bottom, cursor_y + _TREE_Y_STEP * rows)

    tree_nodes: List[Dict[str, Any]] = []
    for node in drawn:
        x, y = place[node]
        tree_nodes.append({
            "id": node, "parent": parent_of.get(node, ""),
            "x": round(x, 1), "y": round(y, 1),
            "group": False,
        })
    for gid in group_ids:
        family = gid[len(_TREE_GROUP_PREFIX):]
        x, y = place[gid]
        tree_nodes.append({
            "id": gid, "parent": "", "x": round(x, 1), "y": round(y, 1),
            "group": True,
            "label": FAMILY_LABELS.get(family, family),
            "kind": "group",
        })

    max_depth = max((p[1] for p in place.values()), default=0)
    max_slot = max((p[0] for p in place.values()), default=0)
    # Only edges the tree DID NOT use are "cross links". Counting every link here
    # would report the tree's own edges as its omissions.
    grouped_total = sum(len(v) for v in grouped.values())
    # `parent_of` now carries both, and they are different claims: an edge the graph
    # holds versus a line onto a shelf it does not. Counting them together would let
    # the page report 381 "hierarchy edges" for a graph with 198.
    tree_edges = set(parent_of.items())
    cross_links = sum(
        1 for l in links
        if (l.get("source"), l.get("target")) not in tree_edges
        and (l.get("target"), l.get("source")) not in tree_edges
    )

    return {
        "nodes": tree_nodes,
        "roots": roots + group_ids,
        "tree_edges": len(parent_of) - grouped_total,
        "cross_links": cross_links,
        "alternates": len(alternates),
        "alternate_examples": [
            {"child": labels.get(c, c), "kept": labels.get(parent_of.get(c, ""), ""),
             "dropped": labels.get(p, p)}
            for c, p, _ in alternates[:5] if c in parent_of
        ],
        "cycles_broken": len(cycles_broken),
        "grouped": grouped_total,
        "group_count": len(group_ids),
        "components": len(roots) + len(group_ids),
        # The canvas the caller must fit. Reported rather than recomputed by the
        # renderer, so the picture and the viewBox cannot disagree about how big the
        # drawing is.
        "width": round(max((x for x, _ in place.values()), default=0.0) + _TREE_MARGIN, 1),
        "height": round(max((y for _, y in place.values()), default=0.0) + _TREE_MARGIN, 1),
    }


def merged_view(
    graph,
    lens: str = DEFAULT_LENS,
    concern: str = "",
    colour: str = DEFAULT_COLOUR,
    ontology_dir: str = "ontology",
) -> Dict[str, Any]:
    """Select the nodes a lens shows and shape them for the map.

    Node shape is what `node_records` and `literal_facts` already produce, plus the
    three things the renderer needs to encode: `group` (the layer), `family` (the
    ontology subset) and `shape` (the C4 level). Inventing a second node shape would
    mean two definitions of "a node for a diagram", which is how the
    projection/viewpoint split gets undone.

    `colour` picks which of the three axes `colour_key` carries — the reader's choice,
    not the view's (ISS-11). All three values are on every node regardless, so the
    payload is self-describing and the legend and the circles cannot disagree: the
    legend is built from the same field the renderer paints.

    `ontology_dir` is where the family vocabulary comes from. Defaulted rather than
    required so a CLI or a test gets the repo's own layout without configuration, and
    `family_of` degrades to the layer when it cannot be read.

    `concern` is the quality filter — an ISO characteristic (`RELIABILITY`) or a
    sub-characteristic (`AVAILABILITY`). It narrows the map to the attribute
    neighbourhood: the attribute nodes themselves, the requirements that state
    them and the elements and techniques that deliver them. The layer `lens` still
    applies, and both are reported with what they hide, because a filter that
    silently drops nodes is the failure every other lens here exists to avoid.
    """
    resolved_lens = lens if lens in LENSES else DEFAULT_LENS
    resolved_colour = colour if colour in COLOUR_AXES else DEFAULT_COLOUR
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
        family = family_of(record["kind"], ontology_dir)
        shape = shape_of(record["kind"], detail)
        node = {
            **record,
            "group": layer_of(record["kind"]),
            "family": family,
            "shape": shape,
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
        node["colour_key"] = _colour_key(node, resolved_colour)
        nodes.append(node)

    counts: Dict[str, int] = {}
    for node in nodes:
        counts[node["kind"]] = counts.get(node["kind"], 0) + 1

    # The tidy-tree representation, computed here rather than in the browser: the
    # server owns the structure AND the coordinates, so "same input, same picture"
    # is a test rather than a hope (see `map_tree`). It reads the graph rather than
    # `links`, because containment is a property on a node and never a drawn edge.
    tree = map_tree(
        nodes,
        links,
        hierarchy_records(graph, [n["id"] for n in nodes]),
        ontology_dir=ontology_dir,
    )

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
        # The second representation: a laid-out forest plus the count of what it
        # could not draw as tree edges, so the page can state its own omission.
        "tree": tree,
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
        # The reader's colour axis, and the legend that necessarily goes with it: the
        # renderer paints from `colour_key`, and this is the same field counted, so a
        # swatch cannot describe a colour the graph is not drawn in.
        "colour": resolved_colour,
        "colour_labels": COLOUR_LABELS,
        "colour_legend": _colour_legend(nodes, resolved_colour),
        # The second channel's own legend, only for the shapes actually drawn — an
        # empty "Component" swatch on a graph with no components is a claim about the
        # legend, not about the graph.
        "shape_legend": _shape_legend(nodes),
        "shape_labels": SHAPE_LABELS,
    }
