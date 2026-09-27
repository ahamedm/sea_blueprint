"""
The run journal — the versioned envelope and its Valkey Stream transport (YB-036).

WHAT THESE TESTS PIN, AND WHY
-----------------------------
Progress streaming has one property that is easy to lose and expensive to lose
silently: **a subscriber that was not there when an event was published must
still receive it.** Pub/Sub would pass every "does the event arrive" test and
still fail this, because it has no history. So the tests here are about replay,
not about delivery:

  1. the envelope survives a round trip, and a reader tolerates fields that an
     older producer did not write — agents and Flask deploy independently, so an
     old stored event must never break a newer reader;
  2. the event vocabulary is a closed set, so a view can switch on `kind`;
  3. `NullJournal` is a true no-op, so a CLI or an unwatched run can emit
     unconditionally;
  4. against a real Redis-compatible container: events read back in order, and
     `read(since=<id>)` returns exactly the events after that id — the
     late-subscriber/replay property the whole design exists for, plus the TTL
     that keeps journals from accumulating.

The container tests SKIP (never fail) when `testcontainers`, a RESP client, or a
Docker daemon is unavailable: the suite must be runnable on a laptop with no
Docker, and a skipped integration test is honest where a mocked Stream is not.
The always-run tests use an injected fake client to cover the wire encoding and
the exclusive-range cursor without a network.
"""

from __future__ import annotations

import json

import pytest

from core.events import (
    ALL_KINDS,
    ERROR,
    EVENT_VERSION,
    PASS_FINISHED,
    PASS_STARTED,
    RETRY,
    RUN_FAILED,
    RUN_FINISHED,
    RUN_STARTED,
    STREAM_PREFIX,
    TERMINAL_KINDS,
    TOOL_CALL,
    NullJournal,
    RunEvent,
    RunJournal,
    ValkeyJournal,
    _import_client,
)


def _client_available() -> bool:
    try:
        _import_client()
    except ImportError:
        return False
    return True


def _event(run_id: str = "run_abc", seq: int = 1, kind: str = PASS_STARTED, **payload):
    return RunEvent(
        run_id=run_id,
        scope_id="ARC-G",
        seq=seq,
        kind=kind,
        payload=payload,
        at="2026-09-26T10:00:00.000Z",
    )


# ============================================================================
# The envelope
# ============================================================================


def test_envelope_round_trips_through_a_dict():
    event = _event(seq=3, kind=PASS_FINISHED, pass_name="structure",
                   chunk_label="chunk 1/2", outcome="ok", elapsed=1.5)
    restored = RunEvent.from_dict(event.to_dict())
    assert restored == event


def test_envelope_is_json_serialisable():
    """It crosses a process boundary as JSON; anything else fails at runtime."""
    event = _event(pass_name="structure")
    assert json.loads(json.dumps(event.to_dict()))["payload"]["pass_name"] == "structure"


def test_the_wire_form_is_flat_and_names_the_envelope_fields():
    wire = _event().to_dict()
    assert set(wire) == {"event_version", "run_id", "scope_id", "seq", "at", "kind", "payload"}
    assert wire["event_version"] == EVENT_VERSION


def test_an_older_stored_event_missing_newer_fields_does_not_raise():
    """The compatibility contract: an older producer wrote a smaller envelope."""
    stored = {"run_id": "run_old", "kind": RUN_STARTED}
    event = RunEvent.from_dict(stored)
    assert event.run_id == "run_old"
    assert event.kind == RUN_STARTED
    assert event.scope_id == ""
    assert event.seq == 0
    assert event.payload == {}
    assert event.at == ""
    assert event.event_version == EVENT_VERSION


def test_a_newer_envelope_with_unknown_fields_is_still_readable():
    stored = {
        "event_version": EVENT_VERSION + 1,
        "run_id": "run_new",
        "scope_id": "ARC-G",
        "seq": 2,
        "at": "2026-09-26T10:00:01.000Z",
        "kind": PASS_FINISHED,
        "payload": {"outcome": "ok"},
        "consumer_group": "future-field",
    }
    event = RunEvent.from_dict(stored)
    assert event.run_id == "run_new"
    assert event.event_version == EVENT_VERSION + 1
    assert event.payload == {"outcome": "ok"}


def test_a_json_encoded_payload_is_parsed():
    """How the payload actually arrives: one stringified field in a stream entry."""
    event = RunEvent.from_dict(
        {"run_id": "r", "kind": PASS_STARTED, "seq": "4", "payload": '{"chunk_label": "1/2"}'}
    )
    assert event.payload == {"chunk_label": "1/2"}
    assert event.seq == 4


def test_an_unparseable_payload_degrades_to_empty_rather_than_raising():
    event = RunEvent.from_dict({"run_id": "r", "kind": PASS_STARTED, "payload": "{not json"})
    assert event.payload == {}


def test_a_non_mapping_envelope_is_rejected_clearly():
    with pytest.raises(ValueError):
        RunEvent.from_dict(["not", "an", "envelope"])


def test_the_event_vocabulary_is_a_closed_set():
    assert {
        RUN_STARTED, PASS_STARTED, PASS_FINISHED, TOOL_CALL, RETRY, ERROR,
        RUN_FINISHED, RUN_FAILED,
    } == ALL_KINDS


def test_only_run_finished_and_run_failed_are_terminal():
    assert TERMINAL_KINDS == {RUN_FINISHED, RUN_FAILED}
    assert _event(kind=PASS_FINISHED).is_terminal is False
    assert _event(kind=RUN_FINISHED).is_terminal is True


def test_a_terminal_event_exposes_its_verdict_and_a_missing_one_is_empty():
    """End of stream is not completeness; the verdict is read, never assumed."""
    assert _event(kind=RUN_FINISHED, completeness="PARTIAL").completeness == "PARTIAL"
    assert _event(kind=PASS_FINISHED).completeness == ""


# ============================================================================
# NullJournal
# ============================================================================


def test_null_journal_satisfies_the_journal_protocol():
    assert isinstance(NullJournal(), RunJournal)


def test_null_journal_retains_nothing_and_returns_an_empty_cursor():
    journal = NullJournal()
    assert journal.append(_event()) == ""
    assert journal.read("run_abc") == []
    assert journal.read("run_abc", since="1-0") == []
    assert journal.close("run_abc", ttl_seconds=60) is None


# ============================================================================
# ValkeyJournal — wire encoding, with an injected client (no network)
# ============================================================================


def _stream_id(value: str) -> tuple[int, int]:
    """Comparable form of a `ms-seq` stream id, so the fake honours range bounds."""
    milliseconds, _, sequence = str(value).partition("-")
    return int(milliseconds), int(sequence or 0)


class FakeResponseClient:
    """The slice of a RESP client `ValkeyJournal` uses, recording its calls."""

    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict]]] = {}
        self.expires: dict[str, int] = {}
        self.calls: list[tuple] = []

    def xadd(self, key, fields, maxlen=None, approximate=True):
        self.calls.append(("xadd", key, maxlen, approximate))
        stream = self.streams.setdefault(key, [])
        stream_id = f"{1000 + len(stream)}-0"
        stream.append((stream_id, dict(fields)))
        return stream_id

    def xrange(self, key, min="-", max="+"):
        self.calls.append(("xrange", key, min, max))
        entries = list(self.streams.get(key, []))
        if min and min != "-":
            exclusive = min.startswith("(")
            bound = _stream_id(min[1:] if exclusive else min)
            entries = [
                (sid, fields)
                for sid, fields in entries
                if (_stream_id(sid) > bound if exclusive else _stream_id(sid) >= bound)
            ]
        return entries

    def expire(self, key, seconds):
        self.calls.append(("expire", key, seconds))
        self.expires[key] = seconds
        return True


def test_valkey_journal_satisfies_the_journal_protocol():
    assert isinstance(ValkeyJournal(client=FakeResponseClient()), RunJournal)


def test_append_writes_one_stream_per_run_with_approximate_trimming():
    client = FakeResponseClient()
    journal = ValkeyJournal(client=client, maxlen=500)
    stream_id = journal.append(_event(run_id="run_1"))
    assert stream_id == "1000-0"
    assert ("xadd", f"{STREAM_PREFIX}run_1", 500, True) in client.calls
    assert journal.stream_key("run_1") == f"{STREAM_PREFIX}run_1"


def test_append_json_encodes_the_payload_so_the_record_stays_out_of_the_stream():
    client = FakeResponseClient()
    journal = ValkeyJournal(client=client)
    journal.append(_event(pass_name="structure", outcome="ok"))
    _sid, fields = client.streams[journal.stream_key("run_abc")][0]
    assert fields["payload"] == '{"pass_name":"structure","outcome":"ok"}'


def test_read_replays_in_order_and_restores_payloads():
    journal = ValkeyJournal(client=FakeResponseClient())
    journal.append(_event(seq=1, kind=PASS_STARTED, pass_name="structure"))
    journal.append(_event(seq=2, kind=PASS_FINISHED, outcome="ok", elapsed=1.25))
    events = journal.read("run_abc")
    assert [e.seq for e in events] == [1, 2]
    assert events[1].kind == PASS_FINISHED
    assert events[1].payload == {"outcome": "ok", "elapsed": 1.25}


def test_read_since_uses_an_exclusive_lower_bound():
    """The replay property: the cursor event itself is not delivered twice."""
    client = FakeResponseClient()
    journal = ValkeyJournal(client=client)
    first = journal.append(_event(seq=1, kind=PASS_STARTED))
    journal.append(_event(seq=2, kind=PASS_STARTED))
    journal.append(_event(seq=3, kind=RUN_FINISHED, completeness="COMPLETE"))

    assert [e.seq for e in journal.read("run_abc")] == [1, 2, 3]
    assert [e.seq for e in journal.read("run_abc", since=first)] == [2, 3]
    assert ("xrange", journal.stream_key("run_abc"), f"({first}", "+") in client.calls
    assert ("xrange", journal.stream_key("run_abc"), "-", "+") in client.calls


def test_close_sets_a_ttl_so_journals_do_not_accumulate():
    client = FakeResponseClient()
    journal = ValkeyJournal(client=client, ttl_seconds=3600)
    journal.close("run_abc")
    assert client.expires[journal.stream_key("run_abc")] == 3600
    journal.close("run_abc", ttl_seconds=60)
    assert client.expires[journal.stream_key("run_abc")] == 60


def test_constructing_without_a_client_names_the_extra_to_install():
    """Laziness is deliberate: importing `core.events` must not need a client."""
    if _client_available():
        pytest.skip("a RESP client is installed, so the ImportError path is unreachable")
    with pytest.raises(ImportError) as excinfo:
        ValkeyJournal(host="127.0.0.1", port=6379)
    message = str(excinfo.value)
    assert "valkey" in message.lower()
    assert "pip install" in message


# ============================================================================
# ValkeyJournal — against a real Redis-compatible container
# ============================================================================


@pytest.fixture(scope="module")
def stream_journal():
    """A `ValkeyJournal` against a containerised Redis-compatible server.

    Skips rather than fails when `testcontainers`, a RESP client, or Docker is
    unavailable: the pinned property below can only be shown against a real
    Stream, so a mock would be a lie and a failure would be noise.
    """
    pytest.importorskip("testcontainers", reason="Valkey stream tests need testcontainers")
    if not _client_available():
        pytest.skip("Valkey stream tests need the 'redis' or 'valkey' client")

    try:
        # Newer testcontainers moved the module; the top-level path is the older
        # one. Both ship RedisContainer, so prefer the current spelling and fall
        # back rather than pinning the suite to one release.
        try:
            from testcontainers.community.redis import RedisContainer
        except ImportError:
            from testcontainers.redis import RedisContainer
    except ImportError as exc:  # testcontainers present but its redis extra is not
        pytest.skip(f"testcontainers Redis support unavailable: {exc}")

    try:
        container = RedisContainer()
        container.start()
    except Exception as exc:  # docker absent, daemon down, image unavailable
        pytest.skip(f"Redis container unavailable: {exc}")

    try:
        host = container.get_container_host_ip()
        port = int(container.get_exposed_port(6379))
        yield ValkeyJournal(host=host, port=port, maxlen=1000)
    finally:
        container.stop()


def test_a_late_subscriber_reads_the_backlog_in_order(stream_journal):
    """The event that a fire-and-forget transport would have dropped."""
    journal = stream_journal
    run_id = "run_ordered"
    journal.append(_event(run_id=run_id, seq=1, kind=PASS_STARTED, pass_name="structure"))
    journal.append(_event(run_id=run_id, seq=2, kind=PASS_FINISHED, outcome="ok"))
    journal.append(_event(run_id=run_id, seq=3, kind=RUN_FINISHED, completeness="COMPLETE"))
    journal.close(run_id, ttl_seconds=120)

    events = journal.read(run_id)
    assert [e.seq for e in events] == [1, 2, 3]
    assert [e.kind for e in events] == [PASS_STARTED, PASS_FINISHED, RUN_FINISHED]
    assert events[-1].is_terminal and events[-1].completeness == "COMPLETE"


def test_a_reconnecting_subscriber_reads_only_what_it_missed(stream_journal):
    """`since` is exclusive: replay resumes after the last id the reader saw."""
    journal = stream_journal
    run_id = "run_replay"
    first = journal.append(_event(run_id=run_id, seq=1, kind=PASS_STARTED))
    journal.append(_event(run_id=run_id, seq=2, kind=PASS_FINISHED, outcome="ok"))
    journal.append(_event(run_id=run_id, seq=3, kind=PASS_STARTED, pass_name="connections"))

    missed = journal.read(run_id, since=first)
    assert [e.seq for e in missed] == [2, 3]
    assert all(e.seq != 1 for e in missed)

    last = journal.append(_event(run_id=run_id, seq=4, kind=RUN_FINISHED, completeness="PARTIAL"))
    assert [e.seq for e in journal.read(run_id, since=last)] == []


def test_an_unknown_run_reads_as_an_empty_backlog(stream_journal):
    assert stream_journal.read("run_never_seen") == []


def test_close_leaves_the_stream_retained_with_a_ttl(stream_journal):
    journal = stream_journal
    run_id = "run_ttl"
    journal.append(_event(run_id=run_id, seq=1, kind=PASS_STARTED))
    journal.close(run_id, ttl_seconds=90)

    ttl = journal.client.ttl(journal.stream_key(run_id))
    assert 0 < ttl <= 90
