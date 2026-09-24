---
id: YB-028
legacy: null
title: "Remove a fact the extractor invented — and stop a dispute withholding the audit"
status: done
priority: high
area: "core/knowledge/review.py, core/knowledge/model.py, app/projections.py, app/templates/"
created: 2026-09-24
updated: 2026-09-24
design: null
record: docs/decisions/ADR-0015-retiring-extracted-facts.md
superseded_by: []
related: ["YB-009", "YB-018", "YB-024"]
blocks: []
blocked_by: []
---

# YB-028 — Remove a fact the extractor invented

> **Closed.** The record is
> [`ADR-0015`](../decisions/ADR-0015-retiring-extracted-facts.md). The entry is kept
> because the record carries follow-ups that stay open; see *Remaining* below.

**Raised:** reviewing the working set, not from the TODO index — the question was
"there are no options to delete facts/assertions that are completely out of zone, or
is it hiding somewhere?"
**Answer at the time:** it was not hiding. Six actions existed and none removed
anything.

---

### The problem

Every available action either vouched for a fact or annotated it. `dispute` rejects
one and deliberately keeps it active, so an invented fact went on being counted by
`unresolved_references()`, the gap report and the map while carrying a flag — and
because `ReviewProgress.outstanding = unverified + disputed` fed `is_auditable`, a
dispute withheld the audit permanently. A reviewer could dispute one hallucinated
fact, verify every other assertion in the graph, and still be told it was not
auditable, with no action left that would change that.

### What was built

Three slices, because they were three different problems:

1. **`retire`** — `status → RETIRED` with `superseded_by → (retired)`, so the fact
   leaves `active()` and therefore every query, the gap report and the map. The
   sentinel is non-empty because `merge_graphs` carries lineage only when it is
   truthy; with `""` the next extraction would fold the identical assertion back in
   and the removal would undo itself. Nothing is deleted — the assertion stays for
   the audit trail and `reset` restores it.
2. **The gate counts what is undecided.** `outstanding` (unverified + disputed, what
   needs attention) and `blocking` (unverified alone, what withholds the audit) are
   now separate numbers, and the gate reads the narrow one.
3. **Exclusions in the queue** — `exclude_predicate`, `exclude_kind`, and a run
   scope, plus ranked predicate counts so the noisiest predicate is visible. Hiding
   is not removing, in the model or the UI.

### Measured

Removing the five `satisfies_quality_attribute` references in the architecture
fixture (which cite NFRs the requirements graph does not contain) took unresolved
references **16 → 11**, leaving the nodes untouched.

### Remaining

- **Node deletion.** Not built, deliberately: identity is durable so re-extraction
  converges, and deleting a node would have the next run recreate it. Retiring the
  assertions about a spurious node leaves an orphan, which `dangling_assertions()`
  reports.
- **Retraction from a frozen baseline.** A promoted fact that is later removed is
  still asserted by an immutable revision snapshot, so a retraction has to be
  expressed as a change relative to it. [YB-009](YB-009-architecture-gaps.md).
- **Bulk removal.** The same shape as bulk verify. [YB-018](YB-018-review-batches.md).
