---
id: ADR-0030
title: "Refusals and accounting at the ingest boundary — and a harness that can be wrong out loud"
status: accepted
date: 2026-09-28
area: "`core/knowledge/model.py`, `core/knowledge/ingest.py`, `core/knowledge/review.py`, `core/knowledge/reconcile.py`, `core/knowledge/serialise.py`, `agents/architecture_extraction/agent.py`, `app/__init__.py`, `scripts/run_extraction_tests.py`, `test_data/arch/payment_platform_arch.md`"
related: ["YB-051", "YB-052", "YB-053", "YB-054", "ADR-0013", "ADR-0029"]
---

# ADR-0030 — Five reliability levers, and what each one bought

> **Record.** The first tranche taken from
> [the reliability brainstorm](../design/extraction-reliability-levers.md) §3:
> output-consumption accounting (§3.1), a paid-for run kept on a post-extraction
> error (§3.3), invariants that refuse rather than flag (§3.4, YB-052), the harness
> with tolerances (§3.13), and the fixture that states its own request path (YB-053
> lever 1). The brainstorm was a holding pen with no entries; this is what was
> chosen, what it measured, and what it deliberately left alone.

### Why these five

§1 of the brainstorm measured nine defects from one session and only two were
model-quality. Five were the system losing or mangling work the model had already done
correctly: a whole pass's output never read, findings computed and dropped, two runs
colliding on an id, a flawless run discarded by a representation mismatch **after**
12/12 passes and 146 s of paid model calls. Separately, four `X part_of X` facts —
which cannot be true of anything — were bulk-verified into `VERIFIED`, and the C4
view found 23 code-level "elements" that were quality attributes and conventions.

So the tranche is biased toward the boundary rather than the prompt: make loss and
impossibility *structural* where a model cannot be trusted to avoid them, and make
the whole thing measurable so the next change is falsifiable.

### 1. An impossible fact is refused at the graph's write boundary, and reported

`KnowledgeGraph.add_assertion` now refuses `subject == object` for a declared set of
irreflexive predicates (`part_of`, `hosts`, `depends_on`, `connects_to`, …) and
appends a `RefusedAssertion` to `graph.refusals` instead of storing it.
`add_assertion` returns `Optional[Assertion]`, which forced the three callers that
use the returned object to state what a refusal means for them:
`review.correct` raises rather than recording a replacement the graph does not hold,
`reconcile.resolve_reference` raises rather than superseding an assertion with one
that was refused, and `merge_graphs` skips the lineage copy.
`graph.refusals` is not serialised — it is a diagnostic, so it is copied onto the
`ExtractionRun` that produced it.

The review gate refuses to verify one, singly and in bulk. `bulk_apply`/`bulk_verify`
now return a `BulkResult` (decisions + skipped-with-reason) instead of a bare
`list[Decision]`, and the page composes its flash from the reasons, so
"1 refused ('part_of' is irreflexive …)" is distinguishable from "2 already in that
state". The type stays list-compatible (`__len__`, `__bool__`, `__iter__`), so callers
that only counted did not change. A graph can still *hold* a reflexive fact — a
revision written before this boundary — which is why the refusal is in `verify` too,
not only in the writer.

### 2. Every emitted output key is accounted for, statically and at runtime

`ingest.INGESTED_OUTPUT_KEYS` is the list of keys `graph_from_extraction` reads, and
the run records `output_counts` (per key: emitted, consumed), `unconsumed_keys`, and
`stored_facts` — the "emitted N, stored M" report §3.1 asked for. Two guards, because
the one that would have caught the original defect is not the one a counter provides:

- **Static**: `tests/test_output_consumption.py` asserts every `PassSpec.output_keys`
  value, for both profiles, is in `ROUTED_OUTPUT_KEYS` or is an explicitly explained
  `UNROUTED_OUTPUT_KEYS` entry. Adding a pass output that nothing reads now fails a
  test rather than losing facts.
- **Runtime**: an emitted non-empty collection of records with no consumer is logged
  and recorded on the run. This is the guard that catches a profile emitting a key
  nobody wrote a test about.

`triples_produced` could not have caught the connections defect: that pass emitted
triples *as well*, so its count was non-zero while its connections were discarded.
The unit of the guard is the key, not the pass.

### 3. A post-extraction error keeps the paid-for run, and the verdict is honest

The architecture agent's merge, repair and validation stages each run under a guard.
A failure appends a `post_extraction_error` finding, keeps whatever merged, and
appends a synthetic `(post-extraction)` `PassRecord` with `outcome="failed"` — so the
run's own `compute_completeness` reports `PARTIAL` rather than a hole presenting as
`COMPLETE` (the false assurance ADR-0013 exists to prevent). Pinned by
`tests/test_post_extraction_failure.py`, including the counter-case: a clean run
carries no such record.

### 4. The harness is a measurement with gates, budgets and a committed baseline

`scripts/run_extraction_tests.py` is no longer `[DRAFT]`. Every `inv_*` is classified
in one table as a **gate** (a structural or contract property: failure means the run
is broken, exit non-zero) or a **budget** (a quality count that legitimately moves;
tolerance band against `scripts/extraction_test_baseline.json`, WARN only).
Classification is self-checked at startup, so an unclassified invariant aborts the
harness rather than passing by omission. `--update-baseline` implies `--validate-only`
and never calls a model. `scripts/c4_scorecard.py` runs as a subprocess and folds in
as gates (`reflexive`, `nesting`, `acyclic`, `arrows`) and budgets (defect counts).

Two of the scorecard's readiness rules were deliberately made budgets, not gates:
`levels_stated` and `runs_complete`. YB-053 records this exact test case at 25/26
levels (one element's level is inferred *and said to be*, "not a defect") and one
`empty` connections answer, which ADR-0013 deliberately counts against completeness.
A gate that fires on a state this project has argued is honest teaches its reader to
ignore gates — the same failure §1 records for the validators' three false positives.

### 5. The fixture states the request path, and extraction now finds it

`test_data/arch/payment_platform_arch.md` stated container relationships in exactly
one place (§2.7's "Relationships" list). The path an architect cares about —
orchestrator → routing engine → PGSP gateway — was never asserted, and appeared in
one run of two. §2.1/§2.2/§2.3 now carry the same shape of `**Relationships:**` list.
A live `deepseek-v4-pro` run on the edited fixture (4 chunks, 16 calls, 15 ok, 1
empty, 0 failed, 188 s) produced **15 connections including both path edges**:

```
Payment Orchestrator            -> Payment Routing Decision Engine
Payment Routing Decision Engine -> PGSP Gateway with Request/Response Adapter
```

That is one sample, not the "in every run" the item's acceptance asks for; the
harness and the baseline are what turn it into a distribution.

### What this measured that was not known

- **The requirements profile emits two collections nothing reads**:
  `contract_violations` and `low_confidence_items`. The new accounting reports them;
  no consumer renders them to a reviewer. Recorded as
  [YB-054](../todos/entries/YB-054-unrouted-requirements-output-keys.md).
- **`is_synchronous` is still unset on every connection** the live run produced,
  although `style` determines it (`SYNCHRONOUS_REQUEST_RESPONSE` was set on 14 of
  15). It stays open in YB-051 decision 2 — a deterministic derivation, not a model
  question.
- **A saved architecture output leaks a technology as an element.** The harness's
  `inv_no_tech_leak` gate fails on the committed `data/output/test_arch.json`
  (`Quartz`). The fixture is a local, gitignored artifact, so this is a stale-output
  finding rather than a current-extraction regression — but the gate is right to
  fail, and the leak is the YB-053 defect-1 family.
- **Two tests in the suite fail on pristine `HEAD`**, from those same stale local
  fixtures (`data/output/test_arch.json`, `test_req_prd.json`): the gap-report counts
  and the realization fixture. They are environmental, not regressions from this work,
  and are left failing rather than "fixed" by editing expectations around a stale file.

### Deliberately not taken

`is_synchronous` derivation (§3.7) and prompt/ontology version stamps on provenance
(§3.15) are the natural riders and were not done here. Span anchoring and constrained
decoding (§3.6/§3.5), self-consistency (§3.9), the verdict feedback loop (§3.12) and
DSPy-class compilation (§3.G) all presuppose this tranche's measurement: §3.13 exists
so that "did it improve?" has an answer, and it now does.
