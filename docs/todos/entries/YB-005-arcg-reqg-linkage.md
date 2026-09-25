---
id: YB-005
legacy: "5"
title: "ARC-G ⇄ REQ-G linkage — Initiative-scoped reconciliation"
status: done
priority: critical
area: "`agents/knowledge_extraction/`, `agents/architecture_extraction/`, ontology, `core/knowledge/ingest.py`"
created: 2026-09-21
updated: 2026-09-24
design: null
record: docs/decisions/ADR-0012-req-arc-reconciliation-inversion.md
superseded_by: []
related: ["YB-011", "YB-018", "YB-027"]
blocks: []
blocked_by: []
---

# YB-005 — ARC-G ⇄ REQ-G linkage — Initiative-scoped reconciliation

> **Closed.** The resolution pass and both directions of the audit are implemented
> and measured; the record is
> [`ADR-0012`](../../decisions/ADR-0012-req-arc-reconciliation-inversion.md).
> Semantic (paraphrase) matching moved to [`YB-027`](YB-027-semantic-reference-matching.md),
> which was deliberately kept out of this work because it is a different mechanism
> with a different cost. This file is kept because it carries those open items.

**Legacy status:** ✅ IMPLEMENTED — see ADR-0012 §4 for the measured result
**Legacy priority:** **Critical** — this was the platform's core purpose
**Legacy area:** `agents/knowledge_extraction/`, `agents/architecture_extraction/`, ontology, `core/knowledge/ingest.py`

---

### Problem

The two graphs are extracted independently and **cannot be joined**. The Semantic
Auditor's whole job — find requirements with no architectural answer, find
architecture answering nothing — is impossible in the current state.

Evidence from the current draft outputs:

```
                               arch nodes: 60
                               req  nodes: 41
                               shared identity: 4   (AES-256, TLS 1.2+, Payment Gateway Platform, Transaction Currency)
```

Those 4 shared values are incidental coincidences of wording, not links. Of the
**10 requirement references** the architecture graph emitted, **0 match any REQ
node by name**:

| Architecture says | In REQ graph? |
|---|---|
| `Request Acceptance and Validation` | ✗ |
| `Rule-Based Routing` | ✗ |
| `Tenancy Mapping and State Tracking` | ✗ |
| `Settlement Request Initialization` | ✗ |
| `PCI-DSS Compliance` | ✗ |
| `Low Latency`, `Scalability`, `Security`, `High Availability` | ✗ |

### Root causes (three, all fixable)

1. **Requirement IDs are discarded during extraction.** The requirements document
   carries 18 stable identifiers (`FR-PM-001`, `NFR-SC-001`, …). **Neither graph
   preserves a single one** — 0 found in either output. The extraction schema has
   no `requirement_id` field and the prompt never asks for one. Losing the
   document's own stable keys is the primary cause.

2. **The architecture document references requirements by paraphrase, not by ID.**
   Its §2.1 says the Payment Orchestrator handles *"Request Acceptance and
   Validation"* — which is `FR-PM-001` ("Payment Acceptance"). The reference is
   semantically correct and lexically unjoinable. Even perfect ID preservation
   would not fix this alone.

3. **No resolution step exists.** Nothing reconciles an architecture-side
   reference against requirement-side nodes. The ontology already has the right
   construct — `RequirementRealization` (requirement ↔ realised_by, with
   `coverage`, `evidence`, `confidence`) — but nothing populates it.

### New approach: Initiative as the primary scoping identifier

Relying on document names or input source names is fragile. `Initiative` (from
`requirements_base.yaml`) is the stable, semantic anchor that spans both graphs:

- **REQ-G:** Requirements are linked to their authorising `Initiative` via `authorised_by_initiative`.
- **ARC-G:** Architecture elements are linked to the `Initiative` they deliver via `delivers_initiative`.
- **The Join:** We can now ask: *"Which architecture elements are delivering the same Initiative that authorised these requirements?"*

This makes cross-reference robust even when requirement IDs are lost or paraphrased.
If both a requirement and an architecture element point to `INIT-2024-001 (Payment Modernisation)`,
they belong in the same reconciliation scope.

**Implemented:** `core/knowledge/ingest.py` now captures `Initiative` nodes and
creates `delivers_initiative` / `authorised_by_initiative` assertions during ingestion.

### The `requirement_type` observation

`requirement_type` is `null` on **all 73** architecture triples. That is the visible
symptom of the above: no architecture triple carries any requirement
classification because none is linked to a requirement.

Note this field means BUSINESS / FUNCTIONAL / NON_FUNCTIONAL / CONSTRAINT — a
*requirement* classification. On architecture triples it should arguably be
populated **only on the traceability edges** (where an arch element points at a
requirement), not on every arch triple. Worth deciding explicitly rather than
leaving it perpetually null.

### Design decision (Ahmed, review session)

**The link structure is established in the extraction phase. Reconciliation
happens in later stages.**

My original proposal leaned the other way — defer matching to the Auditor. That
was wrong. The extraction pass must actively build the linkage structure:

- capture requirement identifiers verbatim
- capture architecture→requirement references, including paraphrased ones
- emit traceability edges even when uncertain

Later stages then *resolve and refine* those links. Anything imperfect or missed
in extraction is correctable by a **human architect** — which is acceptable, and
far better than emitting nothing. An absent link is invisible and unrecoverable;
an imperfect link is a reviewable assertion.

This sets the posture for the whole platform: **capture eagerly, resolve later,
let humans adjudicate.**

### Implemented (this session)

**A. Identifier preservation — DONE.**
`ExtractedEntity` now carries `requirement_id` and `initiative_refs`;
`ArchitectureElementRecord` carries `requirement_refs` and `initiative_refs`.
Both prompts instruct verbatim preservation and explicitly forbid inventing IDs.

Verified against the PRD (18 identifiers in source):

```
requirement_ids captured: 16 of 18
  FR-PM-001..003, FR-SR-001..003, FR-TR-001, FR-TR-004,
  NFR-PS-001..003, NFR-SC-001..003, NFR-UM-001..002
```

Two misses (`FR-TR-002` Routing Criteria, `FR-TR-003` Fallback Mechanism) and one
false positive (`PGP` captured as an ID — it is the platform name). Both are the
class of error a human architect corrects in review; the mechanism is sound and
the misses are visible rather than silent.

**B. Initiative construct — ADDED.**
New `Initiative` class in `requirements_base.yaml` (+ `InitiativeType`,
`InitiativeStatus` enums). The business case / work item that formalises *why* the
work exists. It is the common root of the chain:

```
Initiative (business case)
   │ originates_requirements / delivers_capabilities / affects_systems
   ▼
Requirement (REQ-G, with stable id)
   │ requirement_refs  ← captured at extraction, resolved later
   ▼
Architecture element (ARC-G)
```

`BusinessRequirement.originates_from: Initiative` and
`ArchitectureElement.initiative_ref` complete it, so business case → requirement →
architecture is walkable as one chain.

### Remaining

**C. Resolution pass (later stage).** DONE. `core/knowledge/realization.py` reports
both directions, `/gaps` renders them, and `/reconcile` states the requirement
coverage next to the references it is binding. The dual reporting was the actual
gap analysis, and it now exists at three levels: unresolved references,
requirements with no bound answer, and architecture claiming something that never
bound.

**D. Better ID recall.** 16/18 with 1 false positive. Partially addressed and the
rest is measured, not tuned: the citation rule (a citation of a requirement's own
key is identity, not resemblance) means the recall question now changes what
*binds*, not merely what is proposed — so it is worth revisiting only once a fresh
run exists to measure against. The saved fixture carries zero identifiers, so it
cannot answer this.

**E. Semantic matching. MOVED OUT** to
[`YB-027`](YB-027-semantic-reference-matching.md). The matcher is deterministic and
explainable on purpose, and that constraint is what makes a paraphrase with no
shared vocabulary unreachable. Widening it is a change of mechanism (embeddings or
model-assisted proposals), not a tuning of this one.

### Acceptance criteria

All four are now met, and checked by tests:

- A requirement with no architecture can be listed — `unrealized_requirements`,
  rendered on `/gaps`, with the three coverage states kept apart
- An architecture element answering no requirement can be listed —
  `unmet_obligations`, rendered on `/gaps` as "Architecture claiming a requirement
  that never bound"
- Every resolved `implements_requirement` edge points at a real REQ-G node id —
  `test_every_requirement_is_listed_even_when_nothing_claims_it` and
  `test_the_requirements_key_joins_end_to_end`
- Unresolved references are surfaced, never silently dropped —
  `bulk_resolve`'s `below_threshold` / `no_candidate` / `unknown_ids`, plus
  `unbound_claims` in the report

