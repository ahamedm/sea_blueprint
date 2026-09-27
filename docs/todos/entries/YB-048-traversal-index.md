---
id: YB-048
legacy: null
title: "Traversal index — cache adjacency per scope load, and use networkx for algorithms, not speed"
status: open
priority: medium
area: "`core/knowledge/` (new traversal projection), `app/projections.py`, `core/knowledge/quality.py`"
created: 2026-09-26
updated: 2026-09-26
design: docs/design/workspace-and-sor-enablers.md
record: null
superseded_by: []
related: ["YB-043", "YB-042", "YB-044", "YB-010", "YB-027"]
blocks: []
blocked_by: []
---

# YB-048 — Traversal index

> **Open work.** This file is the source of truth for this item; `TODO.md` is generated from it.

**Full analysis:** [`docs/design/workspace-and-sor-enablers.md`](../../design/workspace-and-sor-enablers.md) §10

### The premise did not survive measurement

`networkx>=3.2` is already a declared dependency and **used nowhere**, so "add networkx
for a performance boost" looked free. It was measured first
(`scripts/bench_traversal.py`, 5,000 nodes / 9,995 assertions, indexes cached):

| Operation | Today's shape | Cached dict index | networkx |
|---|---|---|---|
| build the index, once per scope load | — | **2.0 ms** | 8.1 ms (4.0×) |
| build the `part_of` index only | — | **0.3 ms** | 2.4 ms (7.4×) |
| 500 neighbour lookups | 8.4 ms | **~0 ms (2795×)** | ~0 ms (1128×) |
| 50 transitive containment walks | 31.4 ms | **14.9 ms (2.1×)** | 119.4 ms (**0.3× — slower**) |

**The performance win is an adjacency index built once per scope load — and that needs
no library.** `nx.descendants` is slower than a tight cached BFS, and `nx.DiGraph` costs
4–7× a plain dict to build.

**networkx's value is capability:** tested `ancestors`/`descendants`, `shortest_path`,
`topological_sort`, `find_cycle`, `connected_components` — the algorithms a hand-rolled
walk gets subtly wrong. Use it for those, over an index that already exists.

### Not urgent, and worth saying so

At the current size nothing here is a bottleneck: the largest scope is ~150 nodes /
~635 assertions and the 500-query loop it replaces is 8 ms. This is a **scale wall**
item, not a live defect — which is why it is Medium and not High. It becomes load-bearing
when cross-scope traversal arrives (YB-044's platform instances serving many products)
and when scopes grow 10–100×.

### Slices

1. **The index.** A `Traversal` projection built from a loaded graph: adjacency both
   directions, a `part_of` parent→child map, and predicate-indexed views. Cached **beside**
   the loaded snapshot and keyed by the store's version token.
2. **A cheap version probe first.** `SqliteStore.load_working()` hydrates the whole graph
   to read `meta["version"]`, so a cache hit would cost a full load. Add
   `current_version(scope)` to the `Store` protocol — one indexed row — or there is no
   cache to speak of. This is slice 1's prerequisite, not a nicety.
3. **The networkx view.** `to_networkx(graph, predicates=None)` over the same loaded
   graph, for the algorithms rather than for lookups.
4. **Route the traversals.** Point the existing neighbourhood walks
   (`review_progress`, `quality_report`, `realization_report`, `app/projections.py`) at
   the index where they re-scan per node. Leave the single full passes alone — a library
   cannot improve a scan you perform once.

### What NOT to cache — corrected 2026-09-26

The first cut of this item said "cache indexed state per scope" without saying which
scopes or where. At 50 architects and 200 products that is wrong as written: an in-process
cache of every scope is ~1.9 GB at 20,000 assertions per scope, multiplied by every
worker. See [`workspace-and-sor-enablers.md`](../../design/workspace-and-sor-enablers.md) §11.

The rule is **mutability**, not size:

- **Working sets** — mutable, so in-process, LRU-bounded, invalidated by the version
  token. Bounded by **concurrent editors** (tens), not by product count.
- **Frozen revisions and their projections** — immutable, so shareable, replicable and
  **never invalidated**. These belong in a shared store, and they are where the large safe
  win is.
- **Aggregations and workspace listings** — a read model in SQL, not a cache.

And the CPU case is weaker than it looks: no cache at all, at 20,000 assertions and ten
requests a second, is about half a core. Cache when a measured hit rate justifies it, and
delete the cache when it does not.

### Guardrails

- **A projection, not a second model.** Like RDF, the networkx view is derived and
  disposable, never a store of truth. It must not acquire state that can disagree with
  the graph.
- **The analytical core stays in Python.** The 4,200 lines of tested projection code are
  not moving into a query language; this item makes their *lookups* indexed, not their
  logic external.
- **Cache invalidation is the version token**, which `SqliteStore` already returns from
  `save_working`. No new coherence mechanism.

### Acceptance

- A repeated neighbourhood query over one loaded scope does not re-scan every assertion.
- The index is rebuilt when — and only when — the scope's version changes.
- The networkx view is used for at least one algorithm that would otherwise be
  hand-rolled, with a test that pins its direction (see the correction in §10: the first
  benchmark explored the wrong side of the tree and reported a 614× win that was an empty
  traversal).
- Whole-graph projections are unchanged in behaviour and still pass.
