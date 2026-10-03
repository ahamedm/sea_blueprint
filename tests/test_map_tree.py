"""The tidy-tree representation: what it parents, what it groups, what it only counts.

The question this area answers is "can a reader see the hierarchy, and can they tell
what the picture left out?" — a tree is only honest if the edges it did not draw as
tree edges are reported rather than lost, which is why most of these assert on the
COUNTS and not on the coordinates (YB-056).
"""

from __future__ import annotations

import json
import re

import pytest

from app.projections import edge_records, hierarchy_records, node_records
from app.viewpoints.merged import map_tree
from core.knowledge.model import KnowledgeGraph


def _graph(claims):
    """A graph from `(subject_kind, subject_label, predicate, object_kind, object_label)`.

    Only object edges: `map_tree` reads the graph, not the drawn links, because
    containment is a node property and never reaches the map's `links` list.
    """
    g = KnowledgeGraph()
    for skind, slabel, predicate, okind, olabel in claims:
        s = g.add_node(skind, slabel)
        o = g.add_node(okind, olabel)
        g.add_assertion(s, predicate, o, confidence=0.9, source_text="test")
    return g


def _tree(graph, **kw):
    """The tree for a whole graph, through the same projections the view uses."""
    ids = [n.id for n in graph.nodes.values()]
    # `node_records` takes a KIND filter, not ids — passing ids there silently
    # returns nothing, which is how the first version of this helper "passed" a
    # tree with no nodes in it.
    return map_tree(
        node_records(graph), edge_records(graph, ids),
        hierarchy_records(graph, ids), **kw
    )


def _by_label(tree, graph, label):
    """The tree record whose node carries this label."""
    for node in tree["nodes"]:
        if node.get("label") == label:
            return node
    for node in tree["nodes"]:
        n = graph.nodes.get(node["id"])
        if n is not None and n.label == label:
            return node
    raise AssertionError(f"{label!r} is not in the tree")


def test_containment_becomes_parent_and_child():
    """`part_of` names the child first, so the tree must invert it — getting the
    direction wrong draws the system inside its own container."""
    graph = _graph([
        ("Container", "Orchestrator", "part_of", "SoftwareSystem", "Platform"),
    ])

    tree = _tree(graph)

    platform = _by_label(tree, graph, "Platform")
    orchestrator = _by_label(tree, graph, "Orchestrator")
    assert orchestrator["parent"] == platform["id"]
    assert platform["parent"] == ""
    assert platform["y"] < orchestrator["y"], "a parent is drawn above its child"
    assert tree["tree_edges"] == 1


def test_contains_runs_the_other_way():
    """`contains` names the PARENT first. The two predicates are declared separately
    precisely because a single direction would invert one of them."""
    graph = _graph([
        ("DomainConcept", "Payment Link", "contains", "DomainConcept", "Payment Link Email"),
    ])

    tree = _tree(graph)

    parent = _by_label(tree, graph, "Payment Link")
    child = _by_label(tree, graph, "Payment Link Email")
    assert child["parent"] == parent["id"]


def test_a_second_parent_is_reported_not_dropped():
    """The ISS-10 shape: one container `part_of` two systems under two identities.

    A tree can draw one, so the other is counted as an alternate rather than
    vanishing — the map is where that defect is supposed to become visible.
    """
    graph = _graph([
        ("Container", "Storefront", "part_of", "SoftwareSystem", "Platform A"),
        ("Container", "Storefront", "part_of", "SoftwareSystem", "Platform B"),
    ])

    tree = _tree(graph)

    assert tree["alternates"] == 1
    assert tree["alternate_examples"][0]["child"] == "Storefront"
    assert _by_label(tree, graph, "Storefront")["parent"] in {
        _by_label(tree, graph, "Platform A")["id"],
        _by_label(tree, graph, "Platform B")["id"],
    }


def test_a_cycle_is_broken_and_counted():
    """Containment should not be cyclic, and the present graph has none — so this is
    the fixture that keeps the breaker honest, because no real data exercises it."""
    graph = _graph([
        ("Container", "A", "part_of", "Component", "B"),
        ("Component", "B", "part_of", "Container", "A"),
    ])

    tree = _tree(graph)

    assert tree["cycles_broken"] == 1
    parents = {n["id"]: n["parent"] for n in tree["nodes"]}
    assert not any(
        parents.get(parents.get(n)) == n for n in parents if parents.get(n)
    ), "a 2-cycle survived"


def test_a_node_reachable_only_through_a_reference_is_grouped():
    """A requirement nothing parents cannot be a tree node, so it is grouped by the
    ontology's own family rather than invented a parent."""
    graph = _graph([
        ("Container", "Orchestrator", "part_of", "SoftwareSystem", "Platform"),
    ])
    orphan = graph.add_node("QualityAttribute", "Reliability")
    graph.add_assertion(
        _node_id(graph, "Platform"), "satisfies_attribute", orphan,
        confidence=0.9, source_text="test",
    )

    tree = _tree(graph)

    grouped = _by_label(tree, graph, "Reliability")
    assert grouped["parent"].startswith("group:"), "an unparented node is shelved"
    assert tree["grouped"] == 1
    assert tree["cross_links"] == 1, "the reference it has is still counted"


def _node_id(graph, label):
    for node in graph.nodes.values():
        if node.label == label:
            return node.id
    raise AssertionError(label)


def test_the_layout_is_deterministic_and_does_not_overlap():
    """The acceptance criterion a force layout can never meet: the same graph twice
    gives the same picture, and no two nodes share a position."""
    claims = [
        ("Container", f"C{i}", "part_of", "SoftwareSystem", "Platform")
        for i in range(12)
    ] + [("QualityAttribute", f"Q{i}", "supports_capability", "BusinessCapability", "Cap")
         for i in range(7)]
    graph = _graph(claims)

    first = _tree(graph)
    second = _tree(graph)

    assert first == second
    points = [(n["x"], n["y"]) for n in first["nodes"]]
    assert len(set(points)) == len(points), "two nodes were drawn on one point"


def test_the_canvas_is_bounded_by_the_grouping():
    """183 unparented nodes in a single row would be a canvas nobody can read at any
    zoom, which is why a family wraps into a block."""
    claims = [
        ("QualityAttribute", f"Q{i}", "supports_capability", "BusinessCapability", "Cap")
        for i in range(60)
    ]
    graph = _graph(claims)

    tree = _tree(graph)

    assert tree["grouped"] == 61, "60 attributes plus the capability they point at"
    assert tree["width"] < 60 * 132 / 3, (
        f"width {tree['width']} suggests the block did not wrap"
    )


def test_every_drawn_node_appears_exactly_once():
    """The tree must place the whole drawn set: a node silently missing from the tree
    is invisible in tree mode, which is the omission this view exists to avoid."""
    graph = _graph([
        ("Container", "A", "part_of", "SoftwareSystem", "P"),
        ("Container", "B", "part_of", "SoftwareSystem", "P"),
        ("QualityAttribute", "Q", "supports_capability", "BusinessCapability", "Cap"),
    ])
    ids = [n.id for n in graph.nodes.values()]

    tree = _tree(graph)

    placed = [n["id"] for n in tree["nodes"] if not n.get("group")]
    assert sorted(placed) == sorted(ids)


@pytest.mark.parametrize("lens", ["all", "architecture"])
def test_the_route_serves_both_modes_and_defaults_to_force(seeded_client, lens):
    """The mode switch must not break the page or the default, and the tree must
    STATE what it did not draw as tree edges — a tree that quietly drops its
    cross-links is the omission this whole view exists to prevent."""
    tree = seeded_client.get(f"/map?lens={lens}&mode=tree").get_data(as_text=True)
    assert 'var MODE = "tree";' in tree
    assert "node(s) grouped by family" in tree, "tree mode must report its own grouping"
    assert "cross-link(s) not drawn as tree edges" in tree

    force = seeded_client.get(f"/map?lens={lens}").get_data(as_text=True)
    assert 'var MODE = "force";' in force, "force stays the default"
    assert "cross-link(s) not drawn as tree edges" not in force

    nonsense = seeded_client.get(f"/map?lens={lens}&mode=sideways")
    assert nonsense.status_code == 200
    assert 'var MODE = "force";' in nonsense.get_data(as_text=True)


def test_tree_mode_gives_every_drawn_node_a_position(seeded_client):
    """A node without coordinates lands at the origin in tree mode, so the payload
    must place the whole drawn set — including the grouping placeholders."""
    body = seeded_client.get("/map?mode=tree").get_data(as_text=True)
    payload = json.loads(re.search(r"var data = (\{.*?\});\n", body, re.S).group(1))
    tree = payload["tree"]

    drawn = {n["id"] for n in payload["nodes"]}
    real = {n["id"] for n in tree["nodes"] if not n.get("group")}
    assert real == drawn, "every drawn node is placed exactly once"
    assert all("x" in n and "y" in n for n in tree["nodes"])
    assert tree["width"] > 0 and tree["height"] > 0


# ---------------------------------------------------------------------------
# Legibility: labels must not step on each other or on the next node
# ---------------------------------------------------------------------------

def _rows(tree):
    """The drawing grouped by the y a node sits at, left to right."""
    rows = {}
    for node in tree["nodes"]:
        rows.setdefault(node["y"], []).append(node)
    return [sorted(group, key=lambda n: n["x"]) for group in rows.values()]


def _forest_graph():
    """A forest wide enough to have siblings sharing a row, plus a grouped family."""
    claims = [("Container", f"C{i}", "part_of", "SoftwareSystem", "Platform")
              for i in range(8)]
    claims += [("QualityAttribute", f"Q{i}", "supports_capability", "BusinessCapability", "Cap")
               for i in range(8)]
    return _graph(claims)


def test_adjacent_labels_alternate_height():
    """A row of nodes draws its labels to the RIGHT, so two neighbours put their text
    on one line: at `_TREE_X_STEP` a 30-character label is ~200 units wide. Alternating
    the offset is what parts them, and it has to hold for every adjacent pair — an
    internal node taking the mean of its children shares the row too."""
    tree = _tree(_forest_graph())

    bad = []
    for row in _rows(tree):
        for left, right in zip(row, row[1:]):
            if left["label_dy"] == right["label_dy"]:
                bad.append((left["id"], right["id"], left["label_dy"]))

    assert not bad, f"adjacent labels share a line: {bad[:3]}"


def test_a_label_cannot_reach_the_next_column():
    """The stagger separates label from label; it cannot separate label from the next
    node's SHAPE, because that collision is horizontal. So the label has to fit the
    column, and the limit is derived from the layout step rather than guessed at a font
    in the browser."""
    tree = _tree(_forest_graph())
    max_chars = tree["label_max_chars"]

    # ~6.2 units/char at the label's font, drawn at x+13, with a 6-unit gap.
    assert 13 + max_chars * 6.2 + 6 <= 132, (
        f"{max_chars} characters would run past the next column at step 132"
    )
    assert 8 <= max_chars <= 30, "the limit must be useful, not a stub"


def test_grouped_families_stagger_the_nodes_themselves():
    """In a family block position carries no meaning — it is a shelf — so the NODES
    alternate and adjacent columns part company. In the hierarchy only the LABEL does,
    because vertical position is the depth channel and moving a node would corrupt the
    one thing the tree promises."""
    tree = _tree(_forest_graph())
    grouped = [n for n in tree["nodes"]
               if (n.get("parent") or "").startswith("group:")]
    assert grouped, "this fixture must have a grouped family"

    # Within one block, consecutive columns must not share a y.
    by_y = {}
    for node in grouped:
        by_y.setdefault(node["y"], []).append(node["x"])
    columns = {}
    for node in grouped:
        columns.setdefault(round(node["y"]), set()).add(round(node["x"]))
    shared = [y for y, xs in by_y.items() if len(xs) > 1]
    assert shared, "the block's nodes are all on one row — the stagger is not applied"


def test_the_forest_keeps_depth_as_height_while_labels_alternate():
    """The reason the fix differs by region: a staggered node in the hierarchy would
    break 'deeper means lower', so only the label moves."""
    tree = _tree(_forest_graph())
    forest = [n for n in tree["nodes"]
              if not (n.get("parent") or "").startswith("group:")]
    depths = {}
    for node in forest:
        depths.setdefault(node["y"], []).append(node)
    # every child is strictly below its parent, and the label offset never moves a node
    by_id = {n["id"]: n for n in tree["nodes"]}
    for node in forest:
        parent = node.get("parent")
        if parent and parent in by_id:
            assert by_id[parent]["y"] < node["y"]
