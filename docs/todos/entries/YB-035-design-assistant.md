---
id: YB-035
legacy: null
title: "Design Assistant — draft an initial architecture from REQ-G, on request or on an event"
status: done
priority: medium
area: "`agents/design_assistant/`, `core/patterns.py`, `core/knowledge/digest.py`, `core/knowledge/drafts.py`, `app/templates/design.html`"
created: 2026-09-25
updated: 2026-09-25
design: docs/design/design-assistant.md
record: docs/decisions/ADR-0017-design-assistant-proposes-arc-g.md
superseded_by: []
related: ["YB-033", "YB-034", "YB-037", "YB-018", "YB-009", "YB-004", "ADR-0011"]
blocks: []
blocked_by: []
---

# YB-035 — Design Assistant

> **Closed.** The record is
> [`ADR-0017`](../../decisions/ADR-0017-design-assistant-proposes-arc-g.md) and the long
> analysis is [`docs/design/design-assistant.md`](../../design/design-assistant.md).
> The write-up below is preserved as it stood when the item was written; the two
> questions it called load-bearing are annotated inline with what was built.
>
> **The event trigger is NOT done and was deliberately split out.** This item
> implemented the UI path only; [YB-033](YB-033-event-ingress.md),
> [YB-034](YB-034-initiative-delivery-phase.md) and
> [YB-037](YB-037-background-workflow-management.md) own running it from an event.
> The agent is a plain `run(input_data)` and does not care who calls it.

### Why

The PRD's step 5 is *ARC-G Initiation — Solution Shaping*: the agent reads a
verified REQ-G and proposes foundational components, the architect refines them.
Capabilities D1–D3 are written down. `agents/design_assistant/__init__.py` was a
docstring that said `"""Design Assistant Agent Package - Placeholder"""`, and
`config/agent_config.py` carried a system prompt with no code behind it — the latter
is fixed here too: the config now names the architecture ontology layer, runs at 0.3
rather than 0.6, and describes what the profile actually does.

This is the agent that makes the "Initiative entered Design" event worth firing.

### The shape is not a new pipeline

Architecture extraction today reads **prose** and emits `elements`, `connections`,
`technology_stacks` and `design_techniques`. The Design Assistant reads **REQ-G**
and emits the same collections. So the honest framing is *a new profile over the
knowledge layer* — same object contract, same `graph_from_extraction`, same review
gate, same map — with one genuinely new problem:

> **What is the document, when there is no document?**

There is no prose to quote, so `source_text` cannot point at a passage. Everything
downstream that leans on citations — reconciliation's match scoring, the review
page's evidence column, the "why does this fact exist" question — has to be given
something else. The likely answer is to serialise a **REQ-G digest** (the
requirements, their identifiers, their quality attributes) as the prompt input and
record it as the run's `document_ref`, so the proposal is reproducible and the
provenance points at a revision rather than a paragraph.

> **Built as predicted, with one correction.** `core/knowledge/digest.py` renders
> REQ-G **plus the baseline ARC-G** — the baseline turned out to matter as much as
> the requirements, because without it a design proposes containers that already
> exist. The digest is one `Chunk` (a design is a global act), it is deterministic,
> and it degrades in a documented order rather than being chunked. `document_ref` is
> `design:<initiative>@<baseline|working>`, and the digest text itself travels in the
> run output so "what did the model actually see" is answerable.

### Decisions this item has to make

1. **What the model is shown.** The whole graph is too large and mostly irrelevant;
   a digest is a lossy projection that must be defined once, not improvised in a
   prompt. It should select what a designer would read: requirements with
   identifiers, their quality classifications, the Initiative's goals and
   capabilities, and the domain vocabulary in force.
2. **Where proposals land.** A generated architecture must not fold into verified
   facts as though it had been extracted from a document. It is a **proposal**, and
   the natural home is the review batch [YB-018](../entries/YB-018-review-batches.md)
   designs: one batch per draft, reviewable and discardable as a unit. Re-running
   must not disturb the previous draft's review state.

   > **Built as a staging file, not a batch — deliberately.** `/changes/discard`
   > empties the whole working set, so a draft merged straight in could not be undone
   > alone. A run writes `data/sea/drafts/<id>.json`, the page previews it, and only
   > Apply merges it (as unreviewed facts) and commits a revision. That gives the
   > "re-running does not disturb the previous draft" property for free and needs no
   > new gate semantics, which keeps YB-018 free to design the batch model properly.
3. **Provenance of a proposal.** Not `EXTRACTION_AGENT` reading a document, and not
   `HUMAN`. A generated proposal needs to be distinguishable from both, so a reader
   can tell "an architect wrote this" from "a model suggested this and an architect
   kept it".
4. **The pattern library (PRD D1).** D1 says "map them against a library of known
   design patterns relevant to the domain". `DesignTechnique`, `ArchitecturePattern`
   and `ArchitectureStyle` already exist as classes, but nothing seeds them — the
   domain pack is the natural place, and YB-011 is the item that owns packs.
5. **Grounding and honesty.** An agent asked to propose components will invent them.
   The object contract, confidence scoring and the mandatory review gate all apply;
   the draft should lead with *which requirements it did not address* rather than
   presenting a complete-looking architecture. A confident-but-unfounded draft is
   worse than no draft, because it moves the reviewer's starting point without
   moving their evidence.
6. **Non-determinism.** Two runs of the same REQ-G will differ (YB-004). A draft is
   therefore a snapshot with a revision, not a regenerable artifact — regenerating
   silently would make "the design changed" indistinguishable from "the model
   changed its mind".

### Acceptance

- A run produces a draft architecture graph whose provenance says it is a proposal
  and which revision of REQ-G it read.
- The draft is reviewable and discardable as one unit; discarding it leaves the
  verified graph byte-identical.
- Every proposed element traces to at least one requirement or is explicitly marked
  as ungrounded.
- The draft names the requirements it did not address.
- Re-running does not overwrite the previous draft's review decisions.

> **All five met, and the last one by construction.** `SOURCE_DESIGN_ASSISTANT`
> provenance with `document_ref = design:<initiative>@<baseline|working>` answers the
> first; a draft is a file that Apply consumes, so Discard leaves the graph
> untouched; `check_grounded_elements` reports the third; the digest's
> "Requirements with NO architectural answer" list is the fourth; and re-running
> writes a *new* draft file rather than overwriting, which is the fifth.

### What was built

> Added at closure. The record is
> [`ADR-0017`](../../decisions/ADR-0017-design-assistant-proposes-arc-g.md) and the long
> analysis is [`docs/design/design-assistant.md`](../../design/design-assistant.md).

- **A pattern catalogue as data** — `ontology/catalogues/architecture_patterns.yaml`
  (24 named solutions) read and validated by `core/patterns.py`, with strict
  deterministic name resolution. This is the PRD's D1 "library of known design
  patterns".
- **A graph→prompt digest** — `core/knowledge/digest.py`, REQ-G plus the baseline
  ARC-G, deterministic, bounded, degrading with recorded caveats.
- **The agent** — six passes; four reuse the architecture profile's schemas.
- **Ingest and routing** — `architecture_patterns`, `quality_scenarios`, `mandated_by`
  as a routed cross-graph predicate, and a `source_type` parameter so a proposal is
  distinguishable from an extraction.
- **`/design`** — preconditions, a preview of the proposal with its findings, and
  Apply/Discard, backed by `core/knowledge/drafts.py`.
- **A measurable prompt budget** — `tests/test_prompt_budget.py` holds this profile
  to `scaffolding <= digest`, against YB-007's measured 2.5:1.

### Related

- [YB-034](../entries/YB-034-initiative-delivery-phase.md) — the phase that gates the run.
- [YB-033](../entries/YB-033-event-ingress.md) /
  [YB-037](../entries/YB-037-background-workflow-management.md) — the event trigger
  and its execution. Neither is required for a UI-triggered first version.
- [YB-018](../entries/YB-018-review-batches.md) — where a draft should land.
- [YB-009](../entries/YB-009-architecture-gaps.md) — the audit that judges the draft.
