---
id: ADR-0036
title: "The home page as an overview — and the two ways a templated Mermaid diagram silently dies"
status: accepted
date: 2026-09-29
area: "`app/templates/dashboard.html`, `app/static/css/sea.css`, `tests/test_app.py`"
related: ["ADR-0033", "ADR-0029", "ADR-0035"]
---

# ADR-0036 — The home page as an overview

> **Record.** The landing page was a status board: it opened with a graph's element
> count and, on an empty workspace, a single "No knowledge yet" card. A reader
> arriving cold learned what their own workspace contained and nothing about what the
> tool is for. It now opens with an overview of the tool — four diagrams and a table
> of what each page does — and keeps the state board below it as "This workspace,
> right now".

## What was added

Two orientation cards (**Why it exists**, **How it earns trust**), four Mermaid
diagrams, and a table mapping every nav destination to the stage it serves and what
it is for.

The diagrams are the journey, the living system, the two-sided graph, and the
ontology layers. Each is grounded in the repo's own documents rather than invented:
`docs/user-journey.md` §4 for the loop, `docs/living-system-architecture.md` for
Initiative against System Baseline against Domain Ontology, and `ontology/README.md`
for the import chain. The **Why it exists** card asks the four questions the reports
actually answer, which is a claim the tool can be held to.

Two decisions about placement:

- **The overview is not conditional on the graph.** It renders on an empty workspace,
  which is where a reader most needs it. The state board keeps its own empty branch.
- **Mermaid is page-scoped.** `base.html` already argued that 2.5 MB must not be
  loaded on pages that draw nothing, so the home page loads it in a `{% block head %}`
  and `test_mermaid_is_loaded_only_where_something_is_drawn` holds both halves of that
  — present here and on `/c4`, absent on the six pages that draw nothing.

## The two traps, both hit

This is the part worth keeping, because both failures are **silent**: the page
returns 200, every other test passes, and the only symptom is that the diagrams are
not there.

**1. Autoescaping breaks the Mermaid source.** The spec was passed into the macro as
a string and printed into the page, so Jinja escaped it:

```
flowchart LR
  A[&#34;1 · Ingest&lt;br/&gt;document → graph&#34;] --&gt; B[...
```

Mermaid received that and reported *"Syntax error in text"* — for **every** diagram,
into a container it appends to `<main>` itself, so the error was not even inside the
element being inspected. The spec now lives in `<script type="text/plain">`, which is
a **raw-text** element: the parser does not decode entities inside it, so a captured
`{% set %}` block arrives exactly as written. It must not be a `<pre>`, which would
decode them.

**2. The obvious fix for that breaks the attribute.** Escaping the spec is fine in a
text node and fatal in a quoted attribute: a `{% set %}` block yields `Markup`, Markup
is not re-escaped on output, and the first inner `"` in the spec then terminated
`data-spec="…"` and truncated the diagram. That intermediate attempt looked correct in
the template and produced a spec 17 characters long.

The same Markup behaviour caused **3.**: the prose fallback was a string argument, so
it printed literal `<strong>` tags at the reader. It is now a `{% call %}` block, whose
markup renders. `test_the_diagram_source_is_raw_and_the_fallback_is_rendered_markup`
pins all three: no `--&gt;` anywhere, no escaped tags, and the fallback's markup
actually rendering.

## Subgraph titles get one line

Mermaid draws a subgraph title straddling the box's top border, so a second line is
overlapped by the box and clips. `Initiative · transient` was unreadable. The titles
are now single short words and the transient/persistent distinction is carried by the
child labels instead — "Proposed" against "Established" — which was doing that work
anyway.

## The fallback is the diagram's equal

`base.html` and the C4 view both take the position that a diagram which fails to
render must degrade to text rather than to a blank box. Every diagram here carries its
own prose fallback, visible before Mermaid runs and hidden only once an SVG has
actually landed. Nothing on the page depends on the script: a browser without
JavaScript gets the same four explanations as prose.

## Verification

Rendered headlessly against `data/sea_home_01` and against a fresh empty store, in
both cases asserting on the dumped DOM rather than on the screenshot: four
`data-diagram` boxes, four canvases holding an `<svg>`, four fallbacks `hidden`, and
no "Syntax error in text" anywhere. The screenshots confirmed the visual result and
found the subgraph-title clipping that the DOM could not.

Four tests in `tests/test_app.py`; `test_app.py` is 78 passed / 1 failed, the failure
being the pre-existing `test_the_two_lists_are_populated_from_the_same_report`.

## Amendment — the Ingest step named a decision it was hiding

The loop diagram drew `1 · Ingest — document → graph`, and the caption said "Ingest a
document". Raised on review: a document could be requirements *or* architecture, and
the diagram said neither.

That is not a cosmetic omission, and the difference is the test to apply to a diagram
like this:

> Does it omit something whose **wrong answer fails silently**?

`doc_type` is `request.form.get("type") or "requirements"` — a **mandatory** choice with
no auto-detection, which selects one of two extraction profiles with non-overlapping
vocabularies, and which is stored on the run because `node_sides` derives a node's side
*from it*. So a wrong pick is not an error: it fills one side, leaves the other empty,
and **mis-assigns sides** for reconciliation and the realization report. Exactly the
failure class this repo keeps flagging as worse than a missing answer.

The diagram now draws **two inputs into one Ingest step** — Requirements document and
Architecture document, `the type picks the profile` — which also reinforces "One graph,
two sides" instead of contradicting it. Caption and prose fallback both say that a wrong
pick is silent.

**The line drawn:** implementation detail a reader must *act on* belongs in the diagram;
internal machinery does not. The four extractor pass names
(`structure`/`connections`/`technology`/`traceability`) stay out — knowing them changes
nothing a reader does.

**Review stays monolithic** (option (a), chosen deliberately). It is genuinely per-side
and incremental — the requirement side can be signed off with no architecture at all,
and reviewed facts survive the later merge (`merge_graphs` routes through the fold rule
so re-extraction cannot destroy a review decision). But that is a *capability*, not a
different shape of flow, so it belongs in the caption rather than in three more nodes.

Re-verified the same way: four canvases holding an `<svg>`, four fallbacks `hidden`, no
"Syntax error in text", and the spec still reaching the browser unescaped. The
screenshots are not decoration here — these diagrams fail silently into their prose
fallback, so the DOM check is what proves they rendered at all.
