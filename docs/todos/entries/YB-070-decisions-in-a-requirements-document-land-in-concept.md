---
id: YB-070
legacy: null
title: "A decision named in a requirements document lands in Concept, because that profile cannot see the class"
status: open
priority: medium
area: "`ontology/requirements_base.yaml` (its `imports:`), `agents/knowledge_extraction/` (the `triples` pass), `agents/extraction/validators.py` (a finding for the fallback)"
created: 2026-10-03
updated: 2026-10-03
design: null
record: null
superseded_by: []
related: [YB-069, YB-068, YB-010]
blocks: []
blocked_by: []
---

# YB-070 — Decisions in a requirements document have nowhere to go but `Concept`

> **Open.** Found while fixing [ISS-18](../../ISSUES.md#iss-18--an-architecture-ingest-could-never-record-a-decision-and-nothing-failed-when-it-did-not-fixed-2026-10-03):
> with the decisions pass working on the ARCHITECTURE profile, two live examples were still
> unclassified, and they came in through the other profile.

## The measurement

Two nodes in `payments_v3`, both `Concept`, both asserted by the `triples` pass:

```
Concept  "Java Microservices Technology Decision"
Concept  "On-Premises Deployment Decision"
```

`Concept` is the graph's own fallback for "a referent no pass classified". The reason is
layering, not a missing pass: `requirements_base` imports `linkml:types`,
`enterprise_structure` and `sea_common` — **not `architecture_base`** — and `_collect_ontology_names`
scopes a profile's vocabulary to the layers its entry schema imports. So
`ArchitectureDecision` is not merely unemitted by the requirements profile; it is **not in the
prompt**, and a decision the document states has no class to be.

Meanwhile the same document ingested as *architecture* now records decisions properly
(`run_cbf400f6bc66`: `decisions outcome=ok`), so the two profiles disagree about the same
sentence depending on which door the document came through.

## Why it is not a one-line fix

The layering is deliberate and load-bearing: `architecture_base` imports `requirements_base`,
never the reverse, and YB-047 records that adding an import is "a deliberate step with a budget
plan, not a free one" — the governance import alone once pushed the architecture scaffolding past
its document. Reversing this one would put architecture vocabulary into every requirements
prompt, in both profiles' interest of a single class.

## Options

1. **Flag it rather than classify it.** The requirements profile cannot type a decision, but it
   can decline to make a `Concept` of one: a fallback node whose label reads as a decision is a
   finding, which is the ISS-1 shape ("category nouns enter the graph as `Concept` nodes")
   arriving through free-text attribution. Cheapest, honest, changes no vocabulary.
2. **A `Decision` class in a lower layer**, promoted to `ArchitectureDecision` at reconciliation.
   Two classes for one concept is the duplicate-vocabulary risk this repo keeps finding, and the
   promotion step would need to be a real mechanism rather than a rename.
3. **Import `architecture_base` into `requirements_base`.** Inverts the dependency, taxes every
   requirements prompt, and makes requirements extraction aware of architecture rationale it has
   no business proposing.

**Recommended: (1).** The platform does not need requirements extraction to *type* a decision —
it needs it to stop silently absorbing one into the fallback bucket, which is the same rule
`check_names_are_anchored` and the C4 view's near-duplicate gap already apply elsewhere.

## What closes it

A chosen option; if (1), a validator finding for `Concept` fallbacks whose label is
decision-shaped, with the count reported the way the C4 view reports `unplaced` — a number a
reader can act on rather than a silent node kind.
