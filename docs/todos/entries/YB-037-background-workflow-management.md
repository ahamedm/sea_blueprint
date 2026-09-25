---
id: YB-037
legacy: null
title: "Background workflow management — durable execution for runs nobody is waiting for"
status: open
priority: high
area: "new `core/workflow/` or `app/worker.py`, `core/knowledge/store.py`, `app/__init__.py`, `app/templates/` (jobs page)"
created: 2026-09-25
updated: 2026-09-25
design: docs/design/event-driven-integration.md
record: null
superseded_by: []
related: ["YB-033", "YB-035", "YB-036", "YB-018", "YB-026", "YB-004"]
blocks: []
blocked_by: []
---

# YB-037 — Background workflow management

> **Open work.** Design sketch, not yet reviewed. Why this is separate from the
> streaming item is in
> [`docs/design/event-driven-integration.md`](../../design/event-driven-integration.md) §Why streaming and workflow management are two items.

### Why

The moment a run can start without a request — an event, a schedule, a phase change
— there is nobody to notice that it failed. Today that is invisible because the
failure surfaces as a flash message to the person who pressed the button. Unattended,
the same failure is a silent gap in the graph that the audit will later report as
"the architecture never mentioned this requirement".

So this item is a correctness item, not an ops nicety: the work has to be **durable
enough to survive its own failure**, and its failure has to be **visible somewhere
a human will look**.

### What the current pipeline does not guarantee

Measured against the code, not imagined:

| Concern | Today |
|---|---|
| Run state before completion | **Nothing durable.** `_run_id` mixes in `utc_now()`; a run record exists only after `graph_from_extraction`, and only inside the graph after merge |
| Retry | None. A failed run is a flash message; the work is gone |
| Idempotency | Assertions fold on re-ingest, but `_run_id` is fresh every time, so a retry adds a run |
| Revision policy | `/ingest` calls `store.commit` unconditionally — **one revision per ingest, automated or not** |
| Transaction across writes | `save_working` and `commit` are separate atomic writes; a crash between them leaves an uncommitted working set |
| Cancellation | None — a run cannot be stopped once started |
| Concurrency | None. The extractor runs inline, and a second concurrent run would contend for the **single-slot local model** |
| Observability | None. No jobs list, no attempts, no last error |

### The shape this probably wants

A small, explicit state machine, durable before the model call:

```
QUEUED → RUNNING → SUCCEEDED
                 ↘ FAILED → (retry, bounded) → DEAD_LETTER
                 ↘ CANCELLED
                 ↘ AWAITING_REVIEW      # the human gate
```

- **A job record written before execution**, not after. Everything else follows from
  this: without it there is no attempt count, no retry, no cancellation and nothing
  to show on a page.
- **One worker, one slot, deliberately.** The inference backend serves one request
  at a time; a queue with a visible depth is more honest than parallel submits that
  silently serialise. Parallelism is a property of the deployment, and this
  deployment has one slot.
- **Retry is bounded and classified.** A transport error is retryable; a malformed
  payload is not; a `FAILED` extraction run is *not* automatically retryable if the
  merge already applied — the apply step has to be idempotent, and assertions
  already are while runs and revisions are not.
- **The human gate is a workflow state, not a UI detail.** `AWAITING_REVIEW` is what
  makes "the run succeeded" different from "the graph is trustworthy", and it is the
  same gate the review page already implements. [YB-018](../entries/YB-018-review-batches.md)
  is where a batch becomes the unit of that gate.
- **Dead letters are surfaced where a human looks.** A jobs page, and a count on the
  dashboard — a background failure with no surface is the new silent failure.

### Decisions this item has to make

1. **Adopt an engine or build a thin queue.** A durable workflow engine
   (Temporal-shaped) gives retries, timers, HITL gates and replay, at the cost of a
   service dependency and an operational model this deployment does not have. A
   queue table plus one worker gives the states above and nothing else. The honest
   first question is which of the stronger guarantees are actually needed; the
   recommendation should be written down with the reason, not left to taste.
2. **Where job state lives.** `RevisionStore` is file-based and `_write_atomic` –
   a jobs file or a jobs directory is consistent; a database is a deployment
   decision that should be made once, explicitly.
3. **How a completed run lands.** Working set → revision → review log are three
   writes. Either accept a small window with a recovery rule ("a working set newer
   than the last revision is recovered, not discarded"), or introduce a single
   transactional boundary. The recovery rule is probably enough and much cheaper.
4. **What "the workflow" spans.** Does it end at "assertions merged", or at "batch
   reviewed"? If the latter, a workflow can be open for days and the engine question
   changes completely.
5. **Scheduling and timers.** A nightly audit, a "re-run when the source document
   changes" trigger, a phase-change watcher — these need timers, which is the first
   feature that makes an engine interesting.

### Acceptance

- A background run that fails leaves a durable record naming the failure and the
  attempt count; a retry is bounded and its reason is recorded.
- Killing the process mid-run leaves a `RUNNING` job that is recovered or marked
  failed on restart — never a permanently running ghost, and never a lost result
  that was actually produced.
- The same event delivered twice runs once and produces one revision.
- A queued job is visible with its position, and cancellation is possible before it
  starts.
- A run's output reaches `AWAITING_REVIEW` rather than silently implying the graph
  is trusted.
- Nothing in the workflow layer imports the web layer.

### Non-goals

No distributed workers, no message broker, no exactly-once claim. At-least-once with
idempotent apply is the honest target and the graph's content-addressed assertions
already support it.

### Related

- [YB-033](../entries/YB-033-event-ingress.md) — the producer that makes durability
  mandatory.
- [YB-036](../entries/YB-036-modular-run-streaming.md) — the progress of the work
  this executes.
- [YB-026](../entries/YB-026-asynchronous-progress.md) — a background job id and a
  status endpoint is a small piece of this item; they should agree.
- [YB-018](../entries/YB-018-review-batches.md) — the human gate as a workflow state.
- [YB-004](../entries/YB-004-model-output-not-structurally-stable.md) — why a retry
  is not automatically the right response to a `SUCCEEDED` run whose output varies.
