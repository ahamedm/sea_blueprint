---
id: ADR-0028
title: "No workspace migration — MVP validation starts on a fresh scope"
status: accepted
date: 2026-09-27
area: "`data/sea-deepseek/workspace.yaml` (deployment config), no code"
related: ["ADR-0027", "ADR-0026", "YB-026", "YB-037", "YB-042", "YB-043", "YB-050"]
---

# ADR-0028 — No workspace migration

> **Record.** Closes YB-049, which was opened when the async increment landed and
> proposed a script to carry the existing file-backed graph into a SQLite scope. The
> owner's decision is that it is **not required**: MVP validation starts afresh.

### Why the question arose

The background worker re-reads the working set immediately before merging and then
writes under its version token, so a stale write is refused rather than clobbering a
reviewer's edit (ADR-0027, decision 4). That needs a store that *can* refuse one:

- `SqliteStore` declares `concurrency_safe = True` and honours `expected_version`;
- `RevisionStore` (file) declares `concurrency_safe = False` and **refuses**
  `expected_version` rather than ignoring it.

So a file-backed scope cannot run a worker. The deployment was therefore declared with
two scopes — the pre-existing `default` (file, `path: .`) and a new `async` (SQLite) —
and YB-049 existed to give the existing graph a path into the second one.

### Decision

**Do not migrate.** The MVP validations begin on the fresh `async` scope, and the
existing graph stays where it is.

### Consequences

- **Nothing is risked.** The file-backed scope is untouched: it still opens, still
  renders, still reviews. There is no half-migrated state to reason about, no script
  to maintain, no dry-run/verify/rollback surface.
- **Two scopes, which is the intended workspace shape anyway** ([YB-042](../todos/entries/YB-042-workspace-structure.md)
  exists precisely because a workspace holds many products/systems, not one).
- **The validation surface is the SQLite scope**, starting empty. The first real
  baseline is therefore produced on a store that can guard writes — which is what
  makes background runs, and later multi-architect work ([YB-043](../todos/entries/YB-043-system-of-record.md)),
  possible on it.
- **The file scope can be retired by configuration, not migration**: removing its entry
  from `workspace.yaml` is a one-line change and its JSON stays on disk as an archive.
  That is a deployment decision, and deliberately not taken here.
- **A migration may still be wanted later.** The reversal condition is: the accumulated
  graph in the file scope becomes the product's real baseline *and* has to keep its
  history. If that happens, ADR-0027 records the constraint that forced the question,
  and YB-049's original write-up is in git history with the technical traps it had
  already identified — the important one being that `meta["version"]` belongs to the
  backend that issued it and must not be copied across, while revision ids and labels
  must be, because provenance and `baseline_ref` point at them.

### What this does not change

- **Artifact retention** ([YB-050](../todos/entries/YB-050-artifact-retention-and-quota.md))
  is unaffected and now matters sooner: validation on a fresh scope still writes a
  document artifact per ingest, and nothing deletes one.
- **The substrate's per-scope rule** stands: a scope that cannot guard a write runs its
  work in the request, exactly as before.

### Related

- [ADR-0027](ADR-0027-async-run-progress.md) — the increment that raised the question.
- [ADR-0026](ADR-0026-run-journal-and-progress-transports.md) — the progress log.
- [YB-026](../todos/entries/YB-026-asynchronous-progress.md) — the closed item this
  was split out of.
- [YB-050](../todos/entries/YB-050-artifact-retention-and-quota.md) — the other
  residual, which stands.
