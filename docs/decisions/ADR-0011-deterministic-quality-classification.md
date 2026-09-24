---
id: ADR-0011
legacy: "22"
title: "Deterministic quality classification and identifier recovery"
status: accepted
date: 2026-09-23
area: "agents/extraction/quality.py, agents/knowledge_extraction/agent.py"
related: ["YB-019b"]
---

# ADR-0011 — Deterministic quality classification and identifier recovery

> **Record.** Closed work, preserved verbatim from `TODO.md` v1 (ADR-0011).
> Legacy source: [`docs/todos/legacy-todo-v1.md`](../todos/legacy-todo-v1.md).

**Legacy status:** ✅ IMPLEMENTED — and it removed the last model-dependent input
**Legacy priority:** Done
**Legacy area:** `agents/extraction/quality.py` (new), `agents/knowledge_extraction/agent.py`, `tests/test_quality_classifier.py` (39 tests)

---

### What was wrong

With the full ISO/IEC 25010:2023 taxonomy in the prompt, the model left
`quality_category`, `subcharacteristic` and `quality_attribute` **empty on every
NFR**, and invented its own attribute names — `Latency`, `Throughput`, `Usability`,
`Observability` — attaching them to the System via `satisfies_quality_attributes`,
so they landed as untyped `Concept` nodes outside the vocabulary the auditor reads.

### What was built

`agents/extraction/quality.py`, in three parts:

1. **A keyword classifier** over a closed taxonomy — ~110 patterns mapping
   requirement wording onto ISO sub-characteristics, with the characteristic
   derived from the ontology's own `SUBCHARACTERISTIC_PARENT` so the taxonomy has
   one definition. Signals are **weighted**: precise indicators score 6, incidental
   ones 1. That weighting is measured, not decorative — see below.
2. **A requirement inventory** read from the source, not requested from the model.
   Two document shapes (Markdown table row, indented bullet), with an explicit
   prefix allow-list so `AES-256` and `TLS-1.2` are not mistaken for requirement keys.
3. **Enrichment** that recovers a missing `requirement_id` and fills missing quality
   fields, only where the model was silent.

### Why the inventory was necessary — the non-determinism

The first version keyed classification off the entity's `requirement_id`, which put
it at the mercy of the model. Two runs of the **same document with the same config**:

| run | identifiers emitted |
|---|---|
| after the ADR-0001 fix | **18 of 18** |
| the next run | **0 of 18** |

The second run classified nothing. Reading identifiers from the source instead makes
the pass deterministic, and name matching connects a loosely-named entity (`Latency`)
to its source requirement (`NFR-PS-001 (Latency)`).

### Errors found by running it, each fixed and pinned by a test

| Symptom | Cause |
|---|---|
| `NFR-SC-001` unclassified despite being full of security keywords | passage cut at its **own** repeated identifier — `**NFR-SC-001 (PCI-DSS…):** … PCI-DSS` |
| cut landed mid-parenthesis | `found.start()` is relative to the LINE, was used as an offset into the tail |
| a REPORTING requirement classified `SCALABILITY`, top score in the document | table-row passage ran past the row into the next section's "Performance and **Scalability**" heading |
| logging/metrics requirement classified `TIME_BEHAVIOUR` | single `real-time` hit outweighed by nothing; observability has no ISO characteristic and was unmapped |
| `FR-PM-003` → ACCOUNTABILITY | `authoriz` matched the payment state `AUTHORIZED` |
| `FR-SR-001` → CAPACITY | `volume` matched a settlement trigger |
| `Payment Gateway Platform` matched `Payment Acceptance` | name overlap on the single shared word `payment` |
| `AES-256`, `TLS-1.2` accepted as identifiers | shape-based rule; replaced with a prefix allow-list |

### Measured result

On `payment_platform_brief.md`, simulated against the shape of the failing run —
entities named loosely with **no identifiers at all**:

```
NFR-PS-001  TIME_BEHAVIOUR      name=Latency
NFR-PS-002  SCALABILITY         name=Throughput
NFR-PS-003  MODULARITY          name=Architecture
NFR-SC-001  CONFIDENTIALITY     name=PCI-DSS Compliance
NFR-SC-002  CONFIDENTIALITY     name=Data Encryption
NFR-SC-003  ACCOUNTABILITY      name=Access Control
NFR-UM-001  USER_ENGAGEMENT     name=UI Experience
NFR-UM-002  ANALYSABILITY       name=Observability
FR-PM-001   (unclassified)      name=Payment Acceptance

NFRs classified: 8/8   (was 0 before)
```

All eight NFRs classified, identifiers recovered, and **no functional requirement
given a quality attribute** — which is the property that makes the output
trustworthy, since an FR carries no quality concern by definition. A system entity
sharing a word with a requirement name is left untouched.

### Deliberate limits, stated in the module

- A requirement with no keyword is left **unclassified** rather than guessed at.
  A wrong category silently becomes the answer an audit reasons over.
- Keyword matching is not comprehension: *"must not expose latency guarantees"*
  contains `latency`. The score and matched terms are returned so a weak match is
  visibly weaker than a strong one.
- Classification provenance is recorded (`quality_classification_source`), so a
  keyword classification is distinguishable from one the model asserted.

### Not done

- **The model still emits its own invented attribute names** in triples
  (`satisfies_quality_attributes -> Latency`). Those become untyped `Concept` nodes
  alongside the correctly-typed ones. Reconciling or suppressing them is open.
- **The text-parsing path derives entities from triples**, which carry no
  `requirement_id` field at all — so on that path identifier recovery depends
  entirely on name matching. It works for the fixture; it is not exercised broadly.
- Only two document shapes are recognised. A requirement stated as a heading, or in
  prose, yields no inventory entry — degrading to "unclassified", which is visible
  but is a coverage limit.
