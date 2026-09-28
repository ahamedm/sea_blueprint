---
id: YB-054
legacy: null
title: "The requirements profile computes contract violations and low-confidence items that no consumer reads"
status: open
priority: medium
area: "`agents/knowledge_extraction/agent.py` (emits them), `core/knowledge/ingest.py` or the run pipeline (must read them), `app/templates/` (where a reviewer would see them)"
created: 2026-09-28
updated: 2026-09-28
design: docs/design/extraction-reliability-levers.md
record: null
superseded_by: []
related: ["YB-051", "YB-053", "ADR-0030", "ADR-0013"]
blocks: []
blocked_by: []
---

# YB-054 — Requirements output keys with no consumer

> **Open, found by the new accounting on its first real output.** This is the
> [YB-051](YB-051-connections-dropped-at-ingest.md) family caught one layer down:
> not a pass whose output was never read, but two collections the requirements
> profile computes on every run and no part of the pipeline looks at.

### What was measured

`agents/knowledge_extraction/agent.py` writes into its output envelope:

```python
"low_confidence_items": [t.model_dump() for t in low_confidence],
"contract_violations": contract_flags,
```

On the saved requirements output the accounting now reports:

| Key | Emitted | Consumed |
|---|---|---|
| `triples` | 59 | yes |
| `entities` | 52 | yes |
| `contract_violations` | 1 | **no** |
| `relationships` | 1 | no — explained, see below |

`grep -rn "contract_violations\|low_confidence_items"` finds the writer and
`scripts/run_extraction_tests.py`: nothing in `core/knowledge/`, `app/runner.py`, or
a template reads either. The harness gates on the contract ratio, so the number is
*measured* — just never shown to the person reviewing the extraction.

### Why it matters

- **`contract_violations` is the clause-shaped-node defect class.** "Objects must be
  entities, not clauses" is the ADR-0002 change, and the architecture profile routes
  the same kind of flags through `output["findings"]`, where the run page shows them.
  The requirements profile computes them and drops them, so the two profiles disagree
  about whether a deterministic finding is worth reporting — an inconsistency, not a
  design.
- **`low_confidence_items` may be genuinely redundant.** The review gate's threshold
  path selects by `assertion.confidence`, which ingest already stores, so a second
  list of the same triples may duplicate a fact the graph holds. That is a decision
  to make, not an assumption to keep.
- The accounting's `UNROUTED_OUTPUT_KEYS` is for keys whose loss is *explained and
  safe* (`relationships`: a predicate vocabulary with no endpoints, carried by the
  triples). These two are neither: they are unaccounted.

### Shape

1. **Route `contract_violations` where findings go.** Either fold them into the
   profile's `findings` (matching the architecture profile) or teach ingest to read
   the key and record them on the run. One place, because two shapes would drift.
2. **Decide `low_confidence_items`.** If the confidence on the assertion is the same
   fact, delete the key and say so in `UNROUTED_OUTPUT_KEYS`; if it carries something
   the graph does not (the reason it was retained despite the threshold), route it.
3. **Watch the accounting stay honest.** Whatever is chosen, the key must end up in
   `INGESTED_OUTPUT_KEYS`/`DOWNSTREAM_OUTPUT_KEYS` or in `UNROUTED_OUTPUT_KEYS` with
   its reason — the static seam test in `tests/test_output_consumption.py` enforces
   that for `PassSpec` keys and profile `_output_keys()`, and this entry is the
   runtime half that caught a key the static test cannot see.

### Acceptance

- A requirements extraction with contract violations shows them to a reviewer (run
  page or review queue), or the key is removed and the reason recorded.
- `low_confidence_items` is either routed or deleted with the duplication
  demonstrated.
- `run.unconsumed_keys` is empty on a requirements run, or contains only keys listed
  in `UNROUTED_OUTPUT_KEYS`.

### Related

- [YB-051](YB-051-connections-dropped-at-ingest.md) — the original "emitted and never
  read" defect, and the accounting built to catch it.
- [ADR-0030](../../decisions/ADR-0030-boundary-refusals-and-output-accounting.md) —
  where the accounting and the runtime report landed.
- [ADR-0013](../../decisions/ADR-0013-requirements-completeness-reporting.md) — the run
  describes its own completeness; a findings collection nothing reads is the same
  question one step further out.
