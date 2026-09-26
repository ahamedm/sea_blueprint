---
id: YB-046
legacy: null
title: "Branching and guarded promotion — two architects on one initiative, and the merge between them"
status: open
priority: high
area: "`core/knowledge/store.py`, `core/knowledge/model.py`, `core/knowledge/review.py`, `core/knowledge/ingest.py`"
created: 2026-09-26
updated: 2026-09-26
design: docs/design/branching-and-promotion.md
record: null
superseded_by: []
related: ["YB-042", "YB-043", "YB-018", "YB-037", "ADR-0015"]
blocks: []
blocked_by: []
---

# YB-046 — Branching and guarded promotion

> **Open work.** This file is the source of truth for this item; `TODO.md` is generated from it.

**Full analysis:** [`docs/design/branching-and-promotion.md`](../../design/branching-and-promotion.md)

### The requirement

Two architects work the same Initiative. Each produces an updated ARC-G, and one is
promoted into the baseline under a guard — possibly a process or an offline approval.

### Model B does not remove this

Model B (`workspace-structure.md` §11) removes the **cross-store hop** by
co-locating a system's baseline and its branches. It does **not** remove the
**version guard or the merge** — those are where the difficulty actually is, and this
item is about them. `promote_to_baseline` today is synchronous and flips `scope` in
place (`core/knowledge/review.py:616`), which has no meaning once two branches
disagree.

### What the design covers

- **A branch dimension** in the store: `branch(system_id, branch_id, kind, base_ref,
  owner)` with `working_set` and `revision` keyed per branch, so the CAS token is per
  branch rather than per system. Two architects may share a branch (conflict on
  write) or fork personal branches (merge).
- **Three-way merge** from the fork point, where two facts of the identity scheme do
  most of the work: identical additions share a content-addressed assertion id and
  converge silently, and nodes are `kind:label` slugs so node merge is set
  arithmetic. The residue is semantic — divergent values, changed-vs-retired, close
  confidence — which is exactly what the review gate should adjudicate.
- **Promotion as a request**, not a button: `promotion_request` carrying source
  branch and revision, target and `expected_version` (the version guard), the delta
  summary and conflicts, and a `state`. The automated guard already exists —
  `BaselineNotReady` refuses to freeze with unverified or disputed assertions; the
  human approval can be asynchronous/offline, which is the `AWAITING_REVIEW` state
  [YB-018](YB-018-review-batches.md) and [YB-037](YB-037-background-workflow-management.md)
  already anticipate.

### The sharp edge — identity is the label

Node ids are the label (`make_node_id`, `model.py:62`) and assertion ids hash the node
id (`:72`), so identity is the label transitively. Both an explicit rename **and**
ordinary cross-run label drift ("Payment Orchestrator" → "Payment Orchestration
Service") produce a new node and orphan the verified state of everything attached to
the old one.

Two options, explained in the design doc:

- **A — surrogate node ids (recommended).** Assign the id once at creation and never
  recompute it; `(kind, label)` becomes a mutable unique key, so a rename is one
  `UPDATE` and every assertion id survives.
- **B — a rename map.** Keep derived ids and rewrite ~8 id-bearing places on rename.

**The deciding fact is that revisions are immutable.** B cannot migrate them, so its
rename map becomes permanent read-time indirection over all history, and any code path
that forgets to consult it silently reports the old identity. A keeps historical
revisions self-consistent with no indirection.

Cost of A: `add_node` can no longer rely on id collision for cross-run convergence —
it needs a `(kind, label)` index and a look-up before create. The seed stays
deterministic, so fresh builds are still reproducible; only reassignment is
forbidden. This is a `SCHEMA_VERSION` change, so it must land **before** there is data
worth migrating.

### Acceptance

- Two branches on one initiative produce a conflict report, never a silent overwrite.
- A fact both branches added converges without being reported as a conflict.
- Promotion fails cleanly when the target baseline has moved.
- An approval can be recorded later by a different actor, preserving the request,
  its conflicts and its counts.
- A relabel does not orphan the review state of every assertion on that node.
