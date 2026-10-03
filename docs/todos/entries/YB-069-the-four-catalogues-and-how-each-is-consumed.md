---
id: YB-069
legacy: null
title: "Catalogues — Principles, Policies, Technology Radar, Strategies — injected into the pass that needs each"
status: open
priority: medium
area: "`core/patterns.py` (the shape to generalise into `core/catalogues.py`), `ontology/catalogues/` (the instance files), `agents/*/passes.py` (the injection seam), `core/knowledge/` (the curated-load path YB-047 specifies)"
created: 2026-10-03
updated: 2026-10-03
design: null
record: null
superseded_by: []
related: [YB-047, YB-068, YB-066, YB-007]
blocks: []
blocked_by: []
---

# YB-069 — The four catalogues, and the fork that decides how each is consumed

> **Open.** Filed from the stated intent: a Catalogue of Principles, Policies, Technology
> Radar and Strategies, injected into the relevant passes at the right time.

## The mechanism already exists — it has one participant

`ontology/catalogues/` holds `architecture_patterns.yaml`, and the pattern catalogue is
injected exactly the way this intent describes:

- `DesignAssistantAgent._catalogue()` loads it **per run**, from
  `config.pattern_catalogue` with a default fallback
- `design_passes(pattern_prompt_context(catalogue), quality_attributes=…)` binds it into
  **the one pass that needs it** — `design_pattern_pass` appends it to its own instructions
- `canonical_pattern` resolves what the model emitted back onto an entry, so a near-miss name
  becomes the catalogue's name rather than a new node
- a missing file degrades to an empty catalogue with `findings`, never a crash

**And the /ask work already added a second**: `ontology/catalogues/questions.yaml`, with
`core/questions.py` mirroring the same shape. So the directory, the loader convention, the
prompt-context renderer and the degrade-to-empty behaviour are established across two
participants — the question is what the next four should look like, and there is one fork that
decides it.

## The fork: a catalogue is either VOCABULARY or INSTANCES

This is the part worth deciding per catalogue rather than once, because the two are consumed
by different machinery and conflating them is how a catalogue becomes a second source of
truth.

| | Vocabulary (in the prompt) | Instances (in the graph) |
|---|---|---|
| What it is | the allowed names for something the model describes | the authoritative artefacts themselves |
| How it is consumed | rendered into one pass; the model re-emits a name; resolution maps it onto an entry | loaded directly, **no model in the loop** |
| Provenance | whatever asserted it (`EXTRACTION_AGENT`) | `SOURCE_IMPORTED`, which already exists |
| Precedent | `architecture_patterns.yaml` | `docs/decisions/ADR-*.md` via `decisions.py` — the same shape YB-047 specifies |

| Catalogue | Primary use | Why |
|---|---|---|
| **Technology Radar** | **vocabulary** | it classifies technologies the extraction finds. `TechnologyStack` / `TechnologyCategory` / `TechnologyRing` already exist, and `TechnologyRing`'s values reach **no prompt at all** today — this is the concrete gap it closes |
| **Principles** | instances (+ vocabulary for linking) | authoritative, curated: YB-047's decision is a direct load, not extraction |
| **Policies** | instances (+ vocabulary for linking) | same, and the `Policy → Control → StandardClause` spine is only walkable once they are nodes |
| **Strategies** | instances | same layer, same argument |

So the radar is the odd one out: it is vocabulary for a pass that already exists. The other
three are authority that has to enter the graph before anything can reference it.

## The economics, which decide where a catalogue may be injected

Per-pass injection is cheap; the shared block is not. Measured: `pattern_prompt_context` is
**982 chars into one pass**, while the shared ontology context is **5,272 of ~7,374** chars of
scaffolding and is the dominant term in ISS-15's ratio.

**The rule to keep: a catalogue goes into the pass that needs it, never into the shared
block.** Four catalogues in the shared context would tax every pass of both profiles, which is
the YB-007 failure this repo already measured once. Each catalogue also needs its own bound
rather than a raised global one.

## What is actually missing

1. **A generic catalogue shape.** `core/patterns.py` is pattern-specific (`PatternEntry`,
   `canonical_pattern`, `pattern_prompt_context`). Four more implementations would be four
   copies of a loader, a renderer, a resolver and a validator. The shape is now proven three
   times (`patterns.py`, `decisions.py`, `questions.py`) and should be generalised once.
2. **The curated-load path.** YB-047: *"a path the codebase does not have: a direct load from a
   curated instance file into the graph, no model in the loop — same `add_assertion` fold, same
   review gate, provenance `SOURCE_IMPORTED`."* `SOURCE_IMPORTED` exists; the loader does not.
3. **Resolution per catalogue.** A pass that names a principle, policy or technology must be
   resolved onto the catalogue entry the way `canonical_pattern` does, or the model mints
   near-duplicates — the exact thing the pattern aliases exist to prevent.
4. **The binding into the right pass**, per catalogue: the radar into the technology pass, the
   principles/policies into the pass that links elements to controls (which does not exist —
   it is YB-047's deferred conformance slot).
5. **A guard per catalogue**, on the pattern of the questions registry's own tests: a shipped
   catalogue must validate against the engines/classes it names, and its prompt context must
   not leak into the shared block.

## Sequencing

1. **Technology Radar** — it is vocabulary for an existing pass, its values currently reach
   nothing, and it needs no new graph path. Cheapest useful step by some margin.
2. **Generalise the shape** (`core/catalogues.py`) while there are three implementations to
   fold in rather than six.
3. **The curated-load path**, then Principles / Policies / Strategies as instances — YB-047's
   work, with the conformance slots it defers.
4. **The linking pass**, so an element can assert which control it realises. Until that exists
   the governance catalogues are reference data: loadable, queryable, and unlinked.

## Relationship to what is already filed

- **[YB-047](YB-047-enterprise-governance-layer.md)** owns the governance layer, the curated
  load and the deferred conformance slots. This entry is the *catalogue mechanism* those three
  share; it does not restate the layer.
- **[YB-068](YB-068-governed-by-rules-is-literal-on-a-node-ranged-slot.md)** is the same fork
  already misfired once: `governed_by_rules` names a `BusinessRule` and holds a string. A
  catalogue gives that slot something real to point at.
- **[YB-066](YB-066-natural-language-enquiry.md)** contributed `questions.yaml`, and its
  `governance.principles` entry answers `substrate_absent` until this lands.

## What closes it

The generic shape, at least the radar injected into its pass, the curated load, one governance
catalogue loaded as instances with provenance `SOURCE_IMPORTED`, and a test per catalogue that
it resolves rather than duplicates.
