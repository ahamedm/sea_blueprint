---
id: YB-035
legacy: null
title: "Design Assistant — draft an initial architecture from REQ-G, on request or on an event"
status: open
priority: medium
area: "`agents/design_assistant/` (placeholder today), `core/knowledge/`, `app/` (review batches, map)"
created: 2026-09-25
updated: 2026-09-25
design: docs/design/event-driven-integration.md
record: null
superseded_by: []
related: ["YB-033", "YB-034", "YB-037", "YB-018", "YB-009", "YB-004", "ADR-0011"]
blocks: []
blocked_by: []
---

# YB-035 — Design Assistant

> **Open work.** Design sketch, not yet reviewed. Trigger context in
> [`docs/design/event-driven-integration.md`](../../design/event-driven-integration.md).

### Why

The PRD's step 5 is *ARC-G Initiation — Solution Shaping*: the agent reads a
verified REQ-G and proposes foundational components, the architect refines them.
Capabilities D1–D3 are written down. `agents/design_assistant/__init__.py` is a
docstring that says `"""Design Assistant Agent Package - Placeholder"""`, and
`config/agent_config.py` carries a system prompt with no code behind it.

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

### Related

- [YB-034](../entries/YB-034-initiative-delivery-phase.md) — the phase that gates the run.
- [YB-033](../entries/YB-033-event-ingress.md) /
  [YB-037](../entries/YB-037-background-workflow-management.md) — the event trigger
  and its execution. Neither is required for a UI-triggered first version.
- [YB-018](../entries/YB-018-review-batches.md) — where a draft should land.
- [YB-009](../entries/YB-009-architecture-gaps.md) — the audit that judges the draft.
