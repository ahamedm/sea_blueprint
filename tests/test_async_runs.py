"""
The asynchronous increment, end to end (YB-026 phases 2 and 3a).

WHAT THIS FILE PROVES. The parts have unit tests — the journal, the sink, the job
store, the runner — and the joins between them are exactly where an async feature
usually turns out to be wired to nothing. So this drives the real path: a POST that
returns before the model runs, a worker that claims the job, the graph that gains
facts afterwards, and a run page that says what happened.

A SQLite-backed scope is used deliberately. The worker re-merges at the end under a
version guard, and a file-backed scope cannot guard a write — so a file-backed scope
is *not* runnable in the background, and one test below pins that refusal rather
than leaving it to be discovered in production.
"""

from __future__ import annotations

import pytest

from core.artifacts import ArtifactStore
from core.jobs import FAILED, QUEUED, SUCCEEDED, SOURCE, Job, new_job_id
from core.events import RUN_FINISHED, RUN_STARTED, RunEvent

MANIFEST = """\
workspace_id: sea
name: SEA
scopes:
  - scope_id: payments
    name: Payments
    backend: sqlite
    path: payments
"""


class FakeJournal:
    def __init__(self):
        self.rows: list[RunEvent] = []

    def append(self, event: RunEvent) -> str:
        self.rows.append(event)
        return f"{len(self.rows)}-0"

    def read(self, run_id: str, since=None):
        # Stamp the id, as a real journal does: the cursor contract is untested
        # without it, and the fake would quietly make replay impossible.
        import dataclasses

        rows = [(f"{i + 1}-0", e) for i, e in enumerate(self.rows) if e.run_id == run_id]
        return [dataclasses.replace(e, stream_id=sid) for sid, e in rows]

    def wait(self, run_id, since=None, timeout_ms=500, count=100):
        # A journal that retains events would block here; the fake has nothing new
        # between calls, so it returns immediately like `NullJournal`.
        return []

    def close(self, run_id: str, ttl_seconds=None) -> None:
        return None


@pytest.fixture
def sqlite_root(tmp_path):
    """A workspace whose one scope can actually run a background job."""
    root = tmp_path / "sea"
    root.mkdir()
    (root / "workspace.yaml").write_text(MANIFEST, encoding="utf-8")
    return root


@pytest.fixture
def async_app(sqlite_root, fake_factory, design_factory):
    from app import create_app

    journal = FakeJournal()
    app = create_app(
        {"TESTING": True, "STORE_ROOT": str(sqlite_root), "REVIEWER": "tester"},
        store_root=str(sqlite_root),
        extractor_factory=fake_factory,
        design_factory=design_factory,
        journal_factory=lambda: journal,
    )
    app.config["TEST_JOURNAL"] = journal
    return app


def _graph(app):
    def read():
        return app.config["WORKSPACE"].open_store("payments").load_working().graph
    return read


# ============================================================================
# The request only records the work
# ============================================================================


def test_posting_ingest_returns_before_the_model_runs(async_app, sqlite_root):
    """The measured problem was a POST held open for 62 s to 25 min. After this,
    the request's whole job is to write a record and a redirect."""
    calls: list[dict] = []

    class Counting:
        def run(self, input_data):
            calls.append(input_data)
            raise AssertionError("the model ran inside the request")

    async_app.config["EXTRACTOR_FACTORY"] = lambda _t: Counting()
    client = async_app.test_client()

    response = client.post(
        "/ingest",
        data={"text": "FR-PM-001 requirements body", "type": "requirements",
              "text_name": "brief.md"},
    )

    assert response.status_code == 302
    assert "/runs/job_" in response.headers["Location"]
    assert calls == [], "the extractor must not run in the request"


def test_the_queued_job_names_the_bytes_it_will_read(async_app, sqlite_root):
    client = async_app.test_client()
    client.post("/ingest", data={"text": "FR-PM-001 body", "type": "requirements",
                                 "text_name": "brief.md"})

    from core.jobs import SqliteJobStore
    from core.workspace import load_workspace, scope_data_dir

    workspace = load_workspace(sqlite_root)
    scope = workspace.scope("payments")
    store = SqliteJobStore(scope_data_dir(workspace, scope) / "jobs.sqlite")
    jobs = store.list("payments")

    assert len(jobs) == 1
    job = jobs[0]
    assert job.state == QUEUED
    assert job.input_kind == "inline"
    # The document travelled as a digest, not as a blob in the queue: the worker
    # reads the bytes from the artifact store.
    digest = job.input["artifact"]
    assert len(digest) == 64
    artifact = ArtifactStore(scope_data_dir(workspace, scope) / "artifacts")
    assert artifact.get(digest).decode("utf-8") == "FR-PM-001 body"


def test_the_run_page_says_it_is_queued_and_where(async_app):
    client = async_app.test_client()
    response = client.post("/ingest", data={"text": "FR-PM-001 body",
                                            "type": "requirements"})
    page = client.get(response.headers["Location"])

    assert page.status_code == 200
    assert b"QUEUED" in page.data
    # A queue with a position is legible; a spinner is not.
    assert b"position" in page.data or b"waiting" in page.data


def test_an_unknown_run_is_a_404_not_an_empty_page(async_app):
    assert async_app.test_client().get("/runs/job_nope").status_code == 404


# ============================================================================
# The worker does the work
# ============================================================================


def test_the_worker_runs_the_job_and_the_graph_gains_the_facts(async_app):
    from app.worker import Worker

    client = async_app.test_client()
    response = client.post("/ingest", data={"text": "FR-PM-001 body",
                                            "type": "requirements",
                                            "text_name": "brief.md"})
    assert _graph(async_app)().nodes == {}

    worker = Worker(async_app, worker_id="worker-test")
    claimed = worker.run_once()

    assert claimed is not None and claimed.kind == "ingest"
    # The work landed in the record...
    graph = _graph(async_app)()
    assert graph.nodes
    assert claimed.run_id in graph.runs

    # ...the job says so...
    from core.jobs import SqliteJobStore
    from core.workspace import load_workspace, scope_data_dir

    workspace = load_workspace(async_app.config["STORE_ROOT"])
    store = SqliteJobStore(scope_data_dir(workspace, workspace.scope("payments")) / "jobs.sqlite")
    job = store.get(claimed.job_id)
    assert job.state == SUCCEEDED
    assert job.finished_at

    # ...and the run page now shows the verdict rather than a spinner.
    page = client.get(response.headers["Location"])
    assert b"SUCCEEDED" in page.data
    assert graph.runs[claimed.run_id].completeness.encode() in page.data


def test_the_worker_publishes_the_run_into_the_journal(async_app):
    """The job id and the run id are minted together, so the stream a page reads is
    the stream of the run the worker actually performs."""
    from app.worker import Worker

    async_app.test_client().post(
        "/ingest", data={"text": "FR-PM-001 body", "type": "requirements"}
    )
    Worker(async_app, worker_id="worker-test").run_once()

    events = async_app.config["TEST_JOURNAL"].rows
    kinds = [event.kind for event in events]
    assert kinds[0] == RUN_STARTED
    assert kinds[-1] == RUN_FINISHED, kinds
    assert len({event.run_id for event in events}) == 1


def test_the_polling_fragment_stops_asking_once_the_run_is_terminal(async_app):
    from app.worker import Worker

    client = async_app.test_client()
    response = client.post("/ingest", data={"text": "FR-PM-001 body",
                                            "type": "requirements"})
    job_url = response.headers["Location"]

    in_flight = client.get(f"{job_url}/events.html")
    assert b"hx-trigger" in in_flight.data, "a queued run must poll itself"

    Worker(async_app, worker_id="worker-test").run_once()

    done = client.get(f"{job_url}/events.html")
    assert b"hx-trigger" not in done.data, (
        "polling must stop on the terminal event, not run forever"
    )
    assert b"SUCCEEDED" in done.data


def test_an_empty_queue_claims_nothing(async_app):
    from app.worker import Worker

    assert Worker(async_app, worker_id="w").run_once() is None


def test_the_worker_heartbeats_while_a_run_is_in_flight(async_app, sqlite_root):
    """The heartbeat is how the page tells 'slow' from 'the worker died'. It is
    written from a second thread, so it only works if the job store's connection is
    shared safely — the defect that made the feature silently do nothing."""
    import time

    from agents.base_agent import AgentResult
    from app.worker import Worker
    from core.jobs import SqliteJobStore
    from core.workspace import load_workspace, scope_data_dir

    workspace = load_workspace(sqlite_root)
    store = SqliteJobStore(scope_data_dir(workspace, workspace.scope("payments")) / "jobs.sqlite")
    seen: dict[str, str] = {}

    class Slow:
        def run(self, input_data):
            time.sleep(0.30)  # long enough for several heartbeats
            job = store.list("payments")[0]
            seen["heartbeat"] = job.worker_heartbeat_at
            seen["started"] = job.started_at
            return AgentResult(success=True, output={"triples": []},
                               metadata={"model_id": "slow"})

    async_app.config["EXTRACTOR_FACTORY"] = lambda _t: Slow()
    async_app.test_client().post(
        "/ingest", data={"text": "FR-PM-001 body", "type": "requirements"}
    )

    Worker(async_app, worker_id="w", heartbeat_seconds=0.05).run_once()

    # ISO-8601 UTC timestamps sort lexicographically, which is why the format is
    # fixed (`Z`, millisecond precision) rather than left to `isoformat()`.
    assert seen["heartbeat"] > seen["started"], "the heartbeat never advanced"


def test_a_queue_with_no_worker_is_said_out_loud(async_app, sqlite_root):
    """The new silent failure. Nothing starts the worker automatically, so a job
    older than a worker would plausibly take to notice must be surfaced at submit
    time — otherwise the page looks busy while nothing is consuming it."""
    from core.jobs import SqliteJobStore, new_job_id
    from core.workspace import load_workspace, scope_data_dir

    workspace = load_workspace(sqlite_root)
    scope = workspace.scope("payments")
    store = SqliteJobStore(scope_data_dir(workspace, scope) / "jobs.sqlite")
    store.enqueue(Job(
        job_id=new_job_id(), run_id="run_stale", scope_id="payments", kind="ingest",
        created_at="2020-01-01T00:00:00.000Z",
    ))

    response = async_app.test_client().post(
        "/ingest", data={"text": "FR-PM-001 body", "type": "requirements"},
        follow_redirects=True,
    )

    assert b"sea-worker" in response.data
    assert b"No worker appears to be running" in response.data


# ============================================================================
# The refusals
# ============================================================================


def test_a_source_job_fails_legibly_instead_of_extracting_nothing(async_app):
    """The failure this prevents: a fetch that is not implemented yet quietly
    yielding an empty document, which extracts to an empty graph that looks like a
    real answer."""
    from app.worker import Worker
    from core.jobs import SqliteJobStore
    from core.workspace import load_workspace, scope_data_dir

    workspace = load_workspace(async_app.config["STORE_ROOT"])
    scope = workspace.scope("payments")
    store = SqliteJobStore(scope_data_dir(workspace, scope) / "jobs.sqlite")
    store.enqueue(Job(
        job_id=new_job_id(), run_id="run_source", scope_id="payments", kind="ingest",
        input_kind=SOURCE,
        input={"system": "confluence", "item_id": "12345"},
        parameters={"document_type": "requirements"},
    ))

    Worker(async_app, worker_id="w").run_once()

    failed = store.list("payments")[0]
    assert failed.state == FAILED
    assert "not fetched yet" in failed.error, (
        "the failure must name the missing phase, not read as a malformed job"
    )
    # And nothing was merged.
    assert _graph(async_app)().nodes == {}


def test_the_app_reads_its_own_settings_from_dotenv(tmp_path, monkeypatch):
    """`SEA_DATA_DIR` and friends live in `.env`, and the app resolved `STORE_ROOT`
    before any agent (which does load it) was constructed — so a declared workspace
    was invisible and the app fell back to `data/sea`. Real environment variables
    still win, so an explicit override on the command line keeps working."""
    import os

    import app as app_module

    env_file = tmp_path / ".env"
    env_file.write_text("SEA_DATA_DIR=data/from-dotenv\n", encoding="utf-8")
    monkeypatch.delenv("SEA_DATA_DIR", raising=False)

    app_module.load_env(str(env_file))
    assert os.environ["SEA_DATA_DIR"] == "data/from-dotenv"

    # A real variable is not overwritten by the file.
    monkeypatch.setenv("SEA_DATA_DIR", "data/from-shell")
    app_module.load_env(str(env_file))
    assert os.environ["SEA_DATA_DIR"] == "data/from-shell"


def test_a_file_backed_scope_is_not_runnable_in_the_background(tmp_path, fake_factory):
    """A worker re-merges under a version guard. The file backend has no guard and
    says so, so its jobs must not be run — refusing is the honest outcome, because
    the alternative is silently overwriting a reviewer's edit."""
    from app import create_app
    from app.worker import Worker

    root = tmp_path / "bare"
    root.mkdir()
    app = create_app(
        {"TESTING": True, "STORE_ROOT": str(root), "REVIEWER": "tester"},
        store_root=str(root),
        extractor_factory=fake_factory,
    )
    worker = Worker(app, worker_id="w")

    assert app.config["WORKSPACE"].scopes[0].backend == "file"
    assert worker.job_store(app.config["WORKSPACE"].scopes[0].scope_id) is None
    # With no job store, the route still runs the work synchronously — so a
    # file-backed install keeps working exactly as it did.
    response = app.test_client().post(
        "/ingest", data={"text": "FR-PM-001 body", "type": "requirements"}
    )
    assert response.status_code == 302
    assert "/runs/" not in response.headers["Location"]


# ============================================================================
# The default deployment: no journal at all
# ============================================================================


@pytest.fixture
def no_journal_app(sqlite_root, fake_factory, design_factory, monkeypatch):
    """The shipping configuration has no Valkey, so there are never any events.

    Everything the page says must therefore survive `NullJournal` — a run that is
    over must stop polling, and a completed run must still show its verdict.
    """
    from app import create_app

    monkeypatch.delenv("SEA_VALKEY_HOST", raising=False)
    return create_app(
        {"TESTING": True, "STORE_ROOT": str(sqlite_root), "REVIEWER": "tester"},
        store_root=str(sqlite_root),
        extractor_factory=fake_factory,
        design_factory=design_factory,
        journal_factory=None,
    )


def test_polling_stops_without_any_journal_events(no_journal_app):
    from app.worker import Worker

    client = no_journal_app.test_client()
    response = client.post("/ingest", data={"text": "FR-PM-001 body",
                                            "type": "requirements"})
    job_url = response.headers["Location"]

    assert b"hx-trigger" in client.get(f"{job_url}/events.html").data
    Worker(no_journal_app, worker_id="w").run_once()
    assert b"hx-trigger" not in client.get(f"{job_url}/events.html").data


def test_a_design_run_shows_its_verdict_without_a_journal(no_journal_app):
    """A design run's record lives with its DRAFT, not in the working graph, so the
    verdict cannot be read the way an ingest run's is."""
    from app.worker import Worker

    client = no_journal_app.test_client()
    client.post("/ingest", data={"text": "FR-PM-001 body", "type": "requirements"})
    Worker(no_journal_app, worker_id="w").run_once()

    response = client.post("/design/draft", data={})
    assert response.status_code == 302 and "/runs/" in response.headers["Location"]
    job_url = response.headers["Location"]
    Worker(no_journal_app, worker_id="w").run_once()

    page = client.get(job_url)
    assert b"SUCCEEDED" in page.data
    assert b"COMPLETE" in page.data, "a finished design run must show its verdict"
    assert b"hx-trigger" not in client.get(f"{job_url}/events.html").data


# ============================================================================
# The events API's guard and cursor
# ============================================================================


def test_the_events_api_404s_an_unknown_run_once_jobs_exist(async_app):
    """With a job store, a run id nobody queued here is not this scope's to show.
    Answering 200 with an empty list reads as "nothing yet", not "no such run"."""
    assert async_app.test_client().get(
        "/api/runs/run_nope/events"
    ).status_code == 404


def test_the_events_api_keeps_reporting_terminal_after_the_cursor_advances(async_app):
    """`terminal` is a property of the run, not of the slice the cursor asked for.
    Computed from the slice, a client that consumed the last event would poll
    forever."""
    from app.worker import Worker

    client = async_app.test_client()
    response = client.post("/ingest", data={"text": "FR-PM-001 body",
                                            "type": "requirements"})
    Worker(async_app, worker_id="w").run_once()

    # Find the run id from the enqueued job (the page URL carries the job id).
    from core.jobs import SqliteJobStore
    from core.workspace import load_workspace, scope_data_dir

    workspace = load_workspace(async_app.config["STORE_ROOT"])
    store = SqliteJobStore(scope_data_dir(workspace, workspace.scope("payments")) / "jobs.sqlite")
    run_id = store.list("payments")[0].run_id

    first = client.get(f"/api/runs/{run_id}/events").get_json()
    assert first["terminal"] is True
    assert first["cursor"]

    second = client.get(
        f"/api/runs/{run_id}/events?since={first['cursor']}"
    ).get_json()
    assert second["events"] == []
    assert second["terminal"] is True, "the client must be able to stop"


# ============================================================================
# Bytes, and the queue with no consumer
# ============================================================================


def test_the_artifact_is_the_bytes_as_uploaded(async_app, sqlite_root):
    """A Latin-1 upload must not be stored as the UTF-8 re-encoding of lossily
    decoded text: that makes the digest address a different object."""
    import io

    raw = "café naïve".encode("latin-1")
    async_app.test_client().post(
        "/ingest",
        data={"document": (io.BytesIO(raw), "latin1.md"), "type": "requirements"},
        content_type="multipart/form-data",
    )

    from core.artifacts import ArtifactStore
    from core.jobs import SqliteJobStore
    from core.workspace import load_workspace, scope_data_dir

    workspace = load_workspace(sqlite_root)
    scope = workspace.scope("payments")
    store = SqliteJobStore(scope_data_dir(workspace, scope) / "jobs.sqlite")
    digest = store.list("payments")[0].input["artifact"]
    stored = ArtifactStore(scope_data_dir(workspace, scope) / "artifacts").get(digest)

    assert stored == raw, "the store must address the bytes it was given"
    assert ArtifactStore.digest(raw) == digest


def test_a_queued_job_with_no_worker_says_so_on_its_page(async_app, sqlite_root):
    from core.jobs import SqliteJobStore, new_job_id
    from core.workspace import load_workspace, scope_data_dir

    workspace = load_workspace(sqlite_root)
    scope = workspace.scope("payments")
    store = SqliteJobStore(scope_data_dir(workspace, scope) / "jobs.sqlite")
    job = store.enqueue(Job(
        job_id=new_job_id(), run_id="run_old", scope_id="payments", kind="ingest",
        created_at="2020-01-01T00:00:00.000Z",
    ))

    page = async_app.test_client().get(f"/runs/{job.job_id}")

    # Phrase chosen so a template line-wrap cannot hide it.
    assert b"Nothing has taken this job" in page.data
    assert b"sea-worker" in page.data


# ============================================================================
# The ingest page: where a background run is reachable afterwards
# ============================================================================


def test_the_ingest_page_lists_recent_runs(async_app, sqlite_root):
    """Without this, a run is reachable only through the redirect that created it:
    navigate away and there is no route back to it."""
    from core.jobs import SqliteJobStore
    from core.workspace import load_workspace, scope_data_dir

    client = async_app.test_client()
    client.post("/ingest", data={"text": "FR-PM-001 body", "type": "requirements"})

    workspace = load_workspace(sqlite_root)
    store = SqliteJobStore(scope_data_dir(workspace, workspace.scope("payments")) / "jobs.sqlite")
    job_id = store.list("payments")[0].job_id

    page = client.get("/ingest")
    assert b"Background runs" in page.data
    assert b"background worker" in page.data, "the page must say the work is async"
    assert f"/runs/{job_id}".encode() in page.data


def test_the_ingest_page_says_why_a_file_backed_scope_is_synchronous(
    tmp_path, fake_factory
):
    """An inert async path looks like a bug. The page has to explain the rule —
    a store that cannot guard a write cannot be run by the worker."""
    from app import create_app

    root = tmp_path / "bare"
    root.mkdir()
    app = create_app(
        {"TESTING": True, "STORE_ROOT": str(root), "REVIEWER": "tester"},
        store_root=str(root),
        extractor_factory=fake_factory,
    )

    page = app.test_client().get("/ingest")
    assert b"synchronous" in page.data
    assert b"backend: sqlite" in page.data
    assert b"This scope uses the file store" in page.data


# ============================================================================
# The two acceptance checks that closed YB-026
# ============================================================================


def test_two_submits_produce_one_running_job_and_one_queued(async_app, sqlite_root):
    """Two submits must never become two concurrent model runs.

    The single-slot model is the constraint the whole queue exists for, so this is
    asserted at the route level: two POSTs, one worker, and while the first job is
    inside the model the second is still waiting — with exactly one call made.
    """
    import threading

    from agents.base_agent import AgentResult
    from app.worker import Worker
    from core.jobs import SqliteJobStore
    from core.workspace import load_workspace, scope_data_dir

    started = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    class Blocking:
        def run(self, input_data):
            calls.append(1)
            started.set()
            assert release.wait(10), "the test never released the extractor"
            return AgentResult(success=True, output={"triples": []},
                               metadata={"model_id": "blocking"})

    async_app.config["EXTRACTOR_FACTORY"] = lambda _t: Blocking()
    client = async_app.test_client()
    client.post("/ingest", data={"text": "first document", "type": "requirements"})
    client.post("/ingest", data={"text": "second document", "type": "requirements"})

    workspace = load_workspace(sqlite_root)
    store = SqliteJobStore(scope_data_dir(workspace, workspace.scope("payments")) / "jobs.sqlite")

    worker = Worker(async_app, worker_id="worker-test")
    thread = threading.Thread(target=worker.run_once)
    thread.start()
    assert started.wait(10), "the worker never started the first job"
    try:
        counts = store.counts("payments")
        assert counts.get("RUNNING") == 1, counts
        assert counts.get("QUEUED") == 1, counts
        assert len(calls) == 1, "a second model call started while one was in flight"
    finally:
        release.set()
        thread.join(10)

    # The worker took one job and stopped; the second is still the queue.
    assert store.counts("payments").get("QUEUED") == 1


def test_a_review_edit_during_a_run_survives_the_apply(async_app, sqlite_root):
    """The property the version guard exists for, in its common form.

    A reviewer edits the graph *while* the model is running. The worker re-reads the
    working set immediately before merging, so the edit is folded in rather than
    overwritten — the run's facts and the human's edit both end up in the graph.
    """
    from app.worker import Worker
    from core.knowledge import Node
    from core.workspace import load_workspace

    def competing_edit():
        store = load_workspace(sqlite_root).open_store("payments")
        snapshot = store.load_working()
        snapshot.graph.nodes["human_added_node"] = Node(
            id="human_added_node", kind="Container", label="Added while the run ran"
        )
        store.save_working(snapshot.graph, snapshot.log, snapshot.meta,
                           expected_version=snapshot.meta.get("version"))

    real_factory = async_app.config["EXTRACTOR_FACTORY"]

    class EditsWhileRunning:
        def run(self, input_data):
            competing_edit()  # a reviewer commits mid-run
            return real_factory("requirements").run(input_data)

    async_app.config["EXTRACTOR_FACTORY"] = lambda _t: EditsWhileRunning()
    client = async_app.test_client()
    response = client.post("/ingest", data={"text": "FR-PM-001 body",
                                            "type": "requirements"})
    Worker(async_app, worker_id="worker-test").run_once()

    graph = load_workspace(sqlite_root).open_store("payments").load_working().graph
    assert "human_added_node" in graph.nodes, "the human edit was overwritten"
    assert len(graph.nodes) > 1, "the run's own facts were not merged"
    # The job still succeeded: an edit that arrived before the read is not a conflict.
    assert b"SUCCEEDED" in client.get(response.headers["Location"]).data


def test_a_write_landing_between_read_and_apply_fails_the_job(async_app, sqlite_root):
    """The narrow race: a commit lands after the worker has read the working set and
    before it writes. Nothing may be merged, the job must say why, and the competing
    write must still be there — a lost update is the failure this guard prevents."""
    from app.worker import Worker
    from core.jobs import SqliteJobStore
    from core.knowledge import Node
    from core.workspace import load_workspace, scope_data_dir

    real_factory = async_app.config["EXTRACTOR_FACTORY"]
    workspace = load_workspace(sqlite_root)
    scope_id = "payments"
    real = workspace.open_store(scope_id)
    competing = workspace.open_store(scope_id)

    class RacingStore:
        """Delegates, but slips a competing commit into the read→write window."""

        def __init__(self, inner):
            self._inner = inner

        def load_working(self):
            snapshot = self._inner.load_working()
            other = competing.load_working()
            other.graph.nodes["racing_node"] = Node(
                id="racing_node", kind="Container", label="Committed mid-apply"
            )
            competing.save_working(other.graph, other.log, other.meta,
                                   expected_version=other.meta.get("version"))
            return snapshot  # now stale: the worker will write against an old version

        def __getattr__(self, name):
            return getattr(self._inner, name)

    # The injected fake produces real facts, so "a run that lost the race merged
    # nothing" is an observable difference rather than an empty-to-empty comparison.
    async_app.config["EXTRACTOR_FACTORY"] = real_factory

    client = async_app.test_client()
    response = client.post("/ingest", data={"text": "FR-PM-001 body",
                                            "type": "requirements"})

    worker = Worker(async_app, worker_id="worker-test")
    worker._graph_stores[scope_id] = RacingStore(real)
    claimed = worker.run_once()
    assert claimed is not None

    store = SqliteJobStore(scope_data_dir(workspace, workspace.scope(scope_id)) / "jobs.sqlite")
    job = store.get(claimed.job_id)
    assert job.state == FAILED, job.state
    assert "StoreConflict" in job.error, job.error

    graph = load_workspace(sqlite_root).open_store(scope_id).load_working().graph
    assert "racing_node" in graph.nodes, "the competing write was lost"
    assert set(graph.nodes) == {"racing_node"}, (
        "a run that lost the race must merge nothing"
    )
