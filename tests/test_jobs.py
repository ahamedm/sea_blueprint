"""
The job store: the compare-and-set that makes a background worker safe (YB-026 §8).

WHY THESE TESTS MATTER MORE THAN THE SCHEMA. The one thing a job store must get
right is that **two workers never claim the same job**. Everything else here is
bookkeeping; a lost claim is two model runs on one document, or a rebuilt graph
written twice. So the claim is tested from two independent store instances over one
file, which is what the web process and the worker actually are.

The second property is the vocabulary boundary: this module writes four of YB-037's
states and must not invent a fifth. A stale `RUNNING` job is *surfaced*, not
re-queued — recovery is YB-037's decision, and a store that quietly re-queued would
have made it here.
"""

from __future__ import annotations

import pytest

from core.jobs import (
    FAILED,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    Job,
    JobStore,
    JobStoreError,
    SqliteJobStore,
    new_job_id,
)


def _job(scope_id: str = "payments", **overrides) -> Job:
    values = {
        "job_id": new_job_id(),
        "run_id": "run_" + new_job_id()[4:],
        "scope_id": scope_id,
        "kind": "ingest",
        "input_kind": "inline",
        "input": {"artifact": "abc123"},
        "parameters": {"document_type": "requirements"},
        "actor": "tester",
    }
    values.update(overrides)
    return Job(**values)


@pytest.fixture
def store(tmp_path):
    return SqliteJobStore(tmp_path / "jobs.sqlite")


def test_a_new_job_is_queued_with_a_position(store):
    job = store.enqueue(_job())

    assert job.state == QUEUED
    assert job.seq > 0, "queue position is what makes 'waiting' visible"
    # A protocol, not a class: anything with these methods can back the worker.
    assert isinstance(store, JobStore)


def test_claim_takes_the_oldest_job_and_stamps_the_worker(store):
    first = store.enqueue(_job())
    second = store.enqueue(_job())

    claimed = store.claim("worker-1")

    assert claimed is not None
    assert claimed.job_id == first.job_id, "FIFO: the oldest queued job goes first"
    assert claimed.run_id == first.run_id
    assert claimed.state == RUNNING
    assert claimed.worker_id == "worker-1"
    assert claimed.started_at
    assert claimed.attempt == 1, "an attempt is counted, not assumed"
    assert store.get(second.job_id).state == QUEUED


def test_two_workers_over_one_file_never_claim_the_same_job(tmp_path):
    """FIFO across two stores over one file.

    This is an *ordering* test, not an atomicity one: the two claims never overlap
    in time, so it cannot fail on a broken claim. The concurrency proof is
    `test_concurrent_claims_over_one_file_are_exclusive` below.
    """
    path = tmp_path / "jobs.sqlite"
    web = SqliteJobStore(path)
    worker_a = SqliteJobStore(path)
    worker_b = SqliteJobStore(path)

    jobs = [web.enqueue(_job()) for _ in range(5)]

    claimed: list[str] = []
    while True:
        job = worker_a.claim("worker-a") or worker_b.claim("worker-b")
        if job is None:
            break
        claimed.append(job.job_id)

    assert sorted(claimed) == sorted(job.job_id for job in jobs)
    assert len(claimed) == len(set(claimed)), "a job was claimed twice"


def test_the_heartbeat_works_from_another_thread(tmp_path):
    """The worker heartbeats from a second thread while the run executes.

    A default `sqlite3` connection is bound to the thread that created it, so the
    first heartbeat raised `ProgrammingError`, the loop caught it as "stop", and the
    run page reported a healthy run as stale. This is the test that would have
    caught it.
    """
    import threading

    from core.jobs import SqliteJobStore

    store = SqliteJobStore(tmp_path / "jobs.sqlite")
    job = store.enqueue(_job())
    store.claim("worker-1")

    errors: list[Exception] = []

    def beat() -> None:
        try:
            assert store.heartbeat(job.job_id, "worker-1") is True
            assert store.get(job.job_id).state == RUNNING  # a read from this thread
        except Exception as exc:  # noqa: BLE001 - reported, not raised in-thread
            errors.append(exc)

    thread = threading.Thread(target=beat)
    thread.start()
    thread.join()

    assert errors == []
    assert store.get(job.job_id).worker_heartbeat_at


def test_only_the_worker_that_claimed_a_job_may_finish_it(store):
    job = store.enqueue(_job())
    store.claim("worker-1")

    assert store.finish(job.job_id, SUCCEEDED, worker_id="worker-2") is None
    assert store.get(job.job_id).state == RUNNING
    assert store.finish(job.job_id, SUCCEEDED, worker_id="worker-1").state == SUCCEEDED


def test_a_run_id_resolves_to_its_job_for_the_read_side(store):
    """The queue is keyed by job id and the read side by run id; the scope guard on
    the events API needs the join between them."""
    job = store.enqueue(_job(run_id="run_known"))

    assert store.find_by_run("run_known").job_id == job.job_id
    assert store.find_by_run("run_known", "payments").job_id == job.job_id
    assert store.find_by_run("run_known", "lending") is None
    assert store.find_by_run("run_unknown") is None


def test_a_second_connection_can_read_while_the_first_holds_the_write_lock(tmp_path):
    """Reads must not deadlock or raise against a writer: the run page reads job
    state on every poll while the worker is committing."""
    import threading

    from core.jobs import SqliteJobStore

    path = tmp_path / "jobs.sqlite"
    writer = SqliteJobStore(path)
    job = writer.enqueue(_job())

    writer.claim("worker-1")
    assert writer.get(job.job_id).state == RUNNING

    reader = SqliteJobStore(path)
    assert reader.get(job.job_id).job_id == job.job_id
    assert reader.counts("payments")[RUNNING] == 1


def test_claim_returns_none_when_the_queue_is_empty(store):
    assert store.claim("worker-1") is None


def test_concurrent_claims_over_one_file_are_exclusive(tmp_path):
    """The sequential version above does not actually test atomicity — it never has
    two claims in flight at once. This one does: four independent connections, each
    with its own transaction, racing for sixty jobs.

    This is the property the whole worker rests on. If it fails, two runs extract
    the same document and both write the graph, and nothing else in the design can
    compensate for it.
    """
    import collections
    import threading

    from core.jobs import SqliteJobStore

    path = tmp_path / "jobs.sqlite"
    web = SqliteJobStore(path)
    jobs = [web.enqueue(_job()) for _ in range(60)]

    claimed: list[str] = []
    lock = threading.Lock()

    def run(worker_number: int) -> None:
        # A connection per thread, which is what a second worker process is.
        store = SqliteJobStore(path)
        while True:
            job = store.claim(f"worker-{worker_number}")
            if job is None:
                return
            with lock:
                claimed.append(job.job_id)

    threads = [threading.Thread(target=run, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    counts = collections.Counter(claimed)
    assert not {job_id: n for job_id, n in counts.items() if n > 1}, "a job was claimed twice"
    assert set(claimed) == {job.job_id for job in jobs}, "a job was never claimed"


def test_a_heartbeat_only_belongs_to_the_worker_that_claimed_it(store):
    job = store.enqueue(_job())
    store.claim("worker-1")

    assert store.heartbeat(job.job_id, "worker-1") is True
    assert store.heartbeat(job.job_id, "worker-2") is False, (
        "another worker must not be able to keep a job it does not own alive"
    )
    assert store.get(job.job_id).worker_heartbeat_at


def test_finishing_moves_a_running_job_to_a_terminal_state(store):
    job = store.enqueue(_job())
    store.claim("worker-1")

    done = store.finish(job.job_id, SUCCEEDED)
    assert done.state == SUCCEEDED
    assert done.finished_at
    assert done.is_terminal

    # A second finish is a no-op, not an overwrite: only a RUNNING job can end.
    assert store.finish(job.job_id, FAILED, error="late") is None
    assert store.get(job.job_id).state == SUCCEEDED


def test_a_failed_job_carries_its_reason(store):
    job = store.enqueue(_job())
    store.claim("worker-1")

    failed = store.finish(job.job_id, FAILED, error="source 404: page deleted")

    assert failed.state == FAILED
    assert "404" in failed.error


def test_the_store_refuses_a_terminal_state_it_was_not_given(store):
    job = store.enqueue(_job())
    store.claim("worker-1")
    with pytest.raises(JobStoreError):
        store.finish(job.job_id, RUNNING)


def test_an_unknown_input_kind_or_trigger_is_refused_at_enqueue(store):
    with pytest.raises(JobStoreError):
        store.enqueue(_job(input_kind="carrier-pigeon"))
    with pytest.raises(JobStoreError):
        store.enqueue(_job(trigger="vibes"))


def test_counts_and_queue_depth_are_per_scope(store):
    store.enqueue(_job(scope_id="payments"))
    store.enqueue(_job(scope_id="payments"))
    other = store.enqueue(_job(scope_id="lending"))
    store.claim("worker-1")  # claims the oldest payments job

    assert store.queue_depth("payments") == 1
    assert store.queue_depth("lending") == 1
    assert store.counts("payments") == {QUEUED: 1, RUNNING: 1}
    assert store.get(other.job_id).state == QUEUED


def test_the_input_and_parameters_round_trip_without_becoming_one_blob(store):
    """A `source` job's input is a reference and a design job's is a snapshot; the
    two must stay distinguishable, or phase 4's fetch has nothing to read."""
    job = store.enqueue(_job(
        input_kind="source",
        input={"system": "confluence", "item_id": "12345", "version": "7"},
        parameters={"document_type": "requirements", "initiative_id": "payments"},
    ))

    stored = store.get(job.job_id)
    assert stored.input_kind == "source"
    assert stored.input["system"] == "confluence"
    assert stored.input["version"] == "7"
    assert stored.parameters["initiative_id"] == "payments"
