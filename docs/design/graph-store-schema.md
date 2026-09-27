# Graph store — the MCP boundary and the relational schema

> **Design document.** The concrete follow-through for
> [YB-043](../todos/entries/YB-043-system-of-record.md): what the graph boundary
> looks like, and what the tables actually are.

---

## 1. The model as it already exists

The domain model is already relational-friendly, because identities are derived
rather than random:

| Thing | Identity | Where |
|---|---|---|
| Node | `make_node_id(kind, label)` → `"container:payment-orchestrator"` — a deterministic slug | `core/knowledge/model.py:62` |
| Assertion | `make_assertion_id(s, p, o, v)` → `a_<sha1(s\0p\0o\0v)[:16]>` — **content-addressed** | `:72` |
| Provenance | 1:1 with an assertion, 10 scalar fields | `:250` |
| Side | `declared_by[node_id] = document_type` (`requirements` / `architecture`) | `ingest.py:228` |
| Status | `UNVERIFIED`, `VERIFIED`, `CORRECTED`, `DISPUTED`, `RETIRED` | `:112-117` |
| Scope | `INITIATIVE_PROPOSAL`, `SYSTEM_BASELINE`, `DOMAIN_TRUTH` | `:108-110` |

Two consequences make the SQL almost fall out for free:

- **Uniqueness is structural.** Node and assertion primary keys are derived from
  content, so a re-run converges by construction — `add_assertion`'s folding
  (`:721`) becomes an `ON CONFLICT`/upsert rather than a de-duplication pass.
- **The object contract is checkable.** An assertion points at a **node** or a
  **literal**, never both and never neither (`object` XOR `value`), so the rule the
  extraction harness enforces in Python becomes a `CHECK` constraint.

## 2. Does graph querying and modification need MCP?

**Querying: MCP is a reasonable yes. Modification: yes, but only as one coarse
transactional tool — never per-triple CRUD.**

The governing rule is the one already set for tools
([`deployment-architecture.md`](deployment-architecture.md) §3.3): *if it is
deterministic and lives in `core/`, it is a library import; if it lives over the
network or in another system, it is MCP.* Applied to the graph:

| Surface | Over MCP? | Why |
|---|---|---|
| Read a product's graph snapshot | **Yes** | network resource; needs authz and a versioned contract |
| Apply a change set | **Yes — one tool** | must be transactional and carry a version |
| Per-assertion create/update/delete | **No** | turns one transaction into N round trips and widens the lost-update window |
| `ingest`, `reconcile`, `serialise`, `quality`, `realization` | **No** | pure functions with a no-model test suite; MCP adds a network boundary to logic that must stay deterministic |
| Review decisions (`verify` / `correct` / `dispute`) | **No** | a human gate with an authenticated actor; it belongs to the app, not to an agent's tool budget |
| SPARQL over the materialised RDF | **Later** | that is the deferred `YB-010` read layer, not the record |

The write tool has one shape, and it is the one that fixes the concurrency defect:

```
apply_delta(product_id, expected_version, delta) -> {new_version} | Conflict
```

and the client pattern follows from the code that already exists:

1. **Read** the snapshot, recording `version = V`.
2. **Compute in memory** — the whole graph and the pure functions, exactly as
   `state()` + `core/knowledge/*` do today.
3. **Submit the delta** with `expected_version = V`.
4. The store applies it atomically or returns a conflict, and the caller re-reads
   and recomputes.

Two contract details that prevent silent rot: carry the graph `SCHEMA_VERSION`
(`core/knowledge/serialise.py:45`) in the tool envelope so an agent built against
one shape cannot write another, and return the new version on success so the client
can keep a session pinned without re-reading.

## 3. The schema

Every table carries `product_id`: the product **is** the partition, which is what
makes YB-042's isolation real and the concurrency unit coherent. Note the refinement
in [`workspace-structure.md`](workspace-structure.md) §11 — the **central baseline**
partition is the **system**, not the product, and cross-system facts (shared platform
instances) need a shared scope beside it. Read `product_id` below as "the owning
scope": `system_id` for a baseline store, the initiative's product for a proposal
store.

```sql
CREATE TABLE product (
  product_id   TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  name         TEXT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── nodes ────────────────────────────────────────────────────────────────
CREATE TABLE node (
  product_id TEXT NOT NULL REFERENCES product,
  node_id    TEXT NOT NULL,              -- 'container:payment-orchestrator'
  kind       TEXT NOT NULL,              -- ontology class
  label      TEXT NOT NULL,
  side       TEXT,                       -- declared_by: requirements | architecture
  PRIMARY KEY (product_id, node_id)
);
CREATE INDEX node_kind  ON node (product_id, kind);
CREATE INDEX node_label ON node (product_id, lower(label));

CREATE TABLE external_reference (
  product_id       TEXT NOT NULL,
  node_id          TEXT NOT NULL,
  ordinal          INT  NOT NULL,        -- preserves the list order
  identifier       TEXT NOT NULL,
  system           TEXT    NOT NULL DEFAULT '',
  reference_type   TEXT    NOT NULL DEFAULT 'OTHER',
  scope            TEXT    NOT NULL DEFAULT 'DOCUMENT',
  uri              TEXT    NOT NULL DEFAULT '',
  is_authoritative BOOLEAN NOT NULL DEFAULT FALSE,
  attribute_scope  JSONB   NOT NULL DEFAULT '[]',
  notes            TEXT    NOT NULL DEFAULT '',
  PRIMARY KEY (product_id, node_id, ordinal),
  FOREIGN KEY (product_id, node_id)
    REFERENCES node (product_id, node_id) ON DELETE CASCADE
);

-- ── assertions: one row per fact, already content-addressed ──────────────
CREATE TABLE assertion (
  product_id     TEXT NOT NULL REFERENCES product,
  assertion_id   TEXT NOT NULL,          -- a_<sha1(subject,predicate,object,value)[:16]>
  subject_id     TEXT NOT NULL,
  predicate      TEXT NOT NULL,
  object_id      TEXT,                   -- node → node
  value          TEXT,                   -- node → literal
  confidence     REAL    NOT NULL DEFAULT 0,
  source_text    TEXT    NOT NULL DEFAULT '',
  ontology_class TEXT,
  status         TEXT    NOT NULL DEFAULT 'UNVERIFIED',
  scope          TEXT    NOT NULL DEFAULT 'INITIATIVE_PROPOSAL',
  initiative_id  TEXT,
  superseded_by  TEXT,                   -- self-FK: lineage, incl. RETIRED (ADR-0015)
  -- provenance, 1:1 with the assertion (core/knowledge/model.py:250)
  source_type     TEXT NOT NULL DEFAULT 'EXTRACTION_AGENT',
  run_id          TEXT,
  pass_name       TEXT NOT NULL DEFAULT '',
  model_id        TEXT NOT NULL DEFAULT '',
  chunk_label     TEXT NOT NULL DEFAULT '',
  asserted_at     TEXT NOT NULL DEFAULT '',
  asserted_by     TEXT NOT NULL DEFAULT '',
  derived_from    TEXT NOT NULL DEFAULT '',
  correction_note TEXT NOT NULL DEFAULT '',
  domain_pack     TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (product_id, assertion_id),
  -- the object contract, made structural
  CONSTRAINT assertion_object_xor_value CHECK ((object_id IS NULL) <> (value IS NULL)),
  FOREIGN KEY (product_id, subject_id)    REFERENCES node (product_id, node_id),
  FOREIGN KEY (product_id, object_id)     REFERENCES node (product_id, node_id),
  FOREIGN KEY (product_id, superseded_by) REFERENCES assertion (product_id, assertion_id)
);
CREATE INDEX assertion_sp    ON assertion (product_id, subject_id, predicate);
CREATE INDEX assertion_op    ON assertion (product_id, object_id, predicate) WHERE object_id IS NOT NULL;
CREATE INDEX assertion_queue ON assertion (product_id, status) WHERE status <> 'VERIFIED';
CREATE INDEX assertion_pred  ON assertion (product_id, predicate, status);
CREATE INDEX assertion_run   ON assertion (product_id, run_id);

-- ── what produced the facts, and whether it finished ─────────────────────
CREATE TABLE run (
  product_id     TEXT NOT NULL REFERENCES product,
  run_id         TEXT NOT NULL,
  document_ref   TEXT NOT NULL DEFAULT '',
  document_type  TEXT NOT NULL DEFAULT '',
  document_hash  TEXT NOT NULL DEFAULT '',
  document_chars INT  NOT NULL DEFAULT 0,
  model_id       TEXT NOT NULL DEFAULT '',
  started_at     TEXT NOT NULL DEFAULT '',
  completed_at   TEXT NOT NULL DEFAULT '',
  chunk_count    INT  NOT NULL DEFAULT 0,
  completeness   TEXT NOT NULL DEFAULT 'UNKNOWN',   -- COMPLETE | PARTIAL | UNKNOWN
  usage          JSONB NOT NULL DEFAULT '{}',
  ontology_hash  TEXT,                              -- YB-045
  PRIMARY KEY (product_id, run_id)
);

CREATE TABLE pass_record (
  product_id       TEXT NOT NULL,
  run_id           TEXT NOT NULL,
  ordinal          INT  NOT NULL,
  pass_name        TEXT NOT NULL DEFAULT '',
  chunk_label      TEXT NOT NULL DEFAULT '',
  outcome          TEXT NOT NULL DEFAULT '',
  path             TEXT NOT NULL DEFAULT '',
  elapsed          REAL NOT NULL DEFAULT 0,
  error            TEXT NOT NULL DEFAULT '',
  triples_produced INT  NOT NULL DEFAULT 0,
  temperature      REAL,
  PRIMARY KEY (product_id, run_id, ordinal),
  FOREIGN KEY (product_id, run_id) REFERENCES run (product_id, run_id) ON DELETE CASCADE
);

-- ── review log: append-only, the audit trail ─────────────────────────────
CREATE TABLE decision (
  product_id     TEXT NOT NULL REFERENCES product,
  decision_seq   BIGSERIAL,
  at             TEXT NOT NULL,
  actor          TEXT NOT NULL,
  action         TEXT NOT NULL,          -- VERIFY | CORRECT | DISPUTE | PROMOTE | REMOVE …
  assertion_id   TEXT,
  replacement_id TEXT,
  note           TEXT NOT NULL DEFAULT '',
  before         JSONB NOT NULL DEFAULT '{}',
  after          JSONB NOT NULL DEFAULT '{}',
  promoted_ids   JSONB NOT NULL DEFAULT '[]',
  PRIMARY KEY (product_id, decision_seq)
);
CREATE INDEX decision_at ON decision (product_id, at);

-- ── the concurrency token, and revision metadata ─────────────────────────
CREATE TABLE working_set (
  product_id TEXT PRIMARY KEY REFERENCES product,
  version    BIGINT NOT NULL DEFAULT 0,  -- optimistic concurrency token
  label      TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT '',
  updated_by TEXT NOT NULL DEFAULT ''
);

CREATE TABLE revision (
  product_id     TEXT NOT NULL REFERENCES product,
  revision_id    TEXT NOT NULL,
  parent_id      TEXT,
  kind           TEXT NOT NULL DEFAULT 'draft',   -- draft | baseline
  label          TEXT NOT NULL DEFAULT '',
  created_at     TEXT NOT NULL DEFAULT '',
  frozen_at      TEXT NOT NULL DEFAULT '',
  frozen_by      TEXT NOT NULL DEFAULT '',
  note           TEXT NOT NULL DEFAULT '',
  initiative_id  TEXT NOT NULL DEFAULT '',
  graph_ref      TEXT NOT NULL,                   -- immutable artifact: S3 key / blob sha
  stats          JSONB NOT NULL DEFAULT '{}',
  review_summary JSONB NOT NULL DEFAULT '{}',
  progress       JSONB NOT NULL DEFAULT '{}',
  documents      JSONB NOT NULL DEFAULT '[]',
  completeness   JSONB NOT NULL DEFAULT '[]',
  PRIMARY KEY (product_id, revision_id)
);
```

-- ── branches: the baseline, and everything proposing against it ──────────
CREATE TABLE branch (
  scope_id      TEXT NOT NULL,        -- system_id for a baseline store
  branch_id     TEXT NOT NULL,
  kind          TEXT NOT NULL,        -- baseline | initiative | personal
  base_branch   TEXT,                 -- fork point
  base_revision TEXT,
  owner         TEXT,
  created_at    TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (scope_id, branch_id)
);

-- With branches, `working_set` and `revision` gain `branch_id`, and the CAS token
-- becomes per branch rather than per scope: `working_set(scope_id, branch_id,
-- version)`. See YB-046.

-- ── guarded promotion: a request, not a button ───────────────────────────
CREATE TABLE promotion_request (
  scope_id         TEXT NOT NULL,
  request_id       TEXT NOT NULL,
  source_branch    TEXT NOT NULL,
  source_revision  TEXT NOT NULL,
  target_branch    TEXT NOT NULL,
  expected_version BIGINT NOT NULL,   -- the version guard
  delta_summary    JSONB NOT NULL DEFAULT '{}',
  conflicts        JSONB NOT NULL DEFAULT '[]',
  requested_by     TEXT NOT NULL,
  requested_at     TEXT NOT NULL,
  state            TEXT NOT NULL DEFAULT 'PENDING',  -- PENDING|APPROVED|REJECTED|SUPERSEDED
  resolved_by      TEXT,
  resolved_at      TEXT,
  note             TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (scope_id, request_id)
);

### SQLite deltas

`TEXT` timestamps and `JSONB` → `JSON`/`TEXT` are the only mechanical changes;
`BIGSERIAL` → `INTEGER PRIMARY KEY AUTOINCREMENT`; partial indexes are supported;
`CHECK` and composite foreign keys are supported **only with
`PRAGMA foreign_keys = ON`**, which must be set per connection. Enable WAL
(`PRAGMA journal_mode = WAL`) and use `BEGIN IMMEDIATE` for the write transaction so
the read-then-write window is actually closed. `sqlite3` is stdlib — slice 1 needs
no new dependency, whereas Postgres needs `psycopg` (neither `psycopg` nor
`psycopg2` is installed today; `sqlalchemy` is).

## 4. The write path — where the lost update dies

The delta apply is one transaction, guarded by the version token:

```sql
BEGIN IMMEDIATE;                                   -- SQLite; plain BEGIN on Postgres

UPDATE working_set
   SET version = version + 1, updated_at = :now, updated_by = :actor
 WHERE product_id = :p AND version = :expected;
-- 0 rows affected → someone else committed first: roll back, return Conflict

-- nodes: upsert by derived id
INSERT INTO node (product_id, node_id, kind, label, side)
     VALUES (...) ON CONFLICT (product_id, node_id) DO UPDATE
       SET kind = excluded.kind, side = COALESCE(excluded.side, node.side);

-- assertions: upsert by content-addressed id
INSERT INTO assertion (...) VALUES (...) ON CONFLICT (product_id, assertion_id) DO UPDATE
       SET confidence = MAX(assertion.confidence, excluded.confidence),
           source_text = CASE WHEN length(excluded.source_text) > length(assertion.source_text)
                              THEN excluded.source_text ELSE assertion.source_text END,
           status = CASE WHEN assertion.source_type LIKE 'HUMAN%' THEN assertion.status
                         ELSE COALESCE(NULLIF(excluded.status,'UNVERIFIED'), assertion.status) END;

DELETE FROM assertion WHERE product_id = :p AND assertion_id = ANY(:removed);
COMMIT;
```

**Do not re-implement the folding rules in SQL.** `add_assertion`
(`core/knowledge/model.py:721`) encodes the correction-merge guarantee — a human
assertion outranks a later agent observation, confidence rises on re-observation,
a fuller source text wins. The safe implementation applies a delta by (a) selecting
the affected existing rows, (b) running the *tested* in-memory fold, (c) upserting
the results. Keep one implementation of that truth; the SQL above is what it
compiles to, not a second copy of it.

## 5. What is deliberately **not** in these tables

- **Revision graph bodies.** `revision.graph_ref` points at an immutable,
  content-addressed artifact (object storage / blob). Copying every revision's full
  graph into SQL would multiply the largest thing in the system by the number of
  revisions, and revisions are read whole only to diff them — which
  `compute_graph_delta` already does from two loaded graphs.
- **RDF.** Materialised *from* these tables per revision (N-Quads/Turtle) into the
  same artifact store. RDF is a projection; the tables are the record. Jena, when it
  arrives, reads the artifacts.
- **Journals and progress events.** Those live in the Valkey Stream
  ([ADR-0026](../decisions/ADR-0026-run-journal-and-progress-transports.md)), with the terminal
  `run` row written here first.

## 6. Options weighed

| Shape | Change size | SQL audit | Whole-graph load | Verdict |
|---|---|---|---|---|
| **Document per product** — `working_set(product_id, version, graph JSONB)` | minimal — `serialise.py` unchanged, CAS is one statement | none | one row | the cheapest way to fix the lost update; no audit queryability |
| **Normalised (above)** | moderate — assembly + delta projection | full | one join over hundreds of rows | **recommended**: the reason to move to a real store is exactly the queries |
| **Quad/triple table** (`graph, s, p, o`) | large | SPARQL-shaped | needs reassembly | that is the RDF store, and it is deferred |

The access pattern decides this: the app already loads a whole product graph and
computes projections in Python, so SQL-side queryability is **not** needed for
today's features — but it is needed for the workspace index and audit filters that
[YB-042](../todos/entries/YB-042-workspace-structure.md) introduces, and those are
the queries that justify normalising now rather than migrating twice.

## 7. Related

- [YB-043 — System of record](../todos/entries/YB-043-system-of-record.md).
- [YB-042 — Workspace structure](workspace-structure.md) — `product_id` is the
  partition it promised.
- [ADR-0026 — Run streaming](../decisions/ADR-0026-run-journal-and-progress-transports.md) — the
  journal that is not in these tables.
- [YB-010 — RDF knowledge layer](../todos/entries/YB-010-rdf-knowledge-layer.md) —
  the projection this schema feeds.
- [YB-045 — Ontology provenance](../todos/entries/YB-045-ontology-version-provenance.md)
  — the `ontology_hash` column.
- [`deployment-architecture.md`](deployment-architecture.md) §3.3 — the MCP rule.
