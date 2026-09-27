"""
The run-event endpoint — the read side of the journal, through the app.

WHY THIS IS SEPARATE FROM THE STORE. The journal is a notification log, not the record:
the graph stays the source of truth, and these events say it changed. So the endpoint
has to survive without a journal at all (an MVP install with no Valkey behaves exactly
as before), and a *broken* journal must degrade to a 503 rather than a 500 — a page that
cannot show live progress is a smaller problem than a page that cannot load.

The property worth pinning is `since`: an exclusive stream cursor, so a client that
reconnects passes the last id it saw and receives exactly what it missed. That is the
whole reason this is a Stream rather than pub/sub fire-and-forget.
"""

from __future__ import annotations

import pytest

from app import create_app
from core.events import (
    PASS_FINISHED,
    PASS_STARTED,
    RUN_FINISHED,
    RUN_STARTED,
    RunEvent,
)


class FakeJournal:
    """A journal with stream ids, so `since` can be exercised without a server."""

    def __init__(self, events=()):
        self.rows = [(f"{i + 1}-0", event) for i, event in enumerate(events)]

    def append(self, event: RunEvent) -> str:
        self.rows.append((f"{len(self.rows) + 1}-0", event))
        return self.rows[-1][0]

    def read(self, run_id: str, since=None):
        rows = [(sid, e) for sid, e in self.rows if e.run_id == run_id]
        if since is not None:
            cursor = int(str(since).split("-")[0])
            rows = [(sid, e) for sid, e in rows
                    if int(sid.split("-")[0]) > cursor]
        return [event for _sid, event in rows]

    def close(self, run_id: str, ttl_seconds=None) -> None:
        return None


def _events(run_id: str = "run_1"):
    return [
        RunEvent(run_id=run_id, scope_id="payments", seq=1, kind=RUN_STARTED),
        RunEvent(run_id=run_id, scope_id="payments", seq=2, kind=PASS_STARTED,
                 payload={"pass_name": "structure", "chunk_label": "chunk 1/3"}),
        RunEvent(run_id=run_id, scope_id="payments", seq=3, kind=PASS_FINISHED,
                 payload={"pass_name": "structure", "outcome": "ok",
                          "triples_produced": 24}),
        RunEvent(run_id=run_id, scope_id="payments", seq=4, kind=RUN_FINISHED,
                 payload={"completeness": "COMPLETE"}),
    ]


def _client(tmp_path, journal=None):
    root = tmp_path / "sea"
    root.mkdir(exist_ok=True)
    return create_app(
        store_root=str(root),
        journal_factory=(lambda: journal) if journal is not None else None,
    ).test_client()


# ============================================================================
# The endpoint
# ============================================================================


def test_events_are_returned_in_order(tmp_path):
    client = _client(tmp_path, FakeJournal(_events()))

    payload = client.get("/api/runs/run_1/events").get_json()
    assert [e["kind"] for e in payload["events"]] == [
        RUN_STARTED, PASS_STARTED, PASS_FINISHED, RUN_FINISHED,
    ]
    assert payload["run_id"] == "run_1"
    assert payload["terminal"] is True


def test_the_payload_carries_transitions_not_content(tmp_path):
    """The graph is the source of truth. An event says it changed; it does not carry
    what was extracted, or the journal becomes a second, lossier record."""
    client = _client(tmp_path, FakeJournal(_events()))

    events = client.get("/api/runs/run_1/events").get_json()["events"]
    allowed = {"pass_name", "chunk_label", "outcome", "elapsed", "triples_produced",
               "path", "completeness", "error"}
    for event in events:
        assert set(event["payload"]) <= allowed, event


def test_since_returns_exactly_what_the_subscriber_missed(tmp_path):
    client = _client(tmp_path, FakeJournal(_events()))

    payload = client.get("/api/runs/run_1/events?since=2-0").get_json()
    assert [e["seq"] for e in payload["events"]] == [3, 4]
    assert payload["since"] == "2-0"


def test_an_unknown_run_is_empty_rather_than_an_error(tmp_path):
    client = _client(tmp_path, FakeJournal(_events()))

    payload = client.get("/api/runs/run_nope/events").get_json()
    assert payload["events"] == []
    assert payload["terminal"] is False


def test_a_run_still_in_flight_is_not_reported_as_finished(tmp_path):
    """The terminal event is what distinguishes 'ended' from 'complete' — a stream that
    merely stops looks finished whether or not it was."""
    in_flight = [e for e in _events() if e.kind != RUN_FINISHED]
    client = _client(tmp_path, FakeJournal(in_flight))

    assert client.get("/api/runs/run_1/events").get_json()["terminal"] is False


# ============================================================================
# Degrading without a journal
# ============================================================================


def test_the_default_journal_is_a_no_op(tmp_path):
    """No Valkey configured means no dependency and no error — MVP behaviour is that
    runs report nothing live, which is what they already did."""
    client = _client(tmp_path)

    payload = client.get("/api/runs/run_1/events").get_json()
    assert payload["events"] == []


def test_a_broken_journal_degrades_to_503_not_500(tmp_path):
    class Broken:
        def read(self, run_id, since=None):
            raise ConnectionError("valkey is not reachable")

        def append(self, event):
            raise ConnectionError("valkey is not reachable")

        def close(self, run_id, ttl_seconds=None):
            return None

    client = _client(tmp_path, Broken())

    response = client.get("/api/runs/run_1/events")
    assert response.status_code == 503
    assert "journal unavailable" in response.get_json()["error"]


def test_importing_the_app_does_not_require_a_redis_client():
    """The journal client is optional by construction, so a deployment without Valkey
    imports and serves normally."""
    import ast
    from pathlib import Path

    source = Path("app/__init__.py").read_text(encoding="utf-8")
    assert "import redis" not in source
    assert "import valkey" not in source
    # And `core.events` itself must import without the dependency.
    ast.parse(source)
    import core.events as events

    assert events.NullJournal().read("run_1") == []
