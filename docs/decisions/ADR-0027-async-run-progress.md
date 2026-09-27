---
id: ADR-0027
title: "Asynchronous run progress — the execution substrate, and one view over two transports"
status: accepted
date: 2026-09-27
area: "`core/jobs.py` (new), `core/artifacts.py` (new), `core/events.py`, `core/workspace.py`, `app/runner.py` (new), `app/worker.py` (new), `app/__init__.py`, `app/templates/`, `pyproject.toml`"
related: ["ADR-0026", "YB-037", "YB-033", "ADR-0028", "YB-050", "ADR-0013", "YB-020", "YB-023"]
---

# ADR-0027 — Asynchronous run progress

> **Record.** Closes YB-026. The analysis lives in
> [`docs/design/async-run-progress.md`](../design/async-run-progress.md); this record
> holds the decisions and the evidence that they hold, so a later reader does not have
> to re-derive them from the code.

### The problem, measured

`/ingest` called the extractor **inside the request**. For the whole duration the
browser sat on a page with no progress, and — one Flask process, one inference slot —
the user could not so much as load another page. The same small document took **62 s**
on one run and **exceeded 25 minutes** on another, so the stall was not a fixed cost
to tolerate but a variance of an order of magnitude. Worse than the UX: a synchronous
call that outlives a proxy or client timeout leaves the extraction result **discarded**
— the work was done and the graph never updated.

### What was built

| Phase | What | Where |
|---|---|---|
| 1 | The journal producer attached to `/ingest` and `/design/draft`; run id minted before the model call; verdict published after the record is persisted; `_default_journal` degrades instead of failing to boot | `app/__init__.py`, `app/runner.py`, `agents/extraction/progress.py` |
| 1b | A cursor: `RunEvent.stream_id`, stamped on read, returned as `cursor` by the events API | `core/events.py` |
| 2 | The execution substrate: `Job` + `JobStore` protocol + SQLite backend with an atomic claim, a content-addressed artifact store, one `sea-worker` process, a run page | `core/jobs.py`, `core/artifacts.py`, `app/worker.py` |
| 3a | Polling over an HTML fragment, which works with no journal at all | `app/templates/partials/` |
| 3b | Server-sent events over the same fragment, with `Last-Event-ID` resume | `app/__init__.py`, `app/templates/run.html` |

One code path executes a run, whether the request or the worker drives it
(`app/runner.py`), and Flask never appears in it.

### Decisions

1. **The substrate ships with the async work; the engine does not.** Owner decision
   (2026-09-27): the job record, claim, worker and heartbeat were introduced *with* the
   progress work rather than gated behind a latency measurement, because progress
   without a worker and a worker without progress are each half a mechanism, and
   deferring the substrate compounds per initiator. A durable workflow engine
   (Temporal-shaped) stays deferred, with a four-condition trigger.
2. **The workflow boundary is the run, not the review batch.** This is the decision
   that keeps the engine deferrable, and it belongs to YB-018/YB-037 to honour: a
   workflow that spans a days-long human gate needs timers.
3. **A worker is a separate process, not a thread.** The reloader is on by default in
   this deployment and would kill a thread mid-extraction; a thread per Flask replica
   would also duplicate consumers for a single-slot model. `worker.run_once()` exists so
   tests need neither.
4. **The substrate is configured, like the journal.** A scope whose store can guard a
   write runs in the background; a **file-backed** scope gets no job store and runs the
   work in the request, exactly as before. The worker's apply step re-reads the working
   set and writes under its version token, so a scope that cannot refuse a stale write
   must not run one. A scope therefore has to *be* SQLite before it can run in the
   background; [ADR-0028](ADR-0028-no-workspace-migration.md) records that no
   migration is needed for MVP validation.
5. **Both transports, one view.** Polling is the fallback for installs with no journal;
   SSE is the transport when the journal retains events. Both render
   `partials/run_events.html`, and the server decides from `RunJournal.retains` — so
   they cannot drift and nothing has to guess.
6. **Valkey is the notification log; the store is the record.** The worker *publishes*
   progress to a Stream and *claims* work from SQLite. A queue in Valkey would give
   execution state a second durability problem, and the claim needs a compare-and-set.
7. **MCP is not the status transport.** Progress is emitted by the harness at a
   transition, not chosen by the model; wrapping it in a model-tool protocol would make
   a best-effort append a network round trip and a failure surface. MCP remains right
   for *fetching* a service-initiated document — by deterministic worker code, before
   any pass — which is YB-033's work.
8. **Provenance for a fetched document is an additive change, and the artifact is the
   bytes.** The artifact digest is SHA-256 over the bytes as received (not the decoded
   text, which is lossy); `source_type` stays `SOURCE_EXTRACTION` because that is the
   origin of the *fact*, with the acquisition channel as its own field.
9. **The web app loads `.env` itself.** `create_app` resolved `STORE_ROOT` before any
   agent (which does call `load_dotenv`) was constructed, so `SEA_DATA_DIR` in `.env`
   silently did not apply and a declared workspace was invisible.

### Acceptance

| Criterion | Evidence |
|---|---|
| `POST /ingest` returns before the model call | `test_posting_ingest_returns_before_the_model_runs` |
| A job record exists in `QUEUED` before the agent runs, `RUNNING` while it does | `test_the_worker_runs_the_job_and_the_graph_gains_the_facts` |
| Two submits produce one running job and one queued, never two concurrent runs | `test_two_submits_produce_one_running_job_and_one_queued` |
| The claim is atomic | `test_concurrent_claims_over_one_file_are_exclusive` (four threads, one file) |
| A late or reconnecting subscriber gets what it missed, in order | `test_a_poll_from_the_cursor_returns_only_what_was_missed`, `test_a_live_stream_pushes_on_the_event_and_closes_on_the_verdict` |
| An edit during a run is not overwritten | `test_a_review_edit_during_a_run_survives_the_apply` |
| A write landing between read and apply fails the job and merges nothing | `test_a_write_landing_between_read_and_apply_fails_the_job` |
| The terminal event carries `completeness`; partial never renders as success | `test_a_design_run_shows_its_verdict_without_a_journal`; observed live on a real run (`PARTIAL`) |
| With no journal the page still works and the app still boots | `test_polling_stops_without_any_journal_events`, `test_a_configured_but_unusable_journal_degrades_at_boot` |
| Cross-scope reads are refused; an unknown run 404s | `test_the_events_api_404s_an_unknown_run_once_jobs_exist` |
| The worker refuses a scope it cannot guard | `test_a_file_backed_scope_is_not_runnable_in_the_background` |

Verified against a live Valkey and a live run, not only fakes: the worker claimed the
job, heartbeated through it, published `run.started`/`pass.*`/`run.finished` to
`sea:run:<id>`, and the page showed `SUCCEEDED` beside `PARTIAL` — a job that succeeded
producing a verdict that was incomplete, which is precisely the distinction ADR-0013
exists to protect.

### What this closed, and what it handed on

- **ADR-0026** — the progress log itself: envelope, vocabulary, journal, cursor, and
  the transport rule.
- **YB-037** — retry, idempotency, cancellation, recovery, `AWAITING_REVIEW` and the
  jobs page. This item built the substrate; YB-037 owns what the states *mean*.
- **YB-033** — the source fetch (phase 4) and its adapters. A `source` job fails
  legibly today rather than producing a confidently empty graph.
- **ADR-0028** — no workspace migration: MVP validation starts on a fresh SQLite
  scope, and the existing file-backed graph is left untouched.
- **YB-050** — artifact retention and quota. The store's `delete()`/`total_bytes()`
  have no caller; nothing deletes anything yet.

### Open questions, for a human

1. Which source system is first (Confluence, SharePoint, a generic API), and who owns
   its credentials?
2. What happens when a source has moved on from the version an event named? The design
   records and flags the divergence; the policy is not decided.
3. The job backend is a YB-043 coordination point — the protocol is what this item
   committed to.

### Review

Three adversarial passes shaped this. A **design review** by three lenses (code
grounding, architecture, skeptic) corrected the item's factual claims and forced the
substrate to be a protocol; an **acquisition review** found that the first draft had
the fetch in the wrong process and that a document is not universal; a **code review**
found defects the fake-extractor tests could not — a thread-bound SQLite connection
that had silently disabled the heartbeat, terminal state read from the event slice
(so polling never stopped without a journal), a design run showing no verdict, an
unguarded events API, an artifact digest over decoded text, and a locked database
killing the worker loop. All are fixed with tests.

### Related

- [ADR-0026](ADR-0026-run-journal-and-progress-transports.md) — the progress log.
- [YB-037](../todos/entries/YB-037-background-workflow-management.md) — durable
  execution semantics.
- [YB-033](../todos/entries/YB-033-event-ingress.md) — the unattended producer.
- [ADR-0028](ADR-0028-no-workspace-migration.md) — why no migration is needed, and
  what would bring the question back.
- [YB-050](../todos/entries/YB-050-artifact-retention-and-quota.md) — the document
  store's retention.
