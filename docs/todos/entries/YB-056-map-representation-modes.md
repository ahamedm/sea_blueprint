---
id: YB-056
legacy: null
title: "A second representation for the knowledge map — hierarchy over force, and real filtering"
status: open
priority: medium
area: "`app/templates/map.html`, `app/viewpoints/merged.py` (the projection a layout reads), `app/static/js/` (a layout module), `app/__init__.py` (a `/?mode=` route parameter), `app/static/css/sea.css`"
created: 2026-09-28
updated: 2026-09-28
design: null
record: null
superseded_by: []
related: [YB-024, YB-055, ADR-0029, ADR-0032]
blocks: []
blocked_by: []
---

# YB-056 — A second representation for the knowledge map

> **Open.** Filed after the edge-label work that made the *force* view readable
> enough to see what it still cannot show. That work is recorded in
> [ADR-0033](../../decisions/ADR-0033-map-edge-labels-on-demand.md). This entry is
> the larger question it deliberately left alone: a force-directed blob is one
> representation of a graph, and for a C4 hierarchy it is close to the worst one.

### The measurement

Against the real MVP scope (`data/sea_home_01`, scope `acme_pillar_01`), from
`/api/map`:

| Lens | Nodes | Links | of which open references |
|---|---|---|---|
| `all` | **147** | **285** | 59 |
| `architecture` | **90** | **170** | 28 |

285 links on one canvas is what produced 285 labels at 285 midpoints. Hiding them
until hover fixed the *text*; it did nothing about the layout, which is the part
that makes the picture hard to read. In a force layout:

- **Position carries no meaning.** Two nodes are adjacent because the simulation put
  them there. Distance suggests relatedness and does not measure any.
- **Hierarchy is invisible.** C4 *is* a hierarchy — SoftwareSystem → Container →
  Component → CodeElement — and the containment edges are enforced now
  ([ADR-0032](../../decisions/ADR-0032-aspect-guards-container-component-integration.md)),
  so the tree exists in the data. A force layout draws it as a hairball and throws
  the depth information away.
- **There is no "how deep is this?"** question a reader can answer by looking.

### Option A — a tidy tree over the containment hierarchy

[D3's tree layout](https://observablehq.com/@d3/tree/2) (`d3.tree` / `d3.hierarchy`)
gives a deterministic, non-overlapping layered drawing: parent above child, siblings
side by side, depth legible as vertical position, and the same input always produces
the same picture. For "show me the Containers, and what is inside each", that is
strictly better than any force layout.

**The honest obstacle: the graph is not a tree.** A tidy tree requires each node to
have exactly one parent, and this graph does not promise that:

- a Component may be `part_of` a Container while also being referenced from elsewhere;
- the containment predicates are a set (`part_of`, `belongs_to`, `composed_of`), and
  `check_containment` flags a *missing* parent — not a second one;
- cross-graph references (`implements_requirement`, `traces_to_goal`) and the 59 open
  references are edges to nodes that are not children;
- `_resolve` reuses a node by label across sides, so one node can legitimately sit on
  both the requirement and architecture side.

So `d3.hierarchy` would need a **spanning tree** chosen deliberately: prefer
`part_of`, break cycles, and pick one parent per node (the shallowest, or the one on
the same side). Everything not in the spanning tree is then a **cross-link** and has
to be drawn as an arc rather than dropped — dropping it would hide exactly the
traceability this platform exists to expose (`ADR-0029`'s concern, one representation
over).

**[H] Option A′ — a layered DAG rather than a tree.** If a spanning tree loses too
much, a Sugiyama-style layered layout (`d3-dag`) keeps multiple parents and still
gives layers and no overlap. More work than A, and it answers the same question.

### Option B — filtering and limiting, which is orthogonal and probably first

The current controls are the lens (four fixed groups) and a concept *table* filter
that does not touch the drawing. That is not enough to make 147 nodes legible. The
candidates, roughly cheapest first:

1. **Focus + n hops.** Click a node, keep it and its neighbours within *n*, drop the
   rest. The adjacency walk already exists in the map script for hover-dimming; this
   is the same data with the layout re-run.
2. **Depth limit on the containment tree.** "Containers only", or "stop at level 3" —
   natural for C4, and natural for the tree layout in Option A.
3. **Degree threshold.** Hide nodes with fewer than *k* facts; the concept table is
   already sorted by fact count, so the cut is explainable rather than arbitrary, and
   the page must say how many it is hiding (the table's truncation hint is the
   precedent).
4. **Edge-type filter.** Links vs open references, or one predicate family at a time
   (`part_of` / `connects_to` / `implements_requirement`). The distinction is already
   in the payload as `reference`, and 59 of 285 links are a different kind of thing.
5. **Kind/group checkboxes** on top of the lens.

Whichever ships, **the page must state what is not being drawn** — a filter over a
silently truncated graph answers "this is not in the graph" for a node that is. That
rule is already applied to the concept table; it has to apply to the canvas.

### What is explicitly not proposed

- **Replacing the force view.** It shows clusters and the overall shape, which the
  tree cannot. This is a second MODE, not a rewrite — `?mode=force|tree` on the map
  route, with the existing drawing as the default so nothing regresses.
- **A layout that invents hierarchy.** Any grouping must come from edges the graph
  holds. Inventing a parent so the picture looks tidy is the failure mode ADR-0029
  was written about.
- **Animating between modes.** Out of scope, and a plausible way to make a slow page.

### Acceptance

- A second representation selectable from the map page, with the current force view
  unchanged as the default and no route breaking.
- The tree mode states the edges it could not draw as tree edges, and the reason
  (spanning-tree choice), rather than silently omitting them.
- Filtering that states its own effect: "showing 42 of 147 concepts — 105 hidden by
  depth ≤ 2".
- The layout is deterministic for a given graph: same input, same picture, so two
  screenshots of one graph are comparable. The force view can never offer this.
- Pinned by tests in `tests/test_app.py` (the route and the mode switch) and, for the
  spanning-tree choice, a pure-function test over a fixture with (a) a cycle, (b) a
  multi-parent Component and (c) a node reachable only through a reference.
