---
id: ADR-0029
legacy: "25"
title: "A C4 specification view: text notation first, a rendering second, well-formedness as the point"
status: accepted
date: 2026-09-27
area: "`app/viewpoints/c4.py`, `app/templates/c4.html`, `app/static/js/mermaid.min.js` (vendored), `/c4`, `/c4/notation/<fmt>`, `/api/c4`"
related: ["YB-012", "YB-006", "YB-024", "YB-052", "ADR-0014", "ADR-0027"]
---

# ADR-0029 — The C4 specification view

> **Record.** Closes YB-025, whose original write-up is in git history. It
> asked for two halves — emit C4 notation, render it beside the view — and left one
> question open: what to do about `DeploymentNode`, which has no C4 level.

### Why this was worth doing at all

`/map` (ADR-0014) draws the whole knowledge graph as a force layout. That answers "what
is in the graph" and cannot answer "what is the system". A force simulation has no
canonical layout, so the same graph draws differently between loads and there is nothing
to diff; arrow routing is incidental; and no architect looking at it would call it a C4
diagram. The gap was not a missing picture — it was a missing *artefact*.

### Decisions

**1. Text is the deliverable; the diagram is a convenience.** Structurizr DSL is emitted
first, C4-PlantUML second, Mermaid third and rendered client-side. The notation is
deterministic — same graph, same text — which is what makes it diffable in
`/changes/diff`, reviewable, and checkable by an external tool.

**2. No external renderer, and no new service.** A Kroki or PlantUML endpoint would have
been one HTTP call, and it would send architecture data to a third party for a picture.
Mermaid is vendored (2.5 MB, v11.4.1) and loaded on `/c4` alone, through a `head` block
in `base.html` — a page-specific asset does not belong in the shared layout.

**3. The view checks C4's own rules and reports what it cannot represent.** Five checks:
nesting, reflexive containment, acyclic containment, connections resolving, and the drawn
level being populated. Every hole is a named count with examples, not an omission. A
diagram is persuasive, so an unstated hole matters more here than anywhere else in the
app.

**4. Failed checks are written into the emitted DSL as comments.** Structurizr refuses a
nesting C4 does not allow. The alternative to a `// WARNING` at the top of the file is a
parse error in someone else's tool with no explanation attached.

**5. `DeploymentNode` is reported, not placed.** Deployment is a separate C4 view, not
L1–L4. Five live nodes — OpenShift, Docker, DataCentres, DataCentre 1, DataCentre 2 —
are listed under `unlevelled` gaps. Inventing a level for them would put platform
constructs inside a container diagram; modelling deployment properly is its own viewpoint
if it is ever wanted.

**6. `/c4` is a real route again, and `/graph` still redirects.** `/c4` was retired
because the old view at that URL was a force layout claiming to be C4. The claim is now
honoured instead of redirected. `/map` stays the whole-graph view, and the two are
different projections of the same graph rather than one replacing the other.

**7. The notation is never read back.** Emission is one-way until a parser exists
(YB-012). Letting a rendered artefact become authoritative with no path back is how two
representations drift, so the constraint YB-025 set is kept.

**8. The model is exposed as data, not only as HTML.** `/api/c4` returns the elements,
the relationships, the checks and the notation, so the verdict can be asserted or
consumed without parsing a page.

**9. A relationship the drawn level cannot represent is reported, not dropped.**
External systems are drawn at every level — a boundary is defined by what it exchanges
with the outside — and anything still undrawable is named on the page and in the Mermaid
source. The first version dropped them silently; that is how a diagram ends up quietly
missing information.

### Consequences

- **The view found real defects on its first live run**, which is the argument for
  decisions 3 and 8. Against `payment_platform_arch.md` in the `async` scope: 23 elements
  at the code level with no component to contain them (all of them quality attributes,
  conventions and one protocol — none is code); 12 labels extracted twice under two
  kinds; and **three `X part_of X` assertions that human review had passed as
  `VERIFIED`**. None of these were visible anywhere else in the app. Root causes are
  [YB-052](../todos/entries/YB-052-reflexive-and-duplicate-extraction.md); the
  review-gate guard against a reflexive assertion is the general fix and is part of it.
- **A live scope can now show zero relationships honestly.** The same run's connections
  pass produced no `Connection` nodes, so the container diagram has no arrows and the
  page says so — rather than looking finished. Without decision 3 that would have read as
  a working view.
- **`/api/c4` changed meaning.** It used to redirect to `/api/map`. Anything calling it
  gets the C4 model now, which is what the name always promised.
- **Nav highlighting was fixed in passing**: the active-tab comparison reduced the
  request endpoint to its prefix but compared it against the full endpoint name, so Map
  and C4 never lit. Both sides are reduced now.
- **A 2.5 MB asset is in the repository.** Deliberate — the alternative is a CDN, which is
  a network dependency and a data egress — but it is the largest vendored file in the
  project, and YB-050's retention thinking applies to repository weight too, not only to
  runtime artifacts.
- **What is still not represented**: deployment (decision 5), and C4 relationship
  *technology* for graphs whose connections carry no protocol. Both are reported.

### Acceptance

| YB-025 asked for | Where it landed |
|---|---|
| Emit Structurizr DSL | `to_structurizr` — deterministic, nested, escaped, warned |
| PlantUML/C4-PlantUML second | `to_c4_plantuml` — `Container_Boundary` for systems with children |
| Render it beside the view | `to_mermaid` + Mermaid client-side, no network |
| The source copyable | notation blocks with copy buttons and `.dsl`/`.puml`/`.mmd` downloads |
| "Must not invent structure to make the notation valid" | gaps + checks, and `DeploymentNode` reported rather than placed |
| "Say what it could not represent" | `gaps` (7 kinds) and 5 well-formedness checks, both on the page and in `/api/c4` |
| "Do not let the notation become the source of truth" | decision 7 |

### Hand-overs

- **[YB-052](../todos/entries/YB-052-reflexive-and-duplicate-extraction.md)** — the three
  defects this view found, and the review-gate rule that would have caught the worst one.
- **[YB-012](../todos/entries/YB-012-c4-notation-parser.md)** — the inverse direction.
  With both, the round-trip becomes possible; the Structurizr output is the natural
  interchange format for it.
- **A deployment viewpoint** — not scheduled. Decision 5 says what it would need to
  answer, and the five live `DeploymentNode`s are the input waiting for it.

### Review history

Design and implementation reviewed against the YB-025 write-up and the repo's layering
conventions (`core/` knows nothing of `app/`; the viewpoint imports only
`app.projections`). Two defects were found by the tests while writing them and fixed
rather than accommodated:

- `_roots` returned the elements that *have* children instead of the elements with no
  parent, so every leaf was emitted at the top level and every boundary came out empty.
- `Connection` nodes were read from the set of C4 elements, which excludes them by
  design, so every relationship silently vanished and the page reported a graph with no
  arrows as if that were a fact about the graph.
