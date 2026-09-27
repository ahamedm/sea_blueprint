---
id: YB-026
legacy: "26"
title: "Asynchronous progress — stream pass and tool-call completion to the view"
status: open
priority: high
area: "`app/__init__.py` (ingest route), `app/templates/ingest.html`, `agents/extraction/passes.py`, `agents/knowledge_extraction/agent.py`"
created: 2026-09-23
updated: 2026-09-25
design: null
record: null
superseded_by: []
related: ["YB-020", "YB-023", "YB-024", "YB-025", "YB-033", "YB-035", "YB-036", "YB-037"]
blocks: []
blocked_by: []
---

# YB-026 — Asynchronous progress — stream pass and tool-call completion to the view

> **Open work.** This file is the source of truth for this item; `TODO.md` is generated from it.

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

[YB-036](YB-036-modular-run-streaming.md) is the generalisation: a
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
