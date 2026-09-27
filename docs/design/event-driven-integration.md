# Event-driven integration — external triggers, background work, and the seams that have to move

> **Design document** for `YB-033`, `YB-034`, `YB-035`, `ADR-0026` and `YB-037`.
> Status is tracked in those entries; this is the long analysis they share.

---

### The proposal, stated once

Today an agent runs because a person filled in a form and waited. The proposal is
that some agent runs should start because **something happened elsewhere**:

- a **Business Requirement was published** in the system of record (Jira, DOORS,
  a service catalogue);
- an **Initiative moved to its Design phase** in that same system.

The second kind is different from the first in an important way. Publishing a
requirement is an *input* event — it brings content that has to be read. Entering
Design is a *state* event — it brings no content at all, and its whole meaning is
"run the Design Assistant now". One ingress, two payload shapes and two very
different actions.

Three things fall out of that, and they are the three seams this document is
about:

1. **The trigger** has to exist outside the UI request (YB-033), and the graph has
   to be able to represent the state it triggers on (YB-034).
2. **The work** has to be executable in the background — durable, retryable, and
   bounded — because nobody is holding a browser open (YB-037).
3. **The progress** of that work has to be observable to whoever *is* looking,
   which means the streaming mechanism currently bolted to a Flask response has to
   become transport-agnostic (ADR-0026).

The agent that makes the second event worth firing is the Design Assistant
(YB-035), which is a placeholder package today.

### Why the UI request is the wrong trigger, in one paragraph

`/ingest` runs the extractor synchronously inside the request
([`app/__init__.py`](../../app/__init__.py)), which YB-026 already documents and
measures: the same small document has taken 62 seconds and, on another run, more
than 25 minutes. A synchronous POST is the wrong transport for a job of unbounded
duration — and it is a defensible design when a human is waiting, because the human
can see that it is slow. An event has no human. A webhook that holds a connection
open for 25 minutes will be retried by the sender, and the retry will start a
second extraction.

That is the whole argument for backgrounding, and it is a correctness argument
before it is a UX one.

### What already exists to build on

Worth stating precisely, because it decides how much of this is new:

| Affordance | Where | What it buys |
|---|---|---|
| Content-addressed assertion ids | `make_assertion_id` | Re-processing the same content folds instead of duplicating — event **replay is already convergent at the assertion level** |
| Fold, not replace, on merge | `merge_graphs` | A human decision outranks a re-observation, so a replay cannot destroy review work |
| Atomic file writes | `RevisionStore._write_atomic` | A crash never leaves a half-written graph JSON |
| Provenance source enum | `SOURCE_IMPORTED`, `SOURCE_ENRICHMENT` | Imported facts are already a distinguishable origin, not a fiction |
| External identity mixin | `ExternallyReferenced` | The graph already expects to join to a system of record |
| Initiative status enum | `InitiativeStatus` | An approval lifecycle exists to attach to |
| Pass-structured pipeline | `PassRecord`, `PassRunSummary` | Progress data is already produced and currently discarded |
| Revision store | `RevisionStore`, `commit`, `freeze` | The output of a background run has somewhere durable to land |

And what does **not** exist, which is the reason for four of the five entries:

| Missing | Consequence |
|---|---|
| Any trigger that is not an HTTP form POST | Nothing runs unattended |
| Run-level idempotency | `_run_id` mixes in `utc_now()`, so the same content produces a **new** run every time |
| A revision policy for automated runs | `/ingest` calls `store.commit` unconditionally: **one revision per ingest** |
| A cross-write transaction | `save_working` then `commit` are two writes; a crash between them leaves an uncommitted working set |
| A delivery-phase model | `InitiativeStatus` is business-case approval (`PROPOSED` … `DELIVERED`); "in Design" is not in it |
| The Design Assistant | `agents/design_assistant/__init__.py` is a docstring that says "Placeholder" |
| A progress transport | YB-026 has not been built; progress exists only in the final payload |

### Scenario 1 — a requirement is published

The event carries an identity (issue key, version) and possibly content. The
questions that have to be answered before it can be wired:

- **Push or pull?** A webhook carrying the full document is simple and puts the
  parsing burden on the sender's payload schema; a webhook carrying an *identifier*
  that SEA then fetches needs credentials, rate-limit handling and a network
  boundary, but gets the current text rather than whatever was in the payload.
  Jira's webhook payloads are not the document — a fetch is almost certainly
  required.
- **What is the source of truth?** If Jira is authoritative, a requirement edited
  in SEA and then re-imported must not be silently overwritten. The graph's fold
  rule resolves agent-versus-human; it does not resolve *Jira-versus-human-in-SEA*,
  which is a policy decision with a provenance answer.
- **Is imported knowledge pre-verified?** A published, approved requirement has
  arguably already passed a gate — but not *this* platform's gate, and not this
  graph's reviewers. `SOURCE_IMPORTED` facts landing as `UNVERIFIED` keeps the
  audit honest; a "trusted source" fast path is a policy that has to be explicit
  and visible on the review page, not an optimisation hidden in ingest.
- **What happens on redelivery?** At-least-once delivery is the norm. Assertions
  fold, but the run record does not, and the revision does not. So the unit of
  idempotency has to be the **event**, not the assertion.

### Scenario 2 — an Initiative enters Design

This is the trigger from the PRD's own workflow table: step 5, *ARC-G Initiation —
Solution Shaping*, whose trigger point is written as "REQ-G completion". An
external phase change is the same trigger, observed from outside rather than
inferred inside.

Two things are needed that do not exist:

- **A phase the graph can hold** (YB-034). `Initiative.status` ranges over
  `InitiativeStatus`, which is the business-case approval lifecycle. "Design" is a
  delivery stage, and it is not in that enum. Either a second axis is added — which
  forces the question of whether phase is a *property* or a *transition history* —
  or the existing enum is widened, which conflates approval with delivery.
- **A reason to believe the requirements are ready.** The PRD's trigger is "REQ-G
  completion", and the graph can already answer that: `completeness == COMPLETE`
  and the review progress is auditable (`is_auditable`). So the event should be
  *advisory* and the graph should be the gate. Firing the Design Assistant on a
  phase change while REQ-G is `PARTIAL` or 17% reviewed would generate a design
  from a document that was never fully read — and the design would look as
  confident as one built from a complete graph.

### The seams

**YB-033 — the trigger.** A webhook receiver and an event→operation mapping.
Bounded on purpose: verify, deduplicate, map, enqueue, 2xx. It should do no model
work in the request, for the same reason `/ingest` should not.

**YB-034 — the state.** The graph's model of where an Initiative is in delivery,
and who is allowed to move it.

**YB-035 — the agent.** The Design Assistant drafting an initial architecture from
a verified REQ-G. Note the shape: its output is the *same kind of thing* the
architecture extraction profile already produces, and its input is a graph rather
than prose. That makes it a new profile over the knowledge layer, not a new
pipeline — but also means "the requirements are the document", and the same
object-contract discipline has to apply.

**ADR-0026 — the progress.** One run-progress mechanism serving two consumers: the
browser watching a UI-initiated run, and the operator/UI watching an event-driven
one. This is the "modular streaming" the proposal asks for, and it is YB-026's
mechanism with the Flask coupling removed.

**YB-037 — the execution.** Workflow management for background runs: durable
state, retry with backoff, idempotency by event id, a queue that respects a
single-slot inference backend, cancellation, and human-in-the-loop gates.

### Why streaming and workflow management are two items, not one

They are easy to conflate because both are "the async stuff", and conflating them
produces a design where progress is inferred from execution and execution state is
inferred from progress. They answer different questions:

- **A workflow** knows *what should happen next*, *how many times it has been
  tried*, and *whether it is still allowed to run*. It survives a restart.
- **A stream** knows *what has happened so far*, for a consumer that may connect
  late, disconnect, or not exist. It is disposable by design.

A workflow can complete with nobody watching; a stream with nobody watching is not
a stream. Keeping them apart is also what lets the run-progress event log be a
simple append-only thing instead of a database of execution state.

### Decisions the entries deliberately do not make yet

- **Adopt a workflow engine or build a thin queue?** A durable engine
  (Temporal-shaped) answers retries, timers and HITL gates with a large dependency;
  a queue table plus a worker answers a fraction of it with a fraction of the
  operational surface. The honest first question is how many of those guarantees
  this deployment actually needs, and the current deployment is one Flask process
  against one inference slot.
- **Where does the boundary between SEA and the enterprise system sit?** Inbound
  only, or bidirectional? Write-back (a finding posted to Jira) is a much larger
  trust surface — outbound authentication, idempotent writes, and the question of
  who owns a field — and none of these five items assume it.
- **Is the Initiative phase SEA-owned or mirrored?** If Jira owns phases, SEA's
  copy is a cache with a reconciliation problem. If SEA owns them, the event is a
  request rather than a statement.

### Explicit non-goals

- No write-back to Jira or any outbound integration.
- No authentication or authorisation for the webhook beyond a shared secret —
  the platform has no user auth at all today, and inventing it here would hide a
  known gap rather than open this one.
- No distributed execution, no multi-worker scheduling, no broker dependency.
- No change to what extraction *reads* or how completeness is computed. Events
  change when work starts, not what the work is.

### What would falsify the value of this

If Initiatives are never created far enough ahead of design work for the event to
matter — i.e. if the architect is always the one who starts the design step anyway
— then YB-033's second scenario is ceremony, and the honest outcome is to keep the
UI trigger and drop the event. The requirement-published scenario is the stronger
case, because that event originates with someone who is not in SEA at all.
