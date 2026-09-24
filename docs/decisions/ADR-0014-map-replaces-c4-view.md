---
id: ADR-0014
title: "The map replaces the C4 view — one graph, both sides, references visible"
status: accepted
date: 2026-09-24
area: "app/viewpoints/merged.py, app/templates/map.html, app/__init__.py, app/templates/base.html"
related: ["YB-024", "YB-025", "YB-012", "ADR-0007"]
---

# ADR-0014 — The map replaces the C4 view

> **Record.** Closes [YB-024](../todos/entries/YB-024-requirements-graph-view.md).

**Legacy item:** 24
**Status:** implemented, verified end-to-end against the saved fixtures
**Area:** `app/viewpoints/merged.py` (new), `app/templates/map.html`,
`app/__init__.py` (routes), `app/templates/base.html` (nav)

---

### What was wrong

`/c4` was the only graph view and it drew **only architecture elements**. Its own
empty state admitted it:

> *"This graph has no nodes at the C4 element kinds (…). Architecture extraction
> produces these; requirements extraction produces requirement and business-context
> nodes instead."*

Measured on the requirements fixture, the view returned `nodes == []`. So a
requirements-only graph — step 2 of the user journey, and every verified assertion
in a requirements run — had **no view whatsoever**: `/review` and `/gaps` listed its
assertions and nothing ever drew the graph those assertions formed.

The item was filed as "add a requirements viewpoint, parallel to `c4.py`". It was
implemented as **replace `c4.py`**, because the diagnosis was slightly wrong: the
missing requirement view was the symptom, and the cause was that one view had been
named after a notation and then restricted to one side of the graph. Adding a second
view would have left the first one still claiming to be the graph.

### What was built

`app/viewpoints/merged.py` — a viewpoint over the whole knowledge graph:

- **Every node kind is drawn.** Each kind is assigned to one of three layers
  (`business`, `requirements`, `architecture`), which is what the renderer colours
  by. The extraction fallbacks (`Concept`, `ExternalReference`) are placed rather
  than left homeless — a node visible in the graph and invisible in every view of it
  is the failure being repaired.
- **Lenses, not levels.** `all` (default), `traceability`, `business`,
  `requirements`, `architecture`. C4's level concept is kept as a filter over one
  map, which is the honest name for it now, and `excluded_kinds` still reports what
  a lens hid.
- **Cross-graph references are drawn.** `implements_requirement`, `traces_to_goal`
  and friends are stored by ingest as literals, deliberately, so
  `edge_records` — which flattens only node-valued objects — cannot see them. A
  reference bound by citation of a requirement's own key names a node without
  holding its id, so it was invisible too. Both are now edges, drawn dashed, with
  `open` distinguishing the ones nothing answers.
- **An unresolved reference is drawn as a dangling stub** to a synthetic
  `ref:<text>` node, not omitted: the assertion is in the graph, and an assertion
  that exists and cannot be seen is the gap this view exists to close.

`/map` is the route; `/c4` and `/graph` are 301 redirects; the nav label is "Map".

### Decisions worth keeping

1. **`all` is unrestricted, not a union.** `LENSES["all"] is None` so a node kind
   added tomorrow appears in the default view without anyone remembering to
   register it. A default that silently drops a new kind is the same class of bug as
   the C4-only view, one refactor later. The narrower lenses are where a kind must
   be classified, and `unclassified_kinds` reports any that were not.
2. **Only `CROSS_GRAPH_PREDICATES` become reference edges.** Found while testing:
   the first implementation drew every assertion whose *text* resolved to a node, so
   `element_type: Container` and a `part_of` naming a parent became relationships —
   a property of an element rendered as a link, duplicating what the projection
   layer already emits.
3. **One edge per `(source, target, predicate)` across both sources.** An assertion
   holding a node id and one citing the same node by text are the same relationship
   said twice. `edge_records` is passed first, so the shape that survives is the
   real node-to-node edge.
4. **C4 is not this view.** A force layout has no canonical layout and produces no
   stable artefact, which is what a specification needs. Rendering C4 as C4 remains
   [YB-025](../todos/entries/YB-025-c4-specification-view.md), now with no
   conflicting claim on the name.

### Measured

Through the real app on the saved fixtures:

| Graph | `/map` before | `/map` now |
|---|---|---|
| Requirements only | `nodes == []` (page said "no C4 elements") | 69 concepts, 72 links, layers business 10 / requirements 58 / architecture 1 |
| Both documents | architecture only | 99 concepts, 137 links, **22 cross-graph references, 13 of them open** |

Lens filtering verified for all five lenses; unknown lens falls back to `all`;
empty working set renders the empty state; `/c4`, `/graph`, `/api/c4` all redirect.

### Retired

- `app/viewpoints/c4.py` and `C4_LEVELS` — the selection it encoded was one side of
  the graph, and the level concept now lives as a lens.
- `app/templates/c4.html` → `map.html`.
- The fusion guards in `test_projections.py` and `test_ontology_reference.py` keep
  their teeth, retargeted from `C4_LEVELS` to `LENSES`.
