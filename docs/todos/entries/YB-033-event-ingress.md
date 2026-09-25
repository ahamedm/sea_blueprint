---
id: YB-033
legacy: null
title: "Event ingress — an external system's event starts graph work, without a browser in the loop"
status: open
priority: medium
area: "new `app/events.py` or `integrations/` (receiver + adapters), `app/__init__.py`, `core/knowledge/ingest.py`"
created: 2026-09-25
updated: 2026-09-25
design: docs/design/event-driven-integration.md
record: null
superseded_by: []
related: ["YB-034", "YB-035", "YB-036", "YB-037", "YB-018", "YB-026", "ADR-0012"]
blocks: []
blocked_by: []
---

# YB-033 — Event ingress

> **Open work.** Design sketch, not yet reviewed. The shared analysis — the two
> event shapes, the existing affordances, the push/pull question — is in
> [`docs/design/event-driven-integration.md`](../../design/event-driven-integration.md).

### Why

Every agent run today starts with a person filling in the `/ingest` form. Two
things that happen *outside* this platform should be able to start one:

| Event | Payload | Action it implies |
|---|---|---|
| a requirement is published in the system of record | content or a reference to it | ingest it as REQ-G or ARC-G input |
| an Initiative moves to its Design phase | a state change, no content | run the Design Assistant over a verified REQ-G |

They are not the same kind of event. The first brings knowledge that has to be
read; the second brings nothing and exists only to authorise work. One receiver,
two adapters.

### What is missing

- **No trigger that is not an HTTP form POST.** Nothing runs unattended.
- **No event identity.** There is nothing to deduplicate on. This matters more
  than it looks, because the two levels of idempotency differ:
  - *Assertion level: already convergent.* `make_assertion_id` is
    content-addressed and `merge_graphs` folds rather than replaces, so replaying
    the same content cannot duplicate a fact or destroy a human decision.
  - *Run and revision level: not convergent.* `_run_id` mixes in `utc_now()`
    (`core/knowledge/ingest.py`), so identical content produces a **new run**, and
    the route calls `store.commit` unconditionally — **one revision per ingest**.
    An at-least-once webhook replaying five times leaves five runs and five
    revisions of the same document.
- **No origin that survives the trip.** `SOURCE_IMPORTED` exists in the provenance
  enum and is used by nothing. An imported fact cannot currently say which system,
  which event, or which actor produced it.
- **No fetch path.** Jira's webhook payload is not the document; getting the
  document needs credentials, a network boundary, rate-limit handling and a
  decision about what to do when the fetch fails after the event was accepted.

### Shape (deliberately bounded)

The receiver should do five things and nothing else, for the same reason `/ingest`
should not do model work in a request:

```
verify signature → record event id → map to an operation → enqueue → 2xx
```

- **Verify** — a shared secret at minimum. The platform has no user auth at all;
  this should not pretend otherwise or quietly become the place auth gets invented.
- **Record the event id** before acting on it, so a redelivery is recognisable
  rather than merely survivable.
- **Map** — an adapter per source, translating an external payload into one of a
  small closed set of operations (`ingest_document`, `advance_phase`, `noop`).
  Unknown event types must be recorded and ignored, not rejected: a source will add
  event types SEA has never heard of, and a 4xx makes the sender retry forever.
- **Enqueue** — hand off to the workflow layer (YB-037). The receiver never calls a
  model.
- **2xx** — acknowledge separately from completion. Acknowledging work that has not
  run is the only way to avoid the sender's retry storm; the cost is that the
  workflow must be durable enough to be worth the acknowledgement.

### Decisions this item has to make

1. **Push or pull content.** Prefer a reference in the webhook and fetch the
   document, so SEA reads the current text rather than whatever the sender chose to
   embed. That trades a network dependency for correctness.
2. **Who wins when the two systems disagree.** The graph's fold rule resolves
   agent-versus-human. It does not resolve *system-of-record-versus-human-in-SEA*.
   A requirement edited here and re-imported from there needs a stated policy,
   visible in provenance, not an accident of merge order.
3. **Are imported facts pre-verified?** Approval in another system is not review in
   this one. The default should be `UNVERIFIED` with `SOURCE_IMPORTED` provenance;
   a "trusted source" fast path is a policy that has to be shown on the review page
   if it is ever added.
4. **What an event does to the revision history.** Committing a revision per event
   is wrong for the same reason committing one per keystroke is wrong. Options: one
   revision per *n* events, per Initiative per hour, or a draft/batch model — which
   is [YB-018](../entries/YB-018-review-batches.md)'s territory.
5. **Out-of-order and late events.** A phase-changed event older than the phase the
   graph already holds must not move it backwards silently.

### Acceptance

- A redelivered event produces no duplicate run, no duplicate revision, and no
  duplicate assertion.
- An unknown event type is recorded and ignored with a 2xx; a bad signature is
  rejected with a non-2xx.
- Every fact produced by an event carries `SOURCE_IMPORTED` provenance naming the
  source system and the event that caused it.
- The receiver performs no model call and returns promptly under a slow backend.
- A test replays the same event twice and asserts the graph and the revision index
  are unchanged after the second.

### Non-goals

No outbound integration, no full auth, no broker. See the design note's
*Explicit non-goals*.

### Related

- [YB-034](../entries/YB-034-initiative-delivery-phase.md) — the state the second
  event changes.
- [YB-035](../entries/YB-035-design-assistant.md) — the agent the second event runs.
- [YB-036](../entries/YB-036-modular-run-streaming.md) /
  [YB-037](../entries/YB-037-background-workflow-management.md) — the transport and
  the execution this hands off to.
- [YB-018](../entries/YB-018-review-batches.md) — a continuously fed graph makes a
  per-run review queue unavoidable rather than convenient.
- [ADR-0012](../../decisions/ADR-0012-req-arc-reconciliation-inversion.md) — enterprise
  identifiers outrank document-local ones, which is what makes an imported
  requirement joinable at all.
