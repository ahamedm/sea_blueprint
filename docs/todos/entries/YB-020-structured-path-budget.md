---
id: YB-020
legacy: "20"
title: "Structured-path budget — decide, don't retry into the timeout"
status: open
priority: high
area: "`config/agent_config.py`, `.env.example`, `agents/base_agent.py`, `agents/knowledge_extraction/agent.py`"
created: 2026-09-23
updated: 2026-09-24
design: null
record: null
superseded_by: []
related: ["YB-026"]
blocks: []
blocked_by: []
---

# YB-020 — Structured-path budget — decide, don't retry into the timeout

> **Open work.** This file is the source of truth for this item; `TODO.md` is generated from it.

**Legacy status:** Not started — **measured stall, cause not yet chosen between options**
**Legacy priority:** Medium-High
**Legacy area:** `config/agent_config.py`, `.env.example`, `agents/base_agent.py`, `agents/knowledge_extraction/agent.py`

---

### The problem

The structured path **failed on both large documents**, consuming the whole budget before
falling back to text:

| run | elapsed | structured result |
|---|---|---|
| `payment_platform_brief.md` | **624 s** | `model did not satisfy schema within turn budget` |
| `payment_platform_brief.md` | **472 s** | `model did not satisfy schema within turn budget` |
| `sample_requirements.md` (small) | 148 s | satisfied the schema |

`.env.example` ships `MAX_STRUCTURED_TURNS=3` and `STRUCTURED_TIMEOUT_SECONDS=180`. The
observed runs exceeded that several times over before giving up, which means the guard is
**not bounding the cost it claims to bound** — worth understanding in itself, since
`invoke_structured` documents the turn cap as the defence against exactly this loop.

### Why it matters more than latency

The two paths are not equivalent, and the asymmetry is the point:

- **Structured** is the only path that produces real `ExtractedEntity` objects, and so the
  only path that can carry `requirement_id`, the quality classification, and
  `initiative_refs`.
- **Text** completes, but emits only `triples` and derives entities that cannot carry any
  of those fields.

So on a large document the system reliably lands on the path that **structurally cannot**
record identity or quality — after burning ~10 minutes to get there. The fallback is not a
degraded version of the same result; it is a result missing whole categories of fact.

### Options, to be decided rather than drifted into

1. **Raise the budget** so the structured path completes on documents of this size — costs
   wall-clock on every run and may simply not converge on a 4B model.
2. **Route by size** — small documents structured, large ones straight to text with no
   wasted attempt. Cheapest, and honest about the model's capability, but abandons
   per-entity fields on exactly the documents that need them most.
3. **Split the ask** — a smaller structured call for entities only, alongside the existing
   text extraction of triples. More calls, but each is small enough for the model to
   satisfy, and it targets the fields that are otherwise lost.
4. **Decouple the fields from the model** — if identifiers (YB-019a, YB-019b) and quality
   classification become deterministic passes, the structured path's unique value drops
   sharply and option 2 becomes clearly best.

**Option 4 is the reason this is Medium-High rather than Critical:** YB-019a / YB-019b remove the
strongest argument for the structured path, and may make the whole trade moot.

### Status after the ADR-0001 fix — the premise may have been a symptom

Every timing above was measured while requirements extraction ran the **architecture**
prompt. Re-measured with that fixed, on `payment_platform_brief.md`:

| run | path | elapsed |
|---|---|---|
| pre-fix, structured | fell back to text | 472 s |
| pre-fix, structured | fell back to text | 624 s |
| **post-fix** | **`structured_output` completed** | **62–78 s** |

The structured path now finishes in about a minute on a document where it previously
burned ten and gave up. So "the budget is not bounding a retry loop" may describe a
misconfigured system rather than a real guard defect.

**But it is not settled.** A later run of the *smaller* `sample_requirements.md` did not
return within 25 minutes, and the same small document produced **250 triples** in one run
against 34–73 in earlier ones. Output volume is not stable across runs, so neither is
runtime — which means the guard's adequacy depends on something that varies by an order
of magnitude. That is YB-004 ("model output is not structurally stable across runs") showing
up as a latency problem.

### Revised options

Routing by size (option 2) is now less attractive than it looked: the structured path is
fast when it works, and it is the only path that produces real entity objects. The live
questions are instead:

1. **Bound generation, not just turns.** `max_tokens` is the lever that would have made the
   250-triple run stop early.
2. **Re-measure before changing anything**, with repeats, because the current numbers come
   from single runs of a non-deterministic model.

### Acceptance (unchanged)

- A run on `payment_platform_brief.md` either completes structured within the configured
  budget, or the configured budget is demonstrably the reason it did not.
- `MAX_STRUCTURED_TURNS` / `STRUCTURED_TIMEOUT_SECONDS` bound observed elapsed time.
