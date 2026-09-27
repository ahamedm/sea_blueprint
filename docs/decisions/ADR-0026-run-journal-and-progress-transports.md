---
id: ADR-0026
title: "Modular run streaming — one progress mechanism for the browser and for unattended runs"
status: accepted
date: 2026-09-27
area: "`core/events.py`, `agents/extraction/progress.py`, `agents/extraction/passes.py`, `core/workspace.py`, `app/__init__.py`, `app/templates/`"
related: ["YB-026", "YB-033", "YB-037", "YB-020", "ADR-0013"]
---

# ADR-0026 — Modular run streaming

> **Record.** Closes YB-036, which was written on 2026-09-25 as a design sketch and
> built in two passes: the journal and the producer seam first, then the subscribers.
> The body below is the entry's write-up, preserved — the reasoning is the point, not
> the diff.

### Why this was a separate item from YB-026

[YB-026](../todos/entries/YB-026-asynchronous-progress.md) was the right first move:
stop running extraction inside the request and report passes as they complete. But its
phases were written for **one consumer** — "an HTMX poll on the ingest page", then
"SSE to the view". Two consumers were coming:

1. **a person watching a run they started** — the YB-026 case;
2. **a person or an operator watching a run nobody started** — an event-triggered
   ingest, or the Design Assistant drafting overnight. There is no open request, no
   page that initiated it, and possibly nobody watching at all.

If progress were produced by, or shaped for, the Flask response, the second case would
have to reimplement it — and the reimplementation would be the one that drifts. So the
generalisation was decided **before** YB-026's background phase was built: the pipeline
must not know what a subscriber is. The test is one sentence: **the extraction pipeline
must not import a web framework, and a run must be able to complete with no subscriber
at all.**

### What existed when this was written

- `PassRecord` — pass name, chunk, outcome, path, elapsed, triples produced. Already
  produced per pass by the architecture profile, and now by the requirements profile
  too ([ADR-0013](ADR-0013-requirements-completeness-reporting.md)).
- `PassRunSummary` — aggregates calls, successes, empties, failures, fallbacks,
  elapsed; `describe()` renders one line.
- The ingest route read the final payload and **discarded the intermediate progress**:
  it existed for a moment and then only the aggregate survived.
- Nothing persisted progress. `ExtractionRun`s are stored in the graph *after* merge,
  so a run that is still executing had no record anywhere.

### The shape that was built

A **progress event log per run**, and sinks that write to it:

```
agent.run({..., "progress": sink})   # the pipeline emits, and knows nothing else
   ├── NullJournal          # tests, CLI, no-Valkey installs, anything that does not care
   ├── ValkeyJournal        # append to the run's Stream — the durable one
   └── subscribers          # SSE / polling readers, attached later and independently
```

- **The pipeline emits; subscribers attach.** Extraction does not import Flask and does
  not care whether anyone is listening.
- **A late subscriber gets the backlog, then live.** That is why the log is persisted
  rather than held in a request-scoped generator: an operator opening the page 40
  seconds into a 20-minute run must see the 40 seconds they missed. An in-memory-only
  stream cannot do this after a restart either, which matters because the work is
  supposed to survive a restart ([YB-037](../todos/entries/YB-037-background-workflow-management.md)).
- **Backpressure must not become a stall.** A slow or dead subscriber cannot block a
  pass from completing; the durable append is the source of truth and the live push is
  best-effort. `_emit_progress` catches a sink exception and logs it; `JournalProgress`
  itself never raises on an append, so a caller driving it directly gets the same
  guarantee.
- **The terminal event carries the verdict.** End of stream is not completeness. The
  last event states the run's `completeness` explicitly, because a stream that ends
  looks finished whether or not it was — the exact false assurance ADR-0013 exists to
  prevent.

### Decisions, and how they were answered

1. **Where the journal lives.** One Valkey **Stream** per run (`XADD … MAXLEN ~`),
   TTL'd once the run is terminal. Keeps journals out of `data/sea`, gives retention
   for free, and lets several Flask replicas tail one run. The alternative — a JSON
   record per run beside the working set — was rejected as a second persistence
   problem the deployment had not otherwise taken on.
2. **The event vocabulary.** A closed, versioned set: `run.started`, `pass.started`,
   `pass.finished`, `tool.call`, `pass.retry`, `pass.error`, `run.finished`,
   `run.failed`. The payload key set is closed too (`PROGRESS_PAYLOAD_KEYS`), which is
   what makes "no extracted content on the wire" checkable rather than intended. A new
   kind is an addition to a new envelope version, not an ad-hoc string.
3. **Retention.** TTL on the stream (24 h default) plus `MAXLEN ~` as a bound. Journals
   are per-run and disposable once the run is superseded.
4. **Which transport.** Both, and the deployment picks: polling (`hx-trigger="every
   2s"`) is the fallback that works with no journal at all; SSE is the transport when a
   journal retains events. They render one template so they cannot drift. The HTMX SSE
   extension was rejected — a second vendored asset for what a small `EventSource`
   script does, and it does not remove the polling path.
5. **What a UI-triggered and an event-triggered run share.** Everything except who is
   looking. Confirmed rather than assumed: the journal is keyed by run id, the run page
   is reachable by job id, and nothing in `agents/` or `core/events.py` mentions a
   request.

**It must be a Stream, not Pub/Sub.** Pub/Sub is fire-and-forget with no replay, so it
fails three of the acceptance criteria below: a late subscriber misses the backlog, a
restart loses the stream, and a dropped terminal event leaves a run that looks finished
— the false assurance this item exists to prevent. Streams give ordered replay
(`XRANGE` from the subscriber's last id) and consumer groups.

Two constraints carried into the implementation:

1. **The payload is transitions, not state.** Run id, scope, pass, chunk label,
   counters, outcome, elapsed — never the extracted content. The record stays the source
   of truth; the stream says it changed. The envelope is versioned (`event_version`),
   because agents and Flask deploy independently.
2. **The terminal verdict is persisted, not only published.** Asynchronous durability
   on a managed cache risks seconds of uncommitted writes, so a lost terminal event
   must not be the only place a run's completeness lives. Write the `ExtractionRun`,
   then publish.

**Failure contract:** if Valkey is unavailable, runs still execute and their results
still land in the record — only *live* progress degrades, and the UI falls back to
reading state. `_default_journal()` returns `NullJournal` when no endpoint is
configured **and** when a configured endpoint's client library is missing, so a
misconfiguration cannot stop the app booting.

### Acceptance — how each criterion was met

| Criterion | Evidence |
|---|---|
| The extraction pipeline imports no web framework | `agents/` imports `core.events` only; enforced by the no-model tests |
| The same progress log serves a page that initiated the run and a page that did not | The log is keyed by run id and read by `_job_context`, which knows nothing about who started the run; the unattended producer itself is [YB-033](../todos/entries/YB-033-event-ingress.md) |
| A subscriber connecting late receives the events it missed, in order | `RunJournal.read(run_id, since=…)` replays from a cursor; SSE sends `id:` and honours `Last-Event-ID` |
| A subscriber that disconnects mid-run does not affect the run's outcome | The append is best-effort and never raises (`JournalProgress._append`); verified by `tests/test_run_progress.py` and the "dead journal" route test |
| Every run's journal ends with an explicit completeness verdict | `JournalProgress.finished()` requires a verdict positionally; `failed()` defaults to UNKNOWN. Observed on a live run: `run.finished` with `completeness=PARTIAL` |
| Partial progress never renders as completion | The run page renders job state and run verdict as two separate badges, and *"not COMPLETE — absence of a fact is not evidence of its absence"* |

### The protocol change this record closes with

`read()` originally returned `RunEvent`s and **discarded the stream id**, so an SSE
frame could not set `id:` and a polling client could not advance `since` — replay was
impossible even though `append` returned an id. That gap was closed by carrying
`stream_id` on the envelope and stamping it on read; `GET /api/runs/<run_id>/events`
returns a `cursor`, and the SSE route maps `Last-Event-ID` back to it.

### Related

- [YB-026](../todos/entries/YB-026-asynchronous-progress.md) — the consumer side: the
  producer on the app path, the execution substrate, and both transports.
- [YB-037](../todos/entries/YB-037-background-workflow-management.md) — the execution
  that produces progress; a stream is not a workflow.
- [YB-033](../todos/entries/YB-033-event-ingress.md) — the unattended producer.
- [YB-020](../todos/entries/YB-020-structured-path-budget.md) — why a run can be slow;
  progress makes the wait legible, it does not make it shorter.
