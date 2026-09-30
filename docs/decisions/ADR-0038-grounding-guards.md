---
id: ADR-0038
title: "Grounding guards — a name anchors strictly, a description does not, and the queue says what it is"
status: accepted
date: 2026-09-30
area: "`agents/extraction/validators.py`, `agents/architecture_extraction/agent.py`, `app/projections.py`, `app/templates/review.html`, `scripts/run_extraction_tests.py`, `tests/test_grounding_guards.py`"
related: ["YB-057", "YB-056", "YB-010", "ADR-0032", "ADR-0030"]
---

# ADR-0038 — Grounding guards, and the measurement that set the tolerance

> **Record.** The measured half of the interaction analysis
> ([`graph-interaction.md`](../design/graph-interaction.md)): two guards and one
> projection, all driven by the finding that the review queue on the live scope is 486
> assertions carrying only ~270 decisions. Numbers and their corrections live in
> [YB-057](../todos/entries/YB-057-machine-checked-is-not-human-verified.md); this
> records the choices a later reader would otherwise re-litigate.

## 1. A name anchors strictly; a description does not

§3.6 of the reliability brainstorm asks for span anchoring and does not distinguish the
two. Measuring them did, and the two rules are now different on purpose:

| | Rule | Measured on `payment_platform_arch.md` |
|---|---|---|
| **name** | literal containment, case- and whitespace-normalised | **29 of 30 anchor** — the rule discriminates, and the one that fails (`Reconciliation Container` where the document says `Reconciliation`) is the synthesis worth seeing |
| **description** | floor: ≥20% of the description's long words appear in the document | containment fires on **30 of 30**; the floor fires on **0**, with the same 30 scoring min 0.60, mean 0.89 |

**The first draft of this shipped proposal was wrong**, and the measurement is the only
reason it is not in the code: it said "a `description` that is a substring of its source
chunk is checked". A description is a *summary*, so that rule reports every correct
extraction. **A validator that reports a correct graph is worse than none** — a queue
nobody trusts is how a real defect gets waved through. Anyone tempted to "tighten" the
floor back to containment should read the 30-of-30 first.

## 2. Anchoring is an extraction guard, not a design guard

A design is **allowed to invent** — proposing a container the document never names is its
job — so anchoring a design proposal would flag every legitimate element. That is the line
the model already draws between `SOURCE_EXTRACTION` and `SOURCE_DESIGN_ASSISTANT`, applied
to a validator: an extractor reports what a document said, a designer proposes what could be
built. The guard is wired into the architecture extraction profile only.

It is checked against the **whole document**, not the chunk, because `merge_records` has
already merged across chunks and an element carries no record of which one it came from. A
narrower basis would need provenance the merge discards; a wider one is the document the
caller supplied.

## 3. The enum half was two different changes

`quality_category` and `subcharacteristic` are plain `str` on `ElementRecord`, so they
became `check_enum_membership` entries reading their vocabularies from the ontology.
`technique_category` and `technology_category` (spelled `category` on
`TechnologyStackRecord`) are already `Literal`s, so membership was already enforced at the
decoder — what was missing was anything comparing them to the ontology. They got a
**drift** check instead, wired into `inv_schema_ontology_consistency` alongside the
connection vocabularies from ADR-0032. All four are green on arrival; the guard exists so
the next ontology edit cannot leave the schema asserting the old list.

## 4. The queue projection names its own criterion

`project_review_buckets` splits the outstanding queue into closed-vocabulary,
quotation, relational and other, so the page can say how much of it is a question a person
has to answer. Its membership criterion is strict — **a predicate is decidable only if a
guard can be named for it** — and two candidates were removed for failing it:

- `pattern` on `EngineeringConventionRecord` is a **regex** the convention matches element
  names against, not an enum;
- `requirement_type` is a **free string** written at ingest with nothing checking it.

Both look enum-shaped, and counting either would have made the number the page prints a
lie. A wrong number is worse than no number here: it tells a reviewer to skip work that
only they can do.

## 5. What this does NOT do, which is the whole point of saying so

**The queue did not shrink.** It is still 486. A guard does not remove an assertion from
the queue — it converts "unverified" into "unverified, and flagged if the value is wrong".
Removing the decidable ones requires *machine-checked* to be a state distinct from
human-verified, which is deliberately deferred: it weakens "a human vouched for this", the
property the platform sells, unless the two states stay visibly distinct everywhere the
graph is read.

The analysis first claimed this tranche would take the queue to "~230". That was wrong
arithmetically (486 − 156 − 42 = 288) and wrong substantively (the reduction is the
deferred state's, not the guards'). Both documents now carry the correction rather than the
original claim, because a stale number in a design doc is the same defect as a stale number
on a page.

What did change: **the page says 270 of the 486 are decisions.** That is the resistance
being named, and it costs nothing.

## Verification

Thirteen tests in `tests/test_grounding_guards.py`, in both directions — each guard catches
what it is for *and* does not fire on a correct graph, including the 30-of-30 calibration
against the tracked fixture and a regression test pinning `pattern`/`requirement_type` out
of the decidable set. The harness's drift gate is green over all four schemas and the run is
unchanged at 26/27 gates (the failure being the pre-existing `inv_no_tech_leak`). Full suite
1089 passed / 9 skipped / 2 failed, both failures the known fixture-dependent pair.
