"""
The C4 architecture viewpoint.

Kept apart from `test_projections.py` on purpose: the viewpoint and the projection
layer are different things, and testing them together would re-suggest the fusion
this split removed.
"""

from pathlib import Path

import app.viewpoints.c4 as c4
from app.viewpoints.c4 import C4_LEVELS, c4_view

# ============================================================================
# Layer boundary
# ============================================================================


def test_the_viewpoint_composes_projection_primitives():
    """A viewpoint selects; it does not re-derive assertions.

    If this module starts walking `graph.active()` itself, assertion flattening has
    been implemented twice and the two copies will drift.
    """
    source = Path(c4.__file__).read_text()
    assert "node_records" in source
    assert "edge_records" in source
    assert "literal_facts" in source
    assert "graph.active()" not in source


# ============================================================================
# Selection
# ============================================================================


def test_requirements_content_is_not_architecture(req_extraction):
    """A requirement graph has no C4 elements, and the view says so rather than
    drawing business context as if it were architecture."""
    view = c4_view(req_extraction)
    assert view["nodes"] == []
    assert view["links"] == []
    assert view["viewpoint"] == "c4"


def test_levels_select_different_element_kinds(arch_extraction):
    # Containers belong to the container level, not the context level, so the level
    # must be asked for explicitly.
    context = c4_view(arch_extraction, "context")
    assert {n["kind"] for n in context["nodes"]} == {"SoftwareSystem"}

    container = c4_view(arch_extraction, "container")
    kinds = {n["kind"] for n in container["nodes"]}
    assert "SoftwareSystem" in kinds and "Container" in kinds
    assert container["links"], "containment and connections must appear as edges"


def test_each_level_is_cumulative(arch_extraction):
    context = c4_view(arch_extraction, "context")
    container = c4_view(arch_extraction, "container")
    component = c4_view(arch_extraction, "component")

    assert len(component["nodes"]) >= len(container["nodes"]) >= len(context["nodes"])
    assert "DataStore" in {n["kind"] for n in component["nodes"]}


def test_an_unknown_level_falls_back_rather_than_failing(arch_extraction):
    """A URL is user input; a bad level must not 500 the page."""
    assert c4_view(arch_extraction, "nonsense")["level"] == c4.DEFAULT_LEVEL
    assert c4_view(arch_extraction, "")["level"] == c4.DEFAULT_LEVEL


def test_every_declared_level_is_usable(arch_extraction):
    for level in C4_LEVELS:
        view = c4_view(arch_extraction, level)
        assert view["level"] == level
        assert view["kind_counts"]


# ============================================================================
# What the renderer receives
# ============================================================================


def test_edges_are_deduplicated(arch_extraction):
    view = c4_view(arch_extraction)
    keys = [(e["source"], e["target"], e["predicate"]) for e in view["links"]]
    assert len(keys) == len(set(keys))
    assert all("id" in e for e in view["links"])


def test_element_detail_is_carried_for_tooltips(arch_extraction):
    view = c4_view(arch_extraction, "component")
    described = [n for n in view["nodes"] if n["description"]]
    assert described, "the fixture asserts a description"
    assert {"description", "technology", "system_class"} <= set(described[0])


def test_facts_about_an_element_are_never_drawn_as_relationships(arch_extraction):
    """`description` is a property of a node; drawing it as a link is nonsense."""
    view = c4_view(arch_extraction, "component")
    assert not any(e["predicate"] == "description" for e in view["links"])


def test_the_view_reports_what_it_is_hiding(arch_extraction):
    """Omission is the viewpoint working, so it must be visible — a reader should
    never mistake 'not at this level' for 'not in the graph'."""
    context = c4_view(arch_extraction, "context")
    assert "Container" in context["excluded_kinds"]
    assert "Container" in {n.kind for n in arch_extraction.nodes.values()}

    component = c4_view(arch_extraction, "component")
    assert "Container" in {n["kind"] for n in component["nodes"]}
    assert "Container" not in component["excluded_kinds"]
    # Even the widest level is a selection: not every node kind is a C4 element.
    assert "Initiative" in component["excluded_kinds"]


def test_nodes_are_ordered_deterministically(arch_extraction):
    ids = [n["id"] for n in c4_view(arch_extraction, "component")["nodes"]]
    assert ids == sorted(ids)
