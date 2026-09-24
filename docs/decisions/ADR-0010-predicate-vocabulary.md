---
id: ADR-0010
legacy: "21"
title: "Predicate vocabulary — the relationship names were never sent"
status: accepted
date: 2026-09-23
area: "core/ontology.py, agents/base_agent.py"
related: ["YB-007"]
---

# ADR-0010 — Predicate vocabulary — the relationship names were never sent

> **Record.** Closed work, preserved verbatim from `TODO.md` v1 (ADR-0010).
> Legacy source: [`docs/todos/legacy-todo-v1.md`](../todos/legacy-todo-v1.md).

**Legacy status:** Implemented — measured, with an open trade recorded
**Legacy priority:** Done, with a caveat owned by item 7
**Legacy area:** `core/ontology.py` (`relationship_predicates`), `agents/base_agent.py` (`_predicate_vocabulary_context`)

---

### What was wrong

`_collect_ontology_names` had always injected CLASS and ENUM names. The **predicate**
axis was the one nobody wired up: `ExtractedTriple.predicate` is free text, and no
declared relationship name ever reached a prompt. Measured consequence before the fix:
**15 of 16 predicates invented, 1 routed** to reconciliation. The rest became local
edges no consumer reads.

The names existed all along as 147 relationship slots. This is the same inert-layer
pattern as the old `domain` field — the mechanism was there, nothing used it.

### What was built

- `relationship_predicates(model)` derives the vocabulary from the schema, filtered by a
  documented name-shape heuristic. The heuristic matters: a naive "range is a class" filter
  yields **128** candidates, most of them record fields (`assumptions`, `applications`,
  `activities`) that would teach the model to emit `--assumptions-->`. The filter yields
  **47**; `predicate_vocabulary_findings()` reports the 91 it drops, so its blind spots are
  visible rather than silent.
- `CORE_ROUTED_PREDICATES` + `ROUTING_ALIASES` name the 11 predicates the graph routes on
  and the singular spellings reconciliation already accepts. **Tests assert the prompt's
  list cannot drift from `CROSS_GRAPH_PREDICATES`** — that is the lie this duplication
  could otherwise tell.
- `ABSORBED_DRIFT_PREDICATES` records three routing entries that match **no declared
  slot** (`implements_functional_requirement`, `implements_non_functional_requirement`,
  `supports_business_capability`) — aliases with nothing behind them, kept so the routing
  table reads honestly rather than looking merely incomplete.

### The measured result and the cost

See the block above: adoption **6% → 67%**, routed **1 → 3**, at **5× the wall-clock**,
and the brief's dilution ratio crossing YB-007's 2.5:1 threshold. Recorded rather than
celebrated, because a 5× slowdown for a 10-of-15 adoption rate is a trade someone should
be able to reverse.

### Not done

- Nobody has verified this over repeated runs; the elapsed-time delta especially needs
  repeats before it is quoted as a fact.
- The predicate spelling is not aligned repo-wide (schema plural vs prompt singular).
  `ROUTING_ALIASES` documents the accepted pairs instead, which is honest but is a
  workaround rather than a fix.
