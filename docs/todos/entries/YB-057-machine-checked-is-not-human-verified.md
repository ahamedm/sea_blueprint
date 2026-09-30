---
id: YB-057
legacy: null
title: "Machine-checked is not human-verified — the review queue asks 486 questions to get 175 answers"
status: open
priority: high
area: "`core/knowledge/review.py` (the states and `review_progress`), `core/knowledge/model.py` (assertion status/provenance), `agents/extraction/validators.py` (the check list), `app/projections.py` + `app/templates/review.html` (what the queue shows and how it is ranked)"
created: 2026-09-30
updated: 2026-09-30
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
2. **Add a state for machine-checked.** Distinct from `VERIFIED` in name, provenance and
   every surface that reads it — see the risk below. Assertions a validator decides leave
   the human queue and are recorded *with the check that decided them*.
3. **Span-anchor quotations** (reliability §3.6): a `description` that is a substring of
   its source chunk is checked; one that is not is a finding, which is the interesting
   case. **42 more leave the queue.**
4. **Rank the remainder by consequence, not confidence.** The reports are pure functions,
   so an assertion whose decision cannot move one does not belong at the top. Cheap
   version: does the subject appear in a report at all, then degree.
5. **Report the queue as "N need a decision", not "N unverified"** — the page currently
   states the larger number, which is the resistance.

**Measured effect of 1–3: 486 → ~230**, with the 175 relational facts filling the top. No
model calls, no new engine, no new view.

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
