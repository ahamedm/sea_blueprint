---
id: YB-065
legacy: null
title: "A whole-schema view for the ontology page — the 179 range edges, not the 26 is_a ones"
status: open
priority: low
area: "`app/ontology_reference.py` (a whole-model projection beside `class_neighbourhood`), `app/templates/ontology.html` (the canvas that today needs `?focus=`), `app/viewpoints/merged.py` (`map_tree` — the reusable core), `core/ontology.py` (`ClassSpec.is_a` / `.mixins` / `.subsets`, already there)"
created: 2026-10-03
updated: 2026-10-03
design: null
record: null
superseded_by: []
related: [YB-056, YB-064]
blocks: []
blocked_by: []
---

# YB-065 — A whole-schema view for the ontology page

> **Open, low priority.** Filed from the question "can the map's tree view be reused for
> the ontology page?" — the layout can, the renderer cannot, and the measurement says the
> useful view is not a tree at all.

## The gap

The ontology page has a graph canvas, but it is **per class**: it renders only with
`?focus=ClassName`, as a neighbourhood. With no focus the page shows the layer card, the
pack card, the class table and the vocabularies — and no picture of the schema as a whole.
So "show me the shape of this ontology" has no answer today.

## What the schema actually looks like

Measured over `ontology/` (70 classes, 51 enums, 15 subsets):

| | |
|---|---|
| `is_a` edges | **26** |
| mixin edges (multiple inheritance) | 11, across 7 classes |
| roots — no `is_a` | **44 of 70** |
| classes in some hierarchy edge | 36, so **34 would be grouped** |
| `is_a` cycles | 0 |
| **slot `range` edges between classes** | **179** |
| classes per layer | requirements 31, architecture 21, enterprise 7, governance 7, common 4 |

The two numbers that decide the design are **26 and 179**. Inheritance is a small part of
this vocabulary; relationships are most of it. A view whose spine is `is_a` would draw 26
edges and relegate 179 to a "cross-link" count — hiding the majority of the structure in
the one view meant to explain structure.

## Reuse: what carries, what does not

| Piece | Carries? | Note |
|---|---|---|
| `map_tree(nodes, links, hierarchy)` | **yes, directly** | Pure since YB-056's refactor: nodes need `id`/`label`/`kind`, links `source`/`target`, hierarchy `child`/`parent`. No `KnowledgeGraph` dependency to unpick |
| the honesty contract (`cross_links`, `alternates`, `grouped`, `cycles_broken`, stated on the page) | yes | Same reason it mattered on the map, and `alternates` is exactly the mixin case |
| the grouping axis | yes | It is `subsets` / `family_of` — the vocabulary's own classification |
| `hierarchy_records` | **no** | Knowledge-graph specific (walks `graph.active()`). Needs an `ontology_hierarchy(model)` — but its `HIERARCHY_DIRECTIONS` table is the precedent: mixin is genuinely multi-parent, so direction and preference must be declared, not assumed |
| the map's client renderer | **no** | 690 lines inline in `map.html`, coupled to lens/colour/focus/labels/drag/legend. Reusing it means extracting a shared module — a refactor of a working page, with a regression surface, for a page that already has its own renderer |

## The prior art on this page, which the map re-derived

`app/ontology_reference.py`'s module docstring already states the decision the map's tree
mode arrived at a month later:

> *"The class neighbourhood is laid out in **semantic rows**, not by a force simulation:
> supertypes above the focus, subtypes below, relationships and referrers further out.
> Position then means something… Computing the rows server-side also makes the layout
> testable without a browser."*

So the recommendation is to extend **this page's** established pattern rather than import
the map's renderer, and the reuse is of `map_tree`'s generic core.

## Options

1. **Layered whole-schema view (recommended).** Layers as the top structure — the one-way
   import rule is already a card on this page — classes placed within their layer, and the
   **179 range edges drawn as the primary relationship**, with `is_a` as the local spine.
   This is the case where `map_tree`'s cross-link accounting stops being a caveat and
   becomes the subject.
2. **`is_a` tree, reusing `map_tree` as-is.** Cheapest: `ontology_hierarchy` (~15 lines) plus
   an adapter (~20) plus a small renderer. But 44 roots, 34 classes grouped, and 26 of 205
   structural edges on screen. Faithful and honest, and not much use.
3. **Force layout.** This page has an on-record reason against it (arbitrary arrangement,
   a hairball teaches nothing) and the map added a second (27 components, no determinism).
   Not proposed.

## What closes it

A whole-schema view that exists without `?focus=`, states what it is not drawing, places
classes by layer or subset rather than by simulation, leads with the range edges, and is
pinned by a pure-function test over the projection — the same standard this page already
holds itself to.
