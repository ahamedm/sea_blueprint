---
id: YB-025
legacy: "25"
title: "Dedicated C4 specification view — text notation plus rendered diagram"
status: open
priority: medium
area: "new `app/viewpoints/c4_spec.py` (or a `notation/` renderer), `app/templates/`, possibly a Kroki/PlantUML endpoint"
created: 2026-09-23
updated: 2026-09-24
design: null
record: null
superseded_by: []
related: ["YB-012", "YB-024"]
blocks: []
blocked_by: []
---

# YB-025 — Dedicated C4 specification view — text notation plus rendered diagram

> **Open work.** This file is the source of truth for this item; `TODO.md` is generated from it.

**Legacy status:** Not started
**Legacy priority:** Medium — clear value, no blocker
**Legacy area:** new `app/viewpoints/c4_spec.py` (or a `notation/` renderer), `app/templates/`, possibly a Kroki/PlantUML endpoint

---

### What is missing

> **Updated after YB-024.** The route this item needs is now free. The old `/c4` was
> a D3 force layout of *architecture elements only*, and it was replaced by `/map`
> (ADR-0014) — a map of the whole knowledge graph that deliberately does **not**
> claim to be C4. So this item is no longer "make the existing view more C4-like";
> it is a new view, with no conflicting claim on the name and no incentive to keep
> the force layout as a fallback.

There is currently **no C4 view at all**. `/map` is a **D3 force layout drawn from
the graph**. That is good for exploring the graph and poor as a *specification*: a
force simulation has no canonical layout, so the same graph draws differently
between loads, arrow routing is incidental, and there is no stable artefact a human
can review, diff, or attach to a design document. Nothing about it looks like a C4
diagram to an architect who expects one.

There is also **no text notation output at all**. An architecture document is
conventionally carried as Structurizr DSL, PlantUML/C4-PlantUML, or Mermaid, and the
project can produce none of them.

**This is the inverse of YB-012.** YB-012 parses C4 notation *in*; this renders notation
*out*. They are independent: YB-012 could land without this, and this without YB-012,
which is why it is a separate item rather than a bullet under it.

### Shape

Two halves, either of which is useful alone:

1. **Emit notation** from the graph — Structurizr DSL first, since it is the most
   structured C4 source and the cleanest to generate; PlantUML/C4-PlantUML second for
   ubiquity. Deterministic: same graph, same text, which is what makes it diffable.
2. **Render it** beside the view — a diagram image plus its source, with the source
   copyable. Rendering options, cheapest first:
   - an external Kroki/PlantUML endpoint (one HTTP call, but sends the graph to a third
     party — a real consideration for architecture data),
   - a self-hosted Kroki or PlantUML container (same interface, no data egress),
   - pure client-side (Mermaid via `mermaid.js`, which is bundled-able and needs no
     network).

Recommended: **generate Structurizr DSL, render Mermaid client-side, and offer the
PlantUML text for copy/paste.** That gives a real diagram with no new server dependency
and no data leaving the deployment, and keeps the text as the authoritative artefact.

### Why text-first matters here

The rendered diagram is a convenience; the notation is the deliverable. A text
representation is diffable in `/changes/diff`, reviewable in the review gate, and stable
across runs — none of which the D3 layout is. Emitting notation also makes the "is this
graph structurally sane?" question answerable by an external tool, which is a genuinely
independent check on extraction quality.

### Constraints

- **The graph is not always a complete C4 model.** Requirements-only graphs have no
  elements to emit (YB-024 covers that); partially-extracted graphs will emit
  incomplete notation. Emission must not invent structure to make the notation valid —
  it should say what it could not represent (unresolved references, elements with no C4
  level, nodes of unmapped kinds).
- **`DeploymentNode` has no C4 level** and is deliberately excluded from L1–L4. Emission
  needs a decision: model it as deployment notation, or report it as unrepresentable.
- Do not let the notation become the source of truth for the graph while there is no
  parser to read it back — that asymmetry is how the two representations drift.

### Related

- **YB-012** — C4 notation as *input*. Complementary, not the same work.
- **YB-024** — closed. The map draws both sides of the graph; this item draws one
  notation properly. `/map?lens=architecture` is the closest thing today.
- Structurizr DSL is also the natural interchange format if YB-012 later needs a
  round-trip, which is an argument for generating it before PlantUML.
