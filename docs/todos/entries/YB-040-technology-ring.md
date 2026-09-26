---
id: YB-040
legacy: null
title: "Technology Ring — the adoption axis is in the ontology, but nothing writes it"
status: open
priority: medium
area: "`ontology/architecture_base.yaml`, `agents/architecture_extraction/passes.py`, `core/knowledge/ingest.py`"
created: 2026-09-26
updated: 2026-09-26
record: null
superseded_by: []
related: ["YB-041", "YB-011"]
blocks: []
blocked_by: []
---

# YB-040 — Technology Ring

> **Open work.** This file is the source of truth for this item; `TODO.md` is generated from it.

### What already exists

The **kind** axis is complete and in use:

- `TechnologyCategory` — `ontology/architecture_base.yaml:290`, ten values
  including LANGUAGE, FRAMEWORK, LIBRARY, TOOL, PLATFORM, DATA_STORE, MESSAGING,
  PROTOCOL, INFRASTRUCTURE, OBSERVABILITY.
- `TechnologyStack.category` — `:1301`.
- Extraction field `TechnologyStackRecord.category`
  (`agents/architecture_extraction/passes.py`), written by ingest as
  `technology_category` (`core/knowledge/ingest.py:431`).

The **adoption** axis is declared but dead:

- `TechnologyRing` — ADOPT / TRIAL / ASSESS / HOLD, `architecture_base.yaml:320`.
- `TechnologyStack.ring` — `:1306`.
- Referenced by **no Python anywhere**. Repo-wide, "radar" appears only in
  `architecture_base.yaml` and `ontology/sea_common.yaml:115`
  (`TECH_REGISTRY_ID` — a join hook for a registry or radar entry that nothing
  emits either).

### The measurement

`data/sea-deepseek/working.json`: 18 `TechnologyStack` nodes, 18
`technology_category`, 28 `uses_technology` — and **0 `ring`**. The document it
came from already states the rings in prose:

```
test_data/arch/large_architecture_spec.md:226-229
  Approved:          Spring Boot, Java 21, PostgreSQL, Kafka, REST, gRPC,
                     OpenShift, Prometheus, Grafana, ELK Stack, Splunk, Docker
  Under assessment:  Valkey, event sourcing
  Prohibited:        unencrypted transports, shared database integration
```

Approved ≈ ADOPT, under assessment ≈ ASSESS, prohibited ≈ HOLD. Nothing captures
any of it, so the graph cannot answer "which of our technologies is approved?"

### Why it does not reach the graph

`TechnologyStackRecord` carries `name`, `category`, `version`, `used_by`
(`passes.py`, technology pass). There is no `ring` field, so the model is never
asked. Ingest reads exactly two things (`core/knowledge/ingest.py:425-436`):
`category` → `technology_category`, `used_by` → `uses_technology`.

The same silence drops `version`: declared in the record
(`agents/architecture_extraction/passes.py:389`) and in the ontology
(`architecture_base.yaml:1309`) and never written. `vendor` (`:1312`) and
`constraint_note` (`:1315`) have no extraction field at all.

### The decision

1. **Smallest viable.** Add `ring` (ADOPT/TRIAL/ASSESS/HOLD) to
   `TechnologyStackRecord`, teach it in the technology pass, and write it beside
   `technology_category` at `ingest.py:431`. Fix `version` in the same edit — same
   defect, same lines. Bounded: per-initiative observation, so two documents can
   assert different rings for one technology and the graph holds both
   unreconciled.
2. **A catalogue** — `ontology/catalogues/technology_radar.yaml` plus a loader
   modelled on `core/patterns.py` (`:55`, `:202`, `:333`). One authoritative ring
   per technology, resolved deterministically instead of N document-local
   opinions. This is the shape `ontology/catalogues/architecture_patterns.yaml`
   already argues for in its own header. Option 1 is a prerequisite either way.
3. **Not a domain-pack overlay.** Packs must specialise `DomainConcept`
   (`core/ontology.py`) and `ontology/domains/payment_processing.yaml:17-20`
   forbids importing `architecture_base`, so a `TechnologyRadar` extending
   `TechnologyStack` cannot live there.

### Framing note

"Frameworks / Tools / Technologies / Languages" is the **kind** axis, which
already exists as `TechnologyCategory`. What is missing is the ring reaching the
graph, plus a projection grouping stacks by category × ring — a viewpoint over
existing nodes, not new ontology.
