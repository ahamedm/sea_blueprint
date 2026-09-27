# Workspace and system-of-record enablers — plan

> Implementation plan for [YB-042](../todos/entries/YB-042-workspace-structure.md)
> (workspace) and [YB-043](../todos/entries/YB-043-system-of-record.md) (system of
> record), with the Valkey run-journal enabler for
> [ADR-0026](../decisions/ADR-0026-run-journal-and-progress-transports.md).
> Schema detail: [`graph-store-schema.md`](graph-store-schema.md).

---

## 1. What an enabler is here

An **enabler** is an interface that lands now with a working reference backend, so the
backend can be replaced later without the callers noticing. Three of them:

| Enabler | Interface | Reference backend | Target backend | Item |
|---|---|---|---|---|
| **Scoping** | `Workspace` + resolver | directory tree | same | YB-042 |
| **System of record** | `Store` protocol | files (today's `RevisionStore`) | SQLite → PostgreSQL / MariaDB | YB-043 |
| **Run journal** | `RunJournal` protocol | `NullJournal` | Valkey Streams | ADR-0026 |

The rule for each: **the interface is the deliverable; the backend is configuration.**
A new backend is a new implementation plus a contract test, never a change to the
callers.

## 2. The ORM question, measured

"An ORM adds latency" is asserted often enough to be treated as fact. It is
measurable for *this* workload, which is unusual and worth stating: the app loads a
whole scope's graph and computes projections in Python, so the cost that matters is
**one full-table read and hydration**, not a per-row round trip.

`scripts/bench_store_backends.py`, this machine, a table shaped like `assertion`
(11 scalar columns plus a JSON blob):

| Loader | 5,000 rows, median | per row | vs raw |
|---|---|---|---|
| raw `sqlite3` → `list[dict]` | **6.9 ms** | 1.39 µs | 1.00× |
| SQLAlchemy **Core** → mappings | **12.3 ms** | 2.46 µs | 1.77× |
| SQLAlchemy **ORM** → objects | **27.7 ms** | 5.55 µs | 4.00× |

And the baseline it would be replacing — today's whole working-set load, measured on
`data/sea-deepseek/working.json` (587 KB, 148 nodes, 635 assertions, JSON parse plus
`graph_from_dict`): **1.4 ms**.

### What the numbers actually say

At the current scale the ORM would cost roughly **3.5 ms** per full load against
today's 1.4 ms — noticeable, not fatal. At 10× the graph (~6,000 assertions) it is
~35 ms ORM against ~9 ms raw vs a JSON parse that grows too.

**The ORM is not the interesting cost.** The interesting cost is that `state()` reloads
and rehydrates the whole graph on *every request*
([`system-of-record.md`](system-of-record.md) §2). A per-scope cache keyed by the
version token removes that entirely, and when it does, 20 ms of hydration stops
mattering. Optimising hydration while keeping the per-request reload is optimising the
wrong term.

### Pros and cons

| | Raw DBAPI | SQLAlchemy Core | SQLAlchemy ORM |
|---|---|---|---|
| Portability SQLite → Postgres/MariaDB | hand-written shim per dialect | **dialect handled and tested** | dialect handled |
| Hydration cost | lowest | 1.77× | 4.00× |
| Identity map / change tracking | n/a | none | present — the main cost, and a source of surprising writes |
| N+1 risk | none | none | real if relationships are traversed lazily |
| Two models to keep in sync | no | no | yes — ORM classes *and* `core/knowledge/model.py` dataclasses |
| Migration tooling | none | none | Alembic is the usual companion |
| Schema size here | ~9 tables, key + JSON | same | same |

**Recommendation: SQLAlchemy Core, not the ORM.** Portability is the actual
requirement and it is a Core feature; the ORM's additional 2.2× buys mapping
ergonomics we do not need, because the canonical model already exists as dataclasses in
`core/knowledge/model.py`. Keeping one model avoids re-creating the defect the repo
already fights — two shapes of the same information drifting apart is the documented
cause of YB-023.

**And it must be declared.** SQLAlchemy is currently installed only as a transitive
dependency; relying on that is fragile. Either declare it or use raw DBAPI. Given
portability is the requirement, declare it.

## 3. The `Store` interface

`RevisionStore` already defines the shape; the protocol names it so a second backend
can be checked against it. Methods, unchanged in meaning:

```
ensure()                       load_working() / save_working() / discard_working()
has_working()                  list_revisions() / get_revision() / latest() / baselines()
load_revision()                commit() / freeze()
diff() / diff_against_revision() / diff_against_working()
```

Callers are `app/__init__.py`, `agents/cli.py`, `scripts/reconcile.py`,
`scripts/run_real_ingest.py` and the harness — none of them should have to know which
backend is in use.

**The write path is where the defect dies.** Every mutation becomes one
version-guarded transaction:

```sql
UPDATE working_set SET version = version + 1 WHERE scope_id = ? AND version = ?;
-- 0 rows → someone else committed first: roll back, raise StoreConflict
```

That is the fix for the live lost-update defect, independent of which database backs it.

## 4. Schema sequencing

Two slices, and the first is deliberately not the pretty one:

1. **Document shape** — `working_set(scope_id, version, graph, updated_at, updated_by)`.
   The serialised graph lives in one column; `serialise.py` and its `SCHEMA_VERSION`
   remain the contract; CAS is one statement. Smallest change that removes the lost
   update.
2. **Normalised** — `node` / `assertion` / `run` / `decision` per
   [`graph-store-schema.md`](graph-store-schema.md), for the workspace index and audit
   filters YB-042 introduces.

Slice 1 is portable to slice 2 row-by-row (the graph blob can be decomposed once), and
slice 1 is what makes concurrency correct immediately.

## 5. The workspace enabler

```
data/workspaces/<workspace_id>/
  workspace.yaml          # name, scopes, ontology pins, domain packs, brief path
  scopes/<scope_id>/      # a system or product: exactly today's store layout
```

- `Workspace` — id, name, scopes, and the **brief** path (the enterprise one-pager,
  YB-047).
- `resolve(workspace_id, scope_id)` → a `Store`. The store backend is chosen by the
  manifest, so a workspace can migrate one scope at a time.
- `SEA_DATA_DIR` keeps working: a bare directory is read as a single-scope workspace,
  so existing stores load unchanged.

## 6. The run-journal enabler

`RunJournal` protocol — `append`, `read(run_id, since)`, `close(run_id, ttl)` — with
`NullJournal` for tests and CLI, and `ValkeyJournal` over a Valkey **Stream** per run.
Streams, not Pub/Sub: a late subscriber must receive the backlog, which is the whole
reason [ADR-0026](../decisions/ADR-0026-run-journal-and-progress-transports.md) exists. Payloads
carry state **transitions**, never extracted content, and the terminal event carries
the completeness verdict. The client is imported lazily so the module imports without
the dependency.

**Testing with TestContainers.** Docker is available on this machine. The Valkey tests
start a container and assert ordering, replay-from-id and TTL, and they **skip** when
`testcontainers` or the client is absent so the default suite stays runnable. The same
approach covers a PostgreSQL backend later — one container start per session, reused
across tests.

## 7. Dependencies

Declared as extras rather than core, so the MVP install stays small. The names below
are what `pyproject.toml` now carries:

| Extra | Packages | Enables |
|---|---|---|
| `sor-sql` | `sqlalchemy>=2.0` | the SQL system of record (SQLite is stdlib) |
| `sor-postgres` | `psycopg[binary]>=3.1` | the PostgreSQL target |
| `valkey` | `redis>=5.0` | the Valkey run journal (`core/events.py` names this extra in its ImportError) |
| `dev` (extended) | `+ testcontainers[redis]>=4.0` | container-backed tests for Valkey, and PostgreSQL later |

Neither backend is imported at module scope: `import core.knowledge` must not require
SQLAlchemy, and `import core.events` must not require a Redis client. Both are asserted
by tests.

## 8. Slices

| # | Slice | Status |
|---|---|---|
| 0 | `Store` protocol + `StoreError`/`StoreConflict`; `RevisionStore` conforms | **landed** — `core/knowledge/store_api.py`, `errors.py`; `RevisionStore.concurrency_safe = False` refuses a guarded write rather than ignoring it |
| 1 | `SqliteStore` — document shape, version-guarded write | **landed** — `core/knowledge/store_sql.py`; WAL + `BEGIN IMMEDIATE` |
| 2 | `Workspace` + resolver; `SEA_DATA_DIR` still loads | **landed** — `core/workspace.py`; a bare directory is a single-scope workspace |
| 3 | `RunJournal` + `NullJournal` + `ValkeyJournal` + container tests | **landed** — `core/events.py`; the 4 container tests skip until `valkey` + `dev` extras are installed (Docker is present, so they will run) |
| 4 | Normalised tables; workspace index without loading every graph | next |
| 5 | PostgreSQL/MariaDB backend through the same contract tests | next |

Slices 0–3 were this enabler's remit. Two things are deliberately NOT done yet:

- **Nothing is wired into `app/__init__.py`.** `create_app` still resolves one
  `RevisionStore` root directly. Switching it to `load_workspace(...).open_store(...)`
  is a small change, but it is the commit that makes existing deployments depend on the
  new path, so it should land with the SQL migration rather than before it.
- **Reads are not cached.** `SqliteStore.load_working` hits the database every time. A
  per-scope cache keyed by the version token is the fix for the per-request reload
  identified in §2, and it belongs to the workspace layer — not hidden inside a backend.


## 9. Acceptance for the enabler

- `RevisionStore` and `SqliteStore` both satisfy `Store`, proven by one contract test
  run against both.
- Two writers on one scope: both decisions survive as an explicit conflict, never a
  silent overwrite.
- A workspace resolves a scope to a store; a bare `SEA_DATA_DIR` is still a valid
  single-scope workspace.
- `RunJournal` replay returns exactly the events a late subscriber missed, in order.
- `import core.knowledge` must not require SQLAlchemy, and `import core.events` must
  not require a Redis client — the extras are optional by construction.

## 10. Traversal — measured, because the premise does not survive it

"Add networkx" reads like a performance win, and `networkx>=3.2` is already a declared
dependency used nowhere. So it was measured before it was planned
(`scripts/bench_traversal.py`). It is **not** a performance win. The win is an index,
and an index needs no library.

Synthetic scope of 5,000 nodes / 9,995 assertions, indexes built once and cached:

| Operation | Today's shape | Cached dict index | networkx |
|---|---|---|---|
| build the index, once per scope load | — | **2.0 ms** | 8.1 ms (4.0×) |
| build the `part_of` index only | — | **0.3 ms** | 2.4 ms (7.4×) |
| 500 neighbour lookups | 8.4 ms | **~0 ms (2795×)** | ~0 ms (1128×) |
| 50 transitive containment walks | 31.4 ms | **14.9 ms (2.1×)** | 119.4 ms (**0.3× — slower**) |

The same shape holds at 1,000 nodes and on the real store, so it is not a size artefact.

**What this says.** Every repeated traversal wants an adjacency index built once per
scope load — that is the 30× to 3,000× win, and a plain `dict[str, list]` beats
`nx.DiGraph` on both build and query. `nx.descendants` is *slower* than a tight cached
BFS because of its per-node machinery.

**So networkx's value here is capability, not speed:** tested `ancestors`/`descendants`,
`shortest_path`, `topological_sort`, `find_cycle`, `connected_components`. Those are the
algorithms a hand-rolled walk gets subtly wrong. Use it for those, over an index that is
already built — not as the index itself, and never on the whole-graph projection path,
which is a single pass a library cannot improve.

**And a correction worth keeping.** The first version of this benchmark reported
networkx as 614× faster at transitive containment. It had built the `part_of` graph as
`<child> -> <parent>` and then asked for `descendants(root)`, which explores an almost
empty direction and returns nothing very quickly. A traversal benchmark that does not
check the direction of its own edges measures nothing.

## 11. Caching at scale — what in-process memory cannot do

§2 and YB-048 both said "cache indexed state per scope, keyed by the version token".
That was **underspecified**, and at the stated scale (50 architects, 200 products) it is
wrong as written. The correction is not "use a different cache"; it is that different
artifacts need different treatment, and the deciding property is **mutability**.

### What one scope costs in memory

Measured with `tracemalloc`, hydrating a serialised graph:

| Scope | JSON | In memory | Assertions |
|---|---|---|---|
| real store (`data/sea-deepseek`) | 429 KB | **0.27 MB** | 635 |
| synthetic | 1.8 MB | **1.9 MB** | 4,000 |
| synthetic | 8.9 MB | **9.3 MB** | 20,000 |

In-memory is roughly 1.1× the JSON at size. So caching **every** scope of 200 products
at 20,000 assertions each is ~1.9 GB in one process — and ~15 GB across eight workers,
each holding its own copy. That is the wall, and it is a function of scope *size*, not
scope *count*: at today's 635 assertions it is 54 MB for all 200, which is nothing.

### The reframing: cache by artifact class

| Artifact | Mutability | Bound by | Where it belongs |
|---|---|---|---|
| **Working set** | mutable, one writer (guarded) | **concurrent editors** — tens, not hundreds | in-process LRU per scope, invalidated by the version token |
| **Frozen revisions** | immutable | product count × size | shared store (Valkey) and/or object storage — **never invalidated** |
| **Derived projections** of a frozen revision | immutable | the same | shared, keyed by revision id — a permanent entry |
| **Aggregations, workspace listings** | derived | — | a read model in SQL, not a cache |

Two consequences fall out of the table:

1. **The working-set cache is bounded by concurrency, not by product count.** Only scopes
   actually being edited are hot — tens of them. In-process is the right home, and fifty
   architects × a few MB is comfortable.
2. **Baselines are immutable, so they need no invalidation at all.** They can be cached
   *outside* the process, shared by every worker, replicated, and never
   coherence-checked. A cache with no invalidation problem is a very different thing from
   the one YB-048 implied, and it is where the large, safe win lives.

### And the CPU case is weaker than it looks

At 20,000 assertions a full SQL load is ~28 ms raw, ~49 ms through Core. Fifty architects
at one page per five seconds is ~10 requests/second, so **no cache at all** costs ~0.5
CPU-seconds per second — half of one core. The cache is an optimisation, not a saviour,
and the first fix for page cost is to **stop loading the whole graph to render a slice**
(the normalised tables, slice 4) rather than holding every graph in RAM.

### Three gaps this exposes

1. **There is no cheap version probe.** `SqliteStore.load_working()` hydrates the entire
   graph just to read `meta["version"]`. A cache needs a one-row `current_version(scope)`
   on the `Store` protocol, or every cache hit pays a full load — which is not a cache.
2. **No bound is specified and no hit rate is measured.** An LRU needs a size cap and a
   hit-rate metric from the start. A cache with a low hit rate is not a cache problem, it
   is an access-pattern problem, and it should be deleted rather than tuned.
3. **Cross-instance coherence is unsolved.** An in-process cache is per worker; nothing
   shares it. For mutable state that is acceptable — the version token is the correctness
   mechanism, not the cache. For anything shared, the answer is a shared store or a read
   model, not a bigger local dictionary.

**A cache is never the correctness mechanism here.** The version token is. Any cache must
be able to prove it is current by reading one indexed row, or it must not serve the read.

## 12. The app is wired to the workspace — what changed and what did not

`create_app` no longer resolves one `RevisionStore` root. It loads a **workspace**, and
a request resolves a scope:

- `current_scope_id()` — `?scope=` first, then the session, then the configured default,
  then the first scope. A **single-scope workspace needs no selection**, so every
  existing `data/sea` deployment behaves exactly as before; that is the migration path
  and it is tested.
- `current_store()` — one store per scope, built once and reused, because building a SQL
  engine per request would open a connection pool per page view.
- `save(snapshot)` passes `meta["version"]` back as `expected_version`, but only when
  `store.concurrency_safe`. On SQLite a stale version raises `StoreConflict`; the file
  backend has no version, so the guard is *absent* there rather than pretended.
- `@app.errorhandler(StoreConflict)` turns that into a redirect and a flash — **"nothing
  was overwritten"** — because both alternatives are worse: a 500 tells the reviewer
  nothing, and a silent overwrite is the defect the token exists to prevent.
- `current_drafts()` stages design proposals **per scope**, so a proposal drafted against
  one system can never be applied to another.
- `GET /api/workspace` reports the scopes, the current one, and each backend's
  `concurrency_safe`; `GET /scope/<id>` switches scope for the session. The listing asks
  the *backend* whether it can guard a write rather than opening a store to find out —
  a listing endpoint must not create a database as a side effect of asking a question.

Two things deliberately still open:

1. **No template change yet.** `inject_globals` now exposes `workspace`, `scope_id`,
   `scope_name` and `scopes`, so a page *can* render a switcher and say which world it is
   showing — but it does not yet. Until it does, a multi-scope workspace falls back to
   its first scope silently, which is exactly the hazard the exposure is there to fix.
2. **A conflict discards the change rather than merging it.** Correct for a review
   decision (reapply it), questionable for a twenty-minute extraction whose save lands
   after someone else's — that needs the retry/rebase story, not a flash message.

The test that caught a real bug worth noting: `SqliteStore.ensure()` never created its
parent directory, so the first write to `scopes/<id>.sqlite` failed with "unable to open
database file" instead of creating the store. The workspace layout put a scope's database
somewhere that did not exist yet.

### The journal, wired — and the one join still missing

The producer chain is closed and tested end to end
(`tests/test_run_progress_wiring.py`):

- `run_passes(..., progress=...)` emits `PASS_STARTED` / `PASS_FINISHED` (or `ERROR`) with
  transition-only payloads. A raising sink is caught and logged, so a dead subscriber
  cannot fail a run.
- `agents/extraction/progress.JournalProgress` stamps the envelope and owns the monotonic
  `seq`; `finished(completeness)` takes the verdict **positionally**, so a run cannot end
  on this channel without stating whether it was `COMPLETE`, `PARTIAL` or `UNKNOWN`.
- Both extraction profiles forward `input_data["progress"]` to `run_passes`.
- `graph_from_extraction` now accepts a **caller-minted `run_id`** in metadata. Necessary
  rather than cosmetic: `_run_id` includes `utc_now()`, so a caller cannot predict it, and
  events published *during* extraction could never be correlated with the run record
  created *afterwards*.
- `GET /api/runs/<id>/events?since=` reads the journal back, `since` being an exclusive
  stream cursor. The default is `NullJournal` — no Valkey, no dependency, no error — and a
  broken journal degrades to **503**, because a page that cannot show live progress is a
  smaller problem than a page that cannot load.

**What is still missing: `/ingest` does not create a sink yet**, so a real run publishes
nothing. The join is a few lines — mint a run id, build the sink, pass it to the
extractor, publish `finished(run.completeness)` once the record exists — but it belongs
with the asynchronous-progress work in
[YB-026](../todos/entries/YB-026-asynchronous-progress.md): wiring it inside today's
synchronous request buys a user nothing they can see, and the point of the whole channel
is to stop the request blocking on the run.
