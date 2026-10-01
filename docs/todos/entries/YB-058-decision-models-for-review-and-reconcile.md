---
id: YB-058
legacy: null
title: "Decision models (Jev, Clef) as assistance in Review and Reconcile — ranking without authority"
status: open
priority: medium
area: "`core/knowledge/review.py` (the decision log and what a recommendation would be), `core/knowledge/reconcile.py` (candidate scoring and `bulk_resolve`), `app/projections.py` (the queue and the candidate list), `agents/base_agent.py` (a provider path for a decision endpoint)"
created: 2026-10-01
updated: 2026-10-01
design: null
record: null
superseded_by: []
related: [YB-057, YB-052, YB-005, YB-009, ADR-0038, ADR-0030]
blocks: []
blocked_by: []
---

# YB-058 — Decision models as assistance in Review and Reconcile

> **Open, idea-stage.** Raised as "a new class of model — decision models — could help the
> human in the Review or Reconcile step". This entry records what those models are, where
> they fit in this pipeline, what they must not be allowed to touch, and the measurement
> that says the prerequisite is missing.

## What the models are (checked, 2026-10-01)

A **decision model** takes a *state* — text, JSON, sometimes images — plus a **schema of
typed questions**, and returns calibrated probabilities rather than prose. Both of the
named ones share that shape:

- **Jev** (TypeSafe AI, "System One"): text in, floating-point decisions out. Three question
  kinds — yes/no (a Bernoulli probability), choice (a distribution over named options), and
  score (a probability-weighted point on an ordered scale). Output is free, input is priced
  at $0.042/M; questions evaluate in parallel, so many questions cost about the same time as
  one. [Write-up][jev], [JevBench][bench] has already appeared for the class.
- **Clef** (`@cf/cloudflare/clef`, Cloudflare, open source): 27B **multimodal** — state as
  text, JSON, image or video — with the same three question types (`noul`, `choice`,
  `score`) and a probability for every allowed option. 65k context, $0.24/M input.
  [Model card][clef], [announcement][clefblog].

The property that matters here is not speed or price. It is that **the answer is typed and
the option set is closed**, so a recommendation cannot wander outside the action vocabulary
the reviewer is choosing from.

[jev]: https://simonwillison.net/2026/Sep/21/jev/
[bench]: https://benchmarkheaven.com/jev-models
[clef]: https://developers.cloudflare.com/workers-ai/models/clef/
[clefblog]: https://blog.cloudflare.com/clef-decision-models/

## Where it fits, best first

1. **Reconcile candidate reranking — the strongest fit, and the pipeline already has the
   shape.** `project_reconciliation` produces, per unresolved reference, a ranked candidate
   list with lexical scores, split into expected-kind (bindable by `bulk_resolve`) and
   merely plausible. A `choice` question with `criteria` mapping to the candidate labels is
   exactly that: shortlist by the cheap algorithm that exists, then score the shortlist.
   This is the search-reranking use the Jev write-up describes, applied to the join this
   product exists to make (YB-005). `bulk_resolve`'s threshold is already a decision
   function with a measured calibration — 0.90+ defensible against 0.43-0.59 weak,
   deliberately conservative because a wrong link makes the audit wrong rather than merely
   incomplete — so there is a number to compare against rather than a feeling.
2. **Queue triage.** A `score` or `noul` question per row could order the judgement half of
   the queue. The caution here is ADR-0038's, stated in the code: a projection that ranks
   must name its own criterion, and `project_review_buckets` deliberately does not shrink
   the queue. A model score is a *sort hint*, shown as one, never a criterion and never a
   state.
3. **Reference-type classification.** Which predicate a cross-graph reference really is,
   where `EXPECTED_TARGET_KINDS` cannot tell — a `choice` question over the routed
   predicates.

## What it must not do

**It must never verify.** In this codebase the act that grants authority is `verify()`
flipping provenance to human (`HUMAN_SOURCES` outrank agent output on conflict), and the
cost of blurring that is already measured: a bulk verify gave four impossible `X part_of X`
facts human provenance (YB-052). So a model's output has to be a **recommendation
artifact** — proposed action, the option probabilities, the evidence it was given, and the
model id and version — recorded beside the decision log and shown on the row. Accepting it
must be recorded as accepting a recommendation ("accepted clef@… r_x"), which is what
`Decision.before/after/note` can already carry, so the audit trail still answers *who
vouched for this* rather than *what scored it*.

The black-box objection is real and is the reason for that boundary. A decision model
returns numbers, not reasons; even a fluent LLM's self-explanation is unguaranteed, and a
number cannot be interrogated at all. On the **decidable half** of the queue this project
already has something strictly better: deterministic guards that name their criterion, cost
nothing, give their reasons, and cannot drift between runs. A number-only model is weaker
there and conceals what it weighted. Its value is confined to the **residual judgement
half** — precisely where a black box is least defensible — which is the argument for using
it to *rank and rerank* (reversible, cheap, measurable) rather than to *decide*.

## The prerequisite is missing, and it is measured

A recommendation model needs something to be calibrated against. The review log looks like
a large labelled corpus and is not one. Scope `payments_v2`, fully reviewed (695
assertions, `is_auditable` true), **735 log entries**:

| Entry | Count |
|---|---|
| `verify`, note "bulk verify (selected)" | **696** |
| No note | 29 |
| `retire` / `resolve`, both bulk | 8 |
| **Substantive human note** | **2** |

The corpus is clicks, not judgement: 696 of 735 acts are one selection, and the considered
examples number in the tens. Calibrating a decision model on that would teach it the
reviewer's triage, not their reasoning — and a recommendation nobody has grounds to judge
is only a faster click. Recorded in full against YB-057, which owns the underlying defect.

**So the first slice is not a model call.** It is capturing judgement where it is exercised:
a per-row note where a batch is heterogeneous, a "considered" count beside "reviewed", and
"bulk accepted" kept visible in the audit view as what it is. That costs nothing, improves
the product on its own terms, and is what would make a later evaluation meaningful.

## How it would be held to account, if built

The evaluation set is the considered decisions plus whatever the prerequisite slice
accumulates: for each, would the recommendation have agreed with the human, in both
directions (the asymmetry matters — accepting a wrong traceability link is worse than
leaving it open). Then the harness pattern the extraction layer already uses: a gate (no
worse than the current ordering on a held-out set) and budgets (cost per queue, latency),
against a recorded baseline rather than an impression. Cost is not the obstacle —
input-only pricing means a sweep of a few hundred rows is cents — and the configuration is
already externalized (`AgentConfig` carries provider, base URL and key; Workers AI exposes
an OpenAI-compatible endpoint), so a provider path is config plus a thin client. It is a
**batch** job or an on-demand action, never a page render.

## What would close this

Either a first slice that captures judgement and measures the current ranking against it —
with the decision-model call as a second step only if that measurement justifies one — or a
decision to leave the review gate entirely human, recorded here, which is a legitimate
answer given what the guards already do.

**Related:** YB-057 (machine-checked is not human-verified — the queue and the log),
YB-052 (a bulk verify that granted authority to impossible facts), YB-005 (the join
reconciliation exists to make), ADR-0038 (the guards, and the queue that names its own
criterion), ADR-0030 (refusal at the write boundary).
