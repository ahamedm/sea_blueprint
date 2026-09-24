---
id: ADR-0012
title: "REQ ⇄ ARC reconciliation — both directions, and the citation rule"
status: accepted
date: 2026-09-24
area: "core/knowledge/realization.py, core/knowledge/reconcile.py, core/knowledge/ingest.py, core/knowledge/model.py, app/"
related: ["YB-005", "YB-027", "YB-018"]
---

# ADR-0012 — REQ ⇄ ARC reconciliation — both directions, and the citation rule

> **Record.** Closes [YB-005](../todos/entries/YB-005-arcg-reqg-linkage.md), the
> platform's core purpose. The entry is kept because it carries the open items;
> semantic matching moved to [YB-027](../todos/entries/YB-027-semantic-reference-matching.md).

**Legacy item:** 5
**Status:** implemented, measured on the saved fixture
**Area:** `core/knowledge/realization.py` (new), `core/knowledge/reconcile.py`,
`core/knowledge/ingest.py`, `core/knowledge/model.py`, `app/projections.py`,
`app/templates/gaps.html`, `app/templates/reconcile.html`

---

### What was unmet

The two graphs were extracted independently and could not be joined. ADR-0006
built the binding half (`reconcile.py`) and left the other half explicitly open:
*"Invert the direction. All unresolved references currently run ARC → REQ. REQ-G
emits no cross-graph predicates, so 'requirements with no architectural answer'
cannot be computed at all."* On the saved fixture that was measurable: 22
requirements, 13 cross-graph references, zero requirement realizations.

### What was built

1. **The requirement side of the audit** — new `core/knowledge/realization.py`.
   `realization_state` walks every active requirement-ish claim once and reports
   four states per requirement: `none` (nothing claims it), `unresolved` (claimed,
   nothing bound), `partial` (some bound, some not), `full`. `unmet_obligations`
   reports the inverse — architecture asserting it answers something with no bound
   link. `realization_report` is both directions plus the counters.

   The four states are kept apart because the fix differs: `none` means the
   architecture document never mentioned the requirement (an extraction or scope
   problem), `unresolved` means reconciliation has work queued. One number would
   make the smaller, fixable case indistinguishable from the larger one.

2. **The citation rule** — the deepest form of the root cause. A requirements
   document numbers its requirements (`requirement_id` -> a typed external
   reference); an architecture element citing `FR-PM-001` is *quoting that key*.
   Under `implements_*` that is identity, not resemblance, so
   `reference_targets_a_node` resolves it and the reference is a link without a
   human adjudicating two numbering schemes. Before this, even a
   verbatim-preserved identifier could not join across documents — the one thing
   the earlier work existed to fix.

   Scoped deliberately:
   - only `implements_*` (the family whose range is a requirement and whose claim
     is "this answers THAT requirement"); every other predicate joins on meaning
   - only `REQUIREMENT_KEY` references, so an architecture profile's plain label
     (`OTHER`) stays evidence a human accepts
   - only requirement kinds, so the citation cannot reach across kinds
   - an enterprise key (Jira, DOORS) outranks a document-local one when two nodes
     carry the same identifier

3. **Ordering signals, not score changes** — `reference_candidates` now ranks
   within two groups: candidates that clear the threshold first, then
   same-Initiative, then the other document's graph, then score, then label.
   Correcting the order rather than the score is what keeps a wrong Initiative
   from ever displacing a defensible match, and it directly implements the entry's
   "Initiative is the primary scoping identifier".

4. **Confidence as a band** — a resolved link no longer arrives at `1.0` merely
   because a machine proposed it: identity reasons keep `1.0`, a lexical proposal
   keeps `0.5 + score/2` (0.875 at the acceptance threshold), a target the reviewer
   chose by hand keeps `1.0`. `status=VERIFIED` with human provenance remains the
   separate authority claim.

5. **Ingest stops losing what it needs** —
   - `requirement_type` was extracted and dropped. It is now asserted on
     requirement-kind nodes only; on an architecture triple it names the
     requirements document's vocabulary, not a property of the container.
   - cross-graph references are stamped `RequirementRealization` / scoping classes
     from one table in `model.py`, shared with `reconcile`.
   - the triples pass moved *after* the entity pass. A triple's cross-graph target
     is a literal, and whether that literal already names a node can only be
     decided once every declared node carries its identifiers.
   - `ExtractionRun.document_type` and `KnowledgeGraph.declared_by` record which
     document a run read and which document declared a node — the two facts the
     side-ordering needs, neither recoverable afterwards.
   - the requirements profile can now emit outward references
     (`requirement_refs` / `implements_requirements`), so the ARC → REQ direction
     is no longer baked in below reconciliation.

6. **Views** — `/gaps` renders both lists as two cards
   ("Requirements with no architectural answer", "Architecture claiming a
   requirement that never bound"); `/reconcile` states requirement coverage beside
   the references it binds; `/api/realization` serves the report as JSON.

### The measured result

The saved `data/output/` fixture, re-measured after this work:

| | |
|---|---|
| Requirements | 22 |
| Realized | **2** (both by wording coincidence — the entry's own point) |
| Unanswered | 20 |
| Bound realization edges | 9 |
| Unbound cross-graph references | 13 |
| Of those, with a proposal | 4 |
| Of those, the matcher cannot see | 9 |

The fixture predates identifier capture and carries **zero** `requirement_id`
values, so every proposal in it is lexical and 9 of 13 cannot be proposed at all.
That is the honest state, and it is now **stated** rather than approximated. The
mechanism is proven on synthetic graphs where identifiers do exist
(`tests/test_realization.py`, `tests/test_reconcile.py`).

### Deliberately not built

- **Semantic matching.** A paraphrase with no shared vocabulary
  (`"High Availability"` for the NFR *Availability*) is unreachable by a
  deterministic scorer, and that is the price of the property that makes this one
  reviewable: a reviewer can see *why* a link was proposed. Widening it is a
  different mechanism, tracked as [YB-027](../todos/entries/YB-027-semantic-reference-matching.md).
- **Creating the missing target.** Resolution still asserts the referent was
  already extracted (ADR-0006 decision 2). An architecture citing a requirement
  the requirements document never stated is reported as a finding instead.
- **Identifier recall tuning.** 16/18 with 1 false positive remains unmeasured
  against the new path; the saved fixture cannot measure it. Guesswork without a
  fresh run was not worth committing to.

### Worth keeping

- **Two documents disagreeing about what an identifier means is the normal case,
  not an error.** The whole typed-reference design exists for that, and this work
  added the one exception — a citation of a requirement key — without weakening
  the rule anywhere else.
- **Absence has to be a row, not a missing row.** The requirement-side report
  starts from the requirement list rather than from the references, because a
  requirement nothing mentions cannot be found by searching for its links.
- **A report that states its own limits is worth more than a bigger number.** The
  `proposed_claims` / `unproposed_claims` split exists so "waiting for a decision"
  and "the matcher cannot see it" are not the same statistic.
