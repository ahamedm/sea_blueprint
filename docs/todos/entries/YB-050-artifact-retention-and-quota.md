---
id: YB-050
legacy: null
title: "Artifact retention and quota — the document store grows without a bound nobody chose"
status: open
priority: medium
area: "`core/artifacts.py`, `app/worker.py` (sweep), `core/workspace.py`"
created: 2026-09-27
updated: 2026-09-27
design: docs/design/async-run-progress.md
record: null
superseded_by: []
related: ["YB-026", "YB-042", "YB-043"]
blocks: []
blocked_by: []
---

# YB-050 — Artifact retention and quota

> **Open work.** Split out of [YB-026](../entries/YB-026-asynchronous-progress.md)
> when the async increment landed. The design specified retention; the code implements
> the store and **nothing deletes anything**.

### The problem

`core/artifacts.py` is the first place raw documents persist. Today the record keeps
only `document_hash`/`document_chars`/`document_ref`; the bytes themselves were never
stored. The worker needs them (it runs in another process, so an upload cannot stay
request-scoped), so they are written before the job is enqueued.

That makes the store grow monotonically:

- every ingest writes one artifact, addressed by its SHA-256;
- an artifact **orphaned by a failed enqueue**, or by a job that never ran, is never
  reclaimed;
- a retry or a re-delivery re-uses the same digest (that part works), but a
  re-uploaded, edited document is a new artifact;
- the only bound today is `DEFAULT_MAX_BYTES = 8 MiB` **per artifact** — a per-upload
  bound, not a per-scope one. A 4 MB document is ~599 chunks at the 7 000-char
  default, so a handful of large documents is hundreds of megabytes with no ceiling.

This is a data-retention question as much as a disk-space one: these are customer
documents, and the store cannot currently answer "what do we still hold, and why".

### What exists

- `ArtifactStore` — content-addressed, raw bytes, sharded `{hash[:2]}/{hash}`, atomic
  writes (temp file + `os.replace`), digest validated before any path join.
- `digests()`, `total_bytes()`, `delete(digest)` — **all three have no caller.** The
  primitives for a sweep exist; the policy does not.
- The design's rule (`docs/design/async-run-progress.md` §8.3): sweep by **artifact
  age with a grace period**, never by job age, because a job-age sweep can delete
  bytes a live job is still going to read.

### The shape this probably wants

- **A per-scope quota**, reported before it is hit — the run page and the ingest panel
  are already the places a person looks, and "this scope is at 85% of its artifact
  budget" belongs there rather than in a log.
- **An age-based sweep with a grace period**, run by the worker (which is the process
  that already sweeps nothing and runs continuously), refusing to delete a digest any
  job still references.
- **A reference check, not a guess.** A digest is referenced by a job's
  `input.artifact`; the sweep should ask the job store rather than infer from mtime.
  The obvious ordering rule: delete only when *no* job names the digest and the
  artifact is older than the grace period.
- **A stated retention period**, so "how long do we keep source documents" is a
  sentence someone can answer, not an emergent property of when the disk filled.
- **An explicit act for deleting a scope's documents.** These are the inputs an audit
  may want to re-read; deletion should be a deliberate operation with a report.

### Decisions this item has to make

1. **Age, count, or bytes?** A count is easy to explain and easy to get wrong (one
   huge document counts the same as one page). Bytes bound the resource that actually
   runs out; a per-scope byte budget with a grace period is the honest default.
2. **Who sweeps, and when?** The worker is the natural owner — it already runs
   continuously — but a sweep that runs mid-queue must never delete a digest a queued
   job is waiting to read.
3. **Is deleting an artifact allowed while its revision stands?** The revision holds
   the extracted facts, not the bytes, so deleting the artifact loses re-extraction
   but not the graph. That trade needs stating either way.
4. **Do drafts and their base graphs reference artifacts at all?** If not, the
   reference check is simpler than it looks.

### Acceptance

- A per-scope byte budget is enforced and reported before it is reached.
- A sweep deletes only artifacts older than the grace period that no job references.
- A queued or running job's artifact is never deleted, and a test proves it.
- The retention period is configured, not hard-coded, and appears in `.env.example`.
- `total_bytes()` is surfaced somewhere a human looks.

### Non-goals

No archival tier, no object store, no encryption-at-rest story (a real gap, but a
different one), no automatic deletion without a report.
