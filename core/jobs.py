"""
The job record, and the store that owns it (YB-026 phase 2).

WHAT A JOB IS, AND WHAT IT IS NOT
---------------------------------
A job is the durable record of *one attempt to run something*. It exists before
the model call and survives the process, which is what makes a background run
possible at all: without a record written first, there is no queue, no claim, no
heartbeat, and nothing for a page to show.

It is **execution state, not the record and not a progress channel**. The graph is
the record; `ExtractionRun.completeness` is the only verdict; the run journal
(`core.events`) is the only progress stream. A job says "this work is queued /
running / done", never "the graph is trustworthy" — a `PARTIAL` run is a job that
succeeded at producing an incomplete verdict, and the page must say both.

WHY A PROTOCOL, AND WHY THE ONLY SHIPPED BACKEND IS SQLITE
----------------------------------------------------------
Everything engine-shaped is deliberately behind this interface so that adopting a
durable workflow engine later replaces an implementation rather than the agents,
the pass structure or the transport. The design decision (YB-026 §13) is that the
*substrate* ships now and the *engine* is deferred.

`claim()` requires an atomic compare-and-set. A file-per-job store cannot express
one — `RevisionStore` documents itself as single-writer with no lock and no version
check, and refuses a guarded write rather than pretending. So a backend that cannot
claim atomically must not be used to run a worker, and the worker refuses it
(`concurrency_safe`) rather than degrading into a duplicate-worker race.

STATE VOCABULARY
----------------
These are YB-037's states, not a private set. This module writes four of them; the
rest are reserved so that retry, cancellation and the human gate land without a
migration. It deliberately does **not** invent an `INTERRUPTED` state: a `RUNNING`
job whose heartbeat has gone stale is *surfaced* as stale, and whether it is
re-queued or failed is YB-037's recovery rule.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Protocol, runtime_checkable

__all__ = [
    "QUEUED",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "DEAD_LETTER",
    "AWAITING_REVIEW",
    "RESERVED_STATES",
    "ACTIVE_STATES",
    "STATE_ORDER",
    "Job",
    "JobStore",
    "SqliteJobStore",
    "new_job_id",
    "utc_now",
]

# -- the state vocabulary (YB-037's) ----------------------------------------

QUEUED = "QUEUED"
RUNNING = "RUNNING"
SUCCEEDED = "SUCCEEDED"
FAILED = "FAILED"

# Reserved: named here so the schema is whole, written by YB-037/YB-018 when retry,
# cancellation and the human gate arrive. YB-026 never sets these.
CANCELLED = "CANCELLED"
DEAD_LETTER = "DEAD_LETTER"
AWAITING_REVIEW = "AWAITING_REVIEW"

RESERVED_STATES = frozenset({CANCELLED, DEAD_LETTER, AWAITING_REVIEW})
ACTIVE_STATES = frozenset({QUEUED, RUNNING})
STATE_ORDER = (QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED, DEAD_LETTER, AWAITING_REVIEW)

# -- input kinds, the trigger axis, and terminal states ----------------------

#: Where a job's input comes from. A document is NOT universal: an event-supplied
#: run carries a reference the worker fetches, and a design run reads the graph.
INLINE = "inline"       # bytes stored at enqueue (a UI upload or paste)
SOURCE = "source"       # a reference to fetch (Confluence, SharePoint, API, S3)
GRAPH = "graph"         # a snapshot already in the record; no document at all
INPUT_KINDS = frozenset({INLINE, SOURCE, GRAPH})

#: What started the job, independent of what it reads. A nightly audit is
#: `schedule` + `graph`; a CLI ingest is `cli` + `inline`.
TRIGGER_UI = "ui"
TRIGGER_EVENT = "event"
TRIGGER_SCHEDULE = "schedule"
TRIGGER_CLI = "cli"
TRIGGERS = frozenset({TRIGGER_UI, TRIGGER_EVENT, TRIGGER_SCHEDULE, TRIGGER_CLI})

TERMINAL_STATES = frozenset({SUCCEEDED, FAILED, CANCELLED, DEAD_LETTER, AWAITING_REVIEW})


def utc_now() -> str:
    """ISO-8601 UTC, millisecond precision, `Z` suffix (the journal's clock)."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def new_job_id() -> str:
    """A fresh job id. Opaque, unguessable enough not to be enumerated by hand, and
    never reused — the run id it becomes is minted separately and stored beside it."""
    return "job_" + uuid.uuid4().hex[:12]


# ============================================================================
# The record
# ============================================================================


@dataclass
class Job:
    """One attempt to run one thing.

    `input_kind`/`input` say WHERE the input comes from, `parameters` carries the
    extractor's arguments, and the two are kept apart on purpose: a `source` job's
    input is a reference (resolved by the worker's acquisition step), while a
    `graph` job's input is a snapshot reference and no document exists at all.
    """

    job_id: str
    run_id: str
    scope_id: str
    kind: str
    state: str = QUEUED
    trigger: str = TRIGGER_UI
    input_kind: str = INLINE
    input: Dict[str, Any] = field(default_factory=dict)
    parameters: Dict[str, Any] = field(default_factory=dict)
    actor: str = ""
    seq: int = 0
    created_at: str = field(default_factory=utc_now)
    started_at: str = ""
    finished_at: str = ""
    worker_id: str = ""
    worker_heartbeat_at: str = ""
    attempt: int = 0
    error: str = ""
    dedupe_key: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def to_dict(self) -> Dict[str, Any]:
        return {
            "job_id": self.job_id,
            "run_id": self.run_id,
            "scope_id": self.scope_id,
            "kind": self.kind,
            "state": self.state,
            "trigger": self.trigger,
            "input_kind": self.input_kind,
            "input": dict(self.input),
            "parameters": dict(self.parameters),
            "actor": self.actor,
            "seq": self.seq,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "worker_id": self.worker_id,
            "worker_heartbeat_at": self.worker_heartbeat_at,
            "attempt": self.attempt,
            "error": self.error,
            "dedupe_key": self.dedupe_key,
        }


# ============================================================================
# The store contract
# ============================================================================


@runtime_checkable
class JobStore(Protocol):
    """Where jobs are recorded, claimed and finished.

    `claim` MUST be atomic: two workers calling it concurrently must not both take
    the same job. That single requirement is what rules out a backend without a
    compare-and-set, and it is the only thing this protocol asks of storage.
    """

    concurrency_safe: bool

    def enqueue(self, job: Job) -> Job:
        """Record a `QUEUED` job, assign its queue position, return it."""
        ...

    def claim(self, worker_id: str) -> Optional[Job]:
        """Atomically move the oldest `QUEUED` job to `RUNNING`, or return None."""
        ...

    def heartbeat(self, job_id: str, worker_id: str) -> bool:
        """Record that a worker is still alive on a job. False if it no longer owns it."""
        ...

    def finish(self, job_id: str, state: str, error: str = "") -> Optional[Job]:
        """Move a `RUNNING` job to a terminal state and return it."""
        ...

    def get(self, job_id: str) -> Optional[Job]:
        ...

    def list(self, scope_id: Optional[str] = None,
             states: Optional[Any] = None, limit: int = 100) -> List[Job]:
        ...

    def counts(self, scope_id: Optional[str] = None) -> Dict[str, int]:
        ...


class JobStoreError(RuntimeError):
    """The job store could not honour the request."""


# ============================================================================
# The SQLite backend
# ============================================================================

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    seq                 INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id              TEXT NOT NULL UNIQUE,
    run_id              TEXT NOT NULL,
    scope_id            TEXT NOT NULL,
    kind                TEXT NOT NULL,
    state               TEXT NOT NULL,
    trigger_kind        TEXT NOT NULL DEFAULT 'ui',
    input_kind          TEXT NOT NULL DEFAULT 'inline',
    input               TEXT NOT NULL DEFAULT '{}',
    parameters          TEXT NOT NULL DEFAULT '{}',
    actor               TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL,
    started_at          TEXT NOT NULL DEFAULT '',
    finished_at         TEXT NOT NULL DEFAULT '',
    worker_id           TEXT NOT NULL DEFAULT '',
    worker_heartbeat_at TEXT NOT NULL DEFAULT '',
    attempt             INTEGER NOT NULL DEFAULT 0,
    error               TEXT NOT NULL DEFAULT '',
    dedupe_key          TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS jobs_state_seq ON jobs(state, seq);
CREATE INDEX IF NOT EXISTS jobs_scope ON jobs(scope_id, seq);
"""

_COLUMNS = (
    "seq, job_id, run_id, scope_id, kind, state, trigger_kind, input_kind, input, "
    "parameters, actor, created_at, started_at, finished_at, worker_id, "
    "worker_heartbeat_at, attempt, error, dedupe_key"
)


def _dumps(value: Any) -> str:
    return json.dumps(value or {}, separators=(",", ":"), sort_keys=True)


def _loads(text: Any, fallback: Any) -> Any:
    if not text:
        return fallback
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return fallback


def _row_to_job(row: Any) -> Job:
    return Job(
        job_id=row["job_id"],
        run_id=row["run_id"],
        scope_id=row["scope_id"],
        kind=row["kind"],
        state=row["state"],
        trigger=row["trigger_kind"],
        input_kind=row["input_kind"],
        input=_loads(row["input"], {}),
        parameters=_loads(row["parameters"], {}),
        actor=row["actor"],
        seq=int(row["seq"] or 0),
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        worker_id=row["worker_id"],
        worker_heartbeat_at=row["worker_heartbeat_at"],
        attempt=int(row["attempt"] or 0),
        error=row["error"],
        dedupe_key=row["dedupe_key"],
    )


class SqliteJobStore:
    """A `JobStore` on SQLite, in WAL mode.

    SQLite because the requirement is a *conditional* write, not a database: the
    standard library provides one, WAL gives readers concurrent with the single
    writer, and `BEGIN IMMEDIATE` makes the claim a real compare-and-set instead of
    a read-then-write that two workers can both win.

    private rules
    ------------
    One connection, shared across threads **with a lock**, and
    `check_same_thread=False`. Both halves are required and neither is optional:

    - the worker heartbeats from a second thread while the run is executing, so a
      default `sqlite3` connection (`check_same_thread=True`) raises
      `ProgrammingError` on the very first heartbeat — which the heartbeat loop
      catches and treats as "stop", leaving the page to report a healthy run as
      stale;
    - the app holds one store per scope across request threads, so a second
      concurrent request would raise the same error out of a plain `get()`.

    SQLite's own locking is per *connection*, so it coordinates processes but says
    nothing about threads sharing one. The lock is that coordination, and it is the
    reason every read and write below goes through `_lock`.
    """

    concurrency_safe = True

    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            str(self.path), isolation_level=None, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        self._install_pragmas()
        with self._lock:
            self._connection.executescript(_SCHEMA)

    def _install_pragmas(self) -> None:
        with self._lock:
            cur = self._connection.cursor()
            # WAL so a page reading job state never blocks the worker writing it.
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()

    @contextmanager
    def _write_tx(self) -> Iterator[sqlite3.Cursor]:
        """A write transaction that takes the lock, then the database lock.

        `BEGIN IMMEDIATE` acquires the write lock before the read, so the
        SELECT-then-UPDATE in `claim` cannot interleave with another writer. A
        deferred transaction would let two workers read the same `QUEUED` row.

        `BEGIN` itself is inside the `try`: a statement that fails before the
        transaction opens would otherwise leave the cursor open and make the
        rollback mask the real error.
        """
        with self._lock:
            cur = self._connection.cursor()
            try:
                cur.execute("BEGIN IMMEDIATE")
                yield cur
            except Exception:
                try:
                    cur.execute("ROLLBACK")
                except sqlite3.Error:
                    pass  # nothing to roll back; the original error is the story
                raise
            else:
                cur.execute("COMMIT")
            finally:
                cur.close()

    # -- writes ------------------------------------------------------------

    def enqueue(self, job: Job) -> Job:
        if job.input_kind not in INPUT_KINDS:
            raise JobStoreError(f"unknown input_kind {job.input_kind!r}")
        if job.trigger not in TRIGGERS:
            raise JobStoreError(f"unknown trigger {job.trigger!r}")
        with self._write_tx() as cur:
            cur.execute(
                f"INSERT INTO jobs ({_COLUMNS}) VALUES (" + ",".join("?" * 19) + ")",
                (
                    None, job.job_id, job.run_id, job.scope_id, job.kind, QUEUED,
                    job.trigger, job.input_kind, _dumps(job.input),
                    _dumps(job.parameters), job.actor, job.created_at,
                    "", "", "", "", 0, "", job.dedupe_key,
                ),
            )
            seq = cur.lastrowid
        stored = self.get(job.job_id)
        if stored is None:  # pragma: no cover - the insert just succeeded
            raise JobStoreError(f"job {job.job_id} vanished immediately after insert")
        stored.seq = int(seq or stored.seq)
        return stored

    def claim(self, worker_id: str) -> Optional[Job]:
        now = utc_now()
        with self._write_tx() as cur:
            row = cur.execute(
                f"SELECT {_COLUMNS} FROM jobs WHERE state = ? ORDER BY seq LIMIT 1",
                (QUEUED,),
            ).fetchone()
            if row is None:
                return None
            job_id = row["job_id"]
            cur.execute(
                "UPDATE jobs SET state = ?, worker_id = ?, started_at = ?, "
                "worker_heartbeat_at = ?, attempt = attempt + 1 "
                "WHERE job_id = ? AND state = ?",
                (RUNNING, worker_id, now, now, job_id, QUEUED),
            )
            if cur.rowcount != 1:  # pragma: no cover - guarded by BEGIN IMMEDIATE
                return None
        return self.get(job_id)

    def heartbeat(self, job_id: str, worker_id: str) -> bool:
        with self._write_tx() as cur:
            cur.execute(
                "UPDATE jobs SET worker_heartbeat_at = ? "
                "WHERE job_id = ? AND worker_id = ? AND state = ?",
                (utc_now(), job_id, worker_id, RUNNING),
            )
            return cur.rowcount == 1

    def finish(self, job_id: str, state: str, error: str = "",
               worker_id: Optional[str] = None) -> Optional[Job]:
        """End a `RUNNING` job. With `worker_id`, only its own worker may end it.

        The ownership check matters because a job can outlive the worker that
        claimed it: without it, a restarted or duplicated worker could resolve a job
        it never ran. It is not a recovery policy — nothing here re-queues.
        """
        if state not in TERMINAL_STATES:
            raise JobStoreError(f"finish() needs a terminal state, got {state!r}")
        sql = ("UPDATE jobs SET state = ?, finished_at = ?, error = ? "
               "WHERE job_id = ? AND state = ?")
        args: List[Any] = [state, utc_now(), str(error)[:2000], job_id, RUNNING]
        if worker_id is not None:
            sql += " AND worker_id = ?"
            args.append(worker_id)
        with self._write_tx() as cur:
            cur.execute(sql, tuple(args))
            if cur.rowcount != 1:
                return None
        return self.get(job_id)

    # -- reads -------------------------------------------------------------

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            row = self._connection.execute(
                f"SELECT {_COLUMNS} FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return _row_to_job(row) if row else None

    def find_by_run(self, run_id: str,
                    scope_id: Optional[str] = None) -> Optional[Job]:
        """The job that produced a run id, if this store has one.

        The read side is keyed by run id (it mirrors the journal) while the queue is
        keyed by job id, so the scope guard on that side needs this lookup.
        """
        sql = f"SELECT {_COLUMNS} FROM jobs WHERE run_id = ?"
        args: List[Any] = [run_id]
        if scope_id:
            sql += " AND scope_id = ?"
            args.append(scope_id)
        sql += " ORDER BY seq DESC LIMIT 1"
        with self._lock:
            row = self._connection.execute(sql, tuple(args)).fetchone()
        return _row_to_job(row) if row else None

    def list(self, scope_id: Optional[str] = None, states: Optional[Any] = None,
             limit: int = 100) -> List[Job]:
        clauses: List[str] = []
        args: List[Any] = []
        if scope_id:
            clauses.append("scope_id = ?")
            args.append(scope_id)
        if states:
            wanted = list(states)
            clauses.append("state IN (" + ",".join("?" * len(wanted)) + ")")
            args.extend(wanted)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._connection.execute(
                f"SELECT {_COLUMNS} FROM jobs {where} ORDER BY seq DESC LIMIT ?",
                (*args, int(limit)),
            ).fetchall()
        return [_row_to_job(row) for row in rows]

    def counts(self, scope_id: Optional[str] = None) -> Dict[str, int]:
        with self._lock:
            if scope_id:
                rows = self._connection.execute(
                    "SELECT state, COUNT(*) AS n FROM jobs WHERE scope_id = ? "
                    "GROUP BY state",
                    (scope_id,),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT state, COUNT(*) AS n FROM jobs GROUP BY state"
                ).fetchall()
        return {row["state"]: int(row["n"]) for row in rows}

    # -- diagnostics -------------------------------------------------------

    def queue_depth(self, scope_id: Optional[str] = None) -> int:
        """How many jobs are waiting. Shown on the run page, because a queue with no
        consumer is the failure mode this whole layer introduces."""
        return self.counts(scope_id).get(QUEUED, 0)

    def queue_position(self, job_id: str) -> int:
        """A queued job's 1-based place in line; 0 when it is not queued.

        Position is meaningless without it: `created_at` cannot order two jobs
        written in the same millisecond, and the queue is ordered by `seq`.
        """
        with self._lock:
            row = self._connection.execute(
                "SELECT seq, state FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
            if row is None or row["state"] != QUEUED:
                return 0
            ahead = self._connection.execute(
                "SELECT COUNT(*) AS n FROM jobs WHERE state = ? AND seq < ?",
                (QUEUED, row["seq"]),
            ).fetchone()
        return int(ahead["n"]) + 1

    def close(self) -> None:
        self._connection.close()
