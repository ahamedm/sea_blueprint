---
id: YB-020
legacy: "20"
title: "Structured-path budget — decide, don't retry into the timeout"
status: open
priority: high
area: "`config/agent_config.py`, `.env.example`, `agents/base_agent.py`, `agents/knowledge_extraction/agent.py`"
created: 2026-09-23
updated: 2026-09-26
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

### Bound generation, not just turns — DONE (2026-09-26)

The lever above was pulled, and pulling it exposed why the earlier numbers varied by an
order of magnitude.

`config/agent_config.py` has always set `temperature` and `max_tokens` per agent. **Neither
ever reached the model.** `AgentConfig` had no field for either, so Pydantic discarded them
without a word, and `_create_model` passed neither to the provider. Every agent in the
platform ran on whatever the inference server defaulted to, and llama.cpp's defaults are
hostile to a small model:

```
temperature 1.0 | repeat_penalty 1.0 (off) | dry_multiplier 0.0 (off) | n_predict -1 (unbounded)
```

Under those settings a 2B model degenerates into a repetition loop — the Design Assistant
was captured restating *"OK, I'm going to write the final answer now"* until the context
filled. No turn cap can stop that, because a generation looping inside one turn never
advances a turn. The 250-triple run and the 25-minute run are the same defect as the
Designer's loop: generation was never bounded, and nothing said so.

What now holds:

- `AgentConfig` carries `temperature` / `max_tokens` / `extra_params`, and all three reach
  the provider request (`params=` on the OpenAI-compatible path; `max_tokens` + `params` on
  Anthropic). The effective values are logged at construction, so their absence can never
  be invisible again.
- `MODEL_EXTRA_PARAMS` (JSON, merged into every request verbatim) is the escape hatch for
  the samplers that are the actual cure for repetition — `repeat_penalty`, `dry_multiplier`
  — which the per-agent config does not name. Malformed JSON fails loudly rather than
  silently leaving the model unbounded.
- `invoke()` — the plain-text fallback — now carries the turn cap and wall-clock cancel it
  was missing. It ran on exactly the calls the structured path had already failed, and it
  was the one unbounded route to the model left.
- A generation that loops anyway is recorded as **failed with the cause** rather than
  `empty`. "Empty" reads as "the model had nothing to say" and points the next reader at
  the prompt; the truth is a sampler problem.

Tests: `tests/test_generation_bounds.py` (the silent drop as a regression, parameter
plumbing, the escape hatch, the bounded plain call, loop detection) plus
`test_a_model_that_loops_is_reported_as_a_loop_not_as_empty` in `tests/test_design_agent.py`.

**Still open, and it is the acceptance below:** the budget has not been *re-measured* with
repeats. The numbers in this file remain single runs of a non-deterministic model, so
"`MAX_STRUCTURED_TURNS` / `STRUCTURED_TIMEOUT_SECONDS` bound observed elapsed time" is
implemented but not demonstrated. Per-agent `temperature` / `max_tokens` are also still
hand-set in `config/agent_config.py` rather than derived from anything.

### Per-pass temperature — the generative pass is the exception (2026-09-26)

The two live Designer runs settle the question of whether temperature is the loop lever:
it is not. The model looped at the server's **1.0** *and* at the configured **0.3**. Both
runs are the same defect — unbounded generation with `repeat_penalty` off — so moving
temperature up or down would have changed nothing. Verbatim repetition is in fact a
*peaked-decoding* signature, which makes a low temperature a loop risk rather than a
remedy; the designed control is the repetition penalty / DRY exposed through
`MODEL_EXTRA_PARAMS`.

Temperature is still worth getting right, for a different reason: it buys design
diversity on the one pass that is a genuine *choice* rather than a transcription.
`PassSpec` now carries an optional `temperature`, applied for the duration of that pass
and restored in a `finally`, and `design_pattern_pass` sets **0.6** while the other five
stay at the profile's 0.3. The split is defensible in both directions:

- `patterns` names come from the catalogue, copied verbatim, so a warmer sample cannot
  corrupt the identifier the way it could a container name — and choosing among
  alternatives is the one act here where a peaked distribution just returns the first
  plausible answer.
- `structure`, `connections`, `techniques`, `scenarios` and `traceability` emit NAMES that
  merge into the graph and are diffed between runs (see YB-004). Their variance is a
  correctness cost, so they inherit the profile default.

This is a **hypothesis, not a measurement**. On a small model a higher temperature buys
incoherence as readily as diversity, so if the patterns pass starts failing its schema,
`PATTERN_PASS_TEMPERATURE` is the first number to put back. The temperature that produced
each pass is recorded on the `PassRecord`, so the run itself answers "which pass was warm"
without reading a log.

### Acceptance (unchanged)

- A run on `payment_platform_brief.md` either completes structured within the configured
  budget, or the configured budget is demonstrably the reason it did not.
- `MAX_STRUCTURED_TURNS` / `STRUCTURED_TIMEOUT_SECONDS` bound observed elapsed time.
