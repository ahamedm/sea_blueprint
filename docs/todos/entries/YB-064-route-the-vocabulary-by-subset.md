---
id: YB-064
legacy: null
title: "Context ingestion — route the vocabulary by subset instead of shipping every visible name"
status: open
priority: high
area: "`agents/base_agent.py` (`_collect_ontology_names`, `_format_ontology_context`), `core/ontology.py` (`OntologyModel.subsets`, `visible_layer_keys`), the per-profile entry schemas"
created: 2026-10-03
updated: 2026-10-03
design: null
record: null
superseded_by: []
related: [YB-007, YB-047, YB-055, YB-062]
blocks: []
blocked_by: []
---

# YB-064 — Route the vocabulary by subset, not by layer

> **Open. Filed from the context-ingestion analysis, 2026-10-03.** The grouping half is
> already modelled and simply unused; the work is a relevance filter over the groups, and
> it should start deterministic.

## What the agents are actually handed

`_format_ontology_context` builds the shared block every pass repeats. For the
architecture chain (`architecture_base.yaml`, visible to both the Design and
Architecture-extraction agents) the vocabulary half is measured as:

| | |
|---|---|
| Visible layers | `architecture`, `common`, `enterprise`, `requirements` |
| Classes + enums | **63 + 45** |
| As a flat `, `-joined name list | **1,938 chars** |

That is names only — no values, descriptions or groupings. The one block shipped with
full grouping is the ISO 25010 quality model (`_quality_model_context`), and the pattern
catalogue deliberately ships names plus categories (`pattern_prompt_context`, 982 chars),
resolving mechanisms later through `canonical_pattern`. So the reduction problem is the
flat name list, not deep content.

| Family | Design | Arch-extraction | Knowledge-extraction (requirements) | Form |
|---|---|---|---|---|
| Patterns / styles / techniques | yes | yes | no (layer not imported) | class+enum names; catalogue names+categories |
| Tech-Radar (`TechnologyStack`, `TechnologyCategory`, `TechnologyRing`) | yes | yes | no | names; `TechnologyCategory` values reach extraction only via the `TechnologyStackRecord.category` `Literal`; **`TechnologyRing` values reach no prompt at all** |
| Policies (`Policy`, `PolicyDomain`, `StandardClause`) | no | no | no | **reaches no agent** — nothing imports `governance_base` |

## The two facts that make this cheap to start

1. **The grouping axis already exists and routing ignores it.** The ontology declares 15
   `subsets` (`C4Model`, `ArchitectureRationale`, `QualityAttributes`, `PlatformModel`,
   `Compliance`, …), parsed into `OntologyModel.subsets`. `_collect_ontology_names`
   (`base_agent.py`) scopes by `visible_layer_keys` (`core/ontology.py:1464`) — the import
   graph — and never consults them. What is missing is a *relevance filter over groups*,
   not the groups.
2. **A coarse deterministic filter already runs.** `visible_layer_keys` is precedent for
   gating vocabulary on a signal the run already computes, and its comment records why the
   gate exists at all: the governance layer alone had pushed the architecture scaffolding
   past the document it describes.

## Escalating forms, cheapest first

- **A — deterministic subset gating (start here).** Gate each subset on a signal the run
  already has. The Design agent already computes `stated_attributes`
  (`design_assistant/agent.py:143`): when it is empty, drop the ISO 25010 block and the
  `QualityAttributes` subset. No platform noun in the digest, drop `PlatformModel` and the
  Tech-Radar names. An extraction profile does not propose patterns, so drop
  `ArchitectureRationale`. Zero latency, reproducible, auditable.
- **B — a cheap classifier pre-pass.** One structured call scoring each subset against the
  document, threshold, inject the top-K. Must run **once per document**, never per
  pass or chunk, and its own cost must be netted against the saving × (passes × chunks) —
  the shared block is repeated per call, which is exactly why the saving multiplies.
- **C — embedding similarity.** No model call, needs an embedder, least auditable. Not
  until A and B are measured.

Hook point for all three: `_collect_ontology_names` / `_format_ontology_context`, replacing
"all names in the visible layers" with "names in the selected subsets".

## Where this goes wrong, and why the guardrails are the deliverable

1. **A silent vocabulary drop is this repo's worst failure mode.** The top-ranked defects
   are all quiet ungrounding — declared-but-unemitted, invented predicates, the `Concept`
   fallback. A run that reports COMPLETE because a needed term was filtered out is that
   failure with a new cause. So the filter must be **recoverable** (an "expand group X"
   path, or a second retrieval pass) and **recorded** (per-group routing in the run
   metadata), in the same spirit as `compute_completeness` treating "no error" as not the
   same as "complete".
2. **Filter at document level, not per chunk.** Extraction profiles process chunks and
   relevance differs per chunk, but per-chunk filtering makes prompts non-reproducible for
   a signal too noisy to justify it.
3. **Cost accounting is the measurement.** Without netting the classifier against the
   saving this moves the problem rather than solving it.
4. **Reproducibility.** Prompt order is stable today so diffs stay readable. A
   probabilistic filter breaks that unless threshold, scores and selected groups are
   logged. Form A has none of this problem.

## Relationship to ISS-15 and YB-007

[ISS-15](../../../ISSUES.md#iss-15--the-design-profiles-prompt-scaffolding-sits-at-its-ceiling-so-any-vocabulary-growth-breaks-the-budget-guard)
measures the design profile's scaffolding at 100% of its document, dominated by the shared
vocabulary block (5,174 of 7,276 chars). Its option (a) — measure the profile's own
scaffolding and route the shared vocabulary separately — is *enabled by* this item rather
than parallel to it, so the two should land together.

## What closes it

Form A implemented for at least the three subsets with an existing deterministic signal,
the routing decision recorded per run, an expansion path for a filtered-out group, and a
before/after measurement of the shared block against YB-007's ratio.
