---
id: ADR-0033
title: "Edge labels on demand — the map stops drawing 285 pieces of text at once"
status: accepted
date: 2026-09-28
area: "`app/templates/map.html`, `app/static/css/sea.css`, `tests/test_app.py`"
related: ["YB-056", "YB-024", "ADR-0029", "ADR-0032"]
---

# ADR-0033 — Edge labels on demand

> **Record.** A readability fix to the knowledge map. The force view drew every link
> label at its midpoint, unconditionally, which on a real graph is a wall of text
> laid over the nodes it is meant to explain. Labels are now revealed by the pointer.
> The larger question — that a force layout is the wrong representation for a C4
> hierarchy — is deliberately left to [YB-056](../todos/entries/YB-056-map-representation-modes.md).

### The measurement that motivated it

From `/api/map` against the real MVP scope (`data/sea_home_01`, `acme_pillar_01`):

| Lens | Nodes | Links | open references |
|---|---|---|---|
| `all` | 147 | **285** | 59 |
| `architecture` | 90 | **170** | 28 |

285 links meant 285 `<text>` elements at 285 midpoints, at 9.5px, on a canvas whose
node labels are the thing a reader actually needs. The labels were not merely
crowded — they were drawn *over* the nodes, because the label group was appended
after the link group and before the nodes, so every edge label competed with the
node it was supposed to describe. The complaint that the view is uninterpretable when
the graph is large is a direct consequence, not an aesthetic preference.

### What changed

**Labels are off by default and revealed on demand.** A label becomes visible when
the pointer is over its edge, over either endpoint, or when it has been pinned by a
click; the `Labels` control turns all of them on for anyone who wants the old
behaviour, and the choice is remembered in `localStorage`. `Escape` releases a pin.

**The hit area is not the line.** A 1.2px stroke is unhoverable, so each link also
draws a transparent 16px line on top of it. It sits above the labels and below the
nodes, so it cannot steal a drag from a node.

**Hovering a node dims everything that is not a neighbour.** This is the change that
does the most for a dense graph: it makes one node's neighbourhood legible without
filtering, so the reader keeps the context that a filter would remove. Adjacency is
precomputed once into a lookup, so the per-frame cost is O(nodes) and not
O(nodes × links).

**Labels carry a halo** (`paint-order: stroke`, white stroke), because a label that
lands on another edge has to read over it.

### A latent bug fixed on the way past

The stylesheet had `.link line { stroke: #94a3b8; }` while the script set the stroke
per link to distinguish a held link (`#cbd5e1`) from an open reference (`#94a3b8`). A
CSS declaration beats an SVG presentation attribute, so **both drew `#94a3b8`** and
the colour half of that distinction had never worked — only the dash pattern
distinguished them. The colour now belongs to the script and the rule owns the width,
with a comment saying why, because this is the kind of rule a later tidy-up
reintroduces.

### What was deliberately not done

- **No automatic "labels on when the graph is small" threshold.** A threshold makes
  the same graph render differently as it grows past a cut-off the reader cannot see
  or predict, and the control is one click away and persistent. A predictable default
  beats a clever one.
- **No filtering or layout change.** Both are real and both are [YB-056](../todos/entries/YB-056-map-representation-modes.md).
  This change is scoped to text, and mixing a representation change into it would
  have made neither testable.
- **`Re-layout` does not clear a pinned label.** Reading and re-running the
  simulation are different concerns; tying them together would make the pin feel
  like state the layout owns.

### Verification

`tests/test_app.py::test_map_edge_labels_are_revealed_on_demand_rather_than_all_at_once`
pins the contract across the two files that have to agree — because there is no JS
runner in this repo, and a revert to unconditional labels would satisfy every other
map test. It asserts the control and its honest default, the hover plumbing, and the
three stylesheet properties that actually do the hiding (`opacity: 0`), the reading
(`paint-order: stroke`) and the colour bug (that `.link line` names no `stroke`).

The generated script is additionally syntax-checked with `node --check` against a
rendered page, which is a manual step and not part of the suite.
