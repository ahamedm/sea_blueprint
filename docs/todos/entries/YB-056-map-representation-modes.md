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

### Feasibility, measured 2026-10-03

The scope above (`data/sea_home_01`, `acme_pillar_01`) no longer exists, so those numbers
are unreproducible. These are from the current live scope —
`data/sea_home_x/payment_sys_v2.sqlite`, scope `payments_v2`, 201 nodes.

**Two things are cheaper than this entry assumes.**

- **The layout already ships.** `app/static/js/d3.v7.min.js` is the full d3 7.9.0 bundle:
  `d3.hierarchy`, `d3.tree`, `d3.cluster` and `d3.stratify` are all exported — verified by
  executing the vendored file, not by reading its size. A 3-node hierarchy lays out
  correctly. So: no new dependency, no build step, no `THIRD-PARTY-NOTICES` change.
- **The spanning tree is nearly trivial on today's data.** Exactly **one** node has more
  than one containment parent, and there are **no cycles anywhere**. The cycle-breaking
  this entry budgets for is defensive only — it still needs the synthetic fixture the
  acceptance asks for, because no real data exercises it. And `d3.tree` writes `x`/`y`
  onto the same fields `d3.forceSimulation` writes, so a tree mode can reuse the existing
  render path and simply not start the simulation.

**What containment alone offers — the C4-mode numbers.**

| | |
|---|---|
| containment assertions (`part_of`/`belongs_to`/`composed_of`) | 15 |
| nodes placeable in a containment tree | **16 of 201** |
| ...of the 25 C4-kind nodes | 16 — **9 have no place at all** |
| roots | 2, and they are `Payment Platform` and `Payment Gateway Platform` |
| deepest chain | 3 (System → Container → Component) |

**The binding question is WHICH hierarchy, because this entry is about the whole map.**
Option A as written says "a tree over the containment hierarchy", which reads as the C4 half —
but the map is every kind, and this entry's own table measures the `all` lens first. Measured
against all 201 nodes:

| Basis for the tree | Nodes placed | Invents a relation? |
|---|---|---|
| Containment alone (Option A as worded) | **16 of 201** | no |
| Containment + one hop of any edge | **72 of 201** | no |
| Containment + everything reachable | **142 of 201** | no |
| Grouped by the ontology's own classification (family → kind) | **201 of 201** (33 kinds) | no — a declared classification, not a relation |

So the layout is not the constraint, and neither is containment on its own. The constraint is
that **the map is not connected**: **27 components** — 142 nodes, then 23, 8, 3, 2, … A single
tidy tree cannot represent that without either being a **forest** (d3 draws one tree per root,
which is fine) or inventing a grouping root. And the 59 nodes unreachable from the C4 spine
are not debris: the 23-node component is the domain model (`DomainConcept` →
`ConceptAttribute`), which is its own native hierarchy.

Three consequences, and the first corrects a rule this entry states too strictly:

- **"Any grouping must come from edges the graph holds" needs refining.** Grouping by the
  ontology's own classification is not inventing a relation — it is the axis the map *already*
  colours and filters by (`COLOUR_AXES = family | layer | kind`, `family_of(kind)`), and the
  same 15 `subsets` [YB-064](YB-064-route-the-vocabulary-by-subset.md) proposes to route
  context by. Read strictly, that rule forbids the only basis covering the whole map. What it
  must keep forbidding is a PARENT invented from nothing.
- **The C4 spine can carry satellites, but only via resolved references.** The parenting edges
  available are `applies_technique` 33, `satisfies_attribute` 29, `uses_technology` 27,
  `realizes_quality_attribute` 3 and `implements_requirement` **1 object edge** — the rest of
  the requirement links are value-based references, so a tree builder has to resolve them the
  way the map's reference edges already do rather than read `object` alone.
- **The one multi-parent node IS the ISS-10 defect.** `storefront_management_service` is
  `part_of` both `payment_gateway_platform` and `payment_platform`, which are one system under
  two identities, so the tree would draw it as two roots. Visible, but a reviewer reads two
  root systems as a broken view — which makes
  [YB-053](YB-053-category-elements-and-duplicate-system.md) defect 2 a soft prerequisite for
  a tree that looks correct.

**Consequence for the order.** Filtering (Option B) still comes first, but the tree to aim at
is a **grouped or forest tree over all kinds**, not a containment tree with everything else
relegated to a "could not draw" count. The containment tree is worth having as a *C4 mode
within* the map rather than as the map's tree. A requirement tree remains unavailable —
`Requirement` and `FunctionalRequirement` carry no parent slot, so that population can only be
placed by grouping.

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

**Corrected 2026-10-03: this section undercounted what already exists.** It said the current
controls are "the lens (four fixed groups) and a concept *table* filter that does not touch
the drawing". That omits a filter which landed **three days before this entry was written**
(`eba6bc4`, 2026-09-25): the **quality-concern focus**. `?concern=RELIABILITY` filters the
DRAWING server-side (`quality_focus`, `quality_focus_options` in `merged.py`) and reports what
it hides — `focus_hidden_nodes` and `focus_hidden_kinds`, rendered as *"This filter hides N
node(s) the lens would otherwise draw (…)"*. The lens does the same for the kinds it excludes
(*"Not shown by this lens: …"*).

So the "state what is not being drawn" rule this section asks for **is already implemented
twice**, and the pattern to copy exists. What is missing is only the candidates below: none of
the five shipped. Verified by absence — no `hops`, `min_degree`, `edge_type` or depth limit
anywhere in the map, and the concept-table filter is still client-side and still does not touch
the drawing.

That changes the estimate rather than the direction. Filtering is not a from-scratch build but
an **extension of a server-side filter that already reports its own effect**, which is
precisely the shape candidates 1, 3, 4 and 5 need. The candidates, roughly cheapest first:

1. **Focus + n hops.** Click a node, keep it and its neighbours within *n*, drop the
   rest. The adjacency walk already exists in the map script for hover-dimming; this
   is the same data with the layout re-run. Natural extension of the existing focus.
2. **Depth limit on the containment tree.** "Containers only", or "stop at level 3" —
   natural for C4, and natural for the tree layout in Option A.
3. **Degree threshold.** Hide nodes with fewer than *k* facts; the concept table is
   already sorted by fact count, so the cut is explainable rather than arbitrary, and
   the page must say how many it is hiding (the table's truncation hint and
   `focus_hidden_nodes` are both precedent).
4. **Edge-type filter.** Links vs open references, or one predicate family at a time
   (`part_of` / `connects_to` / `implements_requirement`). The distinction is already
   in the payload as `reference`, and 59 of 285 links are a different kind of thing.
5. **Kind/group checkboxes** on top of the lens — the closest to what `excluded_kinds`
   already does.

Whichever ships, **the page must state what is not being drawn** — a filter over a
silently truncated graph answers "this is not in the graph" for a node that is. That rule
is already applied to the concept table, to the focus, and to the lens; it has to apply to
whatever is added next.

### Implemented, 2026-10-03 — tree mode, laid out on the server

`?mode=force|tree`, with `force` still the default so nothing regresses.

| Layer | Change |
|---|---|
| Projection | `hierarchy_records` in `app/projections.py` — the parent/child links the graph holds, normalised to `(child, parent)` through a `HIERARCHY_DIRECTIONS` table, because `part_of` names the child first and `contains` names the parent first |
| Viewpoint | `map_tree(nodes, links, hierarchy)` — a laid-out forest plus the counts of everything it could not draw as a tree edge |
| Route | `?mode=`, with an unknown value falling back to force |
| Client | `draw()` extracted from the tick handler; tree mode skips the simulation, places the server's coordinates, and fits the viewBox to the tree canvas |
| Tests | `tests/test_map_tree.py`, assigned to the views area: a cycle, a multi-parent node, a reference-only node, determinism, non-overlap, bounded width, and the mode switch end to end |

**One deliberate deviation from Option A: the layout is computed on the SERVER, not by
`d3.tree`.** Option A proposed the d3 layout and d3 does ship it — the vendored 7.9.0 bundle
exports `hierarchy`, `tree`, `cluster` and `stratify`. It moved because of this entry's own
acceptance criterion: *"the layout is deterministic for a given graph"* is something a pure
function can be TESTED for, and a browser-side layout cannot be tested here at all, because
the suite has no browser. So structure *and* coordinates come from Python, where non-overlap
and determinism are assertions, and the client only places what it is given. The
`app/static/js/` layout module this entry's `area` anticipated was therefore not needed.

Measured on the live scope (`payments_v2`, 201 nodes), `?mode=tree`:

| | |
|---|---|
| nodes placed | 201, plus 8 family placeholders |
| hierarchy edges drawn | **15** |
| nodes grouped by family | **183** |
| cross-links not drawn as tree edges | 364 |
| forest roots (components) | 11 |
| alternates — a second parent | 1, the ISS-10 duplicate pair |
| cycles broken | 0 |
| canvas | 1704 × 4344 |

**15 is the number that matters.** On today's graph the tree is mostly a *shelf*, not a
hierarchy — and the page says so instead of implying otherwise, which is the containment
coverage finding above seen from the other end. The layout was never the constraint. The
grouped families also wrap into bounded blocks rather than one row: 183 leaves in a line is
a 26,000-unit canvas, technically tidy and unreadable at any zoom.

**Not done: the filtering half (Option B).** The mode is the representation; those five
candidates remain open, and this entry stays open for them.

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
