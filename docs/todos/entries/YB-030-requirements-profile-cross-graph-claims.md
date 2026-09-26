---
id: YB-030
legacy: null
title: "The requirements profile claims architecture-side realization — the join reads as answered when no architecture exists"
status: done
priority: high
area: "`agents/knowledge_extraction/agent.py` (prompt vocabulary), `core/knowledge/realization.py`, `core/knowledge/ingest.py`"
created: 2026-09-25
updated: 2026-09-26
design: docs/design/real-run-readings.md
record: docs/decisions/ADR-0018-cross-graph-vocabulary.md
superseded_by: []
related: ["YB-005", "YB-031", "YB-029", "YB-007"]
blocks: []
blocked_by: []
---

# YB-030 — The requirements profile claims architecture-side realization

> **Closed 2026-09-26.** The record is
> [`ADR-0018-cross-graph-vocabulary`](../../decisions/ADR-0018-cross-graph-vocabulary.md), which carries the decision and
> the evidence. The write-up below is preserved as it stood when the item
> was written.

> **Found by the real run of 2026-09-25**, not by a fixture. The measurement and
> the reproduction are in
> [`docs/design/real-run-readings.md`](../../design/real-run-readings.md) §Reading 2.

### The defect

`implements_requirement` is the cross-graph join: an architecture element answers a
requirement. `realization_report` reads any active realization claim whose target is
a requirement node and counts that requirement **realized**.

The requirements profile emits that same predicate. On the requirements-only dry run
— a graph with **no architecture nodes at all**:

```
requirements 12   realized 11   bound_edges 35
  Payment Gateway Platform --implements_requirement--> Payment Request Acceptance
  Payment Gateway Platform --implements_requirement--> Payment Request Validation
  Payment Gateway Platform --implements_requirement--> Cardholder Data Encryption
```

`Payment Gateway Platform` is the system the requirements describe, not an
architecture element, and "the platform implements the requirement" is not the
architectural answer the audit is asking for. But the report cannot tell the
difference, so eleven of twelve requirements read as realized with nothing on the
architecture side whatsoever.

### Why it is worse than one wrong number

The run-to-run variation is the real problem. In the same session, the *merged* run
emitted the plural `implements_requirements`, which `CROSS_GRAPH_PREDICATES` does not
route ([YB-031](YB-031-cross-graph-predicate-plural-routing.md)), so it became a local
edge and inflated nothing. The singular spelling inflates the audit; the plural
spelling does not. **The trustworthiness of the gap report therefore depends on which
synonym the model chose**, which is not a property anyone can reason about.

### What the fix has to decide

1. **Where the claim is refused.** The prompt offers the whole ontology vocabulary to
   both profiles, so the requirements profile is *taught* a predicate whose domain is
   `ArchitectureElement`. Scoping the vocabulary by profile is the honest fix; gating
   the predicate at ingest is the mechanical one; making `realization_state` require
   the claim's source to be architecture-declared is the query-layer one. They are not
   equivalent — the first two stop the fact being written, the third only stops it
   being read.
2. **Whether the claim is ever legitimate.** A requirements document naming the system
   that will implement it is ordinary prose. If that is worth keeping, it needs its own
   predicate (`implemented_by` / `binds_to_system`), not the join's name.
3. **What happens to graphs already built.** `data/sea` holds 36 architecture-side
   `implements_requirement` claims and one requirements-side one. A query-layer fix
   reclassifies them without a re-ingest; an ingest fix would need one.

### Acceptance

- A requirements-only graph reports zero requirements realized by architecture.
- The claim, if kept at all, is distinguishable from the architecture-side join.
- A test pins the requirements-only case, since that is the one a fixture replay
  cannot produce.

### Related

- [YB-031](YB-031-cross-graph-predicate-plural-routing.md) — the same vocabulary
  boundary, seen from the routing table.
- [YB-029](YB-029-quality-attribute-views.md) — the reason this matters for the
  attribute census too: the profile also emits `satisfies_quality_attributes` from the
  system node.
