---
id: YB-043
legacy: null
title: "System of record — a concurrent multi-user store for 50+ architects on 200+ products"
status: open
priority: critical
area: "`core/knowledge/store.py`, `app/__init__.py`, `run.py`, new store backend"
created: 2026-09-26
updated: 2026-09-26
design: docs/design/system-of-record.md
record: null
superseded_by: []
related: ["YB-042", "YB-037", "YB-010", "YB-018", "YB-026"]
blocks: []
blocked_by: []
---

# YB-043 — System of record

> **Open work.** This file is the source of truth for this item; `TODO.md` is generated from it.

**Full analysis:** [`docs/design/system-of-record.md`](../../design/system-of-record.md)
**Schema and MCP boundary:** [`docs/design/graph-store-schema.md`](../../design/graph-store-schema.md)
— the `node` / `assertion` / `run` / `decision` tables, the version-guarded write
path, and why modification is one coarse transactional tool rather than per-triple CRUD.

### Why it stops being deferrable

The store decision was deferred to YB-037 with the note that "a database is a
deployment decision that should be made once, explicitly." Scaling the workspace
([YB-042](YB-042-workspace-structure.md)) to 50 architects and 200 products makes
it load-bearing: that is a multi-writer workload, and the current store is
single-writer by construction.

### The defect is present, not anticipated

- `RevisionStore._write_atomic` (`core/knowledge/store.py:149`) is atomic per
  file with **no lock and no version check**, and `_append_index` (`:338`) is
  read-modify-write — two concurrent commits lose one.
- `state()` parses the whole graph on every request and `save()` rewrites it on
  every mutation (`app/__init__.py:184`). ~587 KB today
  (`data/sea-deepseek/working.json`); the cost grows with the graph.
- `app.run` sets **`threaded=True` by default** (Flask 3.1.3; `run.py:18`), so the
  server already serves requests concurrently against this unlocked store. Two
  reviewers on one product can lose a decision **today**.
- `reviewer()` reads a form field, then a query argument, then config
  (`app/__init__.py:189-192`) — with 50 architects, "who verified this" is not
  trustworthy and there is no per-user conflict detection.

### The decision, split in two

1. **System of record** — the store humans edit. Transactional and multi-writer;
   the natural answer is relational. Recommended path: put a store interface
   behind `RevisionStore` (the `serialise.py` graph dict stays the boundary), do
   **SQLite/WAL per product** as the first slice — which closes lost updates and
   fits the product isolation YB-042 already gives — and graduate to
   **PostgreSQL** keyed by product when there is more than one app instance.
2. **Query and audit representation** — stays RDF via `rdflib`
   ([YB-010](YB-010-rdf-knowledge-layer.md)). A triple store (Jena/TDB2, Fuseki)
   is a query layer, not an editing store; `SPARQLWrapper` already bridges to it.

Keeping these apart is what avoids choosing a store for its query language and
then finding it cannot do concurrent editing — or the reverse.

### Valkey / ElastiCache: accelerator, not the record

An in-memory KV store is the obvious portability instinct ("deploy on AWS, use
ElastiCache"), and durability is **not** the reason to decline it: ElastiCache for
Valkey now offers Multi-AZ durability with synchronous zero-data-loss writes
(node-based only). The reasons are query shape (every access path becomes a
hand-maintained index), RAM-bound cost for unbounded revision history, no rollback
in `MULTI`/`EXEC`, and the fact that **ElastiCache is AWS-only — so it is not a
portability play at all**. PostgreSQL is: the same engine on RDS/Aurora, Cloud
SQL/AlloyDB, Azure Database, and on-prem. Valkey earns a place for progress
streaming, the job queue, locks and a read-model cache — beside the record. Full
reasoning in the design doc.

### Acceptance

- Two concurrent reviewers on one product both survive; the second sees a
  conflict, not a silent overwrite.
- A workspace lists its products with baseline and review state without loading
  every graph.
- A crash mid-apply leaves the old or the new state — never a half-merged working
  set.
- Revisions stay immutable and content-addressed; the review log is append-only
  and attributable to an authenticated principal.
- Existing `data/sea*` stores still load, and migrating one product does not
  require migrating the workspace.
