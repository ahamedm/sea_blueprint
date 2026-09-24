---
id: ADR-0006
legacy: "14"
title: "Bulk reference resolution — reconciliation, first slice"
status: accepted
date: 2026-09-22
area: "core/knowledge/reconcile.py, core/knowledge/ingest.py, app/"
related: []
---

# ADR-0006 — Bulk reference resolution — reconciliation, first slice

> **Record.** Closed work, preserved verbatim from `TODO.md` v1 (ADR-0006).
> Legacy source: [`docs/todos/legacy-todo-v1.md`](../todos/legacy-todo-v1.md).

**Legacy status:** ✅ IMPLEMENTED — see [`docs/ui-review-workflow.md`](docs/ui-review-workflow.md) §4
**Legacy priority:** Closed for this slice; follow-ups below
**Legacy area:** `core/knowledge/reconcile.py`, `core/knowledge/ingest.py`, `app/`

---

### What this closes

YB-005 called ARC-G ⇄ REQ-G linkage *"the platform's core purpose, currently
unmet"*. It is still unmet in full, but unresolved references now have a place to
go: a projection that proposes targets, a decision that records the binding, and a
bulk path that is honest about what it declines.

| | |
|---|---|
| Unresolved references | 13 |
| Resolvable at the default 0.75 threshold | 1 |
| Below threshold | 3 |
| No candidate of the expected kind | 9 |
| Predicate looks wrong | 2 |

### Design decisions worth keeping

1. **Kind scoping is mandatory.** Measured on the real ARC-G output, unscoped
   best-match picks `Concept:'Card Payment Processing'` (0.94) over the correct
   `BusinessCapability:'Unified Payment Processing'` (0.92) for
   `supports_capability → 'Payment Processing'`. Lexical similarity alone prefers
   the wrong kind of thing, so every predicate declares the kinds it may point at
   and bulk resolve never crosses them. An override is available and recorded.
2. **Resolution is not `review.correct()`.** `correct()` refuses to turn a
   cross-graph reference into a node, because doing that silently erases the
   resolved/unresolved distinction. Resolution is the deliberate opposite act.
3. **Bulk reports what it declined.** `below_threshold`, `no_candidate` and
   `unknown_ids` are returned and surfaced, not swallowed. A wrong traceability
   link is worse than a missing one.
4. **`mislabel_suspected`** — a strong match in the wrong kind means the predicate
   is probably wrong. On the real data, two `traces_to_goal` references score 0.90
   and 0.86 against `FunctionalRequirement`s with no `BusinessGoal` candidate at
   all: the extractor attached a goal predicate to a function name. This is an
   extraction finding surfaced by reconciliation, and it is worth acting on
   upstream (YB-007 / YB-012).

### Bug found and fixed while building this

**Requirement IDs never reached the graph.** The requirements profile emits
`requirement_id`; `ingest._collect_declared_nodes` read only the architecture
profile's `external_references`. So the document's own stable key was discarded at
ingest — meaning root cause 1 of YB-005 was still live *below* the extractor, and
reconciliation's strongest signal was structurally unreachable no matter how good
the extraction got. `ingest._external_refs` now reads both shapes.

**Caveat, stated plainly:** the saved fixture in `data/output/` carries **zero**
`requirement_id` values (0 of 54 entities) and an empty `references` collection,
because it predates that work. The fix is therefore forward-looking for this data;
`test_a_preserved_id_joins_two_documents_end_to_end` proves the path works end to
end when a key does survive.

### Follow-ups

1. **Invert the direction.** All unresolved references currently run ARC → REQ.
   REQ-G emits no cross-graph predicates, so "requirements with no architectural
   answer" cannot be computed at all. It is the inversion of the resolved links,
   and it needs links that resolve first.
2. **Create the missing target.** Deliberately not offered: resolution asserts the
   referent was already extracted. A separate, explicitly different action should
   handle "the architecture references a requirement the document never stated" —
   which is itself a finding, not a binding.
3. **Semantic matching.** Lexical candidates cannot bridge a paraphrase with no
   shared vocabulary. Embedding or model-assisted proposals belong here, with the
   same propose/decide split and the same audit trail.
4. **Surface the mislabel finding upstream.** `traces_to_goal` carrying function
   names is an extraction defect (YB-007's pass-split, or YB-012's deterministic
   C4 parser).
5. **Coverage reporting.** `RequirementRealization` carries `coverage` and
   `evidence` in the ontology; resolution currently sets `ontology_class` but
   populates neither.
