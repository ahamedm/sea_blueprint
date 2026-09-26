---
id: YB-039
legacy: null
title: "`ontology_class` on intra-graph slots is undefined — so the coverage invariant counts edges it cannot classify"
status: open
priority: low
area: "`core/knowledge/ingest.py`, `core/knowledge/model.py`, `scripts/run_extraction_tests.py`"
created: 2026-09-26
updated: 2026-09-26
record: null
superseded_by: []
related: ["YB-010", "YB-029", "ADR-0016", "ADR-0017"]
blocks: []
blocked_by: []
---

# YB-039 — `ontology_class` on intra-graph slots is undefined

> **Open work.** This file is the source of truth for this item; `TODO.md` is generated from it.

### What happens now

`assertion.ontology_class` is populated three ways, and only one of them has a
written rule:

| Producer | Example predicate | Class |
|---|---|---|
| the model, on an emitted triple | `has_functional_requirement` | `FunctionalRequirement` — the model's own choice |
| ingest, for a **reified cross-graph** predicate | `authorised_by_initiative` | `RequirementAuthorization`, from `PREDICATE_ONTOLOGY_CLASS` |
| ingest, for an **intra-graph slot** | `realizes_attribute` | **none** — `core/knowledge/ingest.py:655` passes no class |

`PREDICATE_ONTOLOGY_CLASS` (`core/knowledge/model.py:176`) maps only reified
classes: `implements_*` → `RequirementRealization`, `delivers_initiative` →
`InitiativeDelivery`, `authorised_by_initiative` → `RequirementAuthorization`.
`realizes_attribute` is none of those. It is a slot on `NonFunctionalRequirement`
with `range: QualityAttribute` (`ontology/requirements_base.yaml:1208`), pointing
at a node ingest materialises in the *same* graph.

### Why it matters

`inv_ontology_class_coverage` (`scripts/run_extraction_tests.py:62`) requires ≥80%
of `out["triples"]`. Measured on `data/sea-deepseek` (deepseek-v4-pro,
2026-09-26), on the canonical graph rather than the agent output:

- **requirements run** — 48/53 relationship assertions classified (91%), but
  **18/18 of the model-emitted triples** were classified. The other edges are
  ingest's: `authorised_by_initiative` 27/27 classified, `realizes_attribute`
  0/5 unclassified.
- **architecture run** — 132/191 (69%) in the canonical graph, while the *agent
  output* for the same document is **193/193 (100%)** classified. The
  unclassified edges are `satisfies_attribute` (32/32) and `applies_technique`
  (19/47), and both are **ingest-synthesised from an element field**, not emitted
  as triples: `core/knowledge/ingest.py:418`, `:480`, `:519`
  (`satisfies_attribute`) and `:484` (`applies_technique`) pass no class, exactly
  as `:655` does for `realizes_attribute`. The `arch` case carries no coverage
  invariant, so nothing reports this.

The pattern is consistent: **the model classifies every triple it emits** — 59/59
on the fresh `req_prd` run and 193/193 on the fresh `arch` run — and every
unclassified edge in the canonical graph is one ingest synthesised. The invariant
therefore measures ingest's bookkeeping as much as extraction quality, and is
silent on the architecture side entirely. Two consequences:

1. **A class invented to satisfy the metric would be worse than none.** No
   reified class exists for an NFR→QualityAttribute slot. Assigning one to raise
   a percentage is the "rigorously-derived wrong answer" of
   `docs/architecture-review.md` §2.7.
2. **`realizes_quality_attribute` is the sharper gap.** It *is* in
   `CROSS_GRAPH_PREDICATES` (`core/knowledge/model.py:135`) but absent from
   `PREDICATE_ONTOLOGY_CLASS`, so `ontology_class_for_predicate` returns `None`
   for an edge that genuinely crosses REQ-G ⇄ ARC-G.

### The decision

Decide what `ontology_class` means on an intra-graph slot, and whether the
coverage invariant should scope to reified / cross-graph edges only. Two readings,
to be settled rather than assumed:

- **Empty is correct** for a local slot — then the invariant over-counts and
  should be scoped, or synthesized local edges excluded from it.
- **Non-empty is required** — then the ontology needs a class per such edge, not
  a hardcoded string at the ingest site.

A third question rides along: whether the model should be asked for a class on a
local edge at all, given the model's answer and the ontology's reified class are
different kinds of statement.

### Why not `CROSS_GRAPH_PREDICATES`

`realizes_attribute` must **not** join that set. The contract there is "a
reference into another graph — keep it literal so reconciliation can find it. Do
NOT invent a local node" (`core/knowledge/ingest.py:727`). Routing it would
contradict the ontology slot and split one predicate between the entities path
(node) and the triples path (literal). Both `core/knowledge/realization.py:63-69`
and `core/knowledge/quality.py:88` (`STATED_PREDICATES`) depend on it staying
intra-graph, and the Design Assistant emits the same predicate for
`scenario --realizes_attribute--> QualityAttribute` (ADR-0017).

### Evidence

- `data/sea-deepseek/working.json` — deepseek-v4-pro, requirements + architecture
- `data/output/test_req_prd.json` — `req_prd` 7/7, 59 triples, 100% coverage
- `docs/design/real-run-readings.md` — why fixtures predating a change cannot answer it
