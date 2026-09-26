---
id: YB-036
legacy: null
title: "Modular run streaming — one progress mechanism for the browser and for unattended runs"
status: open
priority: high
area: "`agents/knowledge_extraction/agent.py`, `agents/extraction/passes.py`, `core/knowledge/store.py` (run journal), `app/__init__.py`"
created: 2026-09-25
updated: 2026-09-26
design: docs/design/event-driven-integration.md
record: null
superseded_by: []
related: ["YB-026", "YB-033", "YB-037", "YB-020", "ADR-0013"]
blocks: []
blocked_by: []
---

# YB-036 — Modular run streaming

> **Open work.** Design sketch, not yet reviewed. Why this is a separate item from
> YB-026 is in
> [`docs/design/event-driven-integration.md`](../../design/event-driven-integration.md) §Why streaming and workflow management are two items.

### Why

[YB-026](../entries/YB-026-asynchronous-progress.md) is the right first move: stop
running extraction inside the request and report passes as they complete. But its
phases are written for **one consumer** — "an HTMX poll on the ingest page", then
"SSE to the view". Two consumers are coming:

1. **a person watching a run they started** — the YB-026 case;
2. **a person or an operator watching a run nobody started** — an event-triggered
   ingest, or the Design Assistant drafting overnight. There is no open request, no
   page that initiated it, and possibly nobody watching at all.

If progress is produced by, or shaped for, the Flask response, the second case has
to reimplement it — and the reimplementation will be the one that drifts. So the
generalisation is worth deciding **before** YB-026 phase 2 is built, not after:
the pipeline must not know what a subscriber is.

### What exists

- `PassRecord` — pass name, chunk, outcome, path, elapsed, triples produced. Already
  produced per pass by the architecture profile, and now by the requirements
  profile too ([ADR-0013](../../decisions/ADR-0013-requirements-completeness-reporting.md)).
- `PassRunSummary` — aggregates calls, successes, empties, failures, fallbacks,
  elapsed; `describe()` renders one line.
- The ingest route reads the final payload and **discards the intermediate
  progress**: it exists for a moment and then only the aggregate survives.
- Nothing persists progress. `ExtractionRun`s are stored in the graph *after* merge,
  so a run that is still executing has no record anywhere.

### The shape

A **progress event log per run**, and sinks that write to it:

```
agent.run(..., progress=sink)        # the pipeline emits, and knows nothing else
   ├── NullSink            # tests, CLI, anything that does not care
   ├── RecordingSink       # append to the run journal on disk — the durable one
   └── SubscriptionSink    # push to whoever is attached (SSE / polling readers)
```

- **The pipeline emits; subscribers attach.** Extraction must not import Flask, and
  must not care whether anyone is listening.
- **A late subscriber gets the backlog, then live.** That is why the log is
  persisted rather than held in a request-scoped generator: an operator opening the
  page 40 seconds into a 20-minute run must see the 40 seconds it missed. An
  in-memory-only stream cannot do this after a restart either, which matters because
  the work is supposed to survive a restart ([YB-037](../entries/YB-037-background-workflow-management.md)).
- **Backpressure must not become a stall.** A slow or dead subscriber cannot block a
  pass from completing; the durable append is the source of truth and the live push
  is best-effort.
- **The terminal event carries the verdict.** End of stream is not completeness.
  The last event must state the run's `completeness` explicitly, because a stream
  that ends looks finished whether or not it was — the exact false assurance
  ADR-0013 exists to prevent.

### Decisions this item has to make

1. **Where the journal lives.** A JSON record per run alongside the working set is
   consistent with `RevisionStore`'s file-per-thing approach; anything heavier is a
   database decision the deployment has not made.
2. **The event vocabulary.** It has to serve both a progress bar and a "why is this
   taking so long" question: pass started / pass finished / tool call / retry /
   error / run finished. It should be a closed set, versioned, and stable enough
   that a view can rely on it.
3. **Retention.** Journals are per-run and disposable once the run is superseded —
   they must not accumulate forever in `data/sea`.
4. **Whether YB-026 phase 3 (SSE) is still worth it.** Polling may be entirely
   adequate; SSE adds a bundled extension and a second transport. The item should
   say which transport it actually recommends rather than listing all three.
5. **What a UI-triggered run and an event-triggered run share.** The honest answer
   is "everything except who is looking", and the design should be judged on whether
   that is true.

### Acceptance

- The extraction pipeline imports no web framework.
- The same progress log serves a page that initiated the run and a page that did not.
- A subscriber connecting late receives the events it missed, in order.
- A subscriber that disconnects mid-run does not affect the run's outcome.
- Every run's journal ends with an explicit completeness verdict.
- Partial progress never renders as completion.

### Added 2026-09-26 — the transport, answered

The AWS deployment shape
([`docs/design/deployment-architecture.md`](../../design/deployment-architecture.md) §3.4)
resolves two of the decisions above: agents publish minimal status to **Valkey**, and
Flask fans out to browsers. That is the right decoupling — the pipeline still imports
no web framework, and the browser transport becomes Flask's concern rather than the
agent's.

- **Where the journal lives (decision 1):** one Valkey **Stream** per run
  (`XADD run:<id> MAXLEN ~ …`), TTL'd once the run is terminal. Keeps journals out
  of `data/sea`, gives retention for free, and lets several Flask replicas tail one
  run.
- **Which transport (decision 4):** Stream, tailed by Flask, fanned out to the
  browser.

**It must be a Stream, not Pub/Sub.** Pub/Sub is fire-and-forget with no replay, so
it fails three acceptance criteria already written above: a late subscriber misses
the backlog, a restart loses the stream, and a dropped terminal event leaves a run
that looks finished — the false assurance this item exists to prevent. Streams give
ordered replay (`XRANGE` from the subscriber's last id) and consumer groups.

Two things to settle when this is implemented:

1. **The payload is transitions, not state.** Run id, product id, phase/pass, chunk
   label, counters, outcome, elapsed, usage — never the extracted content. The record
   stays the source of truth; the stream says it changed. Version the envelope
   (`event_version`), because agents and Flask deploy independently.
2. **The terminal verdict is persisted, not only published.** ElastiCache for
   Valkey's asynchronous durability risks up to 10 s of uncommitted writes, so a lost
   terminal event must not be the only place a run's completeness lives. Write the
   `ExtractionRun`, then publish.

**Failure contract:** if Valkey is unavailable, runs still execute and their results
still land in the record — only *live* progress degrades, and the UI falls back to
reading state.

### Related

- [YB-026](../entries/YB-026-asynchronous-progress.md) — the same mechanism for one
  consumer. **Design together.**
- [YB-037](../entries/YB-037-background-workflow-management.md) — the execution that
  produces progress; a stream is not a workflow.
- [YB-033](../entries/YB-033-event-ingress.md) — the unattended producer.
- [YB-020](../entries/YB-020-structured-path-budget.md) — why a run can be slow;
  progress makes the wait legible, it does not make it shorter.
