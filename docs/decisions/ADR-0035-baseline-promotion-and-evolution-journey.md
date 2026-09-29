---
id: ADR-0035
title: "Promoting ARC-G to a baseline, and the evolution journey that was never walked — one silent scope collision"
status: accepted
date: 2026-09-29
area: "`core/knowledge/model.py`, `core/knowledge/serialise.py`, `core/knowledge/review.py`, `core/knowledge/ingest.py`, `core/knowledge/reconcile.py`, `app/__init__.py`, `app/projections.py`, `tests/test_evolution_journey.py`"
related: ["YB-042", "ADR-0030", "ADR-0032", "YB-055"]
---

# ADR-0035 — Promoting a baseline, and evolving a system after it

> **Record.** Two questions: *how does ARC-G become a baseline?* and *is the journey
> of evolving a system through later Initiatives — after baselining — actually
> walked?* The first has an answer that works and had never been driven end to end.
> Driving it found a defect that made the second question's answer "no, and it could
> not have been".

## 1. There are two baselines, and they are different things

Worth stating plainly, because the vocabulary is the source of the confusion:

| | **Frozen revision** | **Promoted scope** |
|---|---|---|
| Mechanism | `RevisionStore.freeze()` → `Revision.kind = "baseline"` | `promote_to_baseline()` → `Assertion.scope = SYSTEM_BASELINE` |
| Granularity | a whole immutable snapshot | individual facts |
| Gate | `BaselineNotReady` — refuses while any assertion is unverified or disputed | only `VERIFIED`/`CORRECTED` facts move; the rest are counted and left |
| Answers | "what is the architecture this design must extend?" | "which facts are established system truth?" |
| Consumed by | the Design Assistant (`baseline` + `base_ref`) | the diff, the review queue's scope column, the merge |

Both are real and neither substitutes for the other. A revision is a **reference**;
a scope promotion is a **merge**. The journey uses both, in that order.

### How to promote ARC-G to a baseline

1. **Review** — `/review`, or bulk-verify. The gate is not advisory: an audit over
   unverified facts reports extraction artifacts as architecture gaps.
2. **Commit** — `/changes/commit`. A revision is a snapshot, so reviewing the working
   set *after* committing does not review the revision.
3. **Freeze** — `/changes/freeze` on that revision. It becomes `kind="baseline"` and
   the Design Assistant extends it. Without a baseline the design run says so, and
   proposes against nothing.
4. **Promote** — `/changes/promote`, to merge verified Initiative facts into the
   system baseline. Repeatable: it is the evolution step, not a one-off.

Steps 2 and 3 being separate is load-bearing. Freezing is refused if the *revision*
has outstanding assertions — which is not the same as the working set having none,
and the flash message says which situation the reviewer is in.

## 2. The defect: one name, two meanings, and a merge that reported success

`core/knowledge/model.py` declared `SCOPE_INITIATIVE` **twice**:

```python
SCOPE_INITIATIVE = "INITIATIVE_PROPOSAL"  # line 109 — assertion lifecycle
...
SCOPE_INITIATIVE = "INITIATIVE"           # line 501 — identifier identity scope
```

Python executes top to bottom, so the second binding won. Every reader of the name
got `"INITIATIVE"`, and the assertion-lifecycle value was unreachable. Three
consequences, only the third of which was visible:

1. **The name meant two unrelated things** — where a *fact* stands, and how far an
   *identifier's* authority reaches. Nothing failed, because everything imported the
   same (wrong) constant, so the merge was self-consistent for facts written by
   ingest.
2. **`serialise` held a copy of the truth.** `assertion_from_dict` restored a missing
   scope from the hardcoded literal `"INITIATIVE_PROPOSAL"` — matching the
   *declaration* and not the *constant in force*.
3. **A restored fact could never be baselined, and nothing said so.**
   `promote_to_baseline` skipped any scope it did not recognise through a bare
   `continue` that counted nothing. Measured on a scope-less payload:

   ```
   written scope  : 'INITIATIVE'
   reloaded scope : 'INITIATIVE_PROPOSAL'
   promoted: 0 | skipped_unverified: 0 | skipped_disputed: 0 | already_baseline: 0
   ```

   The merge reported "0 promoted, 0 left behind" while dropping a VERIFIED fact.
   That is the one shape a reviewer cannot detect — indistinguishable from "there was
   nothing to promote" — and it is the same family as YB-051 (a pass emitting records
   nothing read).

**The measurement that says the journey was never walked.** Across every real store:

| Store | assertions | scope `INITIATIVE` | scope `SYSTEM_BASELINE` |
|---|---|---|---|
| `data/sea_home_01` / `acme_pillar_01` | 628 | 628 | **0** |
| `data/sea_home_02` / `payment` | 827 | 827 | **0** |

1455 assertions, not one of them established truth. The merge had never succeeded
anywhere, which is why no test caught it: the unit tests build graphs with the
constant, and the constant was consistently wrong.

## 3. The fix

- **The collision is removed at the name.** The identity constants became
  `IDENTITY_SCOPE_ENTERPRISE/INITIATIVE/DOCUMENT/RUN` — named for the ontology's own
  `IdentityScope`, which is what they are. Their *values* are unchanged, because the
  ontology enum fixes them.
- **The assertion lifecycle keeps the documented value.** Restoring
  `SCOPE_INITIATIVE = "INITIATIVE_PROPOSAL"` needed **no documentation change**:
  `living-system-architecture.md` §2, `ui-review-workflow.md`, `workspace-structure.md`
  and `graph-store-schema.md` all already said `INITIATIVE_PROPOSAL`. The docs were
  right and the code had drifted, which is the check that the direction was correct.
- **The copy is gone.** `serialise` uses `SCOPE_INITIATIVE`, so the load default and
  the write value cannot disagree again.
- **Existing graphs keep working.** Every assertion in `data/` carries `"INITIATIVE"`,
  so a fix that recognised only the new value would have made 1455 facts permanently
  unbaselineable — the failure being fixed, at scale. `INITIATIVE_SCOPES` accepts both
  on read; only the canonical value is ever written.
- **Nothing is skipped without being counted.** `PromotionResult` gained
  `skipped_inactive` and `unrecognised_scope` plus a `considered` property, and the
  `/changes/promote` flash reports them — an unrecognised scope now reads as
  "a vocabulary problem, not a review one" rather than as silence.
- **The projection layer stopped copying literals.** `app/projections.py` compared
  `a.scope` against bare `"SYSTEM_BASELINE"` / `"DOMAIN_TRUTH"` strings; a vocabulary
  change would have labelled every fact "Initiative", including baseline ones, with
  nothing failing. It reads the constants and `is_initiative_scope` now.

## 4. What the journey now verifies

`tests/test_evolution_journey.py` drives the path through the real routes, because a
unit test of `promote_to_baseline` on a hand-built graph is precisely what missed
this:

- the scope vocabulary — the two namespaces are distinct, and the values are the
  documented ones;
- a scope-less fact is still promotable, pinned at the `serialise` seam that broke it;
- a legacy `"INITIATIVE"` fact still merges;
- every fact the merge considers lands in a bucket, asserted as
  `result.considered == len(graph.assertions)` rather than as five numbers;
- the system is baselined (review → commit → freeze) before any evolution;
- an Initiative merges into the system baseline **and survives a reload** — the
  in-memory mutation is not the claim;
- **a later Initiative evolves the baseline instead of replacing it**: the facts
  already baselined are still present afterwards, the new facts are attributed to the
  new Initiative, and they merge on top;
- the diff reports what the later Initiative added;
- **the frozen baseline reaches the design run** as `baseline` and `base_ref`.

That last one is asserted on the agent's *recorded input*, not the rendered page. The
fake design factory hardcodes its own `design_caveats`, so the preview says "no frozen
baseline" whatever the app does — asserting on the page would have tested the fixture,
which the first draft of this test did.

## 5. What is NOT verified, and the honest limits

- **The real Design Assistant was not run against a real baseline.** The seam is
  verified (the app hands over the right graph and ref) and the digest is unit-tested
  (`test_design_digest.py`), but no end-to-end run with a model has designed against a
  frozen baseline. That needs a paid run.
- **The scope promotion has never happened on real data.** `data/sea_home_01` and
  `sea_home_02` still hold only initiative-scoped facts; nothing in the repo has been
  baselined for real. The journey is verified against a fixture store.
- **A second Initiative in the tests is seeded, not extracted.** The fake extractor
  returns one fixed graph for any document, so a second ingest folds into the facts
  already present and produces no new proposal. The evolution is written through the
  store and the merge is driven through the route — which exercises baselining,
  merging and diffing, but not "a second document extracted into an existing
  baseline".
- **`docs/user-journey.md` is stale.** It still marks stages 4, 6, 7 and 9 as blocked;
  review, reconciliation, the C4 view and baselining have all landed since. Updating
  it is follow-up work, deliberately not folded in here.
- **Two unrelated baselines remain two words for one concept.** The frozen revision
  and the promoted scope are both called "baseline" in the UI. They are genuinely
  different, but the naming invites exactly the conflation this record had to
  disentangle.

## 6. The lesson worth keeping

**A tested step is not a tested path.** Every stage of this journey had a passing
test — freeze refuses, freeze succeeds, promote reports what it left behind, the diff
renders — and the path was broken anyway, because the tests each built their own graph
with the same constant. The defect lived in the *seam between* stages: what one stage
wrote, the next stage could not read.

The specific smell is worth watching for: **two module-level constants with the same
name and different values.** Nothing warns. The later one wins, every consumer agrees
with every other consumer, and the disagreement only appears at a boundary where one
side used a hardcoded copy instead of the constant — which is exactly what
`serialise` did, and exactly what `check_schema_consistency` exists to prevent between
the ontology and the schemas.
