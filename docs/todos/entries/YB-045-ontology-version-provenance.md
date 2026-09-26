---
id: YB-045
legacy: null
title: "Ontology version is not recorded per run — a graph cannot be attributed to the vocabulary that produced it"
status: open
priority: high
area: "`core/knowledge/model.py`, `core/knowledge/ingest.py`, `core/ontology.py`, `agents/base_agent.py`"
created: 2026-09-26
updated: 2026-09-26
record: null
superseded_by: []
related: ["YB-010", "YB-011", "YB-042", "YB-043", "ADR-0022"]
blocks: []
blocked_by: []
---

# YB-045 — Ontology version provenance

> **Open work.** This file is the source of truth for this item; `TODO.md` is generated from it.

### The gap

A run records *what document* it read (`document_hash`), *which model* produced it
(`model_id`), and *which domain pack* was in force (`Provenance.domain_pack`) — but
**not which ontology**. `ExtractionRun` carries no `ontology_version`/`ontology_hash`,
and the `version:` fields the layer files already declare
(`ontology/architecture_base.yaml:27`, `enterprise_structure.yaml:15`,
`requirements_base.yaml:14`, `sea_common.yaml:37`) are read by nothing.

Meanwhile the tests prove the ontology *does* change behaviour: `check_schema_consistency`
exists precisely because the extraction schema and the ontology drift, and `YB-011`
is adding domain packs on top. So a graph can differ between two runs of the same
document with the same model, and nothing in the graph says why.

### Why it matters now

- **Attribution.** "This baseline was produced under ontology X, pack Y" is not
  answerable, so a re-run that changes hundreds of assertions is unattributable.
- **It undermines the RDF diagnostic.** `YB-010`'s central claim is that a *fixed
  query over a varying graph* localises blame to extraction. That holds only if the
  vocabulary is a constant; if the ontology moved, the query moved with it and the
  blame is misattributed.
- **It gets worse with the proposed deployment.** Once the ontology is served from
  S3/EFS — see [`docs/design/deployment-architecture.md`](../../design/deployment-architecture.md)
  §3.2 — two runs can read different bytes with no commit, no diff, and no record.
  An in-repo ontology at least moves visibly in git; a shared mutable one does not.
- **It is cheap to close now and expensive later.** Existing revisions cannot be
  back-filled: any revision written before this lands is permanently unattributable.

### The fix (small, additive)

1. Compute a hash of the **resolved** ontology stack where it is loaded
   (`core/ontology.py` already reads the four-file chain), covering the layer files,
   the active domain pack, and the catalogues that shape prompts.
2. Record it on `ExtractionRun` (`ontology_hash`, and the declared `version` strings),
   alongside `document_hash` and `model_id`.
3. Emit it in the RDF export beside the existing run metadata, so an audit artefact
   carries its own provenance.
4. Surface it where runs are shown (completeness/run report), so "same model, same
   document, different graph" has a visible explanation.

Additive to the serialised shape, so `SCHEMA_VERSION` should not need to move — but
verify against `tests/test_serialise.py`, which asserts field-level survival.

### Acceptance

- A stored run names the ontology version/hash that produced it.
- Two runs over one document with different ontology bytes are distinguishable
  without diffing the repository.
- The audit output can state which vocabulary a baseline was produced under.
- A run whose ontology hash is unrecorded reads as *unknown* rather than being
  silently assumed current — the same honesty rule
  [YB-023](YB-023-requirements-completeness-reporting.md)'s completeness fix
  applied to runs.
