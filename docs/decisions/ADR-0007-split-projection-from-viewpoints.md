---
id: ADR-0007
legacy: "15"
title: "Split graph projection from architecture viewpoints"
status: accepted
date: 2026-09-22
area: "app/projections.py, app/viewpoints/"
related: []
---

# ADR-0007 — Split graph projection from architecture viewpoints

> **Record.** Closed work, preserved verbatim from `TODO.md` v1 (ADR-0007).
> Legacy source: [`docs/todos/legacy-todo-v1.md`](../todos/legacy-todo-v1.md).

**Legacy status:** ✅ IMPLEMENTED — see [`docs/ui-review-workflow.md`](../ui-review-workflow.md) §8
**Legacy priority:** Closed
**Legacy area:** `app/projections.py`, `app/viewpoints/`, `app/templates/c4.html`

---

### The conflation

"Project the graph" and "project the architecture as a C4 view" were one module,
one entry in the docs' module map, and one nav label ("Graph"). They are not the
same thing:

| | Graph projection | Architecture viewpoint |
|---|---|---|
| Question | make the graph readable and judgeable | describe the architecture in a recognised notation |
| Knows about | assertions, confidence, provenance, filters | C4 levels, element kinds, element detail |
| Changes when | the knowledge model changes | the notation, or the views offered, changes |
| Domain-specific | no | yes |

### What changed

1. `app/views.py` → **`app/projections.py`**, renamed for what it is (Flask
   *routes* are the views). Its C4 content was removed.
2. **`app/viewpoints/`** added, holding `c4.py`. The C4 decisions — level→kind
   tables, which facts are element detail, what the renderer receives — now live
   beside the notation they describe.
3. The fused work was split by concern:
   - notation-agnostic → new primitives in `projections.py` (`node_records`,
     `edge_records`, `literal_facts`), which any viewpoint composes;
   - C4-specific → `viewpoints/c4.py`, which now *selects* instead of
     re-deriving. `project_c4_context` → `c4_view`.
4. `/graph` → **`/c4`**, endpoint `c4`, nav label **"C4 view"**. `/graph` is kept
   as a 301 redirect. `/api/graph/c4` → `/api/c4`.
5. `graph.html` → `c4.html`. Its page copy was describing *generic assertion
   flattening* — the projection layer's job — on a page about C4. Rewritten to
   explain what a C4 level is and why it deliberately omits elements.
6. Tests split to mirror the layers: `test_views.py` → **`test_projections.py`**
   plus **`test_viewpoint_c4.py`**, which covers the new primitives.

### Guards against re-fusing

- `test_projection_layer_does_not_own_architecture_notation` — no C4 level tables
  and no import of a viewpoint from the projection layer.
- `test_the_viewpoint_composes_projection_primitives` — the viewpoint must call
  `node_records`/`edge_records`/`literal_facts` and must not walk `graph.active()`
  itself, because a second implementation of assertion flattening is a second
  thing to keep correct.

### Consequence worth keeping

A viewpoint is a **deliberate reduction**: at C4 context level a `Container` is
real, is in the graph, and is not drawn. The view therefore reports
`excluded_kinds`, so "not at this level" is never mistaken for "not in the graph".
The same reasoning is why the direction is one-way — viewpoints compose
projections, never the reverse.
