---
id: ADR-0018
title: "The cross-graph vocabulary — one routed name per relationship, and the profile that may speak it"
status: accepted
date: 2026-09-26
area: "core/ontology.py (ROUTING_ALIASES, canonical_predicate, visible_layer_keys, relationship_predicates), core/knowledge/ingest.py, core/knowledge/realization.py, core/knowledge/reconcile.py (node_sides), agents/base_agent.py (_predicate_vocabulary_context), tests/test_ontology.py, tests/test_semantic_accuracy.py, tests/test_app.py"
related: ["YB-030", "YB-031", "YB-007", "ADR-0010", "ADR-0012", "ADR-0016"]
design: docs/design/real-run-readings.md
---

# ADR-0018 — The cross-graph vocabulary

> **Record.** Closes [YB-031](../todos/entries/YB-031-cross-graph-predicate-plural-routing.md)
> and [YB-030](../todos/entries/YB-030-requirements-profile-cross-graph-claims.md).
> The measurement is [`docs/design/real-run-readings.md`](../design/real-run-readings.md) §Reading 2.

Two defects that looked like separate bugs turned out to be one boundary seen from
two sides: what the model is TAUGHT to write, and what the graph ROUTES when it
writes it. They are closed together because fixing either alone makes the other
worse, which is the load-bearing part of this record.

---

### The defect, measured

**YB-031 — taught plurals, routed singulars.** `CORE_ROUTED_PREDICATES` is what the
prompt spells out with targets, and it holds the schema's own spellings, which are
plural. `CROSS_GRAPH_PREDICATES` is what the router routes, and it holds the
singular. `ROUTING_ALIASES` recorded the difference for six relationships and was
read only to *describe* aliases in the prompt — nothing resolved through it at
ingest. Three relationships had no entry at all, and they are the important ones:
the join itself and two traceability axes. On the merged working set:

| Predicate | Count | Routed |
|---|---|---|
| `implements_requirement` | 36 | yes |
| `implements_requirements` | 1 | **no** — local edge |
| `satisfies_quality_attribute` | 24 | yes |
| `satisfies_quality_attributes` | 4 | **no** — local edge |

An unrouted predicate becomes an edge to a node resolved by label. It is drawn by
the map — which routes on the same set — and is invisible to reconciliation, to the
realization report and to the quality census. Eight facts from one run were
unreachable by every consumer that matters, while looking perfectly present.

**YB-030 — the requirements profile claimed architecture-side realization.**
`realization_edges` counts a claim as bound when `reference_targets_a_node` finds
its target, and that resolution is deliberately generous: an exact label resolves.
So on a requirements-only graph — **no architecture nodes at all** — the
requirements document's own `System` node resolved its `implements_requirement`
straight onto the requirement's label:

```
requirements 12   realized 11   bound_edges 35
  Payment Gateway Platform --implements_requirement--> Payment Request Acceptance
```

Eleven of twelve requirements read as answered by an architecture that does not
exist. The run-dependence made it worse: the same document also emitted the plural,
which did *not* inflate because it was unrouted. **The trustworthiness of the gap
report depended on which synonym the model chose.**

### Why they had to be fixed together

YB-031's obvious fix — resolve aliases at ingest so the taught plural routes —
*routes the plural into the same join*, so the requirements profile's false claims
would start counting too. **031 alone makes 030 worse.** The order is therefore
load-bearing: stop the claim being made, then make the spelling route.

The reverse is also true. Scoping the vocabulary alone (030's prevention) leaves
the existing graphs holding unreachable edges, and the next profile to be taught a
plural repeats 031.

---

### Decision

**1. One routed name per relationship, resolved at ingest.**

`canonical_predicate(name)` in `core/ontology.py` maps a spelling onto the name the
graph writes, and ingest calls it on every triple predicate before the cross-graph
test. Two rules, and the second is the one that matters:

- an alias is followed to its routed target;
- **a predicate the router already routes is never rewritten.**

Rule 2 is why this canonicalises the boundary rather than normalising the
vocabulary. `implements_functional_requirement` routes with the target
`FunctionalRequirement`; collapsing it to the general `implements_requirement`
would lose that precision for nothing. Three aliases were added
(`implements_requirements`, `addresses_goals`, `delivers_initiatives`), and they are
now also *offered* in the prompt, so the model is told the working spelling instead
of being left to guess it.

**2. The vocabulary an agent is taught is scoped to the layers its schema reaches.**

`visible_layer_keys(model, entry)` follows `imports:` from the agent's own
`ontology_path`, and `relationship_predicates(model, layers=...)` filters on the
layer that DECLARES the slot. `architecture_base` imports the three beneath it, so
the architecture profiles still see all 47 predicates and are unchanged. The
requirements profile drops to 22 and no longer sees `implements_requirement`,
`satisfies_quality_attributes` or `mandated_by` — all declared on
`ArchitectureElement`, none of which a requirements document can honestly assert.
No entry schema named means **unscoped**, so an agent with no `ontology_path` keeps
working.

This is the honest half of YB-030's option 1: the prompt was offering the whole
ontology root to every profile, and the join's domain was never checked against
what the profile could speak.

**3. A realization link requires an architecture-side source.**

`realization_edges` and `realization_state` skip a claim whose source node is known
to be on the requirements side, using the `node_sides` derivation reconciliation
already had. The claim is **not deleted**: it still surfaces as an unmet obligation
from the source side, which is the right reading of a requirements document naming
the system that will implement it. A source of *unknown* side is still allowed, so
fixtures and older graphs without recorded `document_type` are unaffected.

---

### What was rejected

- **Extending `CROSS_GRAPH_PREDICATES` with the plurals.** Smaller, but leaves two
  names in the graph for one relationship, which is its own reconciliation debt and
  would leave the graph's vocabulary disagreeing with the router's.
- **Renaming the schema slots to the singular.** Would make taught and routed
  identical, but changes the ontology and every existing graph for no gain the alias
  map does not already give.
- **Deleting the requirements-side claim at ingest.** It is a real statement about
  the document; the defect was that it was read as the join, not that it was said.
- **The `rstrip("s")` comparison.** `tests/test_ontology.py` asserted the two
  vocabularies agreed *after stripping a trailing "s"* — i.e. it normalised away
  exactly the discrepancy that caused the bug, and passed while ingest, comparing
  literal strings, dropped every plural edge. It was asserting the drift was
  tolerable, which was never the claim. It now resolves through
  `canonical_predicate`, the same function ingest calls.

### What this does not do

- It does not re-ingest `data/sea`. The query-layer guard reclassifies the existing
  graph without one; the ingest fix only affects new runs.
- It does not give the requirements-side claim its own predicate (`implemented_by` /
  `binds_to_system`, YB-030 decision 2). The claim is distinguishably *not* the join
  by its source side, which satisfies the acceptance; a dedicated predicate is a
  vocabulary addition nobody has asked for yet.
- It does not settle YB-027. The unseeable references there are a paraphrase gap,
  not a routing one.

### Evidence

```
tests/test_ontology.py            — taught ⇒ routed, through the ingest resolver
tests/test_semantic_accuracy.py   — the plural routes, one name per relationship,
                                    a routed name is never rewritten, the layer
                                    scoping, and the requirements profile's prompt
tests/test_app.py                 — a requirements-only graph realizes nothing
tests/test_realization.py         — the requirements-side unmet obligation survives
```

656 tests pass. The real-run readings that found these are recorded in
`docs/design/real-run-readings.md`, from one document, one model, one run — the
severity is evidenced, the frequency is not.
