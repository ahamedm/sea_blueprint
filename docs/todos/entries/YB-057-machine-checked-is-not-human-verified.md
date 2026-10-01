---
id: YB-057
legacy: null
title: "Machine-checked is not human-verified — the review queue asks 486 questions to get 175 answers"
status: open
priority: high
area: "`core/knowledge/review.py` (the states and `review_progress`), `core/knowledge/model.py` (assertion status/provenance), `agents/extraction/validators.py` (the check list), `app/projections.py` + `app/templates/review.html` (what the queue shows and how it is ranked)"
created: 2026-09-30
updated: 2026-10-01
design: docs/design/graph-interaction.md
record: null
superseded_by: []
related: [YB-056, YB-010, YB-052, YB-009, ADR-0030, ADR-0032]
blocks: []
blocked_by: []
---

# YB-057 — Machine-checked is not human-verified

> **Open.** From the interaction analysis in
> [`docs/design/graph-interaction.md`](../../design/graph-interaction.md). The review
> queue is the primary way a user meets the graph, and at 486 items it faces the
> resistance the analysis was written about. The measurement says most of those 486 are
> not decisions.

## The measurement

**[M] Against `data/sea_home_01` / `acme_pillar_01`:** 613 active assertions, **486
awaiting a decision**, and **399 of those 486 carry confidence `1.0`** (only 7 are below
`0.8`). Classified by what they actually are:

| Bucket | Count | Needs a human? |
|---|---|---|
| Literal, predicate already enum-validated (`element_type`, `c4_level`, `system_class`, `origin`, `deployment_model`) | 57 | No — already decided by `check_enum_membership` / `check_element_types` |
| Literal naming an ontology enum **not** in the check list (`quality_category` 29, `subcharacteristic` 28, `technique_category` 24, `technology_category` 18) | 99 | No — one list away |
| Literal quotations (`description` 29, `description_text` 13) | 42 | No — span-anchoring checks a quotation against its source |
| **Relational** — points at another node | **175** | **Yes** — the architectural call |
| Other literals (`mechanism` prose, `style`, `requirement_type`, …) | ~113 | Mixed |

## Re-measured 2026-10-01, from the other end: what the gate recorded as vouching

Scope `payments_v2`, fully reviewed (695 assertions, 0 outstanding, `is_auditable` true).
The decision log holds **735 entries**, and this is what they are:

| Entry | Count |
|---|---|
| `verify` with the note "bulk verify (selected)" | **696** |
| No note at all | 29 |
| `retire` "bulk retire (selected)" | 6 |
| `resolve` "bulk resolve (threshold 0.75)" | 2 |
| **Carrying a substantive human note** | **2** ("CIA Triad", "Incremental Arch Applied") |

So the scope reads 100% reviewed, and 696 of the 735 acts that produced that reading were
one click on a selection. That is the same defect as the 486-item queue seen from the far
side: the gate cannot tell "a person considered this" from "a person selected a page", and
it is the *bulk* path that has already been caught granting authority to facts that cannot
be true — four `X part_of X` assertions acquired human provenance through a bulk verify
(YB-052).

Two consequences for whatever comes next. A queue projection that ranks judgement is only
as good as the judgement it can be calibrated against, and a corpus of clicks is not one —
the useful labelled examples here number in the tens, not hundreds. And the audit claim
("the graph can prove who vouched for what") is thinner than the percentage suggests: the
provenance is real, the *consideration* is mostly absent. Cheap first steps, no model
required: make the note per-row rather than per-batch where a batch is heterogeneous, count
and show "considered" beside "reviewed", and keep "bulk accepted" visible in the audit view
rather than indistinguishable from a person's verdict.

## The defect, stated

**The gate conflates "recorded" with "vouched for".** An assertion is `UNVERIFIED`
because no human touched it and `VERIFIED` because one did, with **no state in between for
"a deterministic check agrees"**. Every fact that arrives is therefore a decision point
whether or not it is a decision — so the queue is 2.8× the size of the judgement it
actually needs, and the 175 real questions are buried among 198 that the platform can
answer itself.

That is also why the existing ranking does not help. **[M] The queue already defaults to
lowest-confidence-first** with eight filter chips, so this is not a missing-sort problem;
ranking by model self-report is simply the weakest available signal, and it orders 399
equally-certain items arbitrarily.

## What to do, cheapest first

1. **Extend `check_enum_membership`'s field list by four entries** —
   `quality_category` → `QualityAttributeCategory` (10 values), `subcharacteristic` →
   `QualitySubcharacteristic` (40), `technique_category` → `PatternCategory` (11),
   `technology_category` → `TechnologyCategory` (10). The vocabularies are already read
   from the ontology, so this is a list, not machinery. **Covers 99 of the 486.**
   *(Landed, but as two different changes — see Status. Two of the four are `Literal`s and
   needed a drift check, not a membership check.)*
2. **Add a state for machine-checked.** Distinct from `VERIFIED` in name, provenance and
   every surface that reads it — see the risk below. Assertions a validator decides leave
   the human queue and are recorded *with the check that decided them*.
3. **Span-anchor quotations** (reliability §3.6): a `description` that is a substring of
   its source chunk is checked; one that is not is a finding, which is the interesting
   case. **42 more leave the queue.**
   *(Landed with a different rule — containment was measured wrong. See Status.)*
4. **Rank the remainder by consequence, not confidence.** The reports are pure functions,
   so an assertion whose decision cannot move one does not belong at the top. Cheap
   version: does the subject appear in a report at all, then degree.
5. **Report the queue as "N need a decision", not "N unverified"** — the page currently
   states the larger number, which is the resistance.

## Status 2026-09-30 — the cheap half landed, and the estimate was wrong

**Done:** the first, third and fifth changes. The first turned out to be two different
changes rather than one
list — `quality_category`/`subcharacteristic` are plain `str` on `ElementRecord` so they
became `check_enum_membership` entries, while `technique_category` and `technology_category`
(alphabetically `category`) are already `Literal`s and needed a **drift** check against the
ontology instead, not a membership check. Both are in, green on arrival, and
`inv_schema_ontology_consistency` now covers all four schemas.

Item 3 shipped with a **different rule than proposed here**, because the proposal was wrong
and measuring it showed so: descriptions are **summaries, not quotations**. Against
`test_data/arch/payment_platform_arch.md`, **0 of 30 descriptions** appear literally in the
source — so "a `description` that is a substring of its source chunk" would have fired on
every one of them, which is a report nobody would read. The shipped rule is a floor: a
description must use at least 20% of its long words from the document, at which those same
30 score min 0.60 and none is flagged. Names keep the strict rule, where 29 of 30 anchor and
the rule discriminates.

**The estimate of the effect was wrong twice, and the second reason is the one that
matters.**

- Arithmetically: 486 − 156 − 42 = **288**, not the ~230 written here.
- Substantively: **the reduction comes from the machine-checked state, not from the guards.** A guard does not
  remove an assertion from the queue — it converts "unverified" into "unverified, and
  flagged if the value is wrong". With that state deferred, the queue is still 486, and what
  changed is that its composition is now stated.

**[M] Measured by the shipped classifier, on `data/sea_home_01` / `acme_pillar_01`:**

```
outstanding      486
  enum_valued    174   a guard decides
  quotation       42   the document decides
  relational     175   a judgement
  other           95   mixed, mostly prose — a judgement
needs_judgement  270
```

The `enum_valued` figure is 174 rather than the 99 predicted, for two reasons worth
recording: the four intended fields are 99, and the classifier also counts `style` (13) and
`convention_type` (5), which are decoder `Literal`s — plus the two technique fields (42)
that are the drift-check half. Two candidates were **removed** on inspection for failing the
"name the guard" test: `pattern` on `EngineeringConventionRecord` is a regex the convention
matches names against, not an enum, and `requirement_type` is a free string nothing checks.
Counting either would have made the number the page prints a lie, and a wrong number is
worse than none — it tells a reviewer to skip work only they can do.

**Still open: the machine-checked state.** That is the part that shrinks the queue, and it is the
product decision below rather than a mechanical one. Item 4 (rank by consequence) is also
untouched.

**Measured effect of 1, 3 and 5: none on the queue size — 486 stays 486 — and the page now
says 270 of them are decisions.** No model calls, no new engine, no new view.

## The risk, which is a product decision not an implementation detail

**"Machine-checked" weakens "a human vouched for this".** The platform's credibility rests
on that distinction, and it survives only if the two states stay visibly distinct
*everywhere* the graph is read — the review page, the audit trail, the gap and quality
reports, RDF/JSON exports, and the C4 view. If they blur, this lever buys a shorter queue by
spending the property the platform sells. So the state has to be honest about what it is,
and the audit has to be able to *require* human verification for the claims that warrant it.

That is the open question this entry does not answer: **which claims warrant it?** The
analysis's proposal — the relational ones, because "does this really implement that
requirement?" is the architectural judgement — is a hypothesis, not a measurement.

## Acceptance

- The four enum checks are in place and the queue's composition is reported by bucket, so
  "N need a decision" is a number the page can state.
- A machine-checked assertion is distinguishable from a human-verified one in the graph,
  the audit trail, both reports and both exports — pinned by a test, not by convention.
- Quotations are span-anchored, and a quotation that is *not* in its source is a finding
  rather than a queue item.
- The measured queue size on the live scope is recorded before and after, so the effect is
  a number and not an impression.

## Not in scope

- **The views themselves.** Altitude-based projections and the tidy-tree/layered layout are
  [YB-056](YB-056-map-representation-modes.md); this entry is about what the queue *asks*.
- **The NLP interface.** See the analysis §C: an LLM translates to a deterministic query
  and the engine answers — it needs [YB-010](YB-010-rdf-knowledge-layer.md)'s query surface
  and its gate.
