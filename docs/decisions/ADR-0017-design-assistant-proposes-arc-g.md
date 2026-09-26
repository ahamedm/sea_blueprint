---
id: ADR-0017
title: "The Design Assistant proposes an ARC-G draft — a profile over the knowledge layer, and a proposal is not an extraction"
status: accepted
date: 2026-09-25
area: "core/patterns.py (new), core/knowledge/digest.py (new), core/knowledge/drafts.py (new), core/knowledge/ingest.py, agents/design_assistant/ (new), app/templates/design.html (new)"
related: ["YB-035", "YB-033", "YB-037", "YB-018", "ADR-0011", "ADR-0013", "ADR-0016"]
design: docs/design/design-assistant.md
---

# ADR-0017 — The Design Assistant proposes an ARC-G draft

> **Record.** Closes [YB-035](../todos/entries/YB-035-design-assistant.md).
> The long analysis is [`docs/design/design-assistant.md`](../design/design-assistant.md).

**Legacy status:** ✅ IMPLEMENTED — the PRD's capability D1/D2, measured end to end
**Legacy priority:** Medium — the agent that makes "Initiative entered Design" worth
firing

---

### The problem, stated once

Architecture extraction reads a document. The Design Assistant has none: its input
is REQ-G — a graph — plus, where one exists, the frozen baseline ARC-G. Two
questions had to be answered before any code was worth writing:

1. **What is the document when there is no document?**
2. **What does it mean to produce a fact a model PROPOSED rather than one a
   document stated?**

Everything below is an answer to one of those.

### What was built

1. **A pattern catalogue as data** (`ontology/catalogues/architecture_patterns.yaml`
   + `core/patterns.py`). ~24 named solutions with category, mechanism, trade-offs
   and quality links, validated against `PatternCategory`,
   `QualitySubcharacteristic` and `ArchitectureStyleName`. This is the PRD's
   "library of known design patterns", in a form an auditor can query rather than a
   paragraph in a prompt.
2. **A graph→prompt digest** (`core/knowledge/digest.py`). REQ-G plus the baseline
   ARC-G as deterministic text, wrapped as a single `Chunk` so the entire existing
   pass harness applies unchanged. This is the answer to question 1.
3. **The agent** (`agents/design_assistant/`): six passes — structure, connections,
   techniques, patterns, scenarios, traceability — four reusing the architecture
   profile's schemas verbatim.
4. **Ingest for the two new collections** (`architecture_patterns`,
   `quality_scenarios`), plus `mandated_by` promoted to a routed cross-graph
   predicate, plus a `source_type` parameter on `graph_from_extraction`.
5. **A draft store and a `/design` page** (`core/knowledge/drafts.py`,
   `app/templates/design.html`) that preview a proposal and apply it only on an
   explicit click.

### The decisions worth keeping

**A profile, not a pipeline.** The proposed things are the same KINDS of thing an
architecture document describes, so `ElementRecord`, `ConnectionRecord`,
`DesignTechniqueRecord`, `ReferenceRecord`, `merge_records`, `merge_triples` and the
shared validators are all reused. Only ArchitecturePattern and QualityScenario — the
two the architecture profile has no record for — are new. YB-035 predicted this and
it held.

**One chunk, and a bounded digest rather than chunking.** A design is a global act:
chunked, one call could choose a monolith while the next chose microservices, both
locally defensible. So the digest has budgets and degrades in a documented order
(responsibilities → descriptions → non-NFR detail → truncation), recording every
drop as a caveat that reaches both the prompt and the page. A design that silently
saw half the requirements is not a smaller design, it is a wrong one.

**The pattern catalogue's substance never enters a prompt.** The prompt carries
names and categories only; mechanism, trade-offs and quality links are resolved
deterministically by `canonical_pattern`, mirroring `canonical_quality_concern`
(ADR-0011's posture: the mechanical part is not the model's job). Resolution is
strict — exact name, declared alias, name with noise words removed, else
**unresolved** — with no fuzzy matching, because containment cannot tell "Monolith"
from "Modular Monolith". An unresolved pattern is kept under its own name and
reported; renaming it to the nearest neighbour would destroy the information a
reviewer needs.

**A proposal is not an extraction.** `graph_from_extraction` gained `source_type`,
and the design run passes `SOURCE_DESIGN_ASSISTANT`, so every proposed fact is
distinguishable from extracted facts and from human decisions while remaining
`UNVERIFIED` and non-human. `document_type` stays `"architecture"`: the nodes ARE
architecture-side and `reconcile._node_sides` reads exactly this, so a third value
would have forced reconciliation to learn a side for no gain. The proposal marker is
provenance plus `document_ref` (`design:<initiative>@<baseline|working>`).

**Applying is a deliberate second step.** `/changes/discard` empties the whole
working set, so a draft merged straight in could not be undone on its own. A run
writes `data/sea/drafts/<id>.json`, the page previews it, and only Apply merges and
commits a revision. This deliberately stops short of YB-018's review-batch model:
applied facts land in the existing gate as ordinary unreviewed assertions, so no new
gate semantics were needed.

**Scenarios are proposed, and the census notices.** ADR-0016's `has_quality_scenario`
state read 0 on every graph because nothing emitted a `QualityScenario`. The scenario
links to its attribute with `realizes_attribute` — the same predicate an NFR uses,
since both point at the same node — and the census separates them by source kind. A
test pins that a scenario is not counted as a requirement stating the attribute.

**Real pass records.** The architecture profile reconstructs
`pass_name="(unspecified)"` from counters (ADR-0013's left-over). This profile has
its `PassOutcome`s in hand and emits real per-pass records with outcomes, paths and
elapsed times, which is the shape `_passes_from_metadata` prefers.

### Measured

End to end against the real working set (`data/sea`) with the local model, via
`agents/cli.py run --agent design_assistant`, which reads the graph and writes the
proposal as JSON without touching the working set:

```
1 chunk x 6 passes = 6 calls: 6 ok (0 via text), 0 empty, 0 failed in 118s
9 elements · 7 connections · 8 techniques · 2 patterns (2 resolved) · 2 scenarios
25 references · 14 findings
digest 10,657 chars
```

Every collection the item promised arrived:

| Collection | Result |
|---|---|
| Elements | 4 Container, 2 DataStore, 1 SoftwareSystem, 1 ExternalSystem, 1 DeploymentNode |
| Connections | 7, with protocol and synchronous/asynchronous style |
| Design techniques | 8, each linked to the quality attribute it targets |
| Architecture patterns | `Fallback` (with `mandated_by FR-PM-003`) and `CQRS`, **both resolved by name** against the 24-entry catalogue |
| Quality scenarios | 2, both with a number: `p95 authorization latency < 500ms` (Time Behaviour) and `Throughput >= 10,000 transactions per minute` (Capacity) |
| Traceability | 25 references — 10 `implements_requirement`, 7 `satisfies_quality_attribute`, 5 `delivers_initiative`, 2 `supports_capability`, 1 `traces_to_goal` |

**The findings are the useful part, and one of them justifies a check that did not
exist when the run was made.** The run flagged 14: six missing `part_of` triples
(the shared containment validator), four ungrounded elements, and — the notable four
— **name collisions**. The model proposed `Payment Gateway Platform` as a
`SoftwareSystem` where a `System` node already carries that name, `OpenShift` as a
`DeploymentNode` where a `TechnologyStack` does, and `PostgreSQL` and `Valkey` as
`DataStore`s where `TechnologyStack`s do. `ingest._resolve` reuses any node carrying
a matching label **whatever its kind**, so without the check each of those would have
merged silently into the wrong node and produced a fact about the wrong thing, with
nothing failing. This is the hazard `check_name_collisions` was written for, now
confirmed on real output rather than predicted.

Two limitations the run exposed, stated rather than smoothed over:

1. **All 8 techniques were renamed requirements.** The pass returned
   `Role-Based Access Control Enforcement`, `Horizontal Scaling`, `TLS 1.2+
   Transport Security`, `Payment Gateway Fallback Strategy` — the requirements' own
   labels — where the schema asks for a mechanism (`Redundancy / Replicas`,
   `Stateless Services`), and every quality field on them is empty. A technique that
   restates the requirement adds a node and no information, and the quality census
   would report the attribute as having a realizing technique on the strength of a
   renamed requirement. `check_techniques_are_mechanisms`, added in response to this
   run, flags **8 of 8** on the design page; making the pass actually work is
   [YB-038](../todos/entries/YB-038-techniques-pass-echoes-requirements.md), where
   the four options are set out. The other five passes are unaffected, so this is
   specific to the techniques pass rather than to the profile.

   The manual gate says the same thing:
   `scripts/run_extraction_tests.py --only design --validate-only` reports **5 of 6**
   invariants, failing `inv_design_techniques_linked`. Left failing on purpose — it
   is the diagnostic, and softening it would hide the one layer that makes a stated
   quality attribute verifiable. A later run of the same pass did not finish in 19
   minutes where this one took 19 seconds, so the pass is both wrong and sometimes
   unbounded; recorded in YB-038.
2. **Traceability favoured the easy links.** 10 `implements_requirement` against 7
   `satisfies_quality_attribute` and 5 `delivers_initiative`: the model linked
   business intent more readily than the functional requirements the design exists
   to answer. Combined with the ungrounded elements, this is what the review gate is
   for.

### The prompt budget, held to a number

YB-007 measured the architecture prompt at 15,903 characters — 11,361 of scaffolding
against a 4,542-character document, **2.5:1** — and showed why that matters: prompt
fixes are not monotonic, and adding one section silently broke a rule that had been
working. This profile is the first written since, so
`tests/test_prompt_budget.py` asserts `scaffolding <= digest` for every pass, and it
passes. Two properties keep it there: the catalogue never enters the prompt in bulk,
and the digest degrades rather than growing.

### Not done, stated rather than implied

- **Technology stacks and engineering conventions.** The architecture profile
  extracts both; this profile emits neither, because they were not asked for and
  every collection costs prompt budget. One more pass adds them.
- **Iterative design** (PRD D3). An architect corrects the applied facts through the
  existing review gate; feeding those corrections back to the agent is a separate
  item.
- **The event trigger.** [YB-033](../todos/entries/YB-033-event-ingress.md),
  [YB-034](../todos/entries/YB-034-initiative-delivery-phase.md),
  [YB-037](../todos/entries/YB-037-background-workflow-management.md). The agent is
  already a plain `run(input_data)` and does not care who calls it.
- **Review batches.** [YB-018](../todos/entries/YB-018-review-batches.md) still owns
  the batch model; the draft store is a staging file, not a gate.
- **The run is synchronous**, like `/ingest`, and inherits the same problem
  ([YB-026](../todos/entries/YB-026-asynchronous-progress.md)).
