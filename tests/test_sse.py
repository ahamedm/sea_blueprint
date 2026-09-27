"""
Server-sent progress (YB-026 phase 3b).

WHAT THIS PINS. Two transports now render one view: the polling fragment and the SSE
frame both come from `partials/run_events.html`, so they cannot drift. What the tests
have to prove is the part that is easy to get subtly wrong:

  1. the frame carries the **rendered fragment**, not a second JSON shape of it;
  2. `id:` is the stream id, which is what `Last-Event-ID` sends back on a reconnect;
  3. a terminal run closes the stream — the browser retries by itself, so a stream
     that never ends is a background load test, not a feature;
  4. with no journal there is nothing to push, so the route says so once and closes,
     and the page falls back to polling rather than holding an empty connection;
  5. the connection cap is real, because the whole point is to bound held sockets.

Flask's test client buffers a streamed response, so every test here must be bounded:
either the run is terminal, or `?once=1` is used.
"""

from __future__ import annotations

import json

import pytest

from core.events import (
    PASS_FINISHED,
    PASS_STARTED,
    RUN_FINISHED,
    RUN_STARTED,
    RunEvent,
)

MANIFEST = """\
workspace_id: sea
scopes:
  - scope_id: payments
    name: Payments
    backend: sqlite
    path: payments
"""


class RetainingJournal:
    """A journal that keeps events and reports that it does — like Valkey does.

    `retains = True` is what selects the push transport, so it is the property under
    test as much as `read` is.
    """

    retains = True

    def __init__(self, events=()):
        self.rows = [(f"{index + 1}-0", event) for index, event in enumerate(events)]

    def append(self, event: RunEvent) -> str:
        self.rows.append((f"{len(self.rows) + 1}-0", event))
        return self.rows[-1][0]

    def read(self, run_id, since=None):
        import dataclasses

        rows = [(sid, e) for sid, e in self.rows if e.run_id == run_id]
        if since is not None:
            index = next((i for i, (sid, _) in enumerate(rows) if sid == since), None)
            rows = rows[index + 1:] if index is not None else rows
        return [dataclasses.replace(e, stream_id=sid) for sid, e in rows]

    def wait(self, run_id, since=None, timeout_ms=500, count=100):
        # Nothing new arrives between calls in a test; a real journal blocks here.
        return []

    def close(self, run_id, ttl_seconds=None) -> None:
        return None


@pytest.fixture
def sse_root(tmp_path):
    root = tmp_path / "sea"
    root.mkdir()
    (root / "workspace.yaml").write_text(MANIFEST, encoding="utf-8")
    return root


def _app(root, fake_factory, design_factory, journal):
    from app import create_app

    app = create_app(
        {"TESTING": True, "STORE_ROOT": str(root), "REVIEWER": "tester"},
        store_root=str(root),
        extractor_factory=fake_factory,
        design_factory=design_factory,
        journal_factory=lambda: journal,
    )
    app.config["TEST_JOURNAL"] = journal
    return app


def _queued_run(app, journal) -> str:
    """Enqueue one job and return its run id (the key the journal uses)."""
    from core.jobs import SqliteJobStore
    from core.workspace import load_workspace, scope_data_dir

    app.test_client().post(
        "/ingest", data={"text": "FR-PM-001 body", "type": "requirements"}
    )
    workspace = load_workspace(app.config["STORE_ROOT"])
    store = SqliteJobStore(scope_data_dir(workspace, workspace.scope("payments")) / "jobs.sqlite")
    return store.list("payments")[0].run_id


def _frames(body: str):
    """Parse an SSE body into (id, payload) pairs, ignoring comments."""
    frames = []
    for block in body.strip().split("\n\n"):
        if not block.strip() or block.lstrip().startswith(":"):
            continue
        event_id, payload = "", None
        for line in block.splitlines():
            if line.startswith("id: "):
                event_id = line[4:]
            elif line.startswith("data: "):
                payload = json.loads(line[6:])
        frames.append((event_id, payload))
    return frames


def test_the_frame_carries_the_rendered_fragment(sse_root, fake_factory, design_factory):
    journal = RetainingJournal()
    app = _app(sse_root, fake_factory, design_factory, journal)
    run_id = _queued_run(app, journal)

    response = app.test_client().get(f"/api/runs/{run_id}/stream?once=1")

    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    assert response.headers["X-Accel-Buffering"] == "no", "a buffering proxy defeats it"

    frames = _frames(response.get_data(as_text=True))
    assert len(frames) == 1
    _event_id, payload = frames[0]
    # One view, two transports: the payload is the Jinja fragment itself.
    assert "QUEUED" in payload["html"]
    assert payload["terminal"] is False
    assert payload["state"] == "QUEUED"


def test_the_frame_id_is_the_cursor_a_reconnect_sends_back(
    sse_root, fake_factory, design_factory
):
    journal = RetainingJournal()
    app = _app(sse_root, fake_factory, design_factory, journal)
    run_id = _queued_run(app, journal)
    journal.append(RunEvent(run_id=run_id, scope_id="payments", seq=1,
                            kind=PASS_STARTED, payload={"pass_name": "requirements"}))

    response = app.test_client().get(f"/api/runs/{run_id}/stream?once=1")
    event_id, payload = _frames(response.get_data(as_text=True))[0]

    assert event_id == "1-0"
    assert "pass.started" in payload["html"]

    # The same request with Last-Event-ID still renders the whole fragment (the
    # client swaps it wholesale), and does not error on the header.
    again = app.test_client().get(
        f"/api/runs/{run_id}/stream?once=1", headers={"Last-Event-ID": event_id}
    )
    assert again.status_code == 200
    assert _frames(again.get_data(as_text=True))[0][1]["html"]


def test_a_terminal_run_closes_the_stream_without_once(
    sse_root, fake_factory, design_factory
):
    """The browser retries a dropped stream by itself, so ending on the terminal
    event is what stops a finished run from being polled forever by accident."""
    journal = RetainingJournal()
    app = _app(sse_root, fake_factory, design_factory, journal)
    run_id = _queued_run(app, journal)
    journal.append(RunEvent(run_id=run_id, scope_id="payments", seq=1,
                            kind=RUN_STARTED))
    journal.append(RunEvent(run_id=run_id, scope_id="payments", seq=2,
                            kind=PASS_FINISHED,
                            payload={"pass_name": "requirements", "outcome": "ok"}))
    journal.append(RunEvent(run_id=run_id, scope_id="payments", seq=3,
                            kind=RUN_FINISHED, payload={"completeness": "PARTIAL"}))

    response = app.test_client().get(f"/api/runs/{run_id}/stream")

    frames = _frames(response.get_data(as_text=True))
    assert len(frames) == 1, "the terminal frame must be the last one"
    assert frames[0][1]["terminal"] is True
    assert "PARTIAL" in frames[0][1]["html"]


def test_without_a_journal_the_stream_says_so_and_closes(
    sse_root, fake_factory, design_factory, monkeypatch
):
    """No journal means nothing to push. Holding a connection open would be a
    resource leak dressed as a feature."""
    from app import create_app
    from core.events import NullJournal

    monkeypatch.delenv("SEA_VALKEY_HOST", raising=False)
    app = create_app(
        {"TESTING": True, "STORE_ROOT": str(sse_root), "REVIEWER": "tester"},
        store_root=str(sse_root),
        extractor_factory=fake_factory,
        design_factory=design_factory,
        journal_factory=lambda: NullJournal(),
    )
    run_id = _queued_run(app, NullJournal())

    response = app.test_client().get(f"/api/runs/{run_id}/stream")

    assert response.status_code == 200
    frames = _frames(response.get_data(as_text=True))
    assert len(frames) == 1
    assert frames[0][1]["terminal"] is True
    assert "no journal" in frames[0][1]["reason"]


def test_the_page_pushes_when_the_journal_retains_events(
    sse_root, fake_factory, design_factory
):
    journal = RetainingJournal()
    app = _app(sse_root, fake_factory, design_factory, journal)
    client = app.test_client()

    response = client.post("/ingest", data={"text": "FR-PM-001 body",
                                            "type": "requirements"})
    body = client.get(response.headers["Location"]).get_data(as_text=True)

    assert "new EventSource(" in body
    assert "hx-trigger" not in body, "push and polling must not both be wired up"


def test_the_page_polls_without_a_journal(sse_root, fake_factory, design_factory,
                                          monkeypatch):
    from app import create_app
    from core.events import NullJournal

    monkeypatch.delenv("SEA_VALKEY_HOST", raising=False)
    app = create_app(
        {"TESTING": True, "STORE_ROOT": str(sse_root), "REVIEWER": "tester"},
        store_root=str(sse_root),
        extractor_factory=fake_factory,
        design_factory=design_factory,
        journal_factory=lambda: NullJournal(),
    )
    client = app.test_client()
    response = client.post("/ingest", data={"text": "FR-PM-001 body",
                                            "type": "requirements"})
    body = client.get(response.headers["Location"]).get_data(as_text=True)

    assert "new EventSource(" not in body
    assert "hx-trigger" in body, "the fallback is the polling fragment"


def test_the_connection_cap_refuses_rather_than_holding_more_sockets(
    sse_root, fake_factory, design_factory, monkeypatch
):
    import app as app_module

    journal = RetainingJournal()
    app = _app(sse_root, fake_factory, design_factory, journal)
    run_id = _queued_run(app, journal)

    monkeypatch.setattr(app_module, "_SSE_ACTIVE", [app_module.SSE_MAX_CONNECTIONS])
    response = app.test_client().get(f"/api/runs/{run_id}/stream?once=1")

    assert response.status_code == 503
    assert "too many live progress streams" in response.get_json()["error"]


def test_a_stream_that_never_ends_still_closes_on_its_own_deadline(
    sse_root, fake_factory, design_factory, monkeypatch
):
    """`EventSource` reconnects by itself, so a run whose events aged out of the
    journal's TTL would otherwise be retried forever by a forgotten tab. The
    duration cap is what makes that a finite request."""
    import app as app_module

    journal = RetainingJournal()
    app = _app(sse_root, fake_factory, design_factory, journal)
    run_id = _queued_run(app, journal)

    # A non-terminal run, no `?once`, and a deadline already in the past.
    monkeypatch.setattr(app_module, "SSE_MAX_SECONDS", -1)
    response = app.test_client().get(f"/api/runs/{run_id}/stream")

    body = response.get_data(as_text=True)
    assert "stream closed after the maximum duration" in body
    # It still delivered the frame it had before giving up.
    assert len(_frames(body)) == 1


# ============================================================================
# Against a real Valkey, when one is reachable
# ============================================================================

SEA_VALKEY_HOST = "127.0.0.1"
SEA_VALKEY_PORT = 6379


def _reachable_valkey():
    """The real journal, or None when no server is running.

    The in-memory fakes above prove the framing and the lifecycle; only a real
    `XREAD BLOCK` can prove that a frame is pushed *when an event lands* rather than
    when the next poll happens, which is the whole difference between this transport
    and the one it replaces.
    """
    try:
        import redis  # noqa: F401
    except ImportError:
        return None
    try:
        from core.events import ValkeyJournal

        journal = ValkeyJournal(host=SEA_VALKEY_HOST, port=SEA_VALKEY_PORT)
        journal.client.ping()
        return journal
    except Exception:  # noqa: BLE001 - absent server means skip, not fail
        return None


def test_a_live_stream_pushes_on_the_event_and_closes_on_the_verdict(
    tmp_path, fake_factory, monkeypatch
):
    journal = _reachable_valkey()
    if journal is None:
        pytest.skip("no Valkey reachable on 127.0.0.1:6379")

    from app import create_app
    from core.events import (
        PASS_FINISHED,
        RUN_FINISHED,
        RUN_STARTED,
        RunEvent,
    )
    from core.jobs import Job, SqliteJobStore, new_job_id
    from core.workspace import load_workspace, scope_data_dir

    root = tmp_path / "sea"
    root.mkdir()
    (root / "workspace.yaml").write_text(MANIFEST, encoding="utf-8")
    monkeypatch.setenv("SEA_VALKEY_HOST", SEA_VALKEY_HOST)
    app = create_app(
        {"TESTING": True, "STORE_ROOT": str(root), "REVIEWER": "tester"},
        store_root=str(root),
        extractor_factory=fake_factory,
    )

    run_id = "run_sse_live"
    workspace = load_workspace(root)
    store = SqliteJobStore(scope_data_dir(workspace, workspace.scope("payments")) / "jobs.sqlite")
    store.enqueue(Job(job_id=new_job_id(), run_id=run_id, scope_id="payments",
                      kind="ingest"))

    def emit(kind, seq, payload):
        journal.append(RunEvent(run_id=run_id, scope_id="payments", seq=seq,
                                kind=kind, payload=payload))

    emit(RUN_STARTED, 1, {"document_ref": "probe.md"})

    # `buffered=False` is what makes this testable at all: the default test client
    # would read the whole stream to the end, and this stream ends when the run does.
    response = app.test_client().get(f"/api/runs/{run_id}/stream", buffered=False)
    frames = iter(response.response)

    first = _frame_payload(next(frames))
    assert first["terminal"] is False

    emit(PASS_FINISHED, 2, {"pass_name": "requirements", "outcome": "ok"})
    second = _frame_payload(next(frames))
    assert "pass.finished" in second["html"], "an event must push, not wait for a poll"

    emit(RUN_FINISHED, 3, {"completeness": "PARTIAL"})
    third = _frame_payload(next(frames))
    assert third["terminal"] is True
    assert "PARTIAL" in third["html"]

    with pytest.raises(StopIteration):
        next(frames)  # the stream ends on the verdict
    journal.client.delete(journal.stream_key(run_id))


def _frame_payload(raw):
    text = raw.decode() if isinstance(raw, bytes) else raw
    for line in text.strip().splitlines():
        if line.startswith("data: "):
            return json.loads(line[6:])
    raise AssertionError(f"not a data frame: {text[:80]!r}")
