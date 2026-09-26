# System of record — the store concurrent architects edit

> **Design document** for `YB-043`. Status is tracked in that entry.
> The partition question is [YB-042](workspace-structure.md); this is the write
> and coherence question behind it.

---

## 1. Why this is now its own decision

The workspace layout decides how work is **partitioned**; this decides how it is
**stored and written**. The store choice was deferred to
[YB-037](../todos/entries/YB-037-background-workflow-management.md) with the note
that *"a database is a deployment decision that should be made once, explicitly."*
Scaling the workspace to an enterprise makes that decision load-bearing: 50
architects editing 200 products is a multi-writer workload, and the current store
is single-writer by construction.

The trap to avoid is choosing a store for its **query language** and then
discovering it cannot do concurrent editing — or the reverse, choosing an editing
store and expecting it to answer graph queries.

## 2. What the current store is

| Property | Today |
|---|---|
| Layout | `<root>/working.json`, `index.json`, `revisions/<id>.json`, `drafts/` |
| Write | `_write_atomic` — temp file + `os.replace` (`core/knowledge/store.py:149`) |
| Locking | **none** |
| Index update | **read-modify-write** (`_append_index`, `store.py:338`) |
| Read cost | whole graph parsed per request (`state()` → `load_working()`) |
| Write cost | whole graph rewritten per mutation (`save_working`) |
| Revisions | immutable snapshots — the one part already fit for purpose |
| Working set | one mutable graph, shared by every reviewer |
| Identity | `reviewer()` — form field, then query arg, then config |

Measured payloads: `data/sea-deepseek/working.json` 586,917 bytes / 148 nodes /
635 assertions; `data/sea/working.json` 427,768 bytes / 96 nodes / 403 assertions.

**Concurrency is already live.** `app.run` sets `threaded=True` by default
(Flask 3.1.3; `run.py:18`), so the current server serves requests concurrently
against this unlocked store. Two reviewers acting on one product can lose a
decision today; at enterprise scale, with several app instances, the same file is
written by several processes.

## 3. The two decisions, kept separate

1. **System of record** — the store humans edit. Must be transactional and
   multi-writer. The natural answer at 50 writers is relational.
2. **Query and audit representation** — what the audit and cross-product
   reasoning read. Stays RDF via `rdflib` (`YB-010`); a triple *store*
   (Jena/TDB2/Fuseki) earns its place only when graph queries outgrow memory, and
   `SPARQLWrapper` already bridges to it.

The audit layer **reads from** the system of record; it does not become the record.

## 4. What the store must guarantee

- **No lost updates.** Optimistic concurrency (a version/etag per graph, checked
  on write) or real transactions. This is the defect that exists now.
- **Cross-product isolation.** One product's write must not block or corrupt
  another's — the property YB-042's layout already gives.
- **A workspace read model.** Listing 200 products with baseline state and
  outstanding review must not mean loading 200 graphs.
- **Identity and audit.** Every decision carries an authenticated principal; the
  review log is append-only and attributable.
- **Immutability of revisions.** Content-addressed snapshots stay snapshots.
- **Recovery.** The working-set → revision → review-log sequence (three writes,
  YB-037) either becomes one transaction or gets an explicit recovery rule.
- **Migration.** Existing `data/sea*` stores must load. `serialise.py` and its
  `SCHEMA_VERSION` are the stable seam for that.

## 5. Options

| Option | Concurrency | Cross-product | Ops cost | Verdict |
|---|---|---|---|---|
| Files (today) | none | N stores, N reads | zero | adequate for one writer only |
| SQLite + WAL, one file per product | one writer per DB, ACID, busy-timeout | attach/query across files | zero | **credible first slice**; hazard on network filesystems |
| PostgreSQL | row-level, transactions, roles, LISTEN/NOTIFY | native, `product_id` or schema-per-product | one deployment | **the enterprise answer** |
| RDF store (Jena/TDB2, Fuseki) | TDB2 single-writer; Fuseki adds a service | SPARQL across graphs | JVM service | query layer, **not** an editing store |
| Property graph (Neo4j et al.) | transactions | native traversals | new dependency | attribute-carrying edges fit, but another stack |

### Why not an in-memory KV store (Valkey, ElastiCache)?

This comes up for portability — "deploy on AWS, use ElastiCache" — so it deserves a
direct answer, and the answer has moved recently.

**Durability is no longer the objection.** Amazon ElastiCache for Valkey now
supports durability through a Multi-AZ transactional log: synchronous writes give
zero data loss at single-digit-millisecond write latency, asynchronous writes keep
microsecond latency with up to 10 seconds at risk, and reads stay microseconds
([AWS docs](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/durability.html)).
It is **node-based ElastiCache only — not Serverless**. On-prem, AOF with
`appendfsync always` plus replication gives a comparable guarantee at more
operational cost.

**The real objections are query shape, cost, and the portability premise itself.**

- **Query shape.** SEA's hot access pattern is "load one product's graph whole and
  compute projections in Python" — which suits a key-per-product blob well. But
  the workspace layer wants an index ("200 products with baseline state and
  outstanding review") and the audit wants filtered scans. In a KV store every
  access path is a structure maintained by hand (sets, sorted sets, or a Search
  module whose availability differs per managed offering). That is re-implementing
  the query planner.
- **Cost.** The dataset must fit in RAM. Immutable revisions are unbounded
  history, so they belong in object storage, not a memory-bound store — which
  means the architecture gains a component rather than losing one.
- **No rollback.** `MULTI`/`EXEC` queues but does not roll back on a runtime error;
  compare-and-swap is `WATCH`/Lua. The working-set → revision → review-log unit
  becomes your own invariant instead of a transaction.
- **Portability runs the other way.** ElastiCache is AWS-only. Valkey's protocol
  is portable, but the *managed offerings differ exactly where it matters*:
  ElastiCache's durability is node-based with two write modes; Memorystore for
  Valkey exposes AOF persistence plus JSON and vector search
  ([GCP docs](https://docs.cloud.google.com/memorystore/docs/valkey/about-aof-persistence));
  Azure Managed Redis differs again. Porting therefore means targeting the lowest
  common denominator — which excludes the guarantees you chose it for.
  **PostgreSQL is the portable managed choice**: the same engine on RDS/Aurora,
  Cloud SQL/AlloyDB, Azure Database for PostgreSQL, and on-prem.

**Where Valkey does earn its place** — beside the record, not as the record:
progress streaming and pub/sub ([YB-026](../todos/entries/YB-026-asynchronous-progress.md),
[YB-036](../todos/entries/YB-036-modular-run-streaming.md)), the thin job queue
[YB-037](../todos/entries/YB-037-background-workflow-management.md) describes
(Streams + consumer groups), a distributed lock or CAS for the working set, and a
cached workspace read model.

## 6. Recommended path

1. **Introduce a store interface** behind `RevisionStore` so the backend is
   swappable, with `serialise.py`'s graph dict as the boundary. No domain model
   change; the workspace layout is unaffected.
2. **Slice 1 — SQLite/WAL per product.** Closes lost updates, gives transactions
   and a real index, keeps zero-ops and per-product isolation. Sufficient for a
   single-instance deployment and a handful of architects, and it makes the
   concurrency defect disappear rather than merely rarer.
3. **Slice 2 — PostgreSQL** when there is more than one app instance or sustained
   contention within a product. Schema keyed by `product_id`; revisions and the
   review log become ordinary tables; the working set is versioned per product.
4. **Keep the RDF/audit layer materialised from the record.** Revisit a triple
   store only when cross-product graph queries need it.
5. **Treat Valkey as an accelerator, not a backend.** Where a deployment wants it,
   it serves streaming, queues, locks and a read-model cache *beside* the record.
   It replaces neither slice 1 nor slice 2, and it is not the portability play —
   PostgreSQL is.

## 7. What this does not decide

- Whether nodes are shared across products — [YB-042](workspace-structure.md) §8.
- The audit language (SHACL vs SPARQL) — `YB-010`.
- The workflow engine — YB-037.

## 8. Signals the choice was right

- Two concurrent reviewers on one product: both decisions survive; the second sees
  a conflict rather than a silent overwrite.
- A workspace lists 200 products with baseline and review state without loading
  200 graphs.
- A crash mid-apply leaves either the old or the new state — never a half-merged
  working set.
- Migrating one product's store does not require migrating the workspace.

## 9. Related

- [YB-042 — Workspace structure](workspace-structure.md) — how work is partitioned.
- [YB-037 — Background workflow management](../todos/entries/YB-037-background-workflow-management.md)
  — job state needs the same transaction.
- [YB-010 — RDF knowledge layer](../todos/entries/YB-010-rdf-knowledge-layer.md)
  — the query/audit half.
- [YB-018 — Review batches](../todos/entries/YB-018-review-batches.md) — a batch is
  the natural transaction unit.
