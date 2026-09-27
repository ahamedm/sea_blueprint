"""
The producer chain through the app: a route attaches a journal sink (YB-026 phase 1).

WHY THIS FILE EXISTS SEPARATELY FROM `test_run_progress_wiring.py`. That file proves
the *agent* forwards a sink it is handed. This one proves the thing that was missing:
**no route passed one at all**. A UI-triggered run produced zero events even with a
journal configured, so the read side (`/api/runs/<id>/events`) answered `[]` for every
run a person could actually start.

What is pinned here:
  1. `/ingest` publishes `run.started` … `run.finished`, and the terminal verdict is
     the one persisted on the run record — not the fact that the stream ended.
  2. The journal's run id is the run record's id, which is the only reason the live
     stream and the stored run can be joined afterwards.
  3. A run that dies publishes `run.failed` and never `run.finished`.
  4. A broken journal degrades live progress and nothing else: no 500, no failed run.
  5. A configured-but-unusable journal endpoint degrades at boot instead of raising.
"""

from __future__ import annotations

from core.events import (
    RUN_FAILED,
    RUN_FINISHED,
    RUN_STARTED,
    RunEvent,
)


class FakeJournal:
    """Appends in order and replays per run, like the real Stream does."""

    def __init__(self, fail: bool = False):
        self.rows: list[RunEvent] = []
        self.closed: list[str] = []
        self.fail = fail

    def append(self, event: RunEvent) -> str:
        if self.fail:
            raise ConnectionError("valkey is down")
        self.rows.append(event)
        return f"{len(self.rows)}-0"

    def read(self, run_id: str, since=None):
        return [event for event in self.rows if event.run_id == run_id]

    def wait(self, run_id, since=None, timeout_ms=500, count=100):
        # A journal that retains events would block here; the fake has nothing new
        # between calls, so it returns immediately like `NullJournal`.
        return []

    def close(self, run_id: str, ttl_seconds=None) -> None:
        self.closed.append(run_id)


def _app(store_root, fake_factory, design_factory, journal):
    from app import create_app

    return create_app(
        {"TESTING": True, "STORE_ROOT": str(store_root), "REVIEWER": "tester"},
        store_root=str(store_root),
        extractor_factory=fake_factory,
        design_factory=design_factory,
        journal_factory=lambda: journal,
    )


def _working(app):
    """Read the working set back AFTER a request — the fixture would snapshot it
    before the POST and hand back an empty graph."""
    from core.knowledge import RevisionStore

    return RevisionStore(app.config["STORE_ROOT"]).ensure().load_working().graph


def test_ingest_publishes_a_correlated_start_and_terminal_verdict(
    store_root, fake_factory, design_factory
):
    journal = FakeJournal()
    app = _app(store_root, fake_factory, design_factory, journal)
    client = app.test_client()

    response = client.post(
        "/ingest", data={"text": "FR-PM-001 requirements body", "type": "requirements"}
    )
    assert response.status_code == 302

    kinds = [event.kind for event in journal.rows]
    assert kinds[0] == RUN_STARTED
    assert kinds[-1] == RUN_FINISHED, kinds
    assert RUN_FAILED not in kinds

    # One run id across the whole stream, and it is the id the record took —
    # otherwise the page can show progress for a run that cannot be looked up.
    assert len({event.run_id for event in journal.rows}) == 1
    run_id = journal.rows[0].run_id
    runs = _working(app).runs
    assert run_id in runs

    # The verdict is READ FROM the record, and the run is only "finished" once the
    # record says so. A stream that ends is not evidence of completion.
    assert journal.rows[-1].payload["completeness"] == runs[run_id].completeness
    assert journal.rows[-1].is_terminal

    # The journal is released (TTL'd), not left open forever.
    assert journal.closed == [run_id]


def test_start_event_names_the_document_without_carrying_its_content(
    store_root, fake_factory, design_factory
):
    journal = FakeJournal()
    client = _app(store_root, fake_factory, design_factory, journal).test_client()
    client.post(
        "/ingest",
        data={"text": "FR-PM-001 SECRET BODY TEXT", "type": "requirements",
              "text_name": "brief.md"},
    )

    started = journal.rows[0]
    assert started.payload["document_ref"] == "brief.md"
    assert started.payload["document_type"] == "requirements"
    # The channel carries transitions, never extracted content — the body must not
    # appear anywhere in a payload.
    assert "SECRET BODY TEXT" not in str(started.payload)


def test_a_run_that_dies_publishes_failed_and_never_finished(
    store_root, design_factory
):
    class Exploding:
        def run(self, input_data):
            raise RuntimeError("model exploded")

    from app import create_app

    journal = FakeJournal()
    app = create_app(
        {"TESTING": True, "STORE_ROOT": str(store_root), "REVIEWER": "tester"},
        store_root=str(store_root),
        extractor_factory=lambda doc_type: Exploding(),
        design_factory=design_factory,
        journal_factory=lambda: journal,
    )
    response = app.test_client().post(
        "/ingest", data={"text": "FR-PM-001 body", "type": "requirements"}
    )
    assert response.status_code == 302

    kinds = [event.kind for event in journal.rows]
    assert RUN_FAILED in kinds
    assert RUN_FINISHED not in kinds
    # A failed run still states a verdict, so end-of-stream is never read as success.
    assert journal.rows[-1].payload["completeness"]


def test_a_dead_journal_degrades_progress_and_nothing_else(
    store_root, fake_factory, design_factory
):
    """The journal is a notification channel: a failing append must not 500 the page
    or fail the run, because the graph is the record and the stream only says it
    changed."""
    journal = FakeJournal(fail=True)
    app = _app(store_root, fake_factory, design_factory, journal)
    client = app.test_client()

    response = client.post(
        "/ingest", data={"text": "FR-PM-001 requirements body", "type": "requirements"}
    )
    assert response.status_code == 302
    # The work still landed.
    assert _working(app).runs
    assert journal.rows == []


def test_a_configured_but_unusable_journal_degrades_at_boot(monkeypatch):
    """`SEA_VALKEY_HOST` set without the optional client must not stop the app: the
    module contract is that losing the journal degrades live progress only."""
    import core.events as events
    from core.events import NullJournal

    import app as app_module

    monkeypatch.setenv("SEA_VALKEY_HOST", "127.0.0.1")

    class UnusableValkey:
        def __init__(self, *args, **kwargs):
            raise ImportError("no redis/valkey client installed")

    monkeypatch.setattr(events, "ValkeyJournal", UnusableValkey)
    assert isinstance(app_module._default_journal(), NullJournal)
