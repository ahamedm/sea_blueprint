---
id: ADR-0037
title: "An empty frozen baseline announces itself — warn, do not fall back, do not refuse"
status: accepted
date: 2026-09-29
area: "`core/knowledge/digest.py`, `tests/test_design_digest.py`"
related: ["ADR-0035", "ADR-0032", "YB-007"]
---

# ADR-0037 — An empty frozen baseline announces itself

> **Record.** `design_input` read the architecture for a design run from the frozen
> baseline and nothing else, and had a caveat for *no baseline* — but none for a
> baseline that **contains no architecture**. A REQ-only revision as `baselines[0]`
> therefore rendered "Input 2 — Existing architecture to extend" as a bare header,
> with no caveat, no finding and no log line. The design extended nothing and said
> nothing.

## The defect, reproduced

Measured against a REQ-only baseline (`architecture_source = baseline` because a
baseline *was* given, so the working set's architecture was not shown either):

```
Input 2 — Existing architecture to extend

Existing architecture (rev_req_v1)

## Requirements with NO architectural answer (1)
- Fallback Routing (FunctionalRequirement)
```

`design_input`'s caveats covered budget cuts, a missing baseline, and the
completeness note — but not this. Since the header is the whole mechanism for "what
the model was not shown", an empty baseline was indistinguishable from a correct one.

## The decision

One condition, three outcomes, keyed on `_ARCHITECTURE_KINDS` — **the same set the
renderer sections on**, so "this baseline has no architecture" cannot disagree with
what the model was actually shown:

| Condition | Caveat |
|---|---|
| `baseline is None` | unchanged — the working set is shown instead, and may be unreviewed |
| baseline has no architecture, **working set does** | *the frozen baseline names no architecture, but the working set holds N architecture element(s) that are not in it — the design extends an empty baseline and may propose duplicates* |
| baseline has no architecture, **nothing does** | *the frozen baseline names no architecture, and neither does the working set — a greenfield design, so a proposal that reuses nothing is expected* |

**Two wordings, because one would cry wolf.** Freezing REQ-G before any design exists
is the greenfield path — `docs/user-journey.md` stages 4 → 5 prescribe exactly it — and
a proposal that reuses nothing is *correct* there. Only the middle row is the hazard:
the enterprise holds architecture that never reached a baseline and the design cannot
see it. A single alarmist message would fire on the intended flow and train a reviewer
to ignore the header, which is worse than the silence it replaces.

**The design still extends nothing.** `architecture_source` stays the baseline. Falling
back to the working set — which the `baseline is None` path already does — would be
attractive here and is rejected: a frozen baseline is the thing a human vouched for,
and silently substituting an unreviewed working set for it would make "the baseline"
mean two things depending on which is emptier. That is the distinction the whole
review/freeze machinery exists to keep. So: warn, and let the architect decide whether
to re-freeze.

**The run is not refused.** Greenfield is legitimate, so a hard gate would break the
intended path; and running a design against an empty baseline is not impossible, only
sometimes wrong. Refusal at the boundary stays reserved for facts that cannot be true
(ADR-0032 §1).

## Where it surfaces — no extra wiring

`caveats` become the digest's `>` header, which already reaches three places:
the prompt, the reviewer (metadata `design_caveats` → **What the model was not shown**,
`design.html:378`), and the run log (`agent.py:119`, one warning per caveat).

## Verification

Three tests in `tests/test_design_digest.py`, one per row of the table — the
duplication case asserts the count and the wording reaches `digest.text`; the
greenfield case asserts the *benign* wording specifically ("a single alarmist message"
pinned as a property); the third asserts a baseline that *does* carry architecture gets
no such caveat, so a correct run stays quiet. `tests/test_prompt_budget.py` still
passes — the header grew by one line and that ratio is a tested invariant (YB-007).

Full suite 1076 passed / 9 skipped / 2 failed, the two failures being the pre-existing
fixture-dependent pair that skip in a clean checkout (see ADR-0035 §5).

## Not fixed here

- **The threshold bulk-verify button ignores the review filter.** `app/__init__.py:1493`
  calls `bulk_verify(graph, …, max_confidence=…)` with no ids and no `subject`, though
  `bulk_verify` accepts one — so "verify all ≤ 0.7" acts on the whole graph while the
  screen shows a filtered view. A different defect in a different area.
- **Choosing which baseline to extend.** "The newest baseline that has architecture"
  would change what *the baseline* means; this record deliberately keeps the newest,
  whatever it holds.
