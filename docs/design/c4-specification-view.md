# The C4 specification view

**Status:** designed and implemented — closes YB-025, recorded in
[ADR-0029](../decisions/ADR-0029-c4-specification-view.md).
**Code:** `app/viewpoints/c4.py`, `app/templates/c4.html`, `app/static/js/mermaid.min.js`.
**Related:** [YB-012](../todos/entries/YB-012-c4-notation-parser.md) (notation *in* — the
inverse of this), [YB-006](../todos/entries/YB-006-c4-structurizr-importer.md),
[ADR-0014](../decisions/) (`/map`, the force layout this is not).

---

## 1. What this is, and what it is not

`/map` draws the whole knowledge graph as a D3 force layout. That is the right picture
for exploring a graph and the wrong one for specifying a system: a force simulation has
no canonical layout, so the same graph draws differently between loads, arrow routing is
incidental, and there is no artefact a human can diff or attach to a design document.

`/c4` reduces the same graph to a **C4 model** and emits it as **text**:

| Output | Why it exists |
|---|---|
| Structurizr DSL | the authoritative artefact — the most structured C4 source, and the one that could be read back if YB-012 lands |
| C4-PlantUML | for teams whose tooling is PlantUML; two `!include` lines and a `Rel(...)` per relationship |
| Mermaid | what the page renders, client-side, with no network call and no new server dependency |

The diagram is a convenience. The text is the deliverable: it is deterministic, so
`/changes/diff` can diff it; and it is validateable by an external tool, which is a
genuinely independent check on extraction quality.

Nothing leaves the deployment. The recommendation in YB-025 ruled out a Kroki or
PlantUML endpoint for exactly that reason: architecture data is not something to POST to
a third party for a picture, and a self-hosted renderer is a new service to run.

## 2. The reduction, and the decisions in it

### 2.1 Which nodes are C4 elements

An allow-list of structural kinds (`_STRUCTURAL_KINDS`). Everything else in the graph —
requirements, business goals, quality attributes, design techniques, conventions,
technology stacks — is **excluded and counted**, and the count is on the page. That is
the right exclusion: those are real knowledge, and they are not boxes in a C4 diagram.
But it is an exclusion *this view made*, so it is stated rather than silent.

### 2.2 Which level an element sits at

`c4_level` (stated by the extraction) first; the node kind second. The fallback exists
because of what the live graph actually contains: the system under design was classified
`Platform`, so requiring a `SoftwareSystem` found no system at all and the diagram was
drawn around an empty middle. Elements whose level was *inferred* are reported in
`gaps` as `inferred-level` — a diagram drawn from an inference must say so, because a
wrong classification then shows up as a box at the wrong level and looks deliberate.

### 2.3 Which system the diagram is about

The element containing the most others, tie-broken by label. Not "the parentless
`SoftwareSystem`": systems carry `part_of` links to each other, and *parentless* is a
property of a tree, not of a graph. On the live graph that rule returned nothing.

### 2.4 Relationships

A `Connection` node is preferred, because it carries `protocol` and `style` — what a C4
arrow is annotated with. A bare `connects_to` triple is the fallback, so a graph written
before Connection nodes existed still draws its arrows. A `Connection` is **not an
element**: no level, no boundary, never a box.

### 2.5 `DeploymentNode` — the question YB-025 explicitly left open

**Reported, not guessed.** Deployment is a different view in C4 (deployment diagrams),
not L1–L4, so `DeploymentNode` has no level and is listed under `unlevelled` gaps with
that sentence attached. On the live graph that is five nodes: `OpenShift`, `Docker`,
`DataCentres`, `DataCentre 1`, `DataCentre 2`. Modelling deployment properly is a
separate viewpoint if it is ever wanted; inventing a level for it now would put
platform constructs inside a container diagram.

## 3. Rolling up, so a higher diagram is not an emptier one

A diagram is drawn at one level. A relationship whose endpoints sit below it is
re-pointed at the ancestors that *are* drawn, and counted. A relationship whose two ends
collapse to the same ancestor is dropped — it is coupling inside one box, which the
box's own diagram shows. The count of rolled-up relationships is printed in the
diagram's header and as a Mermaid comment, so an arrow that means "something inside
this" says so.

## 4. Well-formedness: the half a picture cannot tell you

This is the part that earns the view its place. Five rules C4 actually states are
*checked* against the graph, each reporting `holds`, counts, and up to five named
examples:

| Check | Rule |
|---|---|
| `nesting` | a container belongs in a system, a component in a container, code in a component |
| `reflexive` | nothing is part of itself |
| `acyclic` | containment is a tree, not a cycle |
| `connections` | every connection joins two elements in the model |
| `populated` | the system has at least one container |

Structurizr refuses the nesting `nesting` reports, so every failed check is also written
into the emitted DSL as a `// WARNING` comment: a parse error in someone else's tool then
arrives with its cause attached instead of unexplained.

**Why `reflexive` is its own check.** The first live run of this view found three
`X part_of X` assertions — `Payment Platform`, `Payment Processing Platform`,
`Payment Settlement and Processing Platform` — all `VERIFIED`, all with
`source_type=HUMAN_REVIEWER` from a bulk verify. A self-referential containment makes its
element a containment *root*, so it silently becomes a boundary in the notation rather
than a child of one. The general fix is a review-gate rule that a reflexive assertion is
never valid; that is [YB-052](../todos/entries/YB-052-reflexive-and-duplicate-extraction.md),
not this item.

## 5. What the first live run found

Run against `data/sea-deepseek` scope `async`, document `payment_platform_arch.md`:

| Finding | Count | Where it is reported |
|---|---|---|
| Code-level elements with no component to sit in | 23 | `nesting` |
| Reflexive `part_of`, survived human review | 3 | `reflexive`, `acyclic` |
| The same concept extracted twice under two kinds | 12 | `duplicate-of-concept` gap |
| `DeploymentNode` with no C4 level | 5 | `unlevelled` gap |
| Structural elements with no stated level | 4 | `inferred-level` gap |
| Runs behind the graph not COMPLETE | 2 of 2 | `run-completeness` gap |
| Connections | 0 | `relationships` count |

The code level is the sharpest of these. All 23 elements at L4 are quality attributes,
design techniques, engineering conventions, security measures and one protocol
(`TLS 1.2+`) — not one is a class or a function. Twelve of them are the *same label* that
also exists as a `DesignTechnique`/`EngineeringConvention`/`ArchitectureStyle` node,
excluded from the model. So the extraction is both over-classifying concepts as
`CodeElement` and duplicating them across kinds.

That is a finding about extraction, not about this view — but it was found *by* this view,
and it is only visible here, because deciding which kinds are structural is what makes a
duplicate visible at all. Both halves are [YB-052](../todos/entries/YB-052-reflexive-and-duplicate-extraction.md).

## 6. Failure modes, and what the page does about them

| Failure | Behaviour |
|---|---|
| mermaid.js missing or the render throws | the notation blocks stay on the page and the box says the diagram could not be rendered — the deliverable is text, so a failed render is a degraded view, not a lost one |
| `?level=contexts` (a typo) | reported as not a level, with the valid ones listed; no silent fallback, because a typo and a missing level would look identical |
| Empty graph | "Nothing to specify", with the reason a requirements-only graph is the expected case |
| Graph holds no element at the chosen level | the empty diagram is drawn as empty and said so; hiding the level would hide a fact about the graph |
| 2.5 MB of Mermaid | loaded on `/c4` only, via a `head` block in `base.html` — not on every page |

## 7. Deliberate non-goals

- **Not the source of truth.** The notation is emitted from the graph and never read
  back. Letting a rendered artefact become authoritative while there is no parser is how
  two representations drift (YB-025's constraint, and the reason YB-012 is complementary
  rather than a dependency).
- **No external renderer.** No Kroki, no PlantUML server, no data egress.
- **No layout engine.** Structurizr's own `autolayout` and Mermaid's own layout are used;
  this view never computes positions.
- **No deployment notation.** Reported as unrepresentable instead (§2.5).
