"""
The merged knowledge-map viewpoint.

Kept apart from `test_projections.py` on purpose: the viewpoint and the projection
layer are different things, and testing them together would re-suggest the fusion
this split removed.

The item this replaced drew *only* architecture elements, so a requirements-only
graph had no view at all — the old view returned `nodes == []` for it and said so on
the page. The first two tests here are the ones that would have caught that.
"""

from pathlib import Path

import app.viewpoints.merged as merged
from app.viewpoints.merged import (
    ARCHITECTURE_KINDS,
    BUSINESS_KINDS,
    DEFAULT_LENS,
    LENSES,
    REQUIREMENT_KINDS,
    layer_of,
    merged_view,
)

# ============================================================================
# Layer boundary
# ============================================================================


def test_the_viewpoint_composes_projection_primitives():
    """A viewpoint selects; it does not re-derive assertions.

    If this module starts flattening assertions itself, that work has been
    implemented twice and the two copies will drift.
    """
    source = Path(merged.__file__).read_text()
    assert "node_records" in source
    assert "edge_records" in source
    assert "literal_facts" in source


def test_reading_reference_edges_is_a_named_exception():
    """`_reference_edges` walks `graph.active()` and says why.

    There is no projection primitive for a *reference* edge — ingest stores those
    as literals, deliberately, and nothing but this view has needed to draw one. A
    silent exception would erode the rule, so the docstring is the licence and the
    walk is confined to the one helper.
    """
    source = Path(merged.__file__).read_text()
    assert "deliberate exception" in source
    code = [
        line for line in source.splitlines()
        # Skip the docstring prose, which legitimately names the call.
        if not line.strip().startswith(("#", '"', "*", "-", "Walking"))
    ]
    calls = [line for line in code if "graph.active()" in line and " in " in line]
    assert len(calls) == 1, calls


def test_only_cross_graph_predicates_become_reference_edges(req_extraction, arch_extraction):
    """The restriction that stops literal facts becoming edges.

    Without it, `element_type: Container` resolves to the `Container` *node* and is
    drawn as a relationship — a property of the element rendered as a link, and a
    duplicate of the structural edge the projection layer already emits.
    """
    from core.knowledge import merge_graphs
    from core.knowledge.model import CROSS_GRAPH_PREDICATES

    view = merged_view(merge_graphs(req_extraction, arch_extraction))
    for edge in view["links"]:
        if edge.get("reference"):
            assert edge["predicate"] in CROSS_GRAPH_PREDICATES, edge
    assert not any(e["predicate"] == "element_type" for e in view["links"])
    assert not any(e["predicate"] == "description" for e in view["links"])


# ============================================================================
# Coverage — the defect this item exists to fix
# ============================================================================


def test_a_requirements_graph_is_drawable(req_extraction):
    """The old view was blank for this. It must never be blank again."""
    view = merged_view(req_extraction)
    assert view["nodes"], "a requirements graph has concepts and must be drawn"
    assert view["viewpoint"] == "merged"


def test_both_sides_of_the_graph_appear_in_one_view(arch_extraction, req_extraction):
    """The map's reason to exist: one picture holding both documents' concepts."""
    from core.knowledge import merge_graphs

    both = merge_graphs(req_extraction, arch_extraction)
    view = merged_view(both)

    groups = {n["group"] for n in view["nodes"]}
    assert {"requirements", "architecture"} <= groups
    # And nothing is left outside the three layers.
    assert view["unclassified_kinds"] == []


def test_every_kind_in_the_graph_is_drawn_without_a_filter(req_extraction, arch_extraction):
    """`all` is unrestricted on purpose.

    A kind added tomorrow must appear in the default view without anyone
    remembering to register it — a default that silently drops the new kind is the
    same class of bug as the C4-only view, one refactor later.
    """
    from core.knowledge import merge_graphs

    both = merge_graphs(req_extraction, arch_extraction)
    graph_kinds = {n.kind for n in both.nodes.values()}
    view = merged_view(both, "all")
    assert graph_kinds == {n["kind"] for n in view["nodes"]}
    # ...and the two fallbacks extraction creates are in a layer, not homeless.
    for kind in ("Concept", "ExternalReference"):
        assert layer_of(kind) in ("business", "requirements", "architecture")


def test_an_empty_graph_renders_rather_than_raising():
    from core.knowledge import KnowledgeGraph

    view = merged_view(KnowledgeGraph())
    assert view["nodes"] == [] and view["links"] == []
    assert view["kind_counts"] == {}


# ============================================================================
# Lenses — a deliberate reduction, made visible
# ============================================================================


def test_an_unknown_lens_falls_back_rather_than_failing(arch_extraction):
    """A URL is user input; a bad lens must not 500 the page."""
    assert merged_view(arch_extraction, "nonsense")["lens"] == DEFAULT_LENS
    assert merged_view(arch_extraction, "")["lens"] == DEFAULT_LENS


def test_every_declared_lens_is_usable(arch_extraction):
    for lens in LENSES:
        view = merged_view(arch_extraction, lens)
        assert view["lens"] == lens
        assert view["lens_label"]


def test_the_architecture_lens_shows_only_architecture(req_extraction, arch_extraction):
    arch = merged_view(arch_extraction, "architecture")
    assert {n["group"] for n in arch["nodes"]} <= {"architecture"}

    req = merged_view(req_extraction, "requirements")
    assert req["nodes"], "business + requirement concepts live in this lens"
    assert all(n["group"] in ("business", "requirements") for n in req["nodes"])


def test_a_lens_reports_what_it_hides(arch_extraction):
    """Omission is the lens working, so it must be visible — a reader should never
    mistake 'not in this lens' for 'not in the graph'."""
    arch_only = merged_view(arch_extraction, "architecture")
    assert "Initiative" in arch_only["excluded_kinds"]
    assert "Initiative" in {n.kind for n in arch_extraction.nodes.values()}

    everything = merged_view(arch_extraction, "all")
    assert everything["excluded_kinds"] == []


def test_the_layers_partition_the_vocabulary():
    """No kind in two layers, and none missing from all three.

    A kind in two layers would be coloured by declaration order, which is a bug
    that looks like a preference.
    """
    business = set(BUSINESS_KINDS)
    requirements = set(REQUIREMENT_KINDS)
    architecture = set(ARCHITECTURE_KINDS)

    assert not (business & requirements)
    assert not (business & architecture)
    assert not (requirements & architecture)


# ============================================================================
# Cross-graph references — the join the platform exists to show
# ============================================================================


def test_unresolved_references_are_drawn_not_dropped(req_extraction):
    """Ingest keeps `traces_to_goal` as a literal, so `edge_records` cannot see it.

    An assertion that exists in the graph and is invisible in the only view of that
    graph is precisely the gap this view was built to close, so an unresolved
    reference is drawn as a dangling edge rather than omitted.
    """
    view = merged_view(req_extraction)
    open_refs = [e for e in view["links"] if e.get("open")]
    assert open_refs, "the fixture asserts a cross-graph reference"
    assert all(e["reference"] for e in open_refs)
    assert all(e["target"].startswith("ref:") for e in open_refs)
    assert view["open_reference_count"] == len(open_refs)


def test_a_bound_reference_is_drawn_as_a_link_not_an_open_dangling_edge():
    """The reconciliation payoff: once bound, the reference points at a real node.

    Both an id-bound link and a citation of the requirements document's own key
    count — the second names a node without holding its id, which is exactly the
    case a naive "does it have an object?" check would lose.
    """
    from core.knowledge import graph_from_extraction, merge_graphs

    requirements = {
        "entities": [{"name": "Payment Acceptance", "ontology_class": "FunctionalRequirement",
                      "requirement_id": "FR-PM-001"}],
        "triples": [],
    }
    architecture = {
        "elements": [{"name": "Payment Orchestrator", "element_type": "Container"}],
        "triples": [{"subject": "Payment Orchestrator", "predicate": "implements_requirement",
                     "object": "FR-PM-001", "confidence": 0.9}],
    }
    req_graph, _ = graph_from_extraction(
        requirements, {"document_type": "requirements"}, document_ref="req.md")
    arch_graph, _ = graph_from_extraction(
        architecture, {"document_type": "architecture"}, document_ref="arch.md")
    view = merged_view(merge_graphs(req_graph, arch_graph))

    bound = [e for e in view["links"] if e.get("reference")]
    assert bound, "the citation must appear as a reference edge"
    assert all(e["open"] is False for e in bound)
    assert any(
        e["source"] == "container:payment_orchestrator"
        and e["target"] == "functionalrequirement:payment_acceptance"
        for e in bound
    )


def test_a_reference_whose_referent_is_hidden_by_the_lens_is_not_drawn(arch_extraction):
    """A dashed line to a node that is not on the page is worse than a count."""
    view = merged_view(arch_extraction, "architecture")
    assert all(not e.get("open") or e["source"] in {n["id"] for n in view["nodes"]}
               for e in view["links"])
    assert all(
        e["target"] in {n["id"] for n in view["nodes"]} or e["target"].startswith("ref:")
        for e in view["links"]
    )


def test_a_node_never_references_itself(req_extraction):
    view = merged_view(req_extraction)
    assert all(e["source"] != e["target"] for e in view["links"])


def test_edges_are_deduplicated_and_identifiable(req_extraction, arch_extraction):
    from core.knowledge import merge_graphs

    view = merged_view(merge_graphs(req_extraction, arch_extraction))
    keys = [(e["source"], e["target"], e["predicate"]) for e in view["links"]]
    assert len(keys) == len(set(keys))
    assert all(e.get("id") for e in view["links"])


# ============================================================================
# What the renderer receives
# ============================================================================


def test_element_detail_is_carried_for_tooltips(arch_extraction):
    view = merged_view(arch_extraction)
    described = [n for n in view["nodes"] if n["description"]]
    assert described, "the fixture asserts a description"
    assert {"description", "technology", "system_class", "group"} <= set(described[0])


def test_facts_about_a_node_are_never_drawn_as_relationships(arch_extraction):
    """`description` is a property of a node; drawing it as a link is nonsense."""
    view = merged_view(arch_extraction)
    assert not any(e["predicate"] == "description" for e in view["links"])


def test_nodes_are_ordered_deterministically(arch_extraction):
    ids = [n["id"] for n in merged_view(arch_extraction)["nodes"]]
    assert ids == sorted(ids)


def test_the_view_counts_what_it_drew(arch_extraction):
    view = merged_view(arch_extraction)
    assert sum(view["group_counts"].values()) == len(view["nodes"])
    assert sum(view["kind_counts"].values()) == len(view["nodes"])


# ============================================================================
# The quality focus — selection by concern, not by document layer
# ============================================================================


def quality_graph():
    """A graph with one concern stated by a requirement and delivered by an element.

    Built here rather than added to `conftest.requirements_output`: the quality
    collections are what this filter reads, and putting them in the shared fixture
    would change what every other map test is looking at.
    """
    from core.knowledge import graph_from_extraction

    graph, _run = graph_from_extraction(
        {
            "entities": [
                {
                    "name": "Uptime",
                    "ontology_class": "NonFunctionalRequirement",
                    "quality_attribute": "Availability",
                },
                {"name": "Note", "ontology_class": "BusinessGoal"},
            ],
            "elements": [
                {
                    "name": "Gateway",
                    "element_type": "SoftwareSystem",
                    "satisfies_attributes": ["High Availability"],
                },
                {
                    "name": "Billing",
                    "element_type": "Container",
                    "satisfies_attributes": ["Scalability"],
                },
            ],
        },
        {"model_id": "fake"},
        document_ref="q.md",
        document_text="q source",
    )
    return graph


def test_the_quality_focus_keeps_the_attribute_neighbourhood():
    """The two spellings, the requirement that states it, the element that delivers it."""
    view = merged_view(quality_graph(), "all", "AVAILABILITY")
    labels = {n["label"] for n in view["nodes"]}
    assert labels == {"Availability", "High Availability", "Uptime", "Gateway"}
    assert view["focus"]["counts"]["concerns"] == 1


def test_the_quality_focus_reports_what_it_hides():
    """A narrowed map must never be mistaken for a narrowed graph."""
    view = merged_view(quality_graph(), "all", "AVAILABILITY")
    assert view["focus_hidden_nodes"] > 0
    assert "Billing" not in {n["label"] for n in view["nodes"]}
    assert "Container" in view["focus_hidden_kinds"]


def test_a_characteristic_focus_selects_every_concern_under_it():
    view = merged_view(quality_graph(), "all", "RELIABILITY")
    assert view["focus"]["kind"] == "characteristic"
    assert view["focus"]["counts"]["concerns"] == 1
    assert "Scalability" not in {n["label"] for n in view["nodes"]}


def test_an_unknown_focus_selects_nothing_rather_than_everything():
    view = merged_view(quality_graph(), "all", "NOT_A_CONCERN")
    assert view["focus_unknown"] is True
    assert view["nodes"] == []


def test_the_focus_accepts_a_label_as_the_graph_spells_it():
    view = merged_view(quality_graph(), "all", "High Availability")
    assert view["focus"]["label"] == "Availability"
    assert {n["label"] for n in view["nodes"]} == {
        "Availability", "High Availability", "Uptime", "Gateway",
    }


def test_the_focus_options_only_offer_what_the_graph_has():
    """An option that selects nothing is a dead end dressed as a filter."""
    options = merged_view(quality_graph())["focus_options"]
    assert {o["value"] for o in options["characteristics"]} == {"RELIABILITY", "FLEXIBILITY"}
    assert {o["value"] for o in options["attributes"]} == {"AVAILABILITY", "SCALABILITY"}


def test_without_a_focus_the_whole_graph_is_drawn(arch_extraction):
    view = merged_view(arch_extraction)
    assert view["focus"] is None
    assert view["focus_unknown"] is False
    assert view["focus_hidden_nodes"] == 0

