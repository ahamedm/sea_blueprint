---
id: YB-018
legacy: "18"
title: "Review batches — scope the review gate to a run, without fragmenting the graph"
status: open
priority: high
area: "`core/knowledge/store.py`, `app/projections.py`, `app/__init__.py`, `core/knowledge/review.py`"
created: 2026-09-23
updated: 2026-09-24
design: docs/design/review-batches.md
record: null
superseded_by: []
related: ["YB-005", "YB-009", "YB-011", "ADR-0015"]
blocks: []
blocked_by: []
---

# YB-018 — Review batches — scope the review gate to a run, without fragmenting the graph

> **Open work.** This file is the source of truth for this item; `TODO.md` is generated from it.

**Legacy status:** Not started — **design sketch, not yet reviewed**
**Legacy priority:** Medium-High
**Legacy area:** `core/knowledge/store.py`, `app/projections.py`, `app/__init__.py`, `core/knowledge/review.py`

**Full analysis:** [`docs/design/review-batches.md`](../../design/review-batches.md)

The complete write-up for this item lives in the design document above, preserved verbatim from `TODO.md` v1 (YB-018).

---

### Added by ADR-0015 (retiring extracted facts)

ADR-0015 added a single-fact `Remove` action and a `run` scope in the review
filters — a first, narrow slice of what this item designs. Two pieces are
deliberately still here rather than there:

- **Bulk removal.** Retiring many facts at once is the same shape as bulk verify,
  and it is the action a reviewer actually wants when an extractor produced 200
  invented triples. Exclusions (ADR-0015) hide them from the queue; this would
  remove them from the graph in one act, with one audit entry.
- **The fuller run scoping.** The `run` filter answers "which run did this fact come
  from?"; this item is about scoping the whole gate to a batch, which is a different
  and larger design.
