# Workspace and system-of-record enablers — plan

> Implementation plan for [YB-042](../todos/entries/YB-042-workspace-structure.md)
> (workspace) and [YB-043](../todos/entries/YB-043-system-of-record.md) (system of
> record), with the Valkey run-journal enabler for
> [YB-036](../todos/entries/YB-036-modular-run-streaming.md).
> Schema detail: [`graph-store-schema.md`](graph-store-schema.md).

---

## 1. What an enabler is here

An **enabler** is an interface that lands now with a working reference backend, so the
backend can be replaced later without the callers noticing. Three of them:

| Enabler | Interface | Reference backend | Target backend | Item |
|---|---|---|---|---|
| **Scoping** | `Workspace` + resolver | directory tree | same | YB-042 |
| **System of record** | `Store` protocol | files (today's `RevisionStore`) | SQLite → PostgreSQL / MariaDB | YB-043 |
| **Run journal** | `RunJournal` protocol | `NullJournal` | Valkey Streams | YB-036 |

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
reason [YB-036](../todos/entries/YB-036-modular-run-streaming.md) exists. Payloads
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
