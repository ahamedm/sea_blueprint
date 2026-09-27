#!/usr/bin/env python3
"""
The background worker: claim a job, run it, record how it ended (YB-026 phase 2).

WHY A PROCESS, AND WHY IT IS NOT A WORKFLOW ENGINE
--------------------------------------------------
The work used to run inside the request that started it, which is why a slow
extraction showed a blank page and a client timeout threw the result away. This
process exists so the work outlives the request. It is deliberately *not* a durable
workflow engine: there is no retry policy, no timers, no cancellation, no replay.
Those are YB-037's, and the design defers them because adopting an engine before the
human-gate boundary is settled is the expensive mistake (see
`docs/design/async-run-progress.md` §13).

WHAT IT IS RESPONSIBLE FOR
--------------------------
- **One worker, one slot.** The inference backend serves one call at a time, so
  more workers would only queue inside the model server. A job is claimed only when
  this process is free, which is what makes the queue depth on the run page honest.
- **Refusing a scope it cannot run safely.** A worker re-merges the result at the
  end, and that guarded write needs a store that can refuse a stale version. On a
  file-backed scope it would silently clobber a reviewer's edit, so the scope is
  skipped with a warning rather than run unsafely.
- **Scope from the job, never from a request.** There is no request here. The store,
  the drafts and the journal are all resolved from the job's own `scope_id`, because
  a worker that guessed would run one product's document into another's graph.

USAGE
-----
    uv run sea-worker                # poll forever
    uv run sea-worker --once         # drain what is queued, then exit (tests, cron)
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import threading
import time
import uuid
from typing import Any, Dict, Optional

from core.artifacts import ArtifactStore
from core.jobs import (
    FAILED,
    GRAPH,
    INLINE,
    SOURCE,
    SUCCEEDED,
    Job,
    SqliteJobStore,
)
from core.workspace import (
    Workspace,
    backend_is_concurrency_safe,
    scope_data_dir,
    scope_drafts_dir,
)

__all__ = ["Worker", "main"]

_log = logging.getLogger("sea.worker")

HEARTBEAT_SECONDS = 10.0
IDLE_SLEEP_SECONDS = 2.0

#: Reserved for the phase that fetches a document from an external system. Named
#: rather than improvised so a `source` job fails *legibly* today instead of
#: pretending an empty document is a real extraction (which would produce a
#: confidently empty graph — the failure mode the design calls out).
SOURCE_FETCH_NOT_IMPLEMENTED = (
    "source inputs are not fetched yet: this worker has no adapter for "
    "input_kind=source (phase 4 in docs/design/async-run-progress.md)"
)


class Worker:
    """Claims jobs from every runnable scope and executes them, one at a time."""

    def __init__(
        self,
        app: Any,
        *,
        worker_id: Optional[str] = None,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
        scope_ids: Optional[list[str]] = None,
    ) -> None:
        self.app = app
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.heartbeat_seconds = heartbeat_seconds
        self.workspace: Workspace = app.config["WORKSPACE"]
        self._only = set(scope_ids) if scope_ids else None
        self._job_stores: Dict[str, SqliteJobStore] = {}
        self._artifacts: Dict[str, ArtifactStore] = {}
        self._graph_stores: Dict[str, Any] = {}

    # -- scope plumbing ----------------------------------------------------

    def job_store(self, scope_id: str) -> Optional[SqliteJobStore]:
        """The job store for a scope, or None when the scope cannot run a worker."""
        if scope_id in self._job_stores:
            return self._job_stores[scope_id]
        try:
            scope = self.workspace.scope(scope_id)
        except Exception:  # noqa: BLE001 - a scope that vanished is not runnable
            return None
        store: Optional[SqliteJobStore] = None
        if backend_is_concurrency_safe(scope.backend):
            store = SqliteJobStore(scope_data_dir(self.workspace, scope) / "jobs.sqlite")
        else:
            _log.warning(
                "scope %s uses the %s backend, which cannot guard a write; the "
                "worker will not run its jobs (%s)",
                scope_id, scope.backend, "atomic claim and a versioned apply are both "
                "required — move the scope to sqlite to run it in the background",
            )
        self._job_stores[scope_id] = store
        return store

    def _scope_ids(self) -> list[str]:
        ids = [scope.scope_id for scope in self.workspace.scopes]
        if self._only is not None:
            ids = [scope_id for scope_id in ids if scope_id in self._only]
        return ids

    def _artifact_store(self, scope_id: str) -> ArtifactStore:
        if scope_id not in self._artifacts:
            scope = self.workspace.scope(scope_id)
            self._artifacts[scope_id] = ArtifactStore(
                scope_data_dir(self.workspace, scope) / "artifacts"
            )
        return self._artifacts[scope_id]

    def graph_store(self, scope_id: str) -> Any:
        """The scope's graph store, cached.

        Opening one per job would build a fresh SQLAlchemy engine (and connection
        pool) every run and never dispose it — a slow leak in a process that is
        meant to run for days.
        """
        if scope_id not in self._graph_stores:
            self._graph_stores[scope_id] = self.workspace.open_store(scope_id)
        return self._graph_stores[scope_id]

    # -- running -----------------------------------------------------------

    def run_once(self) -> Optional[Job]:
        """Claim and run at most one job, from any runnable scope. None if idle.

        One job per call, not one per scope: the single inference slot is global to
        this process, so the loop must not start a second run just because another
        scope has work.

        A locked database (another writer holding the SQLite lock past
        `busy_timeout`) is a transient condition, not a reason to die: the process
        is meant to run for days, so it logs and reports idle.
        """
        for scope_id in self._scope_ids():
            try:
                store = self.job_store(scope_id)
            except Exception as exc:  # noqa: BLE001 - one bad scope is not all of them
                _log.warning("could not open the job store for %s: %s", scope_id, exc)
                continue
            if store is None:
                continue
            try:
                job = store.claim(self.worker_id)
            except sqlite3.Error as exc:
                _log.warning("claim on %s failed (will retry): %s", scope_id, exc)
                continue
            if job is None:
                continue
            self._execute(store, job)
            return job
        return None

    def drain(self, limit: int = 1000) -> int:
        """Run queued jobs until the queue is empty. For `--once` and for tests."""
        done = 0
        while done < limit:
            if self.run_once() is None:
                break
            done += 1
        return done

    def _execute(self, store: SqliteJobStore, job: Job) -> None:
        stop = threading.Event()
        beat = threading.Thread(
            target=self._heartbeat_loop, args=(store, job, stop), daemon=True
        )
        beat.start()
        try:
            self._dispatch(job)
        except Exception as exc:  # noqa: BLE001 - the job records why it failed
            _log.warning("job %s failed: %s: %s", job.job_id, type(exc).__name__, exc)
            self._settle(store, job, FAILED, error=f"{type(exc).__name__}: {exc}")
        else:
            self._settle(store, job, SUCCEEDED)
        finally:
            stop.set()
            beat.join(timeout=1.0)

    def _settle(self, store: SqliteJobStore, job: Job, state: str, error: str = "") -> None:
        """Record the outcome, and never let a transient store error lose the run's
        result: the graph write already happened, and the job's terminal state is the
        only thing left to record."""
        try:
            store.finish(job.job_id, state, error=error, worker_id=self.worker_id)
        except sqlite3.Error as exc:
            _log.error(
                "could not record job %s as %s (%s); the run's own result is "
                "unaffected but the queue will show it as RUNNING",
                job.job_id, state, exc,
            )

    def _heartbeat_loop(self, store: SqliteJobStore, job: Job, stop: threading.Event) -> None:
        """Keep saying 'still working' while the run is in flight.

        The run page needs to distinguish 'slow' from 'the worker died', and the only
        evidence available without a supervisor is silence. Nothing acts on a missed
        heartbeat here — that is YB-037's recovery rule — but a stale one is visible.
        """
        while not stop.wait(self.heartbeat_seconds):
            try:
                if not store.heartbeat(job.job_id, self.worker_id):
                    return
            except Exception as exc:  # noqa: BLE001 - a lost heartbeat is not fatal
                _log.warning("heartbeat failed for %s: %s", job.job_id, exc)
                return

    # -- the two job kinds -------------------------------------------------

    def _dispatch(self, job: Job) -> None:
        if job.kind == "ingest":
            self._run_ingest(job)
        elif job.kind == "design":
            self._run_design(job)
        else:
            raise ValueError(f"unknown job kind {job.kind!r}")

    def _acquire_text(self, job: Job) -> str:
        """Resolve the job's input to the text the extractor reads.

        This is the acquisition step, before any pass. `inline` dereferences the
        bytes stored at enqueue; `graph` has no document at all; `source` is a
        reference the worker must fetch, which is not built yet — and saying so is
        the point, because extracting an empty string would produce a valid-looking
        empty graph rather than a visible failure.
        """
        if job.input_kind == INLINE:
            digest = job.input.get("artifact") or ""
            if not digest:
                raise ValueError("inline job carries no artifact digest")
            payload = self._artifact_store(job.scope_id).get(digest)
            return payload.decode("utf-8", errors="replace")
        if job.input_kind == SOURCE:
            raise NotImplementedError(SOURCE_FETCH_NOT_IMPLEMENTED)
        raise ValueError(f"{job.kind} job does not read a document (input_kind={job.input_kind})")

    def _journal(self):
        # One journal per worker process — a client and a connection pool that
        # should not be rebuilt per job. A Worker instance is one process's worker.
        factory = self.app.config.get("JOURNAL_FACTORY")
        if not hasattr(self, "_journal_singleton"):
            self._journal_singleton = factory() if factory else None
        return self._journal_singleton

    def _run_ingest(self, job: Job) -> None:
        from app.runner import run_ingest

        if job.input_kind not in (INLINE, SOURCE):
            raise ValueError(
                f"ingest jobs read a document; this job has input_kind={job.input_kind}"
            )
        # `_acquire_text` is where a `source` job fails with the phase-4 message,
        # rather than being rejected here as if it were a malformed job.
        document = self._acquire_text(job)
        store = self.graph_store(job.scope_id)
        params = job.parameters or {}
        run_ingest(
            store=store,
            journal=self._journal(),
            scope_id=job.scope_id,
            document=document,
            filename=str(params.get("filename") or ""),
            doc_type=str(params.get("document_type") or "requirements"),
            extractor_factory=self.app.config["EXTRACTOR_FACTORY"],
            initiative_id=str(params.get("initiative_id") or ""),
            domain_pack=str(params.get("domain_pack") or ""),
            actor=job.actor,
            revision_label=str(params.get("revision_label") or ""),
            note=str(params.get("note") or ""),
            document_digest=str(job.input.get("artifact") or ""),
            # The id minted when the job was enqueued: the journal this worker
            # publishes must join the record it eventually writes.
            run_id=job.run_id,
        )

    def _run_design(self, job: Job) -> None:
        from app.runner import run_design
        from core.knowledge import DesignDraftStore

        if job.input_kind != GRAPH:
            raise ValueError(
                f"design jobs read the graph; this job has input_kind={job.input_kind}"
            )
        scope = self.workspace.scope(job.scope_id)
        store = self.graph_store(job.scope_id)
        snapshot = store.load_working()
        baselines = store.baselines()
        baseline = store.load_revision(baselines[0].id).graph if baselines else None
        params = job.parameters or {}
        run_design(
            store=store,
            journal=self._journal(),
            scope_id=job.scope_id,
            snapshot=snapshot,
            design_factory=self.app.config["DESIGN_FACTORY"],
            drafts=DesignDraftStore(scope_drafts_dir(self.workspace, scope)).ensure(),
            baseline=baseline,
            base_ref=str(job.input.get("base_ref") or (baselines[0].id if baselines else "")),
            initiative_id=str(params.get("initiative_id") or ""),
            domain_pack=str(params.get("domain_pack") or ""),
            run_id=job.run_id,
        )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="SEA background worker (YB-026)")
    parser.add_argument("--once", action="store_true",
                        help="drain the queue and exit instead of polling")
    parser.add_argument("--scope", default="", help="only run this scope's jobs")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from app import create_app

    app = create_app()
    worker = Worker(app, scope_ids=[args.scope] if args.scope else None)
    _log.info(
        "worker %s watching scopes: %s", worker.worker_id,
        ", ".join(worker._scope_ids()) or "(none)",
    )

    if args.once:
        _log.info("ran %d job(s)", worker.drain())
        return 0

    try:
        while True:
            try:
                if worker.run_once() is None:
                    time.sleep(IDLE_SLEEP_SECONDS)
            except Exception as exc:  # noqa: BLE001 - a worker must outlive one bad job
                _log.error("worker loop error (continuing): %s: %s", type(exc).__name__, exc)
                time.sleep(IDLE_SLEEP_SECONDS)
    except KeyboardInterrupt:
        _log.info("worker %s stopped", worker.worker_id)
    return 0


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
