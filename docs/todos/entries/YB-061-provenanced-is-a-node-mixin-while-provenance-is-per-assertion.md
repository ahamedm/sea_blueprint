---
id: YB-061
legacy: null
title: "`Provenanced` is a node mixin while provenance actually lives on assertions"
status: open
priority: low
area: "`ontology/sea_common.yaml` (`Provenanced`, `ExternallyReferenced`), their two consumers, and `core/knowledge/model.py` (`Node` vs `Assertion.provenance`)"
created: 2026-10-03
updated: 2026-10-03
design: null
record: null
superseded_by: []
related: [YB-059, YB-060, YB-055]
blocks: []
blocked_by: []
---

# YB-061 — `Provenanced` is a node mixin; provenance is per-assertion

> **Open. Filed from the external ontology review, 2026-10-03**, which recorded the
> mixins as "applied inconsistently (not on Stakeholder/BusinessGoal/Capability/
> Process/DomainConcept/BusinessRule)". Reading the code says the inconsistency is a
> symptom, and the cause is worth a decision.

## The measurement

`sea_common.yaml` declares two mixins: `Provenanced` (`provenance`) and
`ExternallyReferenced` (`external_references`). They are applied to exactly two
classes — `Initiative` and `Requirement`. The review reads that as under-application.

## Why under-application may not be the defect

In the graph, provenance is **not** a property of a node:

- `core/knowledge/model.py`'s `Node` carries `id`, `kind`, `label`,
  `external_references` — and no `provenance`.
- `Assertion` carries `provenance` (run, pass, model, chunk, domain pack), which is the
  whole point of the audit trail: the same node can be asserted by many runs, and the
  fact that matters is *who claimed what, when*.

So `Provenanced.provenance` on a node class is a slot the pipeline cannot populate, and
adding it to six more classes would spread an unpopulatable slot rather than fix an
omission. `ExternallyReferenced` is the opposite: it IS populated, because `Node` has
`external_references` and ingest writes join keys there.

That asymmetry is the finding: the pair travels together but only one of them is real
for nodes.

## Options

1. **Drop `Provenanced` from the node mixins**, keeping `ExternallyReferenced`, and say
   in `sea_common.yaml` that provenance is per-assertion by design. Consistent with the
   code; loses nothing the pipeline can write.
2. **Keep it for hand-authored LinkML instances** (a document-level provenance that the
   graph has no place for) and document that it is not graph-populated — the
   `QualityScenario` precedent.
3. **Apply both to all six classes** the review names, accepting that `provenance` stays
   empty on every extracted node.

## What closes it

A chosen option and a sentence in `sea_common.yaml` stating which of the two mixins a
node is expected to fill, so the pair stops being applied by convention rather than by
rule.
