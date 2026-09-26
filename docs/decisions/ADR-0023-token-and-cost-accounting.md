---
id: ADR-0023
title: "Per-run token and cost accounting — read per invocation, priced from configuration"
status: accepted
date: 2026-09-26
area: "agents/base_agent.py (UsageTotals), core/knowledge/model.py (ExtractionRun.usage), core/knowledge/ingest.py, core/knowledge/serialise.py, agents/cli.py"
related: ["YB-020", "ADR-0021", "ADR-0022"]
---

# ADR-0023 — Per-run token and cost accounting

> **Record.** Built because moving off a local server changes the failure mode from
> "slow" to "expensive". No TODO entry tracked it.

---

### Why the run, and not the request

A provider reports usage per REQUEST. A profile makes many — six passes for a
design, four passes over three chunks for an architecture document, twelve calls on
one real ingest. The number anyone wants is the run's, and nothing in the platform
was reading usage at all: every handler took `structured_output` and dropped the
metrics on the floor.

Accumulating on the agent also survives a pass whose result was discarded, which is
the case most worth knowing about — work that was paid for and thrown away. A call
that hit the turn cap or the wall-clock budget is recorded, not excluded.

### Read per INVOCATION, which is not obvious and was wrong twice

`EventLoopMetrics.accumulated_usage` is documented as accumulating "across all
model invocations (across all requests)", and the SDK's `reset_usage_metrics` only
**appends** an invocation — it never zeroes the lifetime total. Reading it per call
therefore sums a running total. On the twelve-call architecture ingest that
reported 2,042,992 input tokens against a true figure near 314,000.

The first fix did not take: `latest_agent_invocation` is a **property**, so a
`callable()` guard silently took its fallback branch and the bug survived its own
repair. Both the wrong figure and the wrong fix are now pinned by a regression test
that asserts three calls of 100 tokens total 300 — summing the lifetime total would
give 600.

### Prices are configuration, not a table in source

A hard-coded price is wrong within weeks, and silently wrong is worse than absent.
`MODEL_PRICE_INPUT_PER_MTOK`, `MODEL_PRICE_OUTPUT_PER_MTOK` and
`MODEL_PRICE_CACHE_READ_PER_MTOK` are read from the environment; a run reports
tokens and no cost when they are unset.

Cached input is charged at the cache rate when one is given. These profiles repeat
the ontology context in every pass, which is exactly what a provider's prompt cache
is for — on DeepSeek the cache-hit rate is about a thirtieth of the input rate, so
charging cached tokens at the full rate would overstate the bill. When no cache
price is configured and cache reads occurred, the output carries a `cost_basis`
saying so, rather than leaving it to be discovered from an invoice.

### Where it lands

`ExtractionRun.usage`, so the figure is a property of the run rather than of the
process that happened to produce it: serialised, round-tripped, and readable from a
stored revision. `agents/cli.py` prints it for the design leg, which writes no
draft and would otherwise leave the spend only in the provider's dashboard.

**Empty means NOT RECORDED, never zero.** A run predating this, or one whose
provider reported nothing, is not a free run — the same distinction `completeness`
draws between `UNKNOWN` and `FAILED`.

### What was rejected

- **A built-in price table.** Convenient, and stale in weeks.
- **Reporting cost without cache handling.** Would overstate by roughly 30× on a
  cached workload, which is the workload this platform has.
- **Per-pass usage only.** Cheaper to thread, and it cannot answer "what did this
  run cost", which is the question that prompted the work.

### Evidence

```
tests/test_usage_accounting.py — accumulation, per-invocation vs lifetime (the
                                 regression), cache-rate costing and its stated
                                 fallback, the plain-path call, and the round trip
                                 through serialisation
```

Live, on the four-leg DeepSeek run: requirements $0.0083, architecture $0.0690,
design $0.0539, reconciliation $0 — about **$0.13** for 148 nodes and 635
assertions.
