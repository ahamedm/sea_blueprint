---
id: YB-031
legacy: null
title: "The predicate vocabulary is taught in its plural schema form and routed only in the singular"
status: done
priority: high
area: "`core/knowledge/model.py` (`CROSS_GRAPH_PREDICATES`), `core/ontology.py` (`CORE_ROUTED_PREDICATES`, `ROUTING_ALIASES`), `agents/base_agent.py`"
created: 2026-09-25
updated: 2026-09-26
design: docs/design/real-run-readings.md
record: docs/decisions/ADR-0018-cross-graph-vocabulary.md
superseded_by: []
related: ["YB-010", "YB-030", "YB-005"]
blocks: []
blocked_by: []
---

# YB-031 — Taught plurals, routed singulars

> **Closed 2026-09-26.** The record is
> [`ADR-0018-cross-graph-vocabulary`](../../decisions/ADR-0018-cross-graph-vocabulary.md), which carries the decision and
> the evidence. The write-up below is preserved as it stood when the item
> was written.

> **Found by the real run of 2026-09-25.** The measured table is in
> [`docs/design/real-run-readings.md`](../../design/real-run-readings.md) §Reading 2.

### The defect

The prompt sends the model the ontology's own relationship names, so the spellings
it is taught are the schema's: `implements_requirements`, `satisfies_quality_attributes`.
The router knows the singular forms:

```
CROSS_GRAPH_PREDICATES contains   implements_requirement          (singular)
it does NOT contain               implements_requirements         (plural)
it contains                       satisfies_quality_attribute     (singular)
it does NOT contain               satisfies_quality_attributes    (plural)
```

`ROUTING_ALIASES` already records half of this — it maps
`satisfies_quality_attributes → satisfies_quality_attribute` — but the map is only
used to *describe* the aliases in the prompt, not to resolve them at ingest. There is
no alias entry for `implements_requirements` at all.

### What it costs

An unrouted predicate falls to the local-edge branch, so the object is resolved to a
node by label instead of being kept as a cross-graph reference. The edge then exists
in the graph, is drawn by the map (which routes on the same set), is invisible to
reconciliation, and is not counted by the realization report. Measured on the merged
working set:

| Predicate | Count | Routed |
|---|---|---|
| `implements_requirement` | 36 | yes |
| `implements_requirements` | 1 | **no** — local edge |
| `satisfies_quality_attribute` | 24 | yes |
| `satisfies_quality_attributes` | 4 | **no** — local edge |

Eight facts from one real run are therefore unreachable by every consumer that
routes on the cross-graph set. It is not a display problem: reconciliation never
offers them, so they can never be bound.

### What the fix has to decide

1. **Normalise at ingest, or extend the set.** Resolving through a single alias map
   is one definition of "these are the same predicate" and fixes every consumer at
   once. Extending `CROSS_GRAPH_PREDICATES` with the plurals is smaller but leaves two
   names in the graph for one relationship, which is its own reconciliation debt.
2. **Whether the schema names should change.** `satisfies_quality_attributes` ranges
   over `NonFunctionalRequirement` and the singular does not exist in the schema; the
   duplication was invented by the routing table.
3. **`CORE_ROUTED_PREDICATES` duplicates the set deliberately** because `core/ontology.py`
   must stay importable without the knowledge layer. A test asserts the two agree —
   that test currently compares against the *taught* names, so it may be asserting the
   drift is stable rather than absent. Worth reading before changing either.

### Acceptance

- A predicate the prompt teaches routes as a cross-graph reference.
- One relationship has one routed name, whatever the model writes.
- The taught vocabulary and the routed vocabulary are proven equal by a test.
