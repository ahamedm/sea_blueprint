---
id: YB-034
legacy: null
title: "Initiative delivery phase — the graph cannot represent \"this Initiative is in Design\""
status: open
priority: medium
area: "`ontology/requirements_base.yaml` (Initiative, InitiativeStatus), `core/knowledge/ingest.py`, `app/projections.py`"
created: 2026-09-25
updated: 2026-09-25
design: docs/design/event-driven-integration.md
record: null
superseded_by: []
related: ["YB-033", "YB-035", "YB-011", "ADR-0012"]
blocks: []
blocked_by: []
---

# YB-034 — Initiative delivery phase

> **Open work.** Design sketch, not yet reviewed. Context in
> [`docs/design/event-driven-integration.md`](../../design/event-driven-integration.md).

### Why

The Design Assistant is supposed to run when an Initiative reaches its Design
phase (PRD step 5, *ARC-G Initiation — Solution Shaping*). The graph cannot say
that today, so the trigger has nothing to fire on and the UI has nothing to show
an architect who asks "where is this Initiative?".

### What exists, and why it is not this

`Initiative.status` ranges over `InitiativeStatus`
([`ontology/requirements_base.yaml`](../../../ontology/requirements_base.yaml)),
which is the **business-case approval** lifecycle:

```
PROPOSED → UNDER_REVIEW → APPROVED → IN_FLIGHT → DELIVERED
                                     ↘ ON_HOLD ↗        ↘ CANCELLED
```

`APPROVED` means the business case was accepted; it says nothing about whether
requirements are being gathered, the design is being shaped, or the build has
started. "Design" is not a member, and adding it to this enum would conflate two
axes that fail independently — an Initiative can be `IN_FLIGHT` (delivery underway)
while its design is still being shaped, and `APPROVED` while nothing has begun.

`LifecycleStatus` exists in `enterprise_structure.yaml`, but it is for enterprise
constructs (Products, Systems) and carries the same problem: it is a status, not a
delivery stage.

### The shape this probably wants

A **second axis**, not a wider enum:

```
Initiative.status   : InitiativeStatus   — is this authorised?      (approval)
Initiative.phase    : InitiativePhase    — where is the work?        (delivery)
```

Proposed phases, deliberately few and deliberately named after the platform's own
stages rather than a vendor's:

```
DISCOVERY → REQUIREMENTS → DESIGN → BUILD → VERIFY → DELIVERED
```

### Decisions this item has to make

1. **Property or transition history?** The graph is assertion-based, so a phase
   change is naturally an assertion (`initiative --phase--> "DESIGN"`) carrying
   provenance, `asserted_at`, and the actor who moved it. The current phase is the
   latest active one. That gives history, attribution and audit for free, and makes
   "who moved it and when" answerable — which a mutable field cannot do. The cost is
   that every reader must resolve "latest", which is a shared query primitive that
   does not exist yet.
2. **Who owns the transition?** If the external system (Jira) owns phases, SEA's
   copy is a cache and the event is a *statement* to be reconciled — including the
   case where the external workflow is later re-opened. If SEA owns phases, the
   event is a *request* and needs an authority check. These produce different
   handlers for the same webhook.
3. **How an external phase maps.** Jira statuses are per-project and configurable,
   so the mapping is necessarily a table per source
   (`integration_phase_map`), not a rule. An unmapped status must be recorded
   verbatim rather than guessed at — the same posture the rest of ingest takes with
   vocabulary it does not know.
4. **Whether the phase is a `string` or an enum.** An enum keeps the graph clean
   and loses the external value; a string keeps the external value and loses the
   guarantee. The precedent in this repo (`covered`, extraction statuses) is to keep
   an unrecognised value rather than reject it, which argues for an enum plus an
   `external_phase` literal.
5. **What the phase gates.** If `REQUIREMENTS → DESIGN` is the trigger for
   YB-035, then the phase change is also where the readiness check belongs: refuse
   to advance on a `PARTIAL` or unauditable REQ-G, or advance with a warning. The
   design note argues the graph should be the gate and the event merely advisory.

### Acceptance

- An Initiative's approval status and its delivery phase are independently
  readable and cannot overwrite each other.
- A phase change records who or what moved it, when, and under which event.
- An external phase SEA does not recognise is preserved and visible, not dropped.
- Moving backwards is possible and legible (a design phase can be re-opened).
- The current phase is a query any view can call, defined once.

### Related

- [YB-033](../entries/YB-033-event-ingress.md) — the event that changes the phase.
- [YB-035](../entries/YB-035-design-assistant.md) — the work the phase gates.
- [YB-011](../entries/YB-011-domain-ontology-layer.md) — the other ontology-layer
  item; both change the schema and both need the loader's overlay/versioning story.
