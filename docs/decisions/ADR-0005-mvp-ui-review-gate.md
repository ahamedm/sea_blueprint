---
id: ADR-0005
legacy: "13"
title: "MVP UI — extraction projection, review gate, change management"
status: accepted
date: 2026-09-22
area: "app/, core/knowledge/"
related: []
---

# ADR-0005 — MVP UI — extraction projection, review gate, change management

> **Record.** Closed work, preserved verbatim from `TODO.md` v1 (ADR-0005).
> Legacy source: [`docs/todos/legacy-todo-v1.md`](../todos/legacy-todo-v1.md).

**Legacy status:** ✅ IMPLEMENTED (first slice of the journey) — see [`docs/ui-review-workflow.md`](docs/ui-review-workflow.md)
**Legacy priority:** Closed for this slice; follow-ups below
**Legacy area:** `app/`, `core/knowledge/` (`serialise.py`, `review.py`, `store.py`), `tests/`

---

### What this closes

`docs/user-journey.md` §3: *"extraction output has nowhere to go once produced.
The pipeline's only exit is a JSON file."* It now has somewhere to go — a
projection to judge, a write path to record judgement, and revisions to judge
against. Three of the journey's five blocking gaps (1–3) are addressed:

| Journey gap | Status |
|---|---|
| 1. View projection layer | ✅ `/`, `/review`, `/graph`, `/gaps`, `/changes`, `/changes/diff` |
| 2. Correction write path | ✅ verify / correct / dispute / reopen + audit trail |
| 3. Revision / baseline | ✅ working set vs immutable revision vs frozen baseline |

Still open, in dependency order: **4. Reconciliation** (YB-005) and **5. Audit
engine + gap report** (YB-009) — `/gaps` does the structural half only.

### Bugs fixed

1. **Ingest produced an empty graph and reported success.** `app/__init__.py`
   passed `result.model_dump()` (the `AgentResult` envelope) to
   `graph_from_extraction`, which reads `triples`/`elements` from the top level
   and therefore found nothing. Now unwraps `result.output`. A characterisation
   test pins the failure mode.
2. **Document type was read and ignored.** The form collected `type` and always
   ran the requirements extractor. Now selects the REQ-G or ARC-G agent.
3. **`/graph` and `/gaps` were nav links with no routes** — hard 404s. Both now
   exist, plus `/changes` and `/changes/diff`.
4. **Graph state was a module global.** Every decision was lost on restart and two
   workers could not agree on what the graph was. Now persisted per request.
5. **`/graph` would also have 500'd** — the nav called `url_for('graph')` while the
   endpoint was `graph_view`. The route is now `/c4` (endpoint `c4`) with `/graph`
   kept as a redirect: "graph" in this project means the knowledge graph, so the
   URL was itself part of the projection/viewpoint confusion recorded below.

### New knowledge-layer modules

- `serialise.py` — field-complete JSON round trip. The architecture review called
  this transform the highest-risk component and required a named part with tests.
  It did not exist; nothing could persist a graph.
- `review.py` — decisions, audit trail, `ReviewProgress`, baseline promotion.
  Correction is **supersession, not mutation**, so lineage survives.
- `store.py` — working set / revision / baseline, with `BaselineNotReady` as the
  freeze gate. Ordering is by insertion, not `created_at` (second precision would
  order same-second commits arbitrarily).
- `ingest.merge_graphs` — re-extraction **merges** rather than replaces, so human
  corrections survive a re-run (architecture-review §3.3, the "sleeper").

### Verification

`tests/` was empty; it now holds 246 tests covering the knowledge layer, the
projections and every route, running against an injectable fake extractor so the
suite needs no model server. All routes verified 200 against a real booted
server.

### Follow-ups (deliberately not in this slice)

1. **Reconciliation at scale** — see ADR-0006. `/reconcile` binds to existing nodes
   only: it does not create a missing target, does not invert the direction, and
   matches lexically.
2. **Semantic audit** — all checks are structural. "Does this design answer this
   requirement?" is unimplemented.
3. **Authentication / multi-user review** — one reviewer identity from config.
   Open question 1 in the journey (is Reviewer/Auditor a distinct persona?) is
   still unanswered, and it decides whether review is inline or a queue.
4. **Concurrent writers** — reads/writes hit disk per request. Fine for a
   single-process MVP; a multi-worker deployment needs locking or a real store.
5. **Correction merge conflicts** — supersession is sufficient while one document
   owns a graph. Item 9b returns the first time knowledge arrives from two sources.
6. **The graph view is a depiction, not a diagram editor** — no layout persistence,
   no manual arrangement, no write-back from the canvas.
