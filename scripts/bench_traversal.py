#!/usr/bin/env python3
"""
Does an in-memory graph library actually make traversal faster here, or only
differently shaped?

WHY THIS EXISTS. "Add networkx" sounds like a performance improvement, and for a
traversal it is — but the projection code in `app/projections.py` and
`core/knowledge/quality.py` mostly does a SINGLE full pass over every assertion, and a
library cannot beat a scan you are already doing once. The honest question is where the
crossover is, so the plan can say which queries to route through it.

`networkx>=3.2` is already a declared dependency and is used nowhere, so this measures a
capability already paid for.

WHAT IT MEASURES, per graph size:

  build   hand-rolled adjacency (dict[str, list]) vs networkx.DiGraph
  neigh   K repeated "every edge touching node X" queries — a scan is O(edges) each
          time, an index is O(degree)
  desc    D repeated "everything contained in node X" (transitive `part_of`)

Run:  .venv/bin/python scripts/bench_traversal.py [repeats]
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path
from typing import Callable, Dict, List, Tuple

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.knowledge.model import KnowledgeGraph  # noqa: E402
from core.knowledge.serialise import graph_from_dict  # noqa: E402

REPEATS = int(sys.argv[1]) if len(sys.argv) > 1 else 5
NEIGHBOUR_QUERIES = 500
DESCENDANT_QUERIES = 50


# ---------------------------------------------------------------------------
# Graphs to measure against
# ---------------------------------------------------------------------------


def real_graph() -> KnowledgeGraph:
    path = Path("data/sea-deepseek/working.json")
    if not path.exists():
        return synthetic_graph(400, 3, 400)
    return graph_from_dict(json.loads(path.read_text())["graph"])


def synthetic_graph(nodes: int, fanout: int, extra_edges: int) -> KnowledgeGraph:
    """A scope shaped like a large enterprise one: a `part_of` tree plus cross edges."""
    graph = KnowledgeGraph()
    ids = []
    for i in range(nodes):
        parent_kind = "SoftwareSystem" if i < fanout else "Container"
        ids.append(graph.add_node(parent_kind, f"node {i}"))
        if i >= fanout:
            parent = ids[(i - fanout) // fanout] if i >= fanout * fanout else ids[0]
            graph.add_assertion(ids[i], "part_of", obj=parent, confidence=0.9)
    for i in range(extra_edges):
        graph.add_assertion(ids[i % nodes], "connects_to",
                            obj=ids[(i * 7 + 3) % nodes], confidence=0.8)
    return graph


# ---------------------------------------------------------------------------
# The two implementations of the same two queries
# ---------------------------------------------------------------------------


def build_adjacency(graph: KnowledgeGraph) -> Dict[str, List[Tuple[str, str]]]:
    adjacency: Dict[str, List[Tuple[str, str]]] = {}
    for assertion in graph.assertions.values():
        if not assertion.object:
            continue
        adjacency.setdefault(assertion.subject, []).append(
            (assertion.predicate, assertion.object))
        adjacency.setdefault(assertion.object, []).append(
            (assertion.predicate, assertion.subject))
    return adjacency


def build_nx(graph: KnowledgeGraph) -> nx.DiGraph:
    view = nx.DiGraph()
    for node_id, node in graph.nodes.items():
        view.add_node(node_id, kind=node.kind, label=node.label)
    for assertion in graph.assertions.values():
        if assertion.object:
            view.add_edge(assertion.subject, assertion.object,
                          predicate=assertion.predicate)
    return view


def scan_neighbours(graph: KnowledgeGraph, node_id: str) -> int:
    """What the code does today: visit every assertion to find one node's edges."""
    found = 0
    for assertion in graph.assertions.values():
        if assertion.subject == node_id or assertion.object == node_id:
            found += 1
    return found


def index_neighbours(adjacency, node_id: str) -> int:
    return len(adjacency.get(node_id, ()))


def nx_neighbours(view: nx.DiGraph, node_id: str) -> int:
    return view.degree(node_id)


def scan_descendants(graph: KnowledgeGraph, root: str) -> int:
    """Transitive `part_of` the long way: rebuild the child map, then walk it."""
    children: Dict[str, List[str]] = {}
    for assertion in graph.assertions.values():
        if assertion.predicate == "part_of" and assertion.object:
            children.setdefault(assertion.object, []).append(assertion.subject)
    seen, stack = set(), [root]
    while stack:
        for child in children.get(stack.pop(), ()):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return len(seen)


def nx_descendants(view: nx.DiGraph, root: str) -> int:
    try:
        return len(nx.descendants(view, root))
    except nx.NetworkXError:
        return 0


def cached_walk(children: Dict[str, List[str]], root: str) -> int:
    """The same transitive walk, but over an index built ONCE.

    This is the comparison that decides the question: if a cached hand-rolled index
    matches networkx, then the library's value is its tested algorithms rather than
    its speed, and the plan should say so.
    """
    seen, stack = set(), [root]
    while stack:
        for child in children.get(stack.pop(), ()):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return len(seen)


def build_children(graph: KnowledgeGraph) -> Dict[str, List[str]]:
    children: Dict[str, List[str]] = {}
    for assertion in graph.assertions.values():
        if assertion.predicate == "part_of" and assertion.object:
            children.setdefault(assertion.object, []).append(assertion.subject)
    return children


def build_part_of_view(graph: KnowledgeGraph) -> nx.DiGraph:
    """Parent -> child, so `descendants(root)` walks the containment tree DOWNWARD.

    Direction matters and is easy to get wrong: `part_of` is stored as
    `<child> --part_of--> <parent>`, so adding `(subject, object)` builds child->parent
    and `descendants(root)` then explores the wrong side of the tree — returning almost
    nothing, very quickly. The first version of this benchmark made exactly that mistake
    and reported a 600x win that was really an empty traversal.
    """
    view = nx.DiGraph()
    view.add_edges_from(
        (a.object, a.subject) for a in graph.assertions.values()
        if a.predicate == "part_of" and a.object
    )
    return view


# ---------------------------------------------------------------------------


def timeit(fn: Callable[[], object], repeats: int = REPEATS) -> float:
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000)
    return statistics.median(samples)


def measure(label: str, graph: KnowledgeGraph) -> None:
    node_ids = list(graph.nodes)
    if not node_ids:
        return
    subjects = node_ids[:max(1, min(len(node_ids), 50))]
    per_repeat = max(1, NEIGHBOUR_QUERIES // len(subjects))

    # Everything built ONCE, then queried — the realistic deployment, where an index is
    # cached beside the loaded graph rather than rebuilt per page.
    build_dict_ms = timeit(lambda: build_adjacency(graph))
    build_nx_ms = timeit(lambda: build_nx(graph))
    build_children_ms = timeit(lambda: build_children(graph))
    build_partof_ms = timeit(lambda: build_part_of_view(graph))

    adjacency = build_adjacency(graph)
    view = build_nx(graph)
    children = build_children(graph)
    part_of = build_part_of_view(graph)

    scan_ms = timeit(lambda: [scan_neighbours(graph, n) for n in subjects] * per_repeat)
    index_ms = timeit(lambda: [index_neighbours(adjacency, n) for n in subjects] * per_repeat)
    nx_ms = timeit(lambda: [nx_neighbours(view, n) for n in subjects] * per_repeat)

    root = node_ids[0]
    naive_ms = timeit(lambda: [scan_descendants(graph, root) for _ in range(DESCENDANT_QUERIES)])
    cached_ms = timeit(lambda: [cached_walk(children, root) for _ in range(DESCENDANT_QUERIES)])
    nx_desc_ms = timeit(lambda: [nx_descendants(part_of, root) for _ in range(DESCENDANT_QUERIES)])

    print(f"\n{label}: {len(graph.nodes):,} nodes, {len(graph.assertions):,} assertions")
    print(f"  BUILD (once per scope load)")
    print(f"    adjacency dict              {build_dict_ms:9.2f} ms")
    print(f"    nx.DiGraph, all edges       {build_nx_ms:9.2f} ms   "
          f"({build_nx_ms / max(build_dict_ms, 0.001):.1f}x the dict)")
    print(f"    children dict (part_of)     {build_children_ms:9.2f} ms")
    print(f"    nx.DiGraph (part_of only)   {build_partof_ms:9.2f} ms   "
          f"({build_partof_ms / max(build_children_ms, 0.001):.1f}x the dict)")
    print(f"  {NEIGHBOUR_QUERIES} neighbour queries, index cached")
    print(f"    scan every assertion        {scan_ms:9.2f} ms    (today's shape)")
    print(f"    dict index                  {index_ms:9.2f} ms   "
          f"({scan_ms / max(index_ms, 0.001):>6.0f}x the scan)")
    print(f"    networkx degree             {nx_ms:9.2f} ms   "
          f"({scan_ms / max(nx_ms, 0.001):>6.0f}x the scan)")
    print(f"  {DESCENDANT_QUERIES} transitive part_of queries")
    print(f"    rebuild + walk (today)      {naive_ms:9.2f} ms")
    print(f"    cached dict walk            {cached_ms:9.2f} ms   "
          f"({naive_ms / max(cached_ms, 0.001):>6.1f}x today)")
    print(f"    networkx descendants        {nx_desc_ms:9.2f} ms   "
          f"({naive_ms / max(nx_desc_ms, 0.001):>6.1f}x today)")


def main() -> int:
    print(f"repeats={REPEATS}, networkx {nx.__version__}")
    measure("real store (data/sea-deepseek)", real_graph())
    measure("synthetic 1k nodes", synthetic_graph(1_000, 4, 1_000))
    measure("synthetic 5k nodes", synthetic_graph(5_000, 5, 5_000))
    return 0


if __name__ == "__main__":
    sys.exit(main())
