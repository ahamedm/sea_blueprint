# Asynchronous run progress — two paths, one run, and the journal that already exists

> **Design document** for [`YB-026`](../todos/entries/YB-026-asynchronous-progress.md).
> It is the long analysis for that entry and the agreed boundary with
> [`ADR-0026`](../decisions/ADR-0026-run-journal-and-progress-transports.md) (the progress log),
> [`YB-037`](../todos/entries/YB-037-background-workflow-management.md) (durable
> execution) and [`YB-033`](../todos/entries/YB-033-event-ingress.md) (the
> unattended trigger).
> Status is tracked in those entries; this document is the plan they share.
> **Reviewed 2026-09-27** — see §15 for the findings and what changed.

---

## 1. The outline this starts from

The proposal, as given:

> Two paths — (1) background-service-call based, (2) UI based. A run/job is
> initiated; agents are invoked with appropriate parameters. MCPs might help load
> the input documents eventually. Agents update the job status to Valkey (again,
> MCP might be used) at appropriate intervals or passes. The UI gets that status
> delivered via SSE / HTMX SSE by the Flask app.

**Interpretation.** "Two paths" is read here as *two initiators* of the same kind
of work: path 1 is a run started by something that is not a browser request (a
webhook, a schedule, an operator CLI), path 2 is a run started by the web UI. The
second reading — two *transports*, service→Valkey and Flask→browser — is not a
rival reading; it is the two halves of one pipeline, and §4 keeps them as one.

Every clause is checked against the code, because the journal the outline
describes as a possibility was built before this document was written:

| Clause of the outline | Status against the code |
|---|---|
| Agents update job status to Valkey at pass boundaries | **Built.** `core/events.py` defines a versioned `RunEvent` envelope, a closed event vocabulary, the `RunJournal` protocol and `ValkeyJournal` (Streams, `MAXLEN ~`, TTL, exclusive `since` cursor). `agents/extraction/progress.py` (`JournalProgress`) stamps run id/scope/seq and appends; `run_passes` emits per pass and catches sink failures. |
| Agents invoked with appropriate parameters | **Two profiles wired, one not.** `architecture_extraction` and `design_assistant` accept `input_data["progress"]` (`agents/architecture_extraction/agent.py:158`, `agents/design_assistant/agent.py:144`). The requirements profile does not. And **no app route passes a sink at all**, so a UI run emits nothing today. |
| UI gets status from Flask | **Half-built.** `GET /api/runs/<run_id>/events?since=` exists and returns the envelope as JSON, degrading to 503 when the journal is broken. No HTML view consumes it, and `read()` does not return the cursor a subscriber needs (§3). |
| A run/job is initiated | **Missing.** There is no job record, no queue, no worker, no `QUEUED` state. `/ingest` and `/design/draft` still call `agent.run(...)` inline. |
| MCPs load the input documents | **Required for path A, not "eventually".** Not every run starts from a document: a service-initiated run carries a **reference** (Confluence page, SharePoint item, API URL, S3 version) and the job's acquisition step pulls the bytes over MCP; a design run has no document at all. See §6 and §8.2. |
| MCP used to update Valkey | **Recommended against.** See §6. |
| SSE / HTMX SSE delivery | **Missing.** `htmx.min.js` is bundled locally; the SSE extension is not, and no `text/event-stream` route exists. See §7. |

The single most important correction to the outline: **the stream is not the work
that remains.** It exists and is tested (`tests/test_run_journal.py`,
`tests/test_run_progress.py`, `tests/test_run_progress_wiring.py`,
`tests/test_app_run_events.py`). What remains is attaching the producer, moving
the work out of the request, and rendering it.

## 2. What already exists, precisely (so it is not rebuilt)

| Piece | Where | What it guarantees |
|---|---|---|
| Event envelope | `core/events.py` | `event_version`, `run_id`, `scope_id`, `seq`, `kind`, `at`, `payload`; `from_dict` tolerates unknown/missing fields, so an older producer cannot break a newer reader |
| Vocabulary | `core/events.py` | Closed set: `run.started`, `pass.started`, `pass.finished`, `tool.call`, `pass.retry`, `pass.error`, `run.finished`, `run.failed` |
| Journal | `core/events.py` | `RunJournal` protocol (`append`/`read`/`close`), `NullJournal` for no-Valkey installs, `ValkeyJournal` on a Stream per run with replay and TTL (24 h, `MAXLEN ~ 10 000`) |
| Producer sink | `agents/extraction/progress.py` | `JournalProgress` owns identity and monotonic sequencing; `finished()` **requires** a completeness verdict positionally; the payload key set is closed (`PROGRESS_PAYLOAD_KEYS`), and `error` is a truncated string — bounded, not content-free (§10) |
| Pipeline emission | `agents/extraction/passes.py` | `_emit_progress` around each pass; a sink exception is caught and logged, never fails a pass |
| Run-id correlation | `core/knowledge/ingest.py` | `metadata["run_id"]` overrides `_run_id`, so the live stream and the stored `ExtractionRun` share one id |
| Read side | `app/__init__.py` | `GET /api/runs/<run_id>/events?since=`; `NullJournal` when `SEA_VALKEY_HOST` is unset; broken journal → 503, not 500 |
| Fallback | `app/__init__.py:_default_journal` | An MVP install with no Valkey behaves exactly as before |

Two constraints from that work carry forward and are not re-litigated here: **a
Stream, not Pub/Sub** (replay for late and reconnecting subscribers, at the cost
of retention), and **the verdict is persisted before it is published** (the stream
is a notification that state changed, never the state itself).

## 3. The gap

| Missing | Consequence today |
|---|---|
| The app attaches no sink and mints no run id | A UI run produces zero journal events even when Valkey is configured; the endpoint answers `{events: []}` for every run |
| `RunJournal.read()` discards the stream id | Neither an SSE frame nor a polling client can learn the cursor, so replay and `Last-Event-ID` cannot work at all against the current contract. This is a **protocol change**, scheduled as phase 1b |
| The requirements profile emits no pass **events** | `agents/knowledge_extraction/agent.py` chunks and calls the model itself; it does not use `run_passes`. (It *does* emit `PassRecord`s into `metadata["passes"]` — `:465-471, :617` — so run **completeness is already reported**; ADR-0013 closed that, and YB-023 is `done`. What is missing is stream visibility, nothing else.) |
| No job record | Nothing durable exists before the model call; a run in flight has no identity outside the stream, no attempt, no state |
| No worker / queue | The work still dies with the request; a client or proxy timeout discards a completed extraction |
| No HTML consumer | `/ingest` has no `hx-*` attributes and no run page; the JSON endpoint has no reader |
| Document bytes are request-scoped | A worker in another process has nothing to read unless the upload is materialised first |
| A configured-but-unusable journal fails loudly at boot | `_default_journal()` calls `ValkeyJournal(...)` uncaught (`app/__init__.py:157-169`), and the client library is an optional extra. Setting `SEA_VALKEY_HOST` without `redis`/`valkey` installed raises `ImportError` from `create_app` — the app does not start. That contradicts the module's own "losing the journal degrades live progress only" contract |

## 4. Two paths, one spine

The risk in "two paths" is building two mechanisms, which is exactly what
[`event-driven-integration.md`](event-driven-integration.md) §Why streaming and
workflow management are two items exists to prevent. The design is one spine with
two fronts:

```
                ┌─ path A: service-initiated ─┐
 webhook / schedule / CLI                    │
                └─ path B: UI-initiated ──────┘
                          │
                   (a) DECLARE the input — the job says WHERE; nothing is fetched here
                       B: bytes already in the request
                       A: a source reference (Confluence page, SharePoint item,
                          API URL, S3 version) — never the document body
                       design: a graph snapshot, no document at all
                          │
                   (b) mint job_id + run_id, write the job record BEFORE any model call
                          │
                   (c) enqueue  (single slot, visible depth)
                          │
               ┌──────────┴───────────┐
               │  one worker process  │   claims QUEUED → RUNNING
               └──────────┬───────────┘
                          │
                   (d) ACQUIRE the input — the ONLY place a fetch happens (§8.2)
                       B: dereference the stored upload
                       A: fetch from the named source via MCP
                       design: read the graph snapshot
                       → hash the RAW BYTES, persist the artifact, record provenance
                          │
                          │  agent.run({**input_data, "progress": JournalProgress(journal, run_id, scope_id)})
                          ▼
                   run journal  (Valkey Stream, built)
                          │
        ┌─────────────────┴──────────────────┐
        │ subscribers — zero, one, or many   │
   path B browser               path A ops view / nobody
        │                                    │
        └──────────► terminal verdict ◄──────┘
             B: redirect + flash / run page
             A: job page, alert
```

The acquisition boundary is the correction that matters most here: **the trigger
hands over a reference, not a fetch.** Fetching a Confluence page or a SharePoint
item is slow, credentialed and rate-limited, and doing it inside the webhook
request would rebuild the exact anti-pattern this design removes — a request held
open by work of unknown duration. The receiver (YB-033) verifies, records, maps,
enqueues and returns `2xx`; the worker acquires.

Note the other seam: the sink travels **inside `input_data`**
(`input_data["progress"]`), not as a `run()` keyword argument —
`run(self, input_data)` is the profile interface, and phase 1 must not widen it.

What genuinely differs between the paths, exhaustively:

1. **Who mints the job**, and whether its declared input is bytes (B), a source
   reference (A), or a graph snapshot (design).
2. **What the initiator gets back**: B holds an HTTP response and is redirected to
   the run page; A returns `2xx` immediately to the sender (YB-033) and has no
   response to attach anything to.
3. **Who subscribes**: B almost always, A only if someone opens the run page.
4. **What happens at the terminal event**: B re-renders the run page; A may
   notify. Write-back is explicitly out of scope
   ([`event-driven-integration.md`](event-driven-integration.md) §Explicit non-goals).

Everything else — job record, identity, journal, vocabulary, worker, verdict — is
shared. A test that the design is right: **removing the browser from the system
changes nothing about how the work executes.**

## 5. Boundaries with the neighbouring items

| Item | Owns | YB-026 must not |
|---|---|---|
| **ADR-0026** (progress log) | The envelope, vocabulary, `RunJournal` implementations, `JournalProgress`. **Largely landed.** Still owns the `read()` cursor change (§7). | Define a second event format, or a "progress" object distinct from the journal |
| **YB-026** (this) | Attaching the producer on the app path; the minimal job record and single-slot worker needed to get work out of the request; the browser transport and run page | Own retry, backoff, idempotency, cancellation, dead-lettering, or a recovery policy — those are YB-037 |
| **YB-037** (durable execution) | The state machine's semantics, retry/backoff, idempotency, cancellation, the jobs page, recovery policy | Disagree with its vocabulary, or invent a state it does not have |
| **YB-033** (trigger) | The receiver, the event→operation mapping, and the **source adapters and their credentials**. It enqueues a *reference*; it does not fetch (§8.2). | Fetch inside the webhook request, or duplicate the trigger: path A enqueues through the same call YB-033 will make |
| **YB-035** (agent) | The Design Assistant profile, which **exists** (YB-035 is `done`, `agents/design_assistant/agent.py`) | Re-specify it; a design run has no document, which §8.1 makes an input kind rather than a special case |
| **YB-043** (system of record) | Where durable state lives, including the job backend | Pick the backend here; see §8 |
| **ADR-0013 / YB-023** (completeness) | Pass **records** and the run verdict. **Closed.** | Re-open it; phase 1 adds pass **events** to the existing records |

**Decision:** YB-026 introduces a minimal `JobStore` **protocol** — the same move
ADR-0026 made with `RunJournal` and that `Store` made for the store contract — and
writes only the states it needs. Everything the protocol does not force (the
backend choice, recovery, retry, cancellation) stays with YB-037/YB-043.

- **States.** YB-026 writes `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED` — a subset
  of YB-037's published vocabulary (`QUEUED → RUNNING → SUCCEEDED | FAILED →
  (retry) → DEAD_LETTER | CANCELLED | AWAITING_REVIEW`). It does **not** invent
  `INTERRUPTED`. An orphaned `RUNNING` job whose worker heartbeat has gone stale is
  **surfaced as stale** on the page — observability, which is this item's remit.
  Whether it is re-queued, failed, or dead-lettered is YB-037's recovery rule, and
  YB-037's acceptance ("never a permanently running ghost, and never a lost result
  that was actually produced") is recorded here as a dependency, not implemented.
- **Claim.** The protocol requires `claim()` to be an atomic compare-and-set.
  **A scope whose backend is not `concurrency_safe` cannot run a worker**: the
  enqueue path refuses with a visible message rather than degrading to a
  duplicate-worker race. `RevisionStore` documents itself as single-writer with no
  lock and no version check (`core/knowledge/store.py:131-143`), so it cannot back
  a claim, and `save_working` refuses `expected_version` outright (`:195-201`).
- **Backend.** Which concrete backend satisfies the protocol is a **YB-043
  coordination point** (today's SQLite store already declares itself
  concurrency-safe; the file backend does not). §13 lists it as such rather than
  deciding it here.

## 6. MCP: where it belongs, and where it does not

The deployment document states the rule
([`deployment-architecture.md`](deployment-architecture.md) §3.3): *if it is
deterministic and lives in `core/`, it is a library import; if it lives over the
network or in another system, it is MCP.* That rule has two clauses and this
design needs a third category, because the journal is neither:

| Kind | Example | How it is reached |
|---|---|---|
| Deterministic logic in-process | `core/knowledge/*`, `core/events.py` | library import |
| Another system the agent reaches *on its own initiative* | Jira, DOORS, S3, the technology radar | MCP |
| **Provisioned infrastructure the worker is configured with** | **the Valkey journal, the job store** | **a client behind an existing protocol** |

**Input documents — MCP is the mechanism for path A, wrong for path B, and wrong
as a *model-chosen tool* inside the pipeline.**

- **Path B (UI).** The bytes are already in the request. Routing them through an
  MCP server adds a network hop, a process to secure, and a failure mode to data
  in hand. No.
- **Path A (service).** An event carries an *identity* (Confluence page id,
  SharePoint drive item, issue key, version), not a document — and often not even
  a choice of source. Fetching the text from Confluence, SharePoint, an API or S3
  is a network call to another system, which is the textbook MCP case. **It is
  mandatory here, not optional**: without a fetch there is no document to extract
  from at all.
- **Who invokes the fetch, and when.** The **worker's acquisition step**, before any
  extraction pass runs (§8.2). Not the receiver (that would hold a webhook open for
  a slow, rate-limited fetch), and not the extraction model — the profiles hold no
  source credentials, and a model-chosen fetch would bypass the raw-byte hash and
  the run's record of which bytes it read, so a run could honestly report a
  different document than the event named.
- **The pipeline still never fetches its input.** `input_data["document"]` remains
  the contract for both paths. Acquisition materialises it first; that is what
  keeps `agents/` free of credentials and source-specific dependencies.

This is the same harness-versus-model distinction the journal concession below
turns on: an MCP tool invoked by deterministic worker code is a *transport*; an
MCP tool the model decides to call is a different thing with a different contract.

**Status to Valkey — no MCP, and the reasoning is the point of this section.**

1. Progress is emitted by the **harness at a transition**, not chosen by the
   model. MCP is a protocol for *model-invoked tools*; wrapping a non-negotiable
   side effect in it invites the model to be the thing that decides whether
   progress is reported.
2. It would make every pass pay a tool-call round trip and a new failure surface,
   for an append that is deliberately best-effort. `_emit_progress` currently
   catches a sink exception so progress can never change a run; an MCP boundary
   makes that guarantee something to re-establish rather than a property of the
   call.
3. The journal is a **provisioned infrastructure dependency** (the third row
   above): the worker is configured with its endpoint, exactly as it is configured
   with the model endpoint. An MCP server for it is another process to
   authenticate, version and least-privilege, carrying writes that contain no
   extracted content.
4. `core/events.py` already owns the envelope, and `ValkeyJournal` already speaks
   RESP. A library import is the cheapest correct thing for deterministic code
   that lives in `core/`.

**The honest concession.** If the platform standardises *all* agent egress on an
MCP gateway as a security policy, then a thin journal MCP server is a defensible
*transport* for the worker→Valkey hop. Even then it sits **behind the existing
`RunJournal` protocol** — a `McpJournal` alongside `ValkeyJournal` — so no agent
code changes and the failure contract is unchanged. `McpJournal` would be
**harness code the worker calls**, never a tool the model decides to invoke; that
distinction is what keeps the first four arguments intact.

**If a hosted runtime cannot reach Valkey** (a plausible AgentCore constraint),
the same seam absorbs it: a `RunJournal` implementation that batches over HTTP or
a queue. MCP is one such transport; it is not the design.

## 7. The browser transport

**Prerequisite, before either transport (phase 1b).** `RunJournal.read()` returns
`List[RunEvent]` and drops the stream id (`core/events.py:400-408`), `RunEvent`
does not carry it, and the JSON endpoint returns only `event.to_dict()`. So today
**no subscriber can learn its cursor**: an SSE frame cannot set `id:`, and a
polling client cannot advance `since`. "Replay comes free" is false against the
current contract. The change is small but real — return `(stream_id, event)` pairs
from `read()` (or carry `stream_id` on the envelope) — and it belongs to ADR-0026,
which owns the protocol. Both transports below depend on it, which is why it is
scheduled before either.

Two transports, judged against what is in the repo:

| Option | Cost | What it gives | Verdict |
|---|---|---|---|
| **HTMX polling** `hx-get … hx-trigger="every 2s"` | None new — `htmx.min.js` is bundled (`base.html:8`) | Works in **every** configuration, including `NullJournal`; needs an **HTML** partial endpoint | **Built (3a); the fallback** |
| **SSE (`text/event-stream`) + vanilla `EventSource`** | One route + ~30 lines of JS; one held connection per viewer; heartbeats; reconnect loop | Server push, no timer; `Last-Event-ID` automatic once the cursor exists | **Built (3b); the transport when a journal retains events** |
| **HTMX SSE extension** | A second vendored asset for the same thing | Least JS | **No** — more moving parts than the small script, and it does not remove the polling path that every non-Valkey install needs |

**Sequencing, as it happened.** Polling shipped first because the cursor fix is
needed by both, it degrades correctly on the deployment that existed at the time
(`SEA_VALKEY_HOST` unset, so there was literally nothing to stream), and it kept the
first increment free of held connections. SSE followed once a journal was actually
configured — the trigger this document set ("configured, and one of: more than one
viewer, measurable timer load, sub-second latency"). That satisfies ADR-0026 decision 4
with a date rather than a preference.

**Which transport a page uses is decided by the server**, from one property: does
the journal retain events (`RunJournal.retains`)? If yes, the page opens an
`EventSource` and the polling attributes are absent. If no — the default install —
the page polls, because the fragment reads job *state* from the store and works
without a stream. Nothing has to guess, and both transports render the same
template, so they cannot drift.

**Endpoints.**

- `GET /runs/<job_id>` — the run page; renders job state and the backlog.
- `GET /runs/<job_id>/events.html` — the polling wrapper, wrapping the same
  `partials/run_events.html` the stream pushes.
- `GET /api/runs/<run_id>/events?since=` — the JSON cursor source.
- `GET /api/runs/<run_id>/stream` — **built**: `text/event-stream`, `id:` = stream
  id, `data:` = the rendered fragment. `?once=1` yields a single frame and closes,
  which is also what makes it testable.

**Implementation constraints, as built.** A blocking
`wait(run_id, since, timeout_ms)` on the journal (`XREAD BLOCK`) means the server
sleeps rather than the client polling; `NullJournal.wait` returns `[]` immediately
instead of pretending to wait. A host whose only evidence of a "missing" frame is a
timer is exactly the kind of thing that becomes a load test, so the stream also:

- **closes on the terminal event**, and the client closes too — `EventSource`
  retries by itself, so an endless stream is a background load test;
- **bounds itself**: `SSE_MAX_CONNECTIONS = 8` held streams per process (503 beyond
  that) and `SSE_MAX_SECONDS = 30 min` per stream, after which it closes with a
  comment and the browser resumes from its cursor;
- **sends a heartbeat comment** every 15 s of silence, so an idle proxy does not
  sever a connection, and pushes a frame **only when something changed** — a run can
  be silent for minutes inside one model call, and re-sending an identical fragment
  every 15 s would be noise;
- **maps `Last-Event-ID` to the cursor**, so a reconnect resumes rather than
  replaying, which is the property that made phase 1b a prerequisite.

Verified against the running Valkey, not only against a fake: an event appended
mid-stream is pushed without waiting for a poll, and the generator ends on the
verdict (`tests/test_sse.py::test_a_live_stream_pushes_on_the_event_and_closes_on_the_verdict`).

**WSGI.** Flask's dev server is threaded by default (verified: `flask/app.py`
sets `threaded=True`), so the MVP is fine, and `run.py` runs debug on by default
(`SEA_DEBUG`), which also means the reloader — a reason the worker is a separate
process (§9). A production `gunicorn -k sync` pool would exhaust itself on held
connections; the recommendation when that day comes is `gthread` or gevent
([`deployment-architecture.md`](deployment-architecture.md) §3.4 already names an
ASGI/gevent layer) with a connection cap. This is a recommendation, not an
existing config — there is no gunicorn config in the repo.

**No-Valkey installs.** `_default_journal()` returns `NullJournal`, which retains
nothing. The page then reads **job state from the store** and polls the partial.
Live pass-level progress is the one thing a Valkey-less install does not get, and
it degrades to "queued / running / done", not to a blank page. Phase 1 also fixes
the boot fragility in §3: a configured endpoint whose client is missing must fall
back to `NullJournal` with a recorded warning, not stop the app.

## 8. The job record, and where its input comes from

Minimal, with ownership stated so YB-037/YB-043 can extend it without a migration:

| Field | Owner | Why |
|---|---|---|
| `job_id` | 026 | The durable identity of the attempt, independent of the run |
| `run_id` | 026 | Minted with the job and passed to `graph_from_extraction(metadata={"run_id": …})` so stream and record share an id |
| `scope_id` | 026 | Which workspace/product; the worker resolves the store from **this**, never from request state |
| `kind` | 026 | `ingest` \| `design` — decides which parameters and inputs are required |
| `state` | 026 writes 4 | `QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`; the rest of YB-037's vocabulary is reserved |
| `parameters` | 026 | doc type, initiative id, domain pack, revision label; for `kind=design`, the snapshot/`base_ref` instead of a document |
| `input_kind`, `input` | 026 | `inline` \| `source` \| `graph`, plus the descriptor for it — an artifact hash, a source reference (`{system, item_id, version}`), or a snapshot/`base_ref`. A document is **not** universal (§8.1) |
| `queue_seq` | 026 | Monotonic enqueue order — "position" is uncomputable from `created_at` alone |
| `worker_id`, `worker_heartbeat_at` | 026 | How the page shows a stale `RUNNING` job or a queue with no consumer |
| `actor`, `created_at`, `started_at`, `finished_at` | 026 | Attribution and the timeline |
| `trigger`, `trigger_event_id` | 026 | Where the job came from — `ui` \| `event` \| `schedule` \| `cli` — plus the event identity that makes it deduplicable and auditable. Independent of `input_kind`: a nightly audit is `schedule` + `graph`, a CLI ingest is `cli` + `inline` |
| `attempt`, `error`, `dedupe_key` | **reserved — YB-037** | Columns exist so the semantics land without a migration; 026 does not define them |

**Where it lives.** Behind a `JobStore` protocol in `core/` (the layer both the
app and the worker may import), with `claim()` atomic (§5). The concrete backend
is a YB-043 coordination point.

### 8.1 Three kinds of input — a document is not always what starts a run

A job **declares** its input; it does not assume one. Three kinds, and the
difference is load-bearing because only one of them exists inside the request:

| `input_kind` | Supplied by | Acquired by | Example |
|---|---|---|---|
| `inline` | the UI request (upload or paste) | stored as an artifact at enqueue; the worker dereferences it | the `/ingest` form |
| `source` | an event (YB-033), as a **reference** | the worker fetches it via MCP at run time (§8.2) | a requirement published in Confluence |
| `graph` | the graph itself | the worker loads the snapshot/`base_ref` | the Design Assistant over a verified REQ-G |

Two consequences follow, and the first draft got both wrong by treating a document
as universal:

- **`source` cannot be materialised at enqueue time** without doing the fetch in
  the receiver — the anti-pattern this design exists to remove. The job carries the
  reference; the fetch is a run step.
- **`graph` has no document at all**, which is why `_design_inputs` /
  `_design_preconditions` (`app/__init__.py:588-599`) exist rather than a
  `document` form field. §4's acquisition step is therefore a dispatch on
  `input_kind`, not one code path with a special case bolted on.

`input_kind` is one axis; the **trigger** is another, and they are independent. The
outline names schedules and CLI runs alongside webhooks, and a nightly audit is
`schedule` + `graph` while a CLI ingest is `cli` + `inline`. Both are the same
enqueue with a different `trigger` value, which is why the job record carries
`trigger`/`trigger_event_id` rather than assuming a browser or an event.

### 8.2 The source fetch — MCP as the transport, deterministic code as the caller

Acquisition is a named step of the run, before any pass:

```
claim() → acquire(input_kind) → hash RAW BYTES → persist artifact
        → record provenance → agent.run(...) → persist verdict → publish terminal
```

- **A source-adapter protocol**, mirroring `RunJournal` and `JobStore`:
  `fetch(reference) -> FetchedDocument(bytes, media_type, source_version,
  canonical_uri, fetched_at)`. One adapter per source system — Confluence,
  SharePoint, a generic HTTP API/S3, later DOORS/Jira — with **MCP as the transport
  behind each adapter** for the external system, per
  [`deployment-architecture.md`](deployment-architecture.md) §3.3. The adapter
  interface is what lets a **fake source** stand in for a test and keeps the worker
  from knowing Confluence's payload schema.
- **The reference has a schema, and the receiver validates it.** A reference is
  `{system, item_id, version?}` plus the scope that asked for it. YB-033's receiver
  validates the **reference** — known source system, well-formed non-empty
  `item_id`, a scope that is served — and rejects a bad one with 4xx *before*
  enqueue. It does not validate existence or permission: that is the fetch. The
  split matters because a single-slot queue makes an obviously-bad reference an
  expensive failure minutes later, while existence/authorisation genuinely cannot
  be known without the network call the receiver must not make.
- **Called by the worker, never by the model.** This is a structural argument, not
  a stylistic one. Acquisition is sequenced by the worker and its product is
  `input_data["document"]`, the profile contract; the extraction profiles hold no
  source credentials and register no tools (`AgentConfig.tools` exists but the
  extractor profiles add none), so a model-chosen fetch is not merely discouraged —
  there is nothing for it to call. It would also bypass the raw-byte hash, leaving
  a run that cannot say which bytes produced the graph. (MCP as a *model* tool is
  the §6 question; ADR-0002 governs the object contract, not this boundary.)
- **Hash the raw bytes; record what was read.** The artifact hash is over the bytes
  fetched (not the decoded text, §8.3). The run records the acquisition channel and
  the source identity (below); assertions keep `SOURCE_EXTRACTION`, because
  `source_type` is the origin of the **fact**, not the route the bytes took (the
  same distinction `SOURCE_DESIGN_ASSISTANT` already draws at
  `core/knowledge/model.py:99-105`).
- **A version mismatch is recorded and flagged.** If the source returns a newer
  version than the event named — a delayed event, or a mutable "latest" — the run
  records `source_version_at_read` and a **divergent** flag, and the review page can
  say that the bytes read are not the version the event referred to. Proceeding
  silently is the provenance failure this design exists to avoid; the *policy* tier
  (freshness versus enterprise identity versus a human decision) is not decided by
  this document — see §13.
- **Where that provenance lands.** `ExtractionRun` today carries only `document_ref`
  (a filename, pinned by `tests/test_app.py`) and `document_hash`; `Provenance` is
  per-assertion and extraction-oriented (`core/knowledge/model.py:209-231`). So a
  fetched document needs a small **additive** model extension, on **both** carriers:
  the run gains `acquisition`, `source_system`, `source_item_id`, `source_version`,
  `source_uri`, `fetched_at`, `trigger_event_id` and `artifact_hash`; the assertion
  `Provenance` gains the acquisition channel plus `source_system`/`source_version`
  (its `source_type` stays `SOURCE_EXTRACTION`). The identifier part is shaped like
  the existing `ExternalReference` (`system`, `identifier`, `uri`,
  `is_authoritative`, `:441-463`) rather than a new string convention.
  `SOURCE_IMPORTED` (`:98`, defined and used by nothing) is the natural value for
  the **acquisition channel** — which is what makes it used, without overloading
  fact origin. This crosses into `core/knowledge/`, so it is named work with an
  owner (§13), not an incidental edit.
- **`document_ref` is not the URI.** It is a display filename today
  (`pasted-document.md` is pinned in a test), so the canonical URI goes in
  `source_uri`, **sanitized** — no query string, userinfo or embedded token — and
  the artifact hash in `artifact_hash`. With no auth today, the run page must treat
  source system, item id and URI as sensitive metadata rather than echoing them.
- **Idempotency is `event id + (source version | artifact hash)`.** A version alone
  is not enough: a source with no version (an unversioned SharePoint item, a
  "latest" endpoint) would collapse every event into one, and a stale event fetches
  the newest content, so the key would name a version the run did not read. The
  fetch result therefore carries an explicit **`unversioned`** marker, and the
  proposed key is `event_id + source_version` when a version exists, else
  `event_id + artifact_hash`. A post-fetch check against the last run's
  `artifact_hash` may skip re-extraction when the bytes are unchanged even though
  the version moved (the existing `meta["last_ingest"]` records the document name
  but no hash). The key's *value* is recommended here; the policy and its "run
  anyway" escape are YB-037's.
- **Failure is classified here, retried there.** Permanent: item-level 404, an
  item permission denial, an unsupported/binary format, a size-cap breach, a
  malformed reference. Transient: **401 and credential-level 403** (this design
  moves credentials into the worker, so an expired or rotated token is a config
  failure a retry can fix), 5xx, 429 and transport errors. Either way the job fails
  with the reason and **nothing is merged** — a fetch failure must never look like
  an empty document or a partial graph. The receiver already answered the sender
  `2xx`, so a config-failed job needs a **re-enqueue path** a human can trigger;
  the retry/backoff mechanics themselves are YB-037's and are not built here.
- **Format is a real constraint — and conversion is the risk, not just binaries.**
  Confluence returns storage-format HTML/ADF, SharePoint returns `.docx`/`.pdf`,
  APIs return JSON, and the pipeline's contract is text. The first adapters must
  satisfy an **admissibility contract**: the bytes they hand over are text whose
  headings survive, because `chunk_document` splits on headings
  (`agents/knowledge_extraction/agent.py:437-438`) and a mangled heading structure
  produces the same confidently-empty graph as a `.docx` fed to a text extractor.
  Confluence's own text representation is a conversion, not a free read. Binary
  conversion (`.docx`, `.pdf`) is separate work with its own fidelity problems; a
  refused format is a failed job with a clear reason.
- **Size and safety.** A fetched document bypasses `MAX_UPLOAD_BYTES` (enforced by
  Flask's `MAX_CONTENT_LENGTH` for uploads only), so the fetch needs its own size
  cap and an abort. Credentials live with the worker, not the web app, and each MCP
  server is new attack surface requiring least privilege
  (`deployment-architecture.md` §3.3). MCP is **new infrastructure**, not
  something this deployment already has: no MCP client, server or dependency
  exists in the repo, so a configured endpoint and its ownership are part of the
  work (the same third category §6 names for the journal).
- **Acquisition is sequenced durably.** `claim → fetch → hash → persist artifact →
  record provenance → agent.run`. Provenance is written before the passes begin, so
  a crash leaves a job that names the bytes it read rather than a claimed job with
  no record of what it fetched.
- **Visibility.** A slow fetch is the first thing a viewer waits on, so it should
  be visible rather than letting the run appear to hang before its first pass. That
  is a small addition to ADR-0026's closed vocabulary — a new kind, which ADR-0026 owns
  and versions — named here rather than overloading `tool.call`, whose meaning is
  "a tool was invoked inside a pass".

**Two stores, one precedence rule.** The job record is **execution state**; the
`ExtractionRun` is the **record**, and its `completeness` is the only verdict. So:

- The terminal journal event and the stored `ExtractionRun.completeness` must
  agree, and the record is authoritative if they ever do not — the terminal event
  is published *from* the record (§2).
- The no-Valkey page reads completeness from the run record via `job.run_id`; a
  job that has not finished has no verdict and must render as "running", never as
  success.
- `job.state == SUCCEEDED` means "the work ran and the result was applied", not
  "the graph is trustworthy" — a `PARTIAL` run is a successful job with an
  incomplete verdict, and the page says both.

### 8.3 The artifact store

Both the `inline` and `source` kinds end up in the same place, so the store is
described once. For `inline`, the bytes are request-scoped and the worker is
another process, so they are written at enqueue; for `source`, the acquisition
step writes them after the fetch.

- **Location and addressing.** Under the scope: `{scope}/artifacts/{hash[:2]}/{hash}`
  where the hash is over the **raw bytes**, not the decoded text. The existing
  `document_hash` is `sha1(decoded_text)[:16]` (`core/knowledge/ingest.py:60-62`)
  and stays for provenance; addressing artifacts on it would address a lossy
  transform of the bytes, which is a different object.
- **This is the first place raw documents persist.** Today the record keeps only
  `document_hash`/`document_chars`/`document_ref` (pinned by `tests/test_app.py`).
  The store is a content address over the bytes as received, with a per-artifact
  cap (the existing 4 MB `MAX_UPLOAD_BYTES` is per upload, not per scope).
  **Not yet built:** the per-scope quota and the age/count sweep are specified here
  and *not implemented* — nothing currently deletes an artifact, so an orphan from
  a failed enqueue accumulates. Sweep by **artifact age with a grace period**, not
  by job age, or a sweep can delete an artifact a live job still references. That
  is a named follow-up on [`YB-026`](../todos/entries/YB-026-asynchronous-progress.md),
  not a property of the code.
- **Dedupe is not silent.** If a `dedupe_key` is introduced (§8.2 proposes source
  version over content hash), re-submitting the same input must not become a no-op
  with no way out: it is an opt-in policy with an explicit "run anyway", and it is
  YB-037's semantics.
- **`graph` inputs do not come here.** A design run reads the snapshot; no bytes,
  no artifact, no cleanup.

**Throughput honesty.** One worker, FIFO, no run-level wall-clock budget and no
cancellation (both absent today and both YB-037's). A 4 MB document is ~599 chunks
at the 7 000-char default, so one large enqueue starves the queue for hours. The
design states this rather than implying a bound it does not have; `queue_seq` plus
the age shown on the page is what makes it visible.

## 9. The async increment — one delivery, the workflow included

**Decision (2026-09-27, owner):** the execution substrate — (b) in §13's
vocabulary, the job record and the worker — is **introduced together with the async
progress work**, not gated behind a measurement. Phases 1, 1b, 2 and 3a below are
one increment.

**Why they are one change and not two.** Progress without a worker is a stream
nobody can usefully subscribe to, for work that still blocks the request that
started it; a worker without the journal is a queue with no visibility into what it
is doing. Shipping either alone delivers half a mechanism, and — the compounding
cost — leaves every initiator built in the meantime (the YB-033 webhook, an
overnight design run, a schedule) attaching to the synchronous path and growing its
own status handling, which is the duplication ADR-0026 exists to prevent. The
substrate is small *only if* it is introduced before there is anything to migrate.

**The engine is still deferred.** (b) is a job record, one worker and a heartbeat;
(c) — a Temporal-shaped engine with retries, timers, replay and versioning — is not
built here. §13 gives the trigger; the seam (`JobStore` protocol, `RunJournal`)
is what keeps that deferral cheap.

**How the substrate is switched on** (decided during implementation, and worth
stating because it is the difference between a fallback and a migration). A worker
is only introduced for a scope whose store can guard a write, because the apply
step re-merges and must be able to refuse a stale version. So the substrate is
*configured*, exactly like the journal:

- a scope on the `sqlite` backend gets a job store; its routes enqueue and a
  `sea-worker` process runs the work;
- a **file-backed** scope gets none, and its routes keep running the work inside
  the request — byte-for-byte the old behaviour. That is what keeps every existing
  install, and the whole injected test suite, working untouched;
- both routes call **one** runner (`app/runner.py`), so the synchronous path is a
  fallback rather than a second implementation that could disagree about
  completeness, provenance or the version guard.

"Move the scope to `sqlite`" is therefore the single switch that turns background
runs on, and the same rule the design already stated ("a worker will not run
against a scope that cannot guard") is what makes it honest rather than a
limitation discovered later.

For this deployment the switch is a workspace manifest beside the data — the
existing scope preserved, one new scope for background work:

```yaml
workspace_id: sea
scopes:
  - scope_id: default          # existing data, unchanged
    backend: file
    path: .                    # keeps reading working.json/revisions/ at the root
  - scope_id: async            # empty to begin with; the worker runs this one
    backend: sqlite
    path: async
```

`data/` is gitignored, so the manifest is deployment state rather than a repository
artifact; the block above is how to recreate it. Two things to know: a scope with
`path: .` and `backend: sqlite` is invalid (`Path(".").with_suffix()` raises), which
is why the sqlite scope needs its own path; and the web app must load `.env` for
itself (`app.load_env()`), because `create_app` resolves `STORE_ROOT` before any
agent — which is what loads `.env` today — is constructed.

**Implementation status:** closed. All five phases (1, 1b, 2, 3a, 3b) are built and
covered by tests (`tests/test_run_progress_routes.py`, `tests/test_app_run_events.py`,
`tests/test_jobs.py`, `tests/test_async_runs.py`, `tests/test_sse.py`), and the record
is [`ADR-0027`](../decisions/ADR-0027-async-run-progress.md). Phase 4 (the source
fetch) belongs to [`YB-033`](../todos/entries/YB-033-event-ingress.md).

**Phase 0 — the journal (landed).** No work; §2 is the baseline.

**Phase 1 — attach the producer.** Mint `run_id` at
the top of `/ingest` and `/design/draft`; build
`JournalProgress(current_journal(), run_id, scope_id)`; call `progress.started(...)`
before the model call and pass the sink as `input_data["progress"]`; pass `run_id`
through `metadata`; call `progress.finished(run.completeness)` **after** the record
is persisted; call `progress.failed(err)` on any exception; call `progress.close()`
at the end. Note that `started()`/`failed()` have no **production** caller today
(tests do exercise them), so this phase is what first exercises the two terminal
helpers. Also: harden `_default_journal()` to fall back to `NullJournal` with a
recorded warning when the endpoint is configured but the client is missing (§3).
*Files*: `app/__init__.py`, `agents/knowledge_extraction/agent.py` (or an
equivalent event emission that does not require the profile to adopt `run_passes`
wholesale — its `PassRecord`s already exist and are the natural source).
*Tests*: a route-level test with a fake journal and the injected extractor; assert
the terminal event's verdict equals the record's, that an exception yields
`run.failed` and never `run.finished`, and that a missing client degrades to
`NullJournal` instead of raising.
*Value*: real UI runs produce real events; the existing endpoint stops returning
`[]`. Independently shippable, no new process, no new store.

**Phase 1b — the cursor.** `read()` returns stream ids (ADR-0026's protocol change,
§7). Prerequisite for any subscriber that reconnects.

**Phase 2 — job record, single-slot worker, async POST (in scope).** `JobStore`
protocol + atomic claim; `/ingest` POST writes a `QUEUED` job and redirects to
`GET /runs/<job_id>`; a separate worker process (`sea-worker`, alongside
`sea-app`/`sea-agent` in `pyproject.toml`) claims, runs, and renders job state plus
the journal backlog on the run page. The worker's body is
**`acquire(input_kind)` → the phase-1 extraction path**, but phase 2 only needs two
kinds: `inline` (dereference the artifact written at enqueue) and `graph` (load the
snapshot). `source` is phase 4 — the dispatch seam is built here so phase 4 adds an
adapter rather than a second worker path. Three hard requirements come with it:

- **The apply step re-loads and re-merges under the version guard.** The worker
  cannot carry its pre-run snapshot to the end: `state()` → merge → `save()` →
  `commit()` are separate writes (`app/__init__.py:532-569`), and backgrounding
  widens the lost-update window from seconds to tens of minutes. A reviewer
  verifying assertions while the run executes must not be silently overwritten —
  the conflict must surface as `StoreConflict`. On a backend that cannot guard
  (the file store raises on `expected_version`), the worker refuses to run rather
  than run unguarded.
- **A job that never starts must be visible.** Nothing starts the worker
  automatically; the enqueue path warns when the heartbeat is stale, and the page
  shows the queue age. A queue with no consumer is the new silent failure — worse
  than today's loud flash message.
- **Input is acquired before the passes, by the worker.** `acquire(input_kind)`
  runs once, in order, ahead of any model call: `inline` dereferences the stored
  artifact, `graph` loads the snapshot. The extraction contract is unchanged —
  `input_data["document"]` is still what the profile receives — which is what keeps
  acquisition a seam rather than a rewrite.

*Why a process, not a thread*: the reloader is on by default in this deployment
and would kill a thread mid-extraction; a thread per Flask replica would also
duplicate workers the moment there is more than one replica. The cost is a
supervisor and a heartbeat, and `worker.run_once()` exists so tests never need
either. This is a judgement about *this* deployment (one `app.run()` process,
§7), and §13 keeps it as a decision made, not a law.

**Phase 3a — the polling view.** `partials/run_events.html`, the run page, and the
HTMX timer; stops when the terminal event is seen.

**Phase 3b — SSE (built).** The same fragment over `text/event-stream`, with
`id:`/`Last-Event-ID`, a heartbeat on silence, a connection cap, a maximum stream
duration and a terminal close. Selected automatically when the journal retains
events; polling remains the fallback (§7).

**Phase 4 — path A, the source path.** YB-033 calls the same enqueue with an
`input_kind=source` reference; the worker's acquisition step fetches it through a
**source adapter over MCP**, hashes the raw bytes, records provenance, and the run
proceeds identically. This phase is listed to prove the boundary, and it names two
pieces of genuinely new work so they are not discovered late:

- **The source adapters and their MCP transport** (YB-033 owns the credentials and
  the per-source translation; §8.2's protocol is the seam).
- **The text-yielding format restriction.** The first adapter should target a
  source that returns text — Confluence's text representation, an API's Markdown, a
  `.md`/`.txt` object in S3. Binary conversion (`.docx`, `.pdf`) is separate work
  with its own fidelity problems and is not smuggled into this item.

`input_kind=graph` (a design run) needs no fetch and reuses `kind=design` from
phase 2. A `FakeSource` adapter stands in for every test, so nothing in phase 4
depends on a live Confluence.

**Why this order inside the increment.** Phase 1 proves the producer before
anything moves; 1b makes any subscriber possible; 2 moves the work out of the
request and gives the stream something durable to describe; 3a renders it. Building
3 before 2 would produce a live view of a run that still blocks the request that
started it — which is why the four ship together rather than in separate releases.
The measurement in §14 still runs, but it now *sizes* the worker (progress cadence,
queue expectations, what a viewer waits for) instead of deciding whether to build
it.

**Testability.** The suite's discipline is injection — no model, no Valkey
(`tests/conftest.py`). Everything above is checkable that way: a fake journal and
the replaced `invoke_structured` seam for phase 1; `worker.run_once()` against a
temp concurrency-safe store with two concurrent `claim()` calls for the CAS; a
bounded `next_frames(after, limit)` as a pure function plus a `?once=1` route mode
for the tail. That last point matters: **Flask's test client buffers a response**,
so an unbounded tail generator hangs the suite rather than failing it. And the
live Valkey path is *not* covered by a default `pytest` — `tests/test_run_journal.py`
skips its stream tests without Docker and `testcontainers[redis]` — so phase 1b/3b
must run those (or add a RESP-level double) as part of the work.

## 10. Failure contract and constraints

- **Single inference slot.** One worker; `QUEUED` jobs are visible with their
  `queue_seq`; a second submit never starts *concurrently in one worker*. Note what
  the store does and does not guarantee: `claim()` makes a job claimed **once**, so
  two workers cannot duplicate a run — but nothing stops two workers each claiming
  a *different* job and oversubscribing the model. Running one worker is an
  operational rule, and the honest reading of the queue depth depends on it. SSE
  subscribers consume Flask threads, never model slots.
- **Valkey unavailable.** Runs still execute and their results still land in the
  record; only *live* progress degrades. No route may 500 because the journal is
  down (the endpoint returns 503 with an `error` field; the page treats that as
  "no live progress", not as failure). A misconfigured endpoint must not stop the
  app booting (§3).
- **No worker running.** Covered by the heartbeat and the queue age (§9); the page
  says "queued for 4 minutes · no worker seen for 12 minutes", not a spinner.
- **Verdict precedence.** The stored `ExtractionRun.completeness` is the only
  verdict; `job.state` is execution state (§8). End-of-stream is never rendered as
  success.
- **Stale `RUNNING`.** Surfaced, not resolved, by this item; recovery is YB-037's
  and must not discard a result that was actually produced.
- **Concurrent human edits.** The worker re-loads and re-merges under the version
  guard, so a conflict is visible (`StoreConflict`) rather than a silent clobber.
  Background execution requires a `concurrency_safe` backend; otherwise the worker
  refuses.
- **Cross-scope reads.** The journal is one process-global client keyed by run id,
  and `current_scope_id()` is per request (`request.args`/`session`), so the read
  side could otherwise serve another scope's run. The guard is **the job record's
  `scope_id`**, not the events': with `NullJournal` there are no events to compare,
  so an event-based guard fails open on exactly the install it must protect. Both
  the run page and `GET /api/runs/<run_id>/events` resolve the scope's job by run id
  and 404 when there is none (the events API keeps its older behaviour on a
  file-backed scope, which has no jobs to check against). This is workspace
  discipline, not authentication.
- **The worker never resolves scope from request state.** `current_scope_id()`
  reads the request; a worker builds its workspace and store from the job row's
  `scope_id`, or a design run can be drafted against the wrong world.
- **Progress carries no content — with one caveat.** The payload key set is closed
  and contains no extracted text, but `error` is provider/SDK text truncated to
  200 chars (`agents/extraction/progress.py:137`). It must be a bounded
  classification or a sanitized short message, and the page must escape it — this
  is a deliberately content-free channel, and an error string is not a licence to
  leak document content.
- **Uploads.** Persisted under the scope with `secure_filename` (already used) and
  a size cap; the artifact path is derived from the hash, never from the filename.
- **A source fetch fails.** The job fails with the source's reason and **nothing is
  merged** — never an empty document, never a partial graph. Auth/permission and
  not-found are terminal; transport/5xx/429 are retryable (YB-037). The sender was
  already acknowledged, so this surfaces on the run page and, later, in the dead
  letters. A stale-on-arrival event (a newer source version than the event named)
  proceeds and records what was actually read (§8.2).
- **A fetched document is not text.** The first cut accepts only text-yielding
  sources; a binary format is a refused job with a clear reason, not a silently
  empty extraction.
- **Retention.** Streams are already TTL'd at 24 h. Job records need age/count
  retention; artifacts need an artifact-age sweep with a grace period (not a job-age
  sweep, which can delete a referenced artifact) and a per-scope quota.
- **Proxy and client timeouts.** Phase 2 is what removes them; until it ships the
  measured 62 s–25 min range stands and results can still be discarded.

## 11. Acceptance

- The extraction pipeline imports no web framework, and a run completes with zero
  subscribers (already true; keep a test).
- `POST /ingest` returns before the model call starts, with a job id; a test
  double that raises if called during the request proves it.
- A job record exists in `QUEUED` before the agent runs, and `RUNNING` while it
  does; two concurrent `claim()` calls yield one winner.
- Two submits produce one running job and one visible queue entry, never two
  concurrent model runs.
- The run page shows passes as they complete, and a page opened mid-run shows the
  events it missed, in order — with the cursor advancing (phase 1b).
- A `RUNNING` job whose worker heartbeat is stale is shown as stale rather than as
  progressing; recovery is YB-037's.
- The terminal event carries `completeness`; a `PARTIAL` run is never rendered as
  success, and job state is never inferred from end-of-stream. With no journal,
  completeness comes from the run record and an unfinished job has no verdict.
- A run page or stream request for a run in another scope is refused, not served;
  an unknown run id 404s.
- With `SEA_VALKEY_HOST` unset, the run page works and shows job state; the stream
  route degrades without a 500. With it set and the client missing, the app still
  boots.
- The worker refuses to run against a non-`concurrency_safe` scope, and a human
  edit during a run surfaces as a conflict rather than being overwritten.
- **A `source` job fetches through an adapter before any pass**, hashes the raw
  bytes, and records the acquisition channel, source system, item id, version,
  `fetched_at` and the triggering event id on the run — and, for assertions, a
  provenance record that names the source while `source_type` stays
  `SOURCE_EXTRACTION`; a `FakeSource` adapter proves it with no live system.
- **A malformed or unknown-system reference is rejected at the receiver** with a
  4xx and never enqueued; the queue is not spent discovering it.
- **A source fetch that fails merges nothing** and leaves a job whose error names
  the source and whether the cause is permanent or transient — it never yields an
  empty or partial graph, and a config-failed job can be re-enqueued by a human.
- **A divergent read is visible.** With a `FakeSource` that returns a newer version
  than the event named, the run proceeds, records `source_version_at_read`, sets the
  divergent flag, and the page says so.
- **An unversioned source does not collapse redeliveries**: the fetch result marks
  it unversioned and the dedupe value falls back to the artifact hash.
- **A binary source is refused with a reason**, not extracted into a confidently
  empty graph.
- **A design run has no document** (`input_kind=graph`) and still runs end to end.
- **The receiver performs no fetch**: with a source adapter that sleeps, YB-033's
  endpoint still returns promptly, because the reference was enqueued and the
  worker does the waiting.
- Path A reuses the same enqueue/worker/journal path (`source` or `graph` instead
  of an inline document); the only new modules are the adapters, and the only
  `core/` change is the additive provenance extension.

## 12. Non-goals

- No workflow engine, no broker, no distributed workers, no exactly-once claim
  (YB-037's decisions, deliberately not pre-empted).
- No retry, backoff, cancellation, dead-lettering or recovery policy — the *states*
  are in the job vocabulary, and acquisition *classifies* a source error as
  permanent or transient, but the retry mechanism and the idempotency **policy**
  (including the "run anyway" escape) are not built here.
- No version-precedence policy: a divergent read is recorded and flagged (§8.2),
  and what to do about it is not decided in this document (§13).
- No schedule or CLI initiator: the `trigger` field accommodates them and they use
  the same enqueue, but building them is not this item.
- No binary document conversion, and no MCP server implementation here: the design
  defines the adapter protocol and the admissibility contract; the adapters and
  their credentials are YB-033's.
- No write-back to any enterprise system (YB-033's non-goal).
- No change to what extraction reads or how completeness is computed.
- No auth model: the platform has none today, and inventing one here would hide
  that gap rather than open this one. The scope check above is workspace
  discipline, not authentication, and must not be mistaken for one.
- No WebSocket, no ASGI layer, no second progress channel.

## 13. Decisions taken, and the questions that remain

**Taken here** (previously listed as open questions, now decided so the plan can
be read without ambiguity):

| Decision | Choice | Reason |
|---|---|---|
| Worker process or thread | **Separate process** (`sea-worker`), with `run_once()` for tests | The reloader is on by default and kills a thread; a thread per replica duplicates workers. Revisit when the deployment changes |
| Polling or SSE | **Both built.** SSE (3b) is the transport when the journal retains events; polling (3a) is the fallback for installs with no journal | The cursor fix is shared and both render one template, so this is a choice per deployment rather than a rewrite (§7) |
| Phase 1 alone first | **Yes** | Independently valuable, and the prerequisite for everything else |
| Run page or jobs list | **Run page only** | A list is YB-037's; a run page is enough to watch the run you started |
| Content-addressed input artifacts | **Yes** | A Phase 2 prerequisite for a separate worker; bytes as received, with the per-scope quota and sweep rules of §8.3 **specified but not yet built** |
| Where a source fetch happens | **In the worker, as a run step — not in the receiver** | Fetching in the webhook request rebuilds the anti-pattern; the trigger carries a reference (§4, §8.2) |
| Who invokes the fetch | **Deterministic worker code, not the extraction model** | Acquisition materialises `input_data["document"]` before the passes; the extractor profiles hold no credentials and register no tools, and a model-chosen fetch would bypass the raw-byte hash (§6, §8.2) |
| Fetch transport | **MCP behind a source-adapter protocol** | External systems are the stated MCP case; the adapter interface keeps the worker source-agnostic and testable with a fake |
| Which sources first | **Text-yielding only** (Confluence text, an API's Markdown, an S3 `.md`) | Binary conversion is separate work; a `.docx` through a text extractor is a confidently empty graph |
| Reference validation | **By the receiver, on the reference only** — 4xx before enqueue | A malformed or unknown-system reference must not consume the single slot minutes later; existence and permission genuinely need the fetch |
| Provenance for a fetched document | **An additive `core/knowledge/` extension, on the run *and* the assertion** (acquisition channel, source system/item/version/URI, `fetched_at`, `trigger_event_id`, `artifact_hash`); `source_type` stays `SOURCE_EXTRACTION`; `SOURCE_IMPORTED` becomes the acquisition-channel value | Fact origin and byte route are different claims; the fields do not exist today, so this is ownerless work that must be assigned (phase 4), not an edit made in passing |
| Error classification | **Permanent** (item 404, item permission denial, unsupported format, size cap, malformed reference) versus **transient** (401, credential-level 403, 5xx, 429, transport) | Credentials live in the worker, so an expired token is a config failure a retry fixes — the original "auth is terminal" was wrong. Mechanics remain YB-037's |
| Durable workflow engine | **Deferred**, with a four-condition trigger; control flow stays deterministic and is not re-opened | Adopting an engine before the human-gate boundary is settled is the irreversible cost. See the subsection below |
| Execution substrate (b) | **In scope — introduced with the async work (§9), by owner decision 2026-09-27** | Progress and execution are one change; the substrate is cheap now and compounds if deferred. It is a protocol plus one worker, not an engine |

**Genuinely open — needs a human:**

1. **Is the §1 interpretation right** — two initiators, one spine — or does "two
   paths" mean two independent transports? The design assumes the former, and the
   whole boundary argument rests on it.
2. **The measurement's role is now sizing, not a gate** (§14) — how should the run
   page read while a long run is in flight, and when does the polling timer start to
   hurt? Whoever runs `scripts/sanity_check.py` should own that reading.
3. **Job backend.** Not decided here: the design requires only an atomic `claim()`
   and a `concurrency_safe` scope. The concrete backend is a YB-043 coordination
   point, and the current file-backed install cannot run a worker until it moves.
4. **Which source is first, and who owns its credentials?** The design commits to
   the adapter protocol and the text-only restriction; the first system
   (Confluence, SharePoint, or a generic API) also decides where its MCP server
   lives and who is allowed to read what it reads. That is a deployment decision,
   not a design one.
5. **What happens when the read diverges from the event** — a source that moved on
   while the event sat in a queue. The design records and flags it; whether the
   policy is "proceed and surface on the review page", "refuse and re-enqueue", or
   "ask the source for the named version explicitly" is a freshness-versus-identity
   decision this document does not have the standing to make.

### When to introduce a workflow engine — deferred, with a trigger

The question "is it time to introduce a *workflow*?" conflates three decisions with
very different costs. Separating them is the whole answer:

| Sense of "workflow" | Status | Verdict |
|---|---|---|
| **(a) Control flow** — a fixed sequence of steps versus an agent deciding the next step | **Already decided and shipped.** `ARCHITECTURE_PASSES` is a hardcoded list of four `PassSpec`s that `run_passes` iterates; `PassRecord`s record each outcome and `compute_completeness()` derives the verdict from them. No agent chooses the next step. The v1 backlog recorded the same choice: *"orchestrator vs workflow (recommendation: workflow with explicit human gates — a dynamic router adds non-determinism to a product selling determinism)"* (`docs/todos/legacy-todo-v1.md:751`) | **Keep; do not re-open.** Completeness and audit are *computed from pass outcomes* (ADR-0013); if a router chose the passes, neither would be computable the same way |
| **(b) Execution substrate** — a durable job record, a queue, a worker, so work outlives the request | Not built; YB-026 phase 2 | **Introduce now**, with the async work (§9) — a protocol plus one worker, not an engine |
| **(c) Durable workflow engine** — Temporal-shaped: retries, timers, human-in-the-loop replay, workflow versioning | Not built; YB-037 decision 1 | **Defer**, with the trigger below |

**Why (b) ships now and (c) does not.** This is the rework question, and the
asymmetry runs one way:

- **Deferring (b) compounds.** Every initiator built in the meantime — the YB-033
  webhook, an overnight YB-035 design run, a schedule — attaches to the synchronous
  path and grows its own status, progress and lost-result handling. That is exactly
  the duplication ADR-0026 exists to prevent, and unwinding it is *per initiator*.
  The cost is not only UX: the measured >25-minute run whose result is discarded,
  and a webhook redelivery starting a second extraction, are correctness debt.
  **Owner decision 2026-09-27: (b) is introduced with the async work rather than
  gated** (§9).
- **Introducing (c) early is the expensive mistake.** An engine constrains how run
  identity, retries and the human gate are modelled — deterministic replay,
  workflow-versus-activity boundaries, versioning. Those semantics are *unsettled*:
  YB-037's own decision 4 asks whether a workflow ends at "assertions merged" or at
  "batch reviewed", and if the latter a workflow is open for days and needs timers.
  Engine workflows are versioned and long-lived, so migrating in-flight instances
  after getting the boundary wrong is the expensive part.
- **Deferring (c) is cheap because the seam exists.** Everything engine-shaped sits
  behind `JobStore` (an atomic `claim()`) and `RunJournal`. Adopting an engine later
  replaces the worker loop and the enqueue call site — not the agent code, the pass
  structure, completeness, provenance or the transport. That is the payoff for
  introducing (b) as a protocol.

**What to freeze while building (b)** — the parts that are expensive to change
later and none of which depend on the engine choice: the `JobStore` protocol
(`claim`/`complete`/`fail`/heartbeat/list), run and job identity, the three input
kinds, verdict precedence, and the `read()` cursor. These are the surfaces the
increment in §9 must get right, since everything after it keys off them.

**Trigger to revisit (c).** Adopt an engine when any **two** of these hold:

1. two or more initiators run in production (event, schedule, CLI, UI);
2. the workflow must **span the human gate** — days-long, with reminders,
   escalation or compensation;
3. a hosted runtime or a second transport makes an in-process queue untenable;
4. retry/compensation logic starts appearing in more than one place.

Until then, a queue record plus one worker is the honest shape — and if the human
gate is where this is heading, the cheaper move is to keep the workflow boundary at
the **run** (terminal state `AWAITING_REVIEW`) and leave the multi-day review
lifecycle to YB-018's batch model, outside the workflow. That choice, not the
engine, is the one to make deliberately; it belongs in YB-037's decision 4.

## 14. What the measurement is now for: sizing, not a gate

The substrate ships with the async work (§9), so this section no longer decides
*whether* to build phase 2. It still matters, because the numbers shape it. Run
`scripts/sanity_check.py --stages route` on the real documents
(`payment_platform_arch.md`, `sample_requirements.md`) several times and record the
route latency; then use it for:

- **Progress cadence and expectations.** A p95 of 60 s with a 2 s poll means ~30
  polls per run — fine. A p95 of 20 minutes means the run page must read well while
  nothing appears to happen, and acquisition/first-pass latency becomes the thing to
  surface first.
- **Queue expectations.** How long a second submit should expect to wait, which is
  what the queue age on the page is calibrated against.
- **Whether a viewer needs SSE sooner than §7's trigger.** The longer the run, the
  sooner the polling timer is worth replacing.

**The counter-argument, recorded rather than hidden.** The skeptic lens argued that
if the config fix took a run from 472–624 s to 62 s, then building a worker is
ceremony, and phase 1 plus polling would be the whole product. That argument was
weighed and **overruled by the owner on 2026-09-27**, for the reason in §9: the
substrate is cheap now and compounds if deferred, and the correctness problems it
removes — a completed result discarded on a client or proxy timeout, a webhook
redelivery starting a second extraction — do not depend on the common-case latency.
Even at 62 s, a synchronous POST is the wrong transport for work of unbounded
duration.

The timings live in
[`YB-026`](../todos/entries/YB-026-asynchronous-progress.md) §The problem, measured;
`docs/reference/extraction-baseline.md` records output *quality*, not duration, and
is not evidence here.

## 15. Review record

Reviewed 2026-09-27 by three independent adversarial lenses against the 441-line
draft: **code grounding**, **architecture/backlog consistency**, and
**skeptic/operations**. Verdicts: sound with fixes (grounding, architecture) and
*do not build yet — ship phase 1 + polling, gate the rest* (skeptic). All three
were read-only; this revision is the response.

**Facts corrected** (grounding lens, all verified against source):

- YB-023 is `done` and ADR-0013 closed it: the requirements profile **does** emit
  per-attempt `PassRecord`s (`agents/knowledge_extraction/agent.py:465-471, :617`).
  The gap is journal **events**, not pass records. "YB-023's gap closes" and "no
  pass records" removed throughout; the "both profiles accept `progress`" claim
  narrowed to the two that do.
- `RunJournal.read()` discards the stream id, so replay/`Last-Event-ID` **cannot
  work** against the current contract. "Replay comes free" and "needs no protocol
  change" replaced with a named prerequisite (phase 1b, §7).
- The sink travels in `input_data`, not as a `run()` kwarg — the spine diagram was
  corrected.
- `gthread`/connection cap attributed as a recommendation, not an existing note;
  no Dockerfile, gunicorn config or gunicorn dependency exists.
- YB-035 is `done`; the "half that landed is the *might* half" inversion; "no
  caller" → "no production caller"; "472 s" → "472–624 s"; the missing stream-id
  and boot-fragility gaps added to §3.

**Design changed** (architecture + skeptic lenses):

- **Recovery left to YB-037.** `INTERRUPTED` removed; the state set is now a subset
  of YB-037's published vocabulary, and a stale `RUNNING` job is *surfaced*, not
  resolved. YB-037's "never a lost result" acceptance is recorded as a dependency.
- **Verdict precedence stated** (§8): the stored `ExtractionRun.completeness` is
  the only verdict, `job.state` is execution-only, and the no-Valkey path reads
  completeness from the record. The `ADR-0026` entry's "no second status object" is
  qualified accordingly.
- **`JobStore` narrowed to a protocol** with an atomic `claim()`; the backend
  moved to a YB-043 coordination point, and a non-`concurrency_safe` scope may not
  run a worker at all.
- **The lost-update window** is now an explicit phase-2 requirement: re-load and
  re-merge under the version guard.
- **No worker running** is a first-class design element (heartbeat column, queue
  age, enqueue-time warning), not prose.
- **Artifacts**: raw-byte hashing (the existing hash is over decoded text), a named
  path, quota, artifact-age sweep with grace, and the statement that this is the
  first persistent copy of customer documents.
- **`dedupe_key`** is no longer proposed as settled; silent no-op re-ingest is
  called out and the field is reserved for YB-037.
- **Queue position** made computable (`queue_seq`), with the no-budget/starvation
  limitation stated.
- **Cross-scope guard** moved from the events to the job record (an event-based
  guard fails open under `NullJournal`).
- **Polling-first sequencing** adopted over the outline's implicit SSE-first order,
  with the trigger for 3b stated. The skeptic recommended polling permanently; the
  architecture lens recommended SSE primary. The adjudication — SSE as the
  steady state, polling as the first increment — is in §7.
- **Testability** extended: `run_once()`, bounded `next_frames`, `?once=1`, a CAS
  test, and the note that Flask's test client buffers a streamed response.
- Smaller: kind-dependent input for `design`, worker scope resolution, stream caps
  and terminal close, `error` content caveat, `(later) write-back` removed, the
  polling endpoint named.

**Not changed, with reason.** The separate worker process stays, against the
skeptic's "thread is less machinery": the reloader and multi-replica duplication
outweigh the supervisor cost here, and `run_once()` removes the testing cost. The
skeptic's core claim — that phases 2–3 are expensive relative to an unmeasured
problem — is accepted, and §14 turns it into a numeric gate rather than a
judgement call.

The decisions above are provisional until implemented; the ADR that closes YB-026
is where they become a record.

### Revision — 2026-09-27: input acquisition (post-review correction)

Raised after the review, from the platform owner: **"It is not that always the
extraction will be initiated with a document. When by events, the MCP tools will
have to pull the doc from a source — say Confluence, SharePoint, API etc."**

Both halves of that are corrections to the reviewed draft:

1. **A document is not universal.** The draft had one acquisition path with a
   design-run special case. §8.1 now makes the **input kind** explicit —
   `inline` (UI bytes), `source` (an event's reference), `graph` (a snapshot, no
   document) — and the worker's acquisition step dispatches on it. `kind=design`
   stops being an exception and becomes one of three.
2. **The fetch belongs to the job, not the receiver.** The reviewed draft said the
   fetch "must happen before the job is enqueued", i.e. inside YB-033's webhook
   request. That is wrong for the same reason `/ingest` is: fetching from
   Confluence or SharePoint is slow, credentialed and rate-limited, and holding a
   webhook open for it reintroduces exactly the unbounded-request problem this
   design exists to remove — and invites the sender to retry into a second fetch.
   §8.2 now places the fetch **inside the run**, as deterministic worker code
   before any pass. The model still never fetches its own input, so the provenance
   and object-contract argument that motivated the original placement is
   preserved; only the *caller and the timing* moved.
3. **MCP is mandatory for path A, not "eventually".** §1, §5 and §6 were reworded:
   without a source fetch there is no document to extract from, so the adapters and
   their MCP transport are work with an owner (YB-033) rather than a possibility.

New material: §8.1 (three input kinds), §8.2 (the source-adapter protocol, raw-byte
hashing, source-version provenance and idempotency, failure classification, the
text-only restriction), §8.3 (the artifact store now serving both `inline` and
`source`). The acceptance list, failure contract, phasing and decisions table were
extended to match, and a fourth open question added: which source is first, and who
owns its credentials.

**Second review round — the acquisition revision itself.** Reviewed again with a
dedicated adversarial lens (verdict: *sound with fixes*). The core move was upheld;
three blockers and six majors were folded in:

- **A leftover, caught by review**: §4's diagram still carried the *old*
  pre-enqueue acquisition block above the new one, so the figure contradicted its
  own §8.2. Collapsed to a single acquisition step at (d); the pre-enqueue step is
  now "declare the input" only.
- **The receiver validates the reference** (shape: known system, non-empty
  `item_id`, served scope → 4xx before enqueue). Existence and permission stay with
  the fetch — a bad reference must not spend the single worker slot failing minutes
  later.
- **The failure taxonomy was self-contradicting and wrong.** "Auth is terminal" is
  false for the credential failures this design introduces by moving credentials
  into the worker: 401 and credential-level 403 are *transient*, fixed by config.
  Permanent is item-404, item-permission, unsupported format, size cap, malformed
  reference. A config-failed job needs a re-enqueue path, since the sender was
  already answered `2xx`. §12's blanket "no retry" was narrowed to match.
- **`SOURCE_IMPORTED` was the wrong provenance value.** `source_type` is the origin
  of the *fact*, not the route the bytes took — a Confluence page read by the
  extractor still produces `SOURCE_EXTRACTION` facts. The provenance extension is
  now on **both** the run and the assertion, with the acquisition channel as its own
  field and `SOURCE_IMPORTED` as that channel's value. This is a `core/knowledge/`
  change, so it is named work with an owner (phase 4), not an incidental edit.
- **Idempotency hardened**: `event id + (source version | artifact hash)` with an
  explicit `unversioned` marker, because a version-less source would otherwise
  collapse every event into one run.
- **Provenance carriers pinned**: `document_ref` stays the display filename (a test
  pins it), the canonical URI goes in a sanitized `source_uri`, and `artifact_hash`
  becomes a run field. Source metadata is treated as sensitive on a page with no
  auth.
- **The "deterministic code" argument was re-founded** on structure rather than
  assertion: the profiles hold no credentials and register no tools, and a
  model-chosen fetch would bypass the raw-byte hash. ADR-0002 no longer carries an
  argument it does not make, and the version-divergence rule no longer cites a
  precedence that ADR-0012 does not establish — it is recorded, flagged, and left
  as an explicit open question (§13.5).
- **Format**: admissibility is now a contract on adapters (text whose headings
  survive, because the chunker splits on headings), matching YB-033's wording;
  Confluence's text representation is itself a conversion.
- Plus: durable ordering (`hash → persist → record provenance → run`), MCP named as
  new infrastructure rather than existing, the reference/`trigger` axes separated
  (schedule/CLI have an input kind too), and YB-033's unsatisfiable acceptance
  bullet corrected.

### Owner decision — 2026-09-27: the substrate ships with the async work

Asked whether to introduce the workflow substrate (b) now or defer it, given the
rework risk either way, the owner's answer was: **introduce (b) together with the
async work.** Recorded as a scope change, not a plan rewrite:

- §9 is now **one increment** — phases 1, 1b, 2 and 3a ship together — with the
  rationale that progress without a worker and a worker without progress are each
  half a mechanism, and that the compounding cost sits on the deferral side.
- §14 is no longer a gate. The measurement still runs and now **sizes** the worker
  (progress cadence, queue expectations, when SSE is worth it). The skeptic's
  "phase 2 is ceremony if p95 is 62 s" argument is retained verbatim as the recorded
  counter-argument, and explicitly overruled: a synchronous POST is the wrong
  transport for work of unbounded duration even when the common case is fast, and
  the discarded-result and redelivery defects do not depend on p95.
- (c), the durable engine, remains deferred with the same four-condition trigger.
  Nothing else in the design moved: the engine seam is what makes this decision
  reversible, which is why (b) was specified as a protocol plus one worker rather
  than as machinery.
