---
id: ADR-0002
legacy: "1"
title: "Tighten the extraction prompt: objects must be entities, not clauses"
status: accepted
date: 2026-09-20
area: "agents/knowledge_extraction/agent.py"
related: []
---

# ADR-0002 — Tighten the extraction prompt: objects must be entities, not clauses

> **Record.** Closed work, preserved verbatim from `TODO.md` v1 (ADR-0002).
> Legacy source: [`docs/todos/legacy-todo-v1.md`](../todos/legacy-todo-v1.md).

**Legacy status:** ✅ RESOLVED — node quality 35% → 6%
**Legacy priority:** Closed
**Legacy area:** `agents/knowledge_extraction/agent.py`

---

### Outcome

| | Baseline | After |
|---|---|---|
| Clause-like / contract violations | **35%** (14/40) | **6%** (2/34) |
| Triples | 40 | 34 |
| Entities | 14 | 23 |
| Distinct subjects | 5 | 10 |
| `ontology_class` populated | 40/40 | 34/34 |
| Distinct confidence values | 3 | 3 (0.8/0.9/1.0) |

Node quality now reads as actual graph nodes:

```
Payment Gateway Platform --has_functional_requirement--> Payment Request Mapping
Cardholder Data --encrypted_using--> TLS 1.2+
Transaction Routing --is_determined_by--> Country of Transaction
Role-Based Access Control Enforcement  (as a node, not a clause)
```

The two remaining flags are **false positives** of the heuristic — both are
legitimate compound names sitting 1–2 chars over the 40-char threshold
(`Payment Gateway Service Provider Selection`). Conservative over-flagging is the
right default; they cost a human one glance.

### Root cause — the prompt was teaching the bug

The few-shot example in the prompt contained the exact failure:

```json
"object": "Complete within 500ms"    ← a clause, presented as correct
```

The model was faithfully imitating the example. **Check examples before tuning
instructions** — a wrong example outweighs any amount of correct prose.

### What was changed

1. **Schema field descriptions tightened.** With structured output these *are*
   the prompt — they ship to the model as the tool schema. `object` now carries
   the rule, an explicit WRONG/RIGHT pair, and the one-triple-per-item rule.
2. **Prompt rewritten** with an OBJECT CONTRACT section, a worked WRONG→RIGHT
   example from the real document, and the document-structure rule (section
   headings are scaffolding, not concepts).
3. **Confidence guidance** now explicitly asks for variation, since a flat 1.0
   makes the human-review threshold useless.
4. **`_flag_contract_violations()`** — deterministic post-check, independent of
   model behaviour. Flags clause-shaped nodes, comma-lists, and clause markers.
   **Flags, never drops** — silently discarding extracted content is worse than
   surfacing it.

### Regression caught and fixed mid-work

The first pass fixed node quality (35% → 9%) but **silently dropped the
ontology's traceability predicates** — `traces_to_goal`, `traces_to_capability`,
`traces_to_process`, `binds_to_system` all vanished. Those edges are how the
Semantic Auditor finds gaps, so losing them is worse than clause-shaped nodes.

Cause: the contract emphasis crowded out inference of *implicit* traceability.
Fixed by adding explicit guidance that inferred traceability edges should still
be emitted, scored 0.5–0.8. Result: 5 traceability edges restored with honest
confidence scores (0.8–0.9), and violations improved further to 6%.

**Lesson: when tightening one dimension, re-measure the others.** A prompt fix
that improves the metric you're watching can quietly destroy a metric you aren't.

### Verification

Measured on `test_data/prd/sample_requirements.md` via
`metadata["contract_violation_count"]`. Re-run and compare if the prompt changes.
- Multi-value statements should produce N triples, not 1 comma-joined triple
