---
id: YB-019b
legacy: "19b"
title: "Deterministic identifier capture — re-scoped, largely obviated by the ADR-0001 fix"
status: superseded
priority: medium
area: "`agents/extraction/`, `core/knowledge/ingest.py`"
created: 2026-09-23
updated: 2026-09-24
design: null
record: null
superseded_by: ["ADR-0001", "ADR-0011"]
related: ["YB-019a"]
blocks: []
blocked_by: []
---

# YB-019b — Deterministic identifier capture — re-scoped, largely obviated by the ADR-0001 fix

> **Superseded.** Kept only for the evidence it records; it is not work to do.

**Legacy status:** Identifier half resolved by item 0; quality-classification half remains
**Legacy priority:** Medium (was High)
**Legacy area:** `agents/extraction/`, `core/knowledge/ingest.py`

**Superseded by:** ADR-0001, ADR-0011 — kept for the evidence it records, not as work to do.

---

### Correction

This item was written on the finding that the model emits no identifiers. That finding was
caused by ADR-0001: extraction ran a prompt that never asked for them. With the config fixed
the model returns **all 18 source identifiers, none missing**.

So the premise — "stop asking the model to copy literals" — was wrong. It *was* being
asked; the agent never received the ask.

### What genuinely remains

1. **Quality classification is still empty.** All 8 NFRs come back with
   `quality_category`, `subcharacteristic` and `quality_attribute` blank, even with the ISO
   taxonomy in the prompt. The model instead emits
   `Payment Gateway Platform --satisfies_quality_attributes--> Availability`, using names
   like `Latency`, `Throughput`, `Usability`, `Observability` that are **not** in the ISO
   vocabulary and sit outside it — so they become untyped `Concept` nodes.
2. **NFR entities are named by their own id** (`name: "NFR-PS-001"`). The identifier has
   become the label, discarding the readable requirement name and making the node unusable
   in the UI.
3. **Identifier over-capture is narrow but real** (`CHD`, `PGP` emitted as identifiers).

ADR-0002 is the substantive remainder, and it *is* a good fit for a deterministic pass:
classification from requirement text against a closed vocabulary is keyword-and-shape
matching, reviewable, and does not need a model — the argument this item originally made,
applied to the field that actually needs it.
