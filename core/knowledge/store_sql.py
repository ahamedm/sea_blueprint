"""
SQLite system of record — the first concurrency-safe backend.

WHY THIS ONE FIRST
------------------
The file backend is single-writer by construction and the server is not: `app.run`
sets `threaded=True` by default, so two reviewers acting on one scope can lose a
decision today (`docs/design/system-of-record.md` §2). SQLite is available in the
standard library, needs no service, and — with WAL and `BEGIN IMMEDIATE` — gives the
one guarantee that is actually missing: a guarded write either applies or raises.

WHAT IT STORES
--------------
Slice 1 is the **document shape**: one row per scope holding the serialised graph, plus
one row per revision. `serialise.py` and its `SCHEMA_VERSION` stay the contract, so
this backend cannot drift from the domain model, and the normalised tables in
`docs/design/graph-store-schema.md` can decompose the blob later without a migration
of meaning.

PORTABILITY
-----------
Written against SQLAlchemy Core rather than raw DBAPI so the PostgreSQL/MariaDB step
is a URL change plus a contract-test run, not a rewrite. Core, not the ORM: the
measured hydration penalty is 4.00x for the ORM against 1.77x for Core
(`scripts/bench_store_backends.py`, 5,000 rows), and the ORM would add a second model
beside the dataclasses in `model.py`. See `docs/design/workspace-and-sor-enablers.md` §2.

READS ARE NOT CACHED HERE. Every `load_working` hits the database. That is deliberate
for this slice — a per-scope cache keyed by the version token belongs to the workspace
layer, and putting one in the backend would hide the real cost rather than remove it.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    delete,
    event,
    func,
    select,
)
from sqlalchemy.engine import Engine

from .model import KnowledgeGraph, compute_graph_delta, utc_now
from .review import ReviewLog, review_progress
from .serialise import graph_from_dict, graph_to_dict
from .store import BaselineNotReady, Revision, Snapshot
from .store_api import StoreConflict, new_revision_id

DEFAULT_SCOPE = "default"

_metadata = MetaData()

_working_set = Table(
    "working_set", _metadata,
    Column("scope_id", String, primary_key=True),
    Column("version", Integer, nullable=False, server_default="0"),
    Column("graph", String),
    Column("review_log", String),
    Column("meta", String),
    Column("updated_at", String),
    Column("updated_by", String),
)

_revision = Table(
    "revision", _metadata,
    Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("scope_id", String, nullable=False, index=True),
    Column("revision_id", String, nullable=False, unique=True),
    Column("parent_id", String, default=""),
    Column("kind", String, default="draft"),
    Column("label", String, default=""),
    Column("created_at", String, default=""),
    Column("frozen_at", String, default=""),
    Column("frozen_by", String, default=""),
    Column("note", String, default=""),
    Column("initiative_id", String, default=""),
    Column("documents", String, default="[]"),
    Column("completeness", String, default="[]"),
    Column("stats", String, default="{}"),
    Column("review_summary", String, default="{}"),
    Column("progress", String, default="{}"),
    Column("graph", String),
    Column("review_log", String),
    Column("meta", String, default="{}"),
)


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {})


def _loads(text: Optional[str], fallback: Any) -> Any:
    if not text:
        return fallback
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        # A corrupt column degrades to the fallback rather than taking the request
        # down — the same posture `_pass_records_from_metadata` takes for old output.
        return fallback


class SqliteStore:
    """A `Store` backed by SQLite (or any SQLAlchemy URL), scoped to one graph.

    `scope_id` is what makes one database file hold many graphs — a workspace's
    systems — without their labels colliding. The file backend has no equivalent:
    there, the directory *is* the scope.
    """

    concurrency_safe = True

    def __init__(self, db_path: str | Path, scope_id: str = DEFAULT_SCOPE,
                 url: Optional[str] = None):
        self.path = Path(db_path) if url is None else None
        self.scope_id = scope_id
        self.url = url or f"sqlite+pysqlite:///{self.path}"
        self.engine: Engine = create_engine(self.url, future=True)
        self._install_sqlite_pragmas()

    # -- plumbing ----------------------------------------------------------

    def _install_sqlite_pragmas(self) -> None:
        """WAL, foreign keys, and manual transactions on SQLite only.

        `isolation_level = None` hands transaction control to this class, which is what
        lets the write path open with `BEGIN IMMEDIATE` — the difference between a
        guarded write and a race that looks guarded.
        """
        if self.engine.dialect.name != "sqlite":
            return

        @event.listens_for(self.engine, "connect")
        def _on_connect(dbapi_connection, _record):  # pragma: no cover - driver hook
            dbapi_connection.isolation_level = None
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    @contextmanager
    def _write_tx(self) -> Iterator[Any]:
        """A write transaction that takes the write lock up front.

        `BEGIN IMMEDIATE` on SQLite; a normal transaction elsewhere. Without the
        up-front lock the read-then-write in `save_working` is exactly the lost-update
        window this backend exists to close.
        """
        with self.engine.connect() as conn:
            immediate = conn.dialect.name == "sqlite"
            conn.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield conn
            except BaseException:
                conn.exec_driver_sql("ROLLBACK")
                raise
            conn.exec_driver_sql("COMMIT")

    def ensure(self) -> "SqliteStore":
        """Create the schema, and the directory it lives in.

        A workspace puts a scope's database under `scopes/`, which does not exist until
        something writes there — so without the mkdir the first connect fails with
        "unable to open database file" rather than creating the store.
        """
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        _metadata.create_all(self.engine)
        return self

    # -- working set -------------------------------------------------------

    def has_working(self) -> bool:
        with self.engine.connect() as conn:
            return conn.execute(
                select(func.count()).select_from(_working_set)
                .where(_working_set.c.scope_id == self.scope_id)
            ).scalar_one() > 0

    def load_working(self) -> Snapshot:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(_working_set).where(_working_set.c.scope_id == self.scope_id)
            ).mappings().first()
        if row is None:
            return Snapshot(graph=KnowledgeGraph(), meta={"version": 0})
        return Snapshot(
            graph=graph_from_dict(_loads(row["graph"], {})),
            log=ReviewLog.from_dict(_loads(row["review_log"], {})),
            meta={**_loads(row["meta"], {}), "version": row["version"],
                  "updated_at": row["updated_at"], "updated_by": row["updated_by"]},
        )

    def save_working(
        self,
        graph: KnowledgeGraph,
        log: Optional[ReviewLog] = None,
        meta: Optional[Dict[str, Any]] = None,
        expected_version: Optional[int] = None,
        actor: str = "",
    ) -> Snapshot:
        """Write the working set, guarded by the version token.

        `expected_version` is the version the caller read. A mismatch means someone
        else committed first: this raises `StoreConflict` and writes nothing.
        """
        log = log or ReviewLog()
        meta = {**(meta or {}), "saved_at": utc_now()}

        with self._write_tx() as conn:
            current = conn.execute(
                select(_working_set.c.version)
                .where(_working_set.c.scope_id == self.scope_id)
            ).scalar_one_or_none() or 0

            if expected_version is not None and expected_version != current:
                raise StoreConflict(
                    f"scope {self.scope_id!r} moved from version {expected_version} "
                    f"to {current}; re-read and reapply",
                    expected=expected_version, found=current,
                )

            version = current + 1
            payload = {
                "scope_id": self.scope_id,
                "version": version,
                "graph": _dumps(graph_to_dict(graph)),
                "review_log": _dumps(log.to_dict()),
                "meta": _dumps(meta),
                "updated_at": meta["saved_at"],
                "updated_by": actor,
            }
            existing = conn.execute(
                select(_working_set.c.scope_id)
                .where(_working_set.c.scope_id == self.scope_id)
            ).scalar_one_or_none()
            if existing is None:
                conn.execute(_working_set.insert().values(**payload))
            else:
                conn.execute(
                    _working_set.update()
                    .where(_working_set.c.scope_id == self.scope_id)
                    .values(**payload)
                )

        return Snapshot(graph=graph, log=log, meta={**meta, "version": version})

    def discard_working(self) -> None:
        with self._write_tx() as conn:
            conn.execute(delete(_working_set).where(_working_set.c.scope_id == self.scope_id))

    # -- revisions ---------------------------------------------------------

    @staticmethod
    def _revision_from_row(row: Any) -> Revision:
        return Revision(
            id=row["revision_id"], label=row["label"] or "",
            created_at=row["created_at"] or "", parent_id=row["parent_id"] or "",
            kind=row["kind"] or "draft", actor=row["frozen_by"] or row["frozen_by"] or "",
            frozen_at=row["frozen_at"] or "", frozen_by=row["frozen_by"] or "",
            note=row["note"] or "", initiative_id=row["initiative_id"] or "",
            documents=_loads(row["documents"], []),
            completeness=_loads(row["completeness"], []),
            stats=_loads(row["stats"], {}),
            review_summary=_loads(row["review_summary"], {}),
            progress=_loads(row["progress"], {}),
        )

    def list_revisions(self) -> List[Revision]:
        """Newest first, by insertion (`seq`) rather than timestamp — the same reason
        the file index keeps append order: commits within one second must not reorder."""
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(_revision).where(_revision.c.scope_id == self.scope_id)
                .order_by(_revision.c.seq.desc())
            ).mappings().all()
        return [self._revision_from_row(r) for r in rows]

    def get_revision(self, revision_id: str) -> Optional[Revision]:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(_revision).where(_revision.c.revision_id == revision_id)
            ).mappings().first()
        return self._revision_from_row(row) if row else None

    def baselines(self) -> List[Revision]:
        return [r for r in self.list_revisions() if r.is_baseline]

    def latest(self) -> Optional[Revision]:
        revisions = self.list_revisions()
        return revisions[0] if revisions else None

    def load_revision(self, revision_id: str) -> Snapshot:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(_revision).where(_revision.c.revision_id == revision_id)
            ).mappings().first()
        if row is None:
            raise KeyError(f"no such revision: {revision_id}")
        return Snapshot(
            graph=graph_from_dict(_loads(row["graph"], {})),
            log=ReviewLog.from_dict(_loads(row["review_log"], {})),
            meta=_loads(row["meta"], {}),
        )

    def commit(
        self,
        graph: KnowledgeGraph,
        log: Optional[ReviewLog] = None,
        label: str = "",
        actor: str = "",
        note: str = "",
        initiative_id: str = "",
    ) -> Revision:
        log = log or ReviewLog()
        with self._write_tx() as conn:
            parent = conn.execute(
                select(_revision.c.revision_id).where(_revision.c.scope_id == self.scope_id)
                .order_by(_revision.c.seq.desc()).limit(1)
            ).scalar_one_or_none()

            revision_id = new_revision_id()
            stamped = _stamp(graph, revision_id, parent or "")
            progress = review_progress(stamped)
            revision = Revision(
                id=revision_id,
                label=label or f"Revision {revision_id[-4:]}",
                created_at=utc_now(), parent_id=parent or "", kind="draft",
                actor=actor, note=note, initiative_id=initiative_id,
                documents=sorted({r.document_ref for r in stamped.runs.values()
                                  if r.document_ref}),
                completeness=sorted({r.completeness for r in stamped.runs.values()}),
                stats=stamped.stats(), review_summary=log.summary(),
                progress=progress.to_dict(),
            )
            conn.execute(_revision.insert().values(
                scope_id=self.scope_id, revision_id=revision.id,
                parent_id=revision.parent_id, kind=revision.kind,
                label=revision.label, created_at=revision.created_at,
                frozen_at="", frozen_by="", note=revision.note,
                initiative_id=revision.initiative_id,
                documents=_dumps(revision.documents),
                completeness=_dumps(revision.completeness),
                stats=_dumps(revision.stats),
                review_summary=_dumps(revision.review_summary),
                progress=_dumps(revision.progress),
                graph=_dumps(graph_to_dict(stamped)),
                review_log=_dumps(log.to_dict()),
                meta=_dumps({"committed_at": revision.created_at, "actor": actor}),
            ))
        return revision

    def freeze(
        self,
        revision_id: Optional[str] = None,
        label: str = "",
        actor: str = "",
        allow_unverified: bool = False,
    ) -> Revision:
        target = self.get_revision(revision_id) if revision_id else self.latest()
        if target is None:
            raise KeyError(f"no such revision: {revision_id}" if revision_id else "no revisions to freeze")

        snapshot = self.load_revision(target.id)
        progress = review_progress(snapshot.graph)
        if not progress.is_auditable and not allow_unverified:
            raise BaselineNotReady(progress, target.id)

        updated = Revision.from_dict(target.to_dict())
        updated.kind = "baseline"
        updated.frozen_at = utc_now()
        updated.frozen_by = actor
        if label:
            updated.label = label

        with self._write_tx() as conn:
            conn.execute(
                _revision.update().where(_revision.c.revision_id == target.id)
                .values(kind="baseline", frozen_at=updated.frozen_at,
                        frozen_by=updated.frozen_by, label=updated.label)
            )
        return updated

    # -- comparison --------------------------------------------------------

    def diff(self, from_id: str, to_id: str):
        return compute_graph_delta(
            self.load_revision(from_id).graph, self.load_revision(to_id).graph
        )

    def diff_against_revision(self, graph: KnowledgeGraph, revision_id: str):
        return compute_graph_delta(self.load_revision(revision_id).graph, graph)

    def diff_against_working(self, graph: KnowledgeGraph):
        return compute_graph_delta(self.load_working().graph, graph)


def _stamp(graph: KnowledgeGraph, version_id: str, parent_id: str) -> KnowledgeGraph:
    """Same contract as the file backend: stamp a COPY, so the caller's in-memory
    working set never disagrees with what was written."""
    import copy

    stamped = copy.deepcopy(graph)
    stamped.version_id = version_id
    stamped.parent_version_id = parent_id
    return stamped
