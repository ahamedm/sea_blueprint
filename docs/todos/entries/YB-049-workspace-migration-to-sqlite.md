---
id: YB-049
legacy: null
title: "Workspace migration — move a file-backed scope to SQLite so it can run in the background"
status: open
priority: high
area: "new `scripts/migrate_scope.py`, `core/workspace.py`, `core/knowledge/store.py`, `core/knowledge/store_sql.py`, `core/knowledge/drafts.py`"
created: 2026-09-27
updated: 2026-09-27
design: docs/design/async-run-progress.md
record: null
superseded_by: []
related: ["YB-026", "YB-043", "YB-042", "YB-037"]
blocks: []
blocked_by: []
---

# YB-049 — Workspace migration: file-backed scope → SQLite

> **Open work.** Split out of [YB-026](../entries/YB-026-asynchronous-progress.md)
> when that item's async increment landed: the substrate is built, but the scope it
> runs against has to be migrated first, and no path to do that exists.

### The problem

A worker re-merges its result at the end and must be able to **refuse a stale write**,
because a reviewer editing assertions during a 20-minute run must not be silently
overwritten. The file backend has no version token and says so
(`RevisionStore.concurrency_safe = False`; `save_working` refuses `expected_version`
rather than ignoring it), so:

- a **file-backed scope cannot run a worker at all** — `current_job_store()` returns
  `None`, the route runs the extraction inside the request, and the page says
  *"synchronous"*;
- a **SQLite-backed scope runs in the background** — `SqliteStore` declares
  `concurrency_safe = True` and the guarded apply works.

The consequence today is a fork in the road with no bridge: the existing product's
graph lives in `data/sea-deepseek/` as `working.json` + `revisions/` + `drafts/` +
`index.json`, and to get background runs on that graph it would have to start over in
a new, empty SQLite scope.

### What exists

- `SqliteStore` (`core/knowledge/store_sql.py`) — the same `Store` contract, SQLAlchemy
  Core over SQLite, WAL, `concurrency_safe = True`.
- A workspace manifest can already name a backend per scope
  (`core/workspace.py`, `backend: sqlite`, `path: …`), and the deployment now declares
  one scope of each kind. So the *destination* is a config change; only the **copy**
  is missing.
- `RevisionStore` (file) exposes everything the copy needs: `load_working()`,
  `list_revisions()`, `load_revision(id)`, and the review log travels inside
  `Snapshot.log`.
- `DesignDraftStore` is separate and filesystem-based in both backends
  (`core/workspace.py::scope_drafts_dir`), so drafts need moving too — or deliberately
  not moving, which is a decision to state rather than leave implicit.

### The shape this probably wants

A one-shot, **idempotent, dry-run-by-default** script in the spirit of
`scripts/reconcile.py`:

```
verify the destination is empty (or --force)
→ copy working set            (graph + review log + meta, minus the version token)
→ copy every revision, in order, preserving ids and labels
→ copy drafts                 (or report how many are left behind)
→ verify: counts of nodes / assertions / revisions match on both sides
→ report what was written, and what was skipped and why
```

- **Dry run by default.** It writes a graph a human spent weeks reviewing; the
  default has to be a report, not a move, and the diff has to be shown before it
  happens.
- **Idempotent and restartable.** A migration interrupted halfway must be resumable
  and must not double-insert revisions.
- **Do not delete the source.** The file store stays intact; the JSON is the backup,
  and deleting it is a separate, explicit act.
- **Revision ids and labels are preserved**, not regenerated: revision ids appear in
  provenance, review records and `baseline_ref`, so regenerating them would sever the
  audit trail.
- **The version token is not copied.** `meta["version"]` belongs to the backend that
  issued it; carrying it over would make the first guarded write compare against a
  number the SQLite store never handed out.

### Decisions this item has to make

1. **Move the existing scope, or start the async work in a new one?** The deployment
   currently does the latter (`async` scope), which is zero-risk but means two graphs.
   This item is what makes the former possible.
2. **Drafts: moved or abandoned?** A draft is a proposal against a specific working
   set; copying it into a store whose working set may already differ is a judgement
   call, not a mechanical one.
3. **Is this a script or a route?** A script is auditable and re-runnable; a button in
   the UI invites a click nobody can review.
4. **What proves it worked?** "The counts match" is necessary, not sufficient — the
   review log's decisions are the part a count will not catch.

### Acceptance

- `--dry-run` (the default) writes nothing and reports exactly what would be copied.
- The migrated scope opens in the app and the graph renders identically: same nodes,
  same assertions, same revision list, same review decisions.
- Re-running after a successful migration is a no-op, not a duplicate.
- An interrupted run can be resumed without duplicating a revision.
- The source scope is untouched, and still opens.
- After migration, the worker claims and runs a job on that scope — the point of the
  exercise.

### Non-goals

No PostgreSQL move (that is [YB-043](../entries/YB-043-system-of-record.md)), no
schema redesign, no automatic deletion of the source, no online migration while a
worker is running.
