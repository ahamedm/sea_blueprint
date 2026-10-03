---
id: YB-071
legacy: null
title: "An enterprise join key scores 1.00 but does not bind, so /reconcile and /realization disagree about one reference"
status: open
priority: medium
area: "`core/knowledge/model.py` (`READ_BOUND_REFERENCE_TYPES`, `reference_identity_reason`, `reference_targets_a_node`), `core/knowledge/reconcile.py` (`match_score`, `bulk_resolve`)"
created: 2026-10-03
updated: 2026-10-03
design: null
record: null
superseded_by: []
related: [YB-027, YB-005, YB-058]
blocks: []
blocked_by: []
---

# YB-071 — The identity rule is shared; the policy on it is not

> **Open — a decision, not a defect.** Filed while answering "is anything
> non-deterministic in mapping functional requirements to the system design?" The
> machinery there turned out to be internally consistent: one function answers "is
> this reference bound?", and every auto-bound reference is one the matcher also
> scores 1.00. What is *undecided* is where the boundary sits, and the two policies
> that sit either side of it are now named in one place instead of being an
> accident of two comparisons.

## The mechanism

`reference_targets_a_node` is the one answer to "does this reference already name a
node?" — used by `unresolved_references()`, `realization_edges`/`realization_state`,
and (for the input set it scores) `reference_candidates`. Its citation branch binds
only when the identifier's type is in `READ_BOUND_REFERENCE_TYPES`, which is
`{"REQUIREMENT_KEY"}`.

`match_score` is a different question — "how strong is this candidate?" — and it
scores a matching identifier 1.00 `external_ref` on `is_join_key` (i.e.
`scope == ENTERPRISE`), and 1.00 `citing_document_key` under an `implements_*`
predicate. So the matcher's 1.00 set is a **superset** of what binds on read, and
what closes the gap is `bulk_resolve`.

The two extremes are both defensible and both already documented:

- `is_join_key` is described as "an exact identifier match, the most reliable join
  available", and `Node.matchable_refs()` as "Only the identifiers reconciliation may
  match on".
- The read layer's caution rests on the repo's standing rule that a confident wrong
  join is worse than a missing one, because it makes the audit wrong rather than
  incomplete.

## The measurement

Constructed graph: a `Container` citing `implements_requirement → PA-77`, and a
`FunctionalRequirement` publishing `PA-77`.

| Requirement's reference | read layer | `/reconcile` | one `bulk_resolve` |
|---|---|---|---|
| `REQUIREMENT_KEY` + `ENTERPRISE` (Jira) | bound, coverage `full` | 0 proposals | n/a |
| `EA_REPOSITORY_ID` + `ENTERPRISE` | **unbound, coverage `none`** | 1 proposal, best 1.00 `external_ref` | bound, coverage `full` |
| `REQUIREMENT_KEY` + `DOCUMENT` | bound, coverage `full` | 0 proposals | n/a |

Row two is the whole item: within one graph, `/realization` reports the requirement
unanswered while `/reconcile` reports the same reference resolvable at 1.00. Nothing
is wrong — the reference is offered, and one pass writes it — but a reviewer reading
only the audit page sees a gap the matcher already considers certain, and nothing
says which reading they are looking at.

**Why this changes nothing today.** Scanned every stored graph JSON (24 files): 71
external references, **all DOCUMENT-scoped** — 69 `REQUIREMENT_KEY`, 2 `OTHER`, and
**zero ENTERPRISE**. So the branch this item is about has never been exercised by a
real graph; it is reachable only through `ingest._typed_external_refs`, which grants
`ENTERPRISE` scope to any `MANAGED_REFERENCE_TYPES` reference that names a `system`.

## Options

1. **Widen `READ_BOUND_REFERENCE_TYPES` to include `is_join_key`** (or the whole of
   `MANAGED_REFERENCE_TYPES`). The read layer then binds what the matcher calls
   certain, and the two pages agree. Cost: an enterprise-register key binds with no
   human step, which is the confirmation `bulk_resolve` exists to obtain.
2. **Narrow `match_score` to match the read layer** — stop scoring a non-published
   join key at 1.00. Cost: it contradicts two tests that pin the enterprise-key
   precedence (`test_an_enterprise_key_outranks_wording_without_a_document`,
   `test_an_enterprise_key_is_preferred_over_a_document_label_of_the_same_name`),
   and loses the strongest signal the matcher has.
3. **Leave both, and make the state visible.** Keep the boundary and label it in the
   UI: a requirement whose best claim is a 1.00 unbound reference is "certain, pending
   one pass", not simply `none`. Cheapest, and it makes the coverage number honest
   without changing who decides.

## What closes it

A chosen option, `READ_BOUND_REFERENCE_TYPES` and `match_score` agreeing with it, and
— for options 1 and 2 — a test that pins the new boundary the way
`test_a_published_join_key_is_proposed_definitively_but_not_yet_bound` pins the
current one today. If option 3 is chosen, that test stays and the change is in the
projection, not the matcher.

## Note for the reader

The rule itself is no longer duplicated: `reference_identity_reason` in
`core/knowledge/model.py` is the single definition of "identity or resemblance", and
`match_score` and `reference_targets_a_node` both call it. Deciding this item is
therefore a change to one constant plus, if desired, the branch it gates — not a
change to two implementations that have to be kept in step. The reason names
(`external_ref`, `citing_document_key`, and reconcile's evidence-only `unscoped_ref`)
are constants for the same reason.
