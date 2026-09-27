---
id: YB-037
legacy: null
title: "Background workflow management — durable execution for runs nobody is waiting for"
status: open
priority: high
area: "`core/jobs.py` (retry, cancellation and dedupe semantics), `app/worker.py` (recovery on start), `app/__init__.py` + `app/templates/` (jobs page), `core/knowledge/store.py`"
created: 2026-09-25
updated: 2026-09-27
design: docs/design/event-driven-integration.md
record: null
superseded_by: []
related: ["YB-033", "YB-035", "ADR-0026", "YB-018", "YB-026", "YB-004"]
blocks: []
blocked_by: []
---

# YB-037 — Background workflow management

> **Open work, re-scoped 2026-09-27.** The *substrate* this item asked for — a job
> record written before execution, an atomic claim, one worker, a heartbeat — was
> built by [YB-026](../entries/YB-026-asynchronous-progress.md) as part of the async
> increment. What remains is the **semantics**: retry, idempotency, cancellation,
> recovery, the human gate, and the jobs page. Do not rebuild the substrate; the
> table below says what already exists.

### Delivered by YB-026 — do not rebuild

| This item asked for | What exists now |
|---|---|
| A job record written **before** execution | `core/jobs.py` (`Job`, `JobStore`, `SqliteJobStore.enqueue`), written in the request before any model call |
| An atomic claim | `SqliteJobStore.claim` — `BEGIN IMMEDIATE` + conditional `UPDATE`, proven under four concurrent connections (`tests/test_jobs.py`) |
| One worker, one slot | `app/worker.py` / `sea-worker`; `run_once()` claims and runs exactly one job per call |
| Visible queue depth and position | `queue_depth()`, `queue_position()`, the run page and the ingest panel |
| Liveness | `worker_id`, `worker_heartbeat_at`, a heartbeat thread, and a stale marker on the page |
| A failed job that names its failure | `finish(state, error, worker_id)`; the reason is on the run page |
| Nothing in the workflow layer imports the web layer | `core/jobs.py` imports neither `app/` nor Flask; `app/runner.py` is Flask-free and both callers share it |

The states `QUEUED`, `RUNNING`, `SUCCEEDED` and `FAILED` are now **written**. The rest
of the vocabulary in the diagram below is reserved — the columns and the enum exist,
nothing sets them.

### Why

The moment a run can start without a request — an event, a schedule, a phase change
— there is nobody to notice that it failed. Today that is invisible because the
failure surfaces as a flash message to the person who pressed the button. Unattended,
the same failure is a silent gap in the graph that the audit will later report as
"the architecture never mentioned this requirement".

So this item is a correctness item, not an ops nicety: the work has to be **durable
enough to survive its own failure**, and its failure has to be **visible somewhere
a human will look**.

### What is still not guaranteed

Measured against the code as it now stands:

| Concern | Now |
|---|---|
| **Retry** | **None.** Acquisition *classifies* a source error as permanent or transient (`docs/design/async-run-progress.md` §8.2), but nothing acts on the classification. A `FAILED` job stays failed until a human re-enqueues it |
| **Idempotency** | `dedupe_key` is a reserved, unwritten column. A redelivered event still produces a second run and a second revision |
| **Recovery** | **Deliberately absent.** A `RUNNING` job whose worker died is *surfaced* as stale on the page — never re-queued, never failed. Nothing recovers a result that was produced but not applied |
| **Cancellation** | None — a claimed job cannot be stopped |
| **Revision policy** | `/ingest` and the worker still call `store.commit` unconditionally: one revision per run, automated or not |
| **Transaction across writes** | `save_working` then `commit` are still two atomic writes; a crash between them leaves an uncommitted working set |
| **The human gate** | `AWAITING_REVIEW` does not exist as a state, so `SUCCEEDED` still does not distinguish "the work ran" from "the graph is trustworthy" |
| **Surfaces** | A per-run page exists; there is no jobs list, no dead-letter surface, and no count on the dashboard |

### The shape

A small, explicit state machine. The substrate writes the first four transitions; the
annotations say who writes the rest:

```
QUEUED ──▶ RUNNING ──▶ SUCCEEDED            (YB-026 substrate)
                     ↘ FAILED ──▶ (retry, bounded) ──▶ DEAD_LETTER   (this item)
                     ↘ CANCELLED                                   (this item)
                     ↘ AWAITING_REVIEW      # the human gate        (this item)
```

- **A job record written before execution**, not after — **built.** Everything else
  follows from it: without it there is no attempt count, no retry, no cancellation and
  nothing to show on a page. `attempt` is already incremented on claim.
- **One worker, one slot, deliberately** — built as a process. The inference backend
  serves one request at a time; a queue with a visible depth is more honest than
  parallel submits that silently serialise. Note what the store does *not* enforce:
  `claim()` prevents a job being taken twice, but two workers would each take a
  *different* job and oversubscribe the model. Running one worker is an operational
  rule.
- **Retry is bounded and classified.** A transport error is retryable; a malformed
  payload is not; a `FAILED` extraction run is *not* automatically retryable if the
  merge already applied — the apply step has to be idempotent, and assertions are
  while runs and revisions are not. **Not built.**
- **The human gate is a workflow state, not a UI detail.** `AWAITING_REVIEW` is what
  makes "the run succeeded" different from "the graph is trustworthy", and it is the
  same gate the review page already implements. [YB-018](../entries/YB-018-review-batches.md)
  is where a batch becomes the unit of that gate. **Not built.**
- **Dead letters are surfaced where a human looks.** A jobs page, and a count on the
  dashboard — a background failure with no surface is the new silent failure.
  **Not built.**

### Decisions this item has to make

1. **Adopt an engine or build a thin queue.** **Answered in two parts (2026-09-27):**
   [`docs/design/async-run-progress.md`](../../design/async-run-progress.md) §13 defers
   the **engine**, with a four-condition trigger (two initiators in production; a
   workflow that spans the human gate; a hosted runtime or a second transport; retry
   logic appearing in more than one place), because adopting one before the human-gate
   boundary is settled is the irreversible cost while deferring it is cheap behind the
   `JobStore`/`RunJournal` seams. The **substrate** is not deferred: it is built (see
   the table above). The control-flow choice is also already made and shipped —
   `ARCHITECTURE_PASSES` is a fixed list — so this item is about durability, not
   routing. **The one thing to decide deliberately is whether the workflow boundary is
   the run or the review batch (decision 4 below)**; keep it at the run and the engine
   stays deferrable.
2. **Where job state lives.** **Answered enough to build on:** YB-026 introduced a
   minimal `JobStore` **protocol** whose only requirement is an atomic compare-and-set
   `claim()`, implemented on SQLite. The concrete backend remains a YB-043 coordination
   point (the file backend is single-writer, so it cannot run a worker at all).
3. **How a completed run lands.** Working set → revision → review log are three
   writes. Either accept a small window with a recovery rule ("a working set newer
   than the last revision is recovered, not discarded"), or introduce a single
   transactional boundary. The recovery rule is probably enough and much cheaper.
   **Open**, and now the most valuable thing here: the worker applies under a version
   guard, but a crash *between* save and commit is still unrecovered.
4. **What "the workflow" spans.** Does it end at "assertions merged", or at "batch
   reviewed"? If the latter, a workflow can be open for days and the engine question
   changes completely. **Open, and the one to settle before adopting an engine.**
5. **Scheduling and timers.** A nightly audit, a "re-run when the source document
   changes" trigger, a phase-change watcher — these need timers, which is the first
   feature that makes an engine interesting. **Open.**

### Acceptance

Met by the substrate, kept here so a later reader knows what is already true:

- [x] A job record exists before the model call, is claimed atomically, and carries an
  attempt count and a heartbeat.
- [x] A job that fails leaves a durable record naming the failure, and the queue is
  visible with a position.
- [x] Nothing in the workflow layer imports the web layer.

Still this item's:

- [ ] A failed job is retried boundedly, and the reason for the retry is recorded;
  classification is already available from the acquisition step.
- [ ] The same event delivered twice runs once and produces one revision
  (`dedupe_key` populated and enforced).
- [ ] Killing the worker mid-run leaves a `RUNNING` job that is recovered or marked
  failed on restart — never a permanently running ghost, and **never a lost result
  that was actually produced**.
- [ ] Cancellation is possible before a job starts, and a queued job's position is
  shown on a jobs page.
- [ ] A run's output reaches `AWAITING_REVIEW` rather than silently implying the graph
  is trusted.

### Non-goals

No distributed workers, no message broker, no exactly-once claim. At-least-once with
idempotent apply is the honest target and the graph's content-addressed assertions
already support it.

### Related

- [YB-033](../entries/YB-033-event-ingress.md) — the producer that makes durability
  mandatory.
- [ADR-0026](../../decisions/ADR-0026-run-journal-and-progress-transports.md) — the
  progress of the work this executes.
- [YB-026](../entries/YB-026-asynchronous-progress.md) — built the job record, the
  claim, the worker and the run page; this item owns what the states *mean*.
- [YB-018](../entries/YB-018-review-batches.md) — the human gate as a workflow state.
- [YB-004](../entries/YB-004-model-output-not-structurally-stable.md) — why a retry
  is not automatically the right response to a `SUCCEEDED` run whose output varies.
