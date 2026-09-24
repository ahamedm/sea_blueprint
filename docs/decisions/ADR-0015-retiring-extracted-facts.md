---
id: ADR-0015
title: "Removing a fact the extractor invented — retire, and what withholds the audit"
status: accepted
date: 2026-09-24
area: "core/knowledge/review.py, core/knowledge/model.py, app/projections.py, app/templates/"
related: ["YB-009", "YB-018", "YB-024"]
---

# ADR-0015 — Removing a fact the extractor invented

**Status:** implemented, verified end-to-end against the saved fixture
**Area:** `core/knowledge/review.py`, `core/knowledge/model.py`,
`app/projections.py`, `app/templates/partials/`, `app/templates/review.html`

---

### The gap

Six review actions existed and **none removed anything**: `verify`, `correct`,
`dispute`, `reset`, `promote_baseline`, `resolve`. A search for
`delete|remove|retire|drop|purge|reject` across `core/`, `app/`, `agents/` and
`scripts/` returned nothing.

The nearest thing was `dispute`, and it is explicitly not a removal — its docstring
says so ("It stays visible — silently deleting is worse"). Measured on one invented
fact:

```
after dispute          -> outstanding: 2  auditable: False  disputed: 1
all others verified    -> outstanding: 1  auditable: False
   the wrong fact is still: DISPUTED | active: True
```

Two consequences, both real:

1. **The phantom stayed in the audit.** `unresolved_references()`, the gap report
   and the map all read `active()`, which a dispute does not change. So a
   hallucinated `implements_requirement` kept counting as an open reference and kept
   drawing on the map, with a flag next to it.
2. **The gate could never be satisfied.** `ReviewProgress.outstanding = unverified
   + disputed`, and `is_auditable` required it to be zero — so disputing a wrong
   fact and verifying every other one still reported "not auditable", with no action
   left that would change it. A reviewer was trapped.

### What was built

**1. `retire` — removal.** `status → RETIRED`, `superseded_by → RETIREMENT_MARK`,
human provenance, a `Decision` in the audit trail. Supersession *is* this model's
deletion: `is_active` is false once `superseded_by` is set, and every query, audit
and view filters on it. Retirement is that mechanism with a sentinel in place of a
replacement id.

The sentinel is **non-empty** for a concrete reason. Assertion identity is
content-addressed, so a re-extraction re-produces the identical assertion, and
`merge_graphs` carries `superseded_by` across only when it is **truthy**:

```
extracted          : a_3919d75... uses_technology concept:valkey | active: True
marked superseded  : False
after re-ingest    : active: False  status: SUPERSEDED  superseded_by: (retired)
```

With `""` the next run would fold the fact back in as active and the removal would
silently undo itself. Verified to hold in both merge branches — including for an
assertion that was **verified first**, where the human-provenance branch
short-circuits the fold — and through `apply_delta`.

Nothing is deleted. The assertion stays in the graph, in the audit trail, and under
the `Removed` chip, and `reset` restores it.

**2. A dispute stops withholding the audit.** `ReviewProgress` now reports two
numbers, and the gate reads the narrower one:

| | Means | Withholds the audit |
|---|---|---|
| `outstanding` | unverified **+ disputed** — needs attention | no |
| `blocking` | **unverified only** — nobody has judged it | yes |

A disputed fact is judged. It is not unread, so it must not withhold the audit. It
*does* stay active, so the audit still sees it — which is what `retire` is for.

**3. Exclusions in the review queue.** `exclude_predicate`, `exclude_kind` and a
`run` scope, plus a ranked `by_predicate` count in the summary. The queue had every
filter except a way to say "not this", so a reviewer facing 200 invented triples
could isolate the noise but not work past it. Hiding is deliberately separate from
removal, in the model and in the UI, and there is a test asserting an exclusion does
not touch the graph.

### Why not simply make dispute remove the fact

Because the two acts answer different questions, and collapsing them would lose one:

- **Dispute** — "I think this is wrong and it is still in play." It may be argued,
  or resolved later by verifying or correcting it. Those flows need the fact.
- **Retire** — "this should never have been extracted." There is no resolution to
  reach except the graph ceasing to assert it.

`correct` is the third: "here is what it should say instead" — a replacement rather
than a removal, which is why `superseded_by` points at it.

### Bugs found while building this

- **`reset` did not clear the retirement mark.** Setting the status back to
  UNVERIFIED is not enough, because `is_active` reads the lineage too — so reopening
  a removed fact left it inactive and invisible, a restore that silently did nothing.
  The two cases are now told apart by what the lineage points at: `RETIREMENT_MARK`
  is cleared (nothing replaced it), any other value is left alone (a real
  replacement is held in the graph, and clearing it would orphan its correction).
- **Template false positive in my own smoke test**: `t.id.encode() not in
  response.data` returns False for any `str` in `bytes`, so a check written to prove
  removal looked like it was failing when the removal had worked. The unit test uses
  bytes on both sides.

### Measured

Through the real app on the architecture fixture: removing the five
`satisfies_quality_attribute` references (which cite NFRs the requirements graph
does not contain) took unresolved references **16 → 11** and the map's open
references to 11, leaving the nodes alone. The retired assertions appear under
`/review?only=retired` and not in the default view.

### Deliberately not built

- **Node deletion.** Node identity is durable so re-extraction converges and
  corrections have something to attach to; deleting a node would have the next run
  recreate it. Retiring the assertions about a spurious node leaves an orphan, which
  `dangling_assertions()` reports rather than hides.
- **Retraction from a frozen baseline.** Revision snapshots are immutable, so
  retracting a promoted fact has to be expressed as a change *relative* to them.
  That is [YB-009](../todos/entries/YB-009-architecture-gaps.md)'s territory and is
  recorded there rather than half-built here.
- **Bulk removal.** Retiring many facts at once is the same shape as bulk verify and
  belongs with [YB-018](../todos/entries/YB-018-review-batches.md), which is also
  where the run-scoping filter gets its fuller form.
