---
id: YB-026
legacy: "26"
title: "Asynchronous progress — stream pass and tool-call completion to the view"
status: done
priority: high
area: "`app/__init__.py`, `app/runner.py`, `app/worker.py`, `app/templates/run.html`, `core/jobs.py`, `core/artifacts.py`, `core/events.py`, `core/workspace.py`, `agents/extraction/progress.py`, `agents/extraction/passes.py`, `agents/knowledge_extraction/agent.py`, `pyproject.toml`"
created: 2026-09-23
updated: 2026-09-27
design: docs/design/async-run-progress.md
record: docs/decisions/ADR-0027-async-run-progress.md
superseded_by: []
related: ["YB-020", "YB-023", "YB-024", "YB-025", "YB-033", "YB-035", "ADR-0026", "YB-037", "YB-049", "YB-050"]
blocks: []
blocked_by: []
---

# YB-026 — Asynchronous progress — stream pass and tool-call completion to the view

> **Closed.** The record is
> [`ADR-0027`](../../decisions/ADR-0027-async-run-progress.md), and the full analysis
> is [`docs/design/async-run-progress.md`](../../design/async-run-progress.md). The
> write-up below is preserved.
>
> All five phases (1, 1b, 2, 3a, 3b) are built, and the two acceptance checks that
> were outstanding at the last update now exist:
> `test_two_submits_produce_one_running_job_and_one_queued`,
> `test_a_review_edit_during_a_run_survives_the_apply` and
> `test_a_write_landing_between_read_and_apply_fails_the_job`. What used to be this
> item's tail is now other items: the source fetch is
> [YB-033](../entries/YB-033-event-ingress.md), retry/recovery/cancellation and the
> jobs page are [YB-037](../entries/YB-037-background-workflow-management.md), the
> file→sqlite migration is [YB-049](../entries/YB-049-workspace-migration-to-sqlite.md),
> and artifact retention is [YB-050](../entries/YB-050-artifact-retention-and-quota.md).

### Implemented — 2026-09-27

Phases 1, 1b, 2 and 3a of the design, as one increment. What landed:

- **Producer attached (phase 1).** `/ingest` and `/design/draft` mint the run id
  before the model call and pass a `JournalProgress` inside `input_data`; the run
  record takes the same id (`metadata["run_id"]`), and the terminal verdict is
  published *after* the record is persisted. `JournalProgress` gained `close()` and
  now never raises on a journal failure — a caller that drives it directly gets the
  same "a dead journal degrades progress only" guarantee `emit_progress` gives the
  pipeline. `_default_journal()` falls back to `NullJournal` with a warning when an
  endpoint is configured without its client, so the app still boots.
- **Cursor (phase 1b).** `RunEvent.stream_id` carries the journal's id for the
  event; `ValkeyJournal.read` stamps it; `/api/runs/<id>/events` returns `cursor`.
  Without it, replay degraded to "start over" or "miss everything in between".
- **Requirements profile emits pass events.** It does not run `PassSpec`s, so it
  reports `pass.started`/`pass.finished` per chunk through the new public
  `emit_progress`/`outcome_state` — the same decider the architecture profile uses,
  so a watcher and the stored `PassRecord` cannot disagree.
- **Job record + worker (phase 2).** `core/jobs.py` (a `JobStore` protocol with an
  atomic `claim`, and a SQLite backend in WAL with `BEGIN IMMEDIATE`),
  `core/artifacts.py` (content-addressed raw bytes — the first place documents
  persist), `app/runner.py` (the one execution path, Flask-free, with the re-read
  and version-guarded apply) and `app/worker.py` (`sea-worker`, one job at a time,
  heartbeat, `--once`).
- **Polling view (phase 3a).** `GET /runs/<job_id>` and
  `/runs/<job_id>/events.html`, an HTMX fragment that carries its own polling
  attributes and therefore stops when the run is terminal.
- **Server-sent progress (phase 3b).** `GET /api/runs/<run_id>/stream` pushes the
  *same rendered fragment* over `text/event-stream`, so the pushed view and the
  polled one cannot drift. `id:` is the stream id, which is what `Last-Event-ID`
  sends back on a reconnect; the stream closes on the terminal event, heartbeats
  every 15 s of silence, and is bounded by `SSE_MAX_CONNECTIONS` (8) and
  `SSE_MAX_SECONDS` (30 min) so a few stale tabs cannot become a load test.
  `RunJournal.wait()` (`XREAD BLOCK`) makes it push rather than poll.
  `?once=1` yields a single frame, which is what makes it testable. The page
  uses push when the journal retains events and polling otherwise, decided by
  the server from `RunJournal.retains`.

**The switch:** the substrate is configured, like the journal. A scope on the
`sqlite` backend gets a job store and runs asynchronously; a **file-backed** scope
gets none and its routes run the work in the request exactly as before, because the
worker's guarded apply needs a store that can refuse a stale version. One runner
serves both, so the synchronous path is a fallback rather than a second mechanism.
This deployment now declares `data/sea-deepseek/workspace.yaml` with the existing
file scope kept at `path: .` plus an `async` sqlite scope; `uv run sea-worker`
consumes its queue. The concrete manifest is in the design's §9.

**Two things found while switching it on.** (1) `ingest.html` gained a *Background
runs* panel and a note saying whether the scope is asynchronous and, if not, why —
otherwise a run is reachable only through the redirect that created it, and an inert
async path reads as a bug. (2) `create_app` resolved `STORE_ROOT` before any agent
was constructed, and it is the *agents* that call `load_dotenv()`, so `SEA_DATA_DIR`
in `.env` never applied to the web app: it silently used `data/sea` and a declared
workspace was invisible. `app.load_env()` now runs at import, without overriding
real environment variables.

**Reviewed, and fixed.** An adversarial code review found defects the fake-extractor
tests could not: the job store's SQLite connection was thread-bound, so the worker's
heartbeat thread raised and the run page reported healthy runs as stale; the polling
fragment and the events API both treated "terminal" as a property of the events
slice, so with the default `NullJournal` nothing ever stopped; a completed design run
showed no verdict because its record lives with the draft; the events API had no
scope guard and 200'd an unknown run id; the artifact digest addressed the *decoded
text* rather than the bytes; and a locked database could kill the worker loop. All
are fixed with tests (`tests/test_jobs.py`, `tests/test_async_runs.py`), including a
concurrent claim under four threads.

**Every phase is built.** What used to be listed here as "still open" now belongs to
other items, which is why they were split out rather than left as a tail on a
finished one:

- the **source fetch** (phase 4) — [YB-033](../entries/YB-033-event-ingress.md), whose
  adapters and credentials it always was;
- **artifact retention and quota** — [YB-050](../entries/YB-050-artifact-retention-and-quota.md);
- the **file→sqlite migration** that lets the existing graph run in the background —
  [YB-049](../entries/YB-049-workspace-migration-to-sqlite.md);
- **retry, recovery, cancellation, idempotency and the jobs page** —
  [YB-037](../entries/YB-037-background-workflow-management.md), which now inherits a
  working substrate instead of building one.

**The last two acceptance checks are now demonstrated** (2026-09-27), which is what
closed this item:

1. **A concurrent human edit is never silently overwritten.** Two tests, because there
   are two cases and they behave differently — and correctly:
   - an edit made *while the model runs* is **folded in**: the worker re-reads the
     working set immediately before merging, so the reviewer's change survives and the
     run's facts are added beside it
     (`test_a_review_edit_during_a_run_survives_the_apply`);
   - a commit landing in the narrow **read→write window** raises `StoreConflict`: the
     job is `FAILED` with that reason, **nothing is merged**, and the competing write
     is intact (`test_a_write_landing_between_read_and_apply_fails_the_job`).
2. **Two submits produce one running job and one queued job**, never two concurrent
   model runs — asserted at the route level with a blocking extractor:
   `test_two_submits_produce_one_running_job_and_one_queued`.

Tests: `tests/test_run_progress_routes.py`, `tests/test_app_run_events.py`,
`tests/test_run_journal.py`, `tests/test_jobs.py`, `tests/test_async_runs.py`,
`tests/test_sse.py`.

### Design — 2026-09-27

The design moves this item from "build a stream" to "attach the stream that was
built". [`core/events.py`](../../../core/events.py) (envelope, vocabulary,
`ValkeyJournal`), `agents/extraction/progress.py` (`JournalProgress`) and
`GET /api/runs/<run_id>/events` all landed after this entry was written, so the
phases below are corrected:

- **Phase 1 (now first):** the app attaches a `JournalProgress` and mints the run id
  — **no route does either today**, so a UI run emits nothing even when Valkey is
  configured. The requirements profile needs to emit pass **events** through the
  same channel; it already emits `PassRecord`s into `metadata["passes"]`, so run
  completeness is unaffected (ADR-0013, YB-023 — both closed).
- **Phase 1b:** `RunJournal.read()` discards the stream id, so no subscriber can
  advance its cursor. Replay and `Last-Event-ID` need that protocol change first.
- **Phase 2 (in scope, ships with phases 1–3a as one increment):** job record +
  single-slot worker, so the work leaves the request. Requires a `concurrency_safe`
  scope and an apply step that re-merges under the version guard. Owner decision
  2026-09-27: the substrate is introduced with the async work rather than gated on
  the latency measurement; the measurement now sizes the worker.
- **Phase 3:** polling over an HTML partial first (works with no Valkey at all),
  then SSE as the recommended steady state when a journal is configured. The HTMX
  SSE extension is **not** recommended.
- **Not every run starts from a document.** A job declares its input kind:
  `inline` (UI bytes), `source` (an event's reference — the worker fetches it from
  Confluence/SharePoint/API over MCP), or `graph` (a design run; no document at
  all). The fetch is a **run step**, not a receiver step: fetching inside the
  webhook would hold the request open for a slow, credentialed call — the
  anti-pattern this item exists to remove. MCP is therefore **required** for the
  event path, invoked by deterministic worker code, never by the extraction model.
- **MCP is recommended against for status writes** (progress is emitted by the
  harness, not chosen by the model).

The remaining questions (the design's §13): the reading of "two paths", the job
backend, which source system is first, and what to do when a source has moved on
from the version an event named. The rest were decided in the design — including
the sequencing, now that the workflow substrate ships with the async work.

**Legacy status:** Not started
**Legacy priority:** High (the current experience is a multi-minute blank page)
**Legacy area:** `app/__init__.py` (ingest route), `app/templates/ingest.html`, `agents/extraction/passes.py`, `agents/knowledge_extraction/agent.py`

---

### The problem, measured

`/ingest` calls the extractor **synchronously inside the request**:

```python
result = agent.run({...})      # app/__init__.py:261
...
return redirect(url_for("review"))
```

For the whole duration of that call the browser sits on a served page with no progress,
and — because Flask is single-process here — the user cannot so much as load another page
from the app. Measured on the local model, for the **same** document:

| run | elapsed |
|---|---|
| `payment_platform_brief.md`, after the config fix | **62 s** |
| the same brief, before the fix | 472 s, 624 s |
| `sample_requirements.md` (1.9 KB), one run | **> 25 min**, never returned |

So the stall is not a fixed cost to be tolerated: it varies by **an order of magnitude**
and has already exceeded any reasonable request timeout. A synchronous POST is the wrong
transport for a job of unknown and unbounded duration.

### What already exists to build on

The extraction pipeline is **already pass-structured**, so the progress information
exists and is thrown away:

- `ARCHITECTURE_PASSES` runs one `PassSpec` per chunk via `run_passes`, emitting a
  `PassRecord` (pass name, chunk, outcome, path, elapsed, triples produced) — the data a
  progress bar needs is literally already produced.
- `PassRunSummary` aggregates calls, successes, empties, failures, text-fallbacks and
  elapsed, and `describe()` renders it as one line.
- The requirements profile is a single call and reports no pass records at all — which is
  the same gap as YB-023. Fixing 23 and streaming 26 are the same plumbing.

So this is mostly a **transport** change, not a pipeline change.

### Shape

Phases, cheapest first:

1. **Report progress within the existing turn** — the agent already logs per-pass; expose
   the same records as they complete rather than only in the final payload.
2. **Background the job, poll for status.** A managed job id, a status endpoint returning
   `PassRecord`s so far, and an HTMX poll on the ingest page. Removes the HTTP timeout
   risk and gives a visible progress list.

   HTMX is already loaded (`base.html`) but is used in **exactly one place** —
   `partials/review_row.html`, for inline verify/dispute/reopen swaps. There are no
   `hx-*` attributes on `/ingest`, and no SSE extension. So polling is genuinely new
   work, but the mechanism is present and proven at this scale.
3. **Stream** — SSE or chunked HTMX, pushing each pass completion to the view. Nicest, and
   only worth it once (2) exists. Would need the HTMX SSE extension, which is not
   currently bundled.

Note that (2) also fixes a correctness problem, not just a UX one: a synchronous call that
exceeds a proxy or client timeout leaves the **extraction result discarded** — the work
was done and the graph is never updated. Backgrounding makes the result durable
regardless of how long it took.

### Constraints

- **Concurrency against a single-slot local model.** A second concurrent ingest would
  contend for one inference server; the status endpoint must not queue more work than the
  backend can serve. A single-worker queue with explicit "queued" state is safer than
  parallel submits.
- **Partial output must stay honest.** Per-pass progress reveals an incomplete run as it
  happens; it must not make a `PARTIAL` or `UNKNOWN` run *look* finished because the last
  pass completed. YB-023's distinction is what keeps this truthful.
- **Nothing here changes completeness semantics.** Streaming is transport; the run's
  `completeness` must still be computed from pass outcomes, not inferred from the fact
  that the stream ended.

### Added 2026-09-25 — a second consumer changes the seam, not the mechanism

The three phases above are written for one consumer: the page that started the run.
Event-driven work adds a second — a run nobody's browser is attached to
([`docs/design/event-driven-integration.md`](../../design/event-driven-integration.md)) —
and if phase 2 is built as an HTMX poll served by the ingest route, that second
consumer has to reimplement the progress plumbing.

[ADR-0026](../../decisions/ADR-0026-run-journal-and-progress-transports.md) is the generalisation: a
transport-agnostic progress log with attachable subscribers, of which the ingest
page is one. **The two items should be designed together**, and only one of them
should own the mechanism. The test is simple: the extraction pipeline must not
import a web framework, and a run must be able to complete with no subscriber at
all.

That does not make phase 3 (SSE) more likely — polling may be entirely adequate for
both consumers — but it does mean the question "who is this stream for?" gets asked
before the transport is chosen rather than after.

### Baseline before wiring — 2026-09-27

`scripts/sanity_check.py` establishes what "working" means before the run is moved out of
the request, because three faults in a row produced the SAME symptom — *"ingestion fails,
no triples"* — from three different layers, and each was found by hand:

| Stage | What it proves | Why it exists |
|---|---|---|
| `config` | which endpoint, which credential **source**, and whether the sampling flags match the endpoint class | a LAN IP counted as hosted, so the paid key went to a local server and returned 401 |
| `endpoint` | auth, that the configured model is actually served, tools accepted, and **reasoning genuinely off** — one call, four answers | a DeepSeek `thinking` flag was silently ignored by a local server, so every call burned its output budget on chain-of-thought until the wall-clock cancel |
| `extract` | the real profile over the real document: passes, triples, and how close the slowest pass came to the 180s budget | the budget is per call, cancellation is checked BETWEEN turns, and a cancelled pass falls back to text — which recovers only triples |
| `route` | `POST /ingest` completes and the graph gained facts | the save happens last, so a request that dies loses everything |
| `journal` | the run-event endpoint answers — **empty is expected today** | distinguishes "not wired yet" from "broken", which is the difference this item has to make |

Two measured facts that shape the work:

- **A live run is minutes, not seconds.** `payment_platform_arch.md` is 3 chunks × 4 passes
  = **12 model calls**, and nothing is written until the last one. There is no run-level
  cancel or budget anywhere, so *the absence of a "wall-clock budget — cancelled" line
  means the client abandoned the request*, not the agent.
- **Reasoning tokens were the hidden cost.** Disabling them on the local server halved
  most passes (16.8→8.9s, 13.4→6.0s, 13.4→5.7s) and raised triples from 22 to 27 on the
  same document.

```sh
.venv/bin/python scripts/sanity_check.py                  # config + endpoint, seconds
.venv/bin/python scripts/sanity_check.py --stages all     # everything, minutes
```

### Related

- **YB-023** — same missing pass metadata, seen from the reporting side. Fix together:
  one change makes the requirements agent emit pass records, which YB-026 then streams.
- **YB-020** — runtime variance is the reason the duration is unbounded. Streaming does
  not make it faster; it makes the wait legible and the result durable.
- **YB-024 / 25** — a progress view is the natural first consumer of a view shell that
  can update in place.
