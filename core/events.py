"""
Run progress events, and the journal they are appended to.

WHY A JOURNAL OF EVENTS AT ALL
------------------------------
An extraction run can take twenty minutes and span several passes and chunks. The
record of what happened is written to the graph only *after* merge, so a run that
is still executing has no record anywhere and a person watching it has nothing to
watch. This module is the missing notification channel: the pipeline appends a
minimal status event per transition, and any number of subscribers read the log.

WHY STREAMS, NOT PUB/SUB
------------------------
Redis/Valkey Pub/Sub is fire-and-forget: a message published while nobody is
subscribed is gone. That fails the three properties this mechanism exists for
([`docs/todos/entries/YB-036-modular-run-streaming.md`] and
[`docs/design/deployment-architecture.md`] §3.4):

  - **A late subscriber gets the backlog.** An operator opening the page forty
    seconds into the run must see the forty seconds they missed.
  - **A reconnecting subscriber gets exactly what it missed.** The subscriber
    keeps the last stream id it saw and asks for everything after it.
  - **A dropped terminal event cannot erase the verdict.** Streams are retained
    (with a TTL), so the terminal event is still there to be read after the
    browser, the worker, or the Flask replica that was watching went away.

A Stream gives ordered, replayable, per-run history (`XADD` / `XRANGE`), which is
what all three need; Pub/Sub gives none of them.

THE JOURNAL IS A NOTIFICATION LOG, NOT THE RECORD
-------------------------------------------------
The payload carries **state transitions, never extracted content**: run id, scope
or product id, phase/pass, chunk label, counters, outcome, elapsed, usage. The
graph record stays the source of truth; the stream only says that it changed and
that someone should look. The terminal event repeats the run's `completeness`
verdict (`COMPLETE` / `PARTIAL` / `UNKNOWN`) explicitly, because a stream that
*ends* looks finished whether or not it was — the false assurance completeness
reporting exists to prevent. The verdict is persisted in the record *before* it is
published; losing this log must degrade live progress only, never correctness.

WHY THIS LIVES IN `core/`
-------------------------
Both the agent pipeline (producer) and the web app (subscriber) need the same
envelope and vocabulary, and `core/` is the layer both may import. This module
imports neither `agents/` nor `app/`, and it does not import a Redis client at
module scope: the client is loaded lazily by `ValkeyJournal`, so importing this
module costs nothing and needs no optional dependency.

WIRE COMPATIBILITY
------------------
The envelope is versioned (`event_version`, currently 1) because agents and Flask
deploy independently. Readers must tolerate older stored events: `RunEvent.from_dict`
fills missing fields with defaults and ignores fields it does not know, so an
event written by an older producer cannot break a newer reader.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Protocol, runtime_checkable

__all__ = [
    "EVENT_VERSION",
    "RUN_STARTED",
    "PASS_STARTED",
    "PASS_FINISHED",
    "TOOL_CALL",
    "RETRY",
    "ERROR",
    "RUN_FINISHED",
    "RUN_FAILED",
    "ALL_KINDS",
    "TERMINAL_KINDS",
    "RunEvent",
    "RunJournal",
    "NullJournal",
    "ValkeyJournal",
    "STREAM_PREFIX",
]


# ============================================================================
# The envelope version and the event vocabulary
# ============================================================================

EVENT_VERSION = 1
"""Version of the envelope below. Increment when a reader could mistake old for
new; the payload may gain keys without a bump because unknown keys are ignored."""

# The event vocabulary is a CLOSED, VERSIONED set. A view may switch on these and
# rely on their meaning not changing; a new kind is an addition to a new envelope
# version, not an ad-hoc string. Values are dotted `object.action` names so the
# wire spelling is stable even if a Python constant is renamed.

RUN_STARTED = "run.started"
"""A run began. Payload: document/scope identity and the first pass planned."""

PASS_STARTED = "pass.started"
"""A pass over one chunk began. Payload: pass name, chunk label."""

PASS_FINISHED = "pass.finished"
"""A pass over one chunk ended. Payload: outcome, path, elapsed, counters."""

TOOL_CALL = "tool.call"
"""A tool/model call was made inside a pass. Payload: tool name, counters."""

RETRY = "pass.retry"
"""A pass is being retried after a failure. Payload: attempt, reason."""

ERROR = "pass.error"
"""A pass raised or failed. Payload: pass name, chunk label, error text."""

RUN_FINISHED = "run.finished"
"""Terminal. Payload MUST carry `completeness` (COMPLETE/PARTIAL/UNKNOWN)."""

RUN_FAILED = "run.failed"
"""Terminal. The run did not produce a verdict. Payload MUST carry `completeness`
(typically UNKNOWN or FAILED) so end-of-stream is never read as success."""

ALL_KINDS = frozenset(
    {
        RUN_STARTED,
        PASS_STARTED,
        PASS_FINISHED,
        TOOL_CALL,
        RETRY,
        ERROR,
        RUN_FINISHED,
        RUN_FAILED,
    }
)
"""Every legal `RunEvent.kind`. Closed by construction."""

TERMINAL_KINDS = frozenset({RUN_FINISHED, RUN_FAILED})
"""End-of-run events. `ExtractionRun.compute_completeness()` uses the same
COMPLETE/PARTIAL/FAILED/UNKNOWN vocabulary in `core.knowledge.model`."""


# ============================================================================
# The event envelope
# ============================================================================


def _utc_now() -> str:
    """ISO-8601 UTC, millisecond precision, `Z` suffix."""
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


@dataclass
class RunEvent:
    """One state transition of one run.

    The envelope is deliberately small and versioned; everything run-specific
    lives in `payload`, which must stay JSON-serialisable and must never carry
    extracted content (the graph record is the source of truth for that).
    """

    run_id: str
    scope_id: str
    seq: int
    kind: str
    payload: Dict[str, Any] = field(default_factory=dict)
    at: str = field(default_factory=_utc_now)
    event_version: int = EVENT_VERSION
    # The journal's own id for this event, when a reader wants to resume from it.
    # A Stream id is opaque to everything except `read(..., since=...)`, so it is
    # carried on the envelope rather than parsed by subscribers: without it a
    # reconnecting SSE frame or a polling client has no cursor to ask from, and
    # replay silently becomes "start over" or "miss everything in between".
    # Empty means "this journal does not retain ids" (`NullJournal`).
    stream_id: str = ""

    @property
    def is_terminal(self) -> bool:
        """True for the last event of a run, whose payload carries the verdict."""
        return self.kind in TERMINAL_KINDS

    @property
    def completeness(self) -> str:
        """The run's verdict on a terminal event, else `""`.

        Never infer completion from end-of-stream: read this field, and treat an
        absent verdict as UNKNOWN rather than as success.
        """
        value = self.payload.get("completeness", "")
        return value if isinstance(value, str) else ""

    def to_dict(self) -> Dict[str, Any]:
        """The wire form: a flat, JSON-serialisable mapping."""
        return {
            "event_version": self.event_version,
            "run_id": self.run_id,
            "scope_id": self.scope_id,
            "seq": self.seq,
            "at": self.at,
            "kind": self.kind,
            "payload": dict(self.payload),
            "stream_id": self.stream_id,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RunEvent":
        """Read an envelope, tolerating older and newer stored events.

        Missing fields take defaults, `payload` may arrive already-JSON-encoded
        (as it does in a stream field), and unknown top-level keys are ignored.
        This is the forward/backward compatibility contract: an event written by
        an older producer must not break a newer reader.
        """
        if not isinstance(data, Mapping):
            raise ValueError(f"run event must be a mapping, got {type(data).__name__}")

        payload: Any = data.get("payload", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {}
        if not isinstance(payload, Mapping):
            payload = {}

        return cls(
            run_id=_as_str(data.get("run_id")),
            scope_id=_as_str(data.get("scope_id")),
            seq=_as_int(data.get("seq")),
            kind=_as_str(data.get("kind")),
            payload=dict(payload),
            at=_as_str(data.get("at")),
            event_version=_as_int(data.get("event_version"), default=EVENT_VERSION),
            stream_id=_as_str(data.get("stream_id")),
        )


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value if isinstance(value, str) else str(value)


def _as_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ============================================================================
# The journal
# ============================================================================


@runtime_checkable
class RunJournal(Protocol):
    """Where run progress events are appended and read back.

    A journal is append-only per run and ordered. It is a notification log: the
    graph record remains the source of truth, and losing the journal degrades
    live progress only.
    """

    def append(self, event: RunEvent) -> str:
        """Append `event`; return the journal's id for it.

        The id is opaque to callers except as a cursor: pass it as `since` to a
        later `read`. Implementations that do not retain events return `""`.
        """
        ...

    def read(self, run_id: str, since: Optional[str] = None) -> List[RunEvent]:
        """Return the run's events in order.

        `since` is an EXCLUSIVE stream id: the event with that id is not
        returned. `None` means from the beginning. This is what gives a
        reconnecting subscriber exactly the events it missed.

        Each returned event carries the journal's id for it in
        `RunEvent.stream_id`, which is the value a subscriber passes back as
        `since` on its next read. A journal that does not retain events
        (`NullJournal`) leaves it empty.
        """
        ...

    def wait(
        self,
        run_id: str,
        since: Optional[str] = None,
        timeout_ms: int = 500,
        count: int = 100,
    ) -> List[RunEvent]:
        """Block up to `timeout_ms` for events after `since`, then return them.

        Returns an empty list on timeout — never blocks forever, so a caller can
        emit a heartbeat and check its own deadline. A journal that retains
        nothing returns `[]` immediately rather than sleeping, which is how a
        subscriber learns there is nothing to wait for.
        """
        ...

    def close(self, run_id: str, ttl_seconds: Optional[int] = None) -> None:
        """Release the run's journal, retaining it for `ttl_seconds` at most."""
        ...


class NullJournal:
    """A journal that retains nothing. For tests, CLI and runs nobody watches.

    Producer code must not branch on whether anyone is listening; a NullJournal
    lets it emit unconditionally at no cost.

    `retains = False` is how a *subscriber* learns the same thing: with no stream
    there is nothing to replay and nothing to wait for, so a live-push transport
    has nothing to push and must degrade to reading state instead.
    """

    retains = False

    def append(self, event: RunEvent) -> str:
        return ""

    def read(self, run_id: str, since: Optional[str] = None) -> List[RunEvent]:
        return []

    def wait(
        self,
        run_id: str,
        since: Optional[str] = None,
        timeout_ms: int = 500,
        count: int = 100,
    ) -> List[RunEvent]:
        # Deliberately not sleeping: a caller that waited here would hold a
        # connection open for a stream that cannot ever produce an event.
        return []

    def close(self, run_id: str, ttl_seconds: Optional[int] = None) -> None:
        return None


# A Stream per run, TTL'd once the run is terminal (YB-036).
STREAM_PREFIX = "sea:run:"
DEFAULT_MAXLEN = 10_000
"""Approximate cap on entries per run, so a pathological loop cannot grow a
stream without bound. `MAXLEN ~` lets Redis trim in whole macro-nodes."""

DEFAULT_TTL_SECONDS = 24 * 60 * 60
"""A day is long enough for an operator to look at last night's run and short
enough that journals do not accumulate in Valkey forever."""


def _import_client() -> Any:
    """Return the Redis-protocol client module, or raise naming the extra.

    Redis and Valkey share the RESP protocol; `redis-py` and `valkey-py` are
    interchangeable here, so prefer `redis` and fall back to `valkey`.
    """
    try:
        import redis  # type: ignore

        return redis
    except ImportError:
        pass
    try:
        import valkey  # type: ignore

        return valkey
    except ImportError:
        raise ImportError(
            "ValkeyJournal needs a Redis-protocol client, but neither 'redis' nor "
            "'valkey' is installed. Install the optional extra: "
            "pip install 'sea-agents[valkey]' (or: pip install 'redis>=5.0')."
        ) from None


class ValkeyJournal:
    """A `RunJournal` on a Redis/Valkey Stream, one stream per run.

    Streams (not Pub/Sub) because subscribers must be able to replay: `XRANGE`
    from a last-seen id gives a late or reconnecting subscriber the events it
    missed, and several Flask replicas can tail one run.

    The client library is imported in `__init__`, so importing `core.events`
    never requires the optional dependency. Construct against any RESP endpoint:

        journal = ValkeyJournal(host="localhost", port=6379)
    """

    retains = True

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        *,
        db: int = 0,
        prefix: str = STREAM_PREFIX,
        maxlen: Optional[int] = DEFAULT_MAXLEN,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        client: Any = None,
        **client_kwargs: Any,
    ) -> None:
        self._prefix = prefix
        self._maxlen = maxlen
        self._ttl_seconds = ttl_seconds
        if client is not None:
            # Injected client: tests and callers that already pool a connection.
            self._client = client
        else:
            redis = _import_client()
            self._client = redis.Redis(
                host=host, port=port, db=db, decode_responses=True, **client_kwargs
            )

    @property
    def client(self) -> Any:
        """The underlying client. An escape hatch for tests and operators; the
        journal's contract is the three methods above."""
        return self._client

    def stream_key(self, run_id: str) -> str:
        """The stream key for a run. Public because subscribers must tail the
        same key the producer writes."""
        return f"{self._prefix}{run_id}"

    def append(self, event: RunEvent) -> str:
        fields = event.to_dict()
        # The stream id is the journal's to assign, not the caller's to claim: it is
        # what `read` hands back so a subscriber can resume.
        fields.pop("stream_id", None)
        # Stream fields are flat strings; the payload is the one structured field.
        fields["payload"] = json.dumps(fields.get("payload", {}), separators=(",", ":"))
        for key, value in list(fields.items()):
            if not isinstance(value, (str, bytes, int, float)):
                fields[key] = str(value)
        kwargs: Dict[str, Any] = {}
        if self._maxlen is not None:
            kwargs["maxlen"] = self._maxlen
            kwargs["approximate"] = True  # MAXLEN ~
        stream_id = self._client.xadd(self.stream_key(event.run_id), fields, **kwargs)
        return _as_str(stream_id)

    def read(self, run_id: str, since: Optional[str] = None) -> List[RunEvent]:
        # `(` makes the lower bound exclusive: the event at `since` was already
        # seen by the subscriber and must not be delivered twice.
        start = f"({_as_str(since)}" if since else "-"
        entries = self._client.xrange(self.stream_key(run_id), start, "+")
        events: List[RunEvent] = []
        for stream_id, fields in entries:
            event = RunEvent.from_dict(_decode_fields(fields))
            # The journal owns the cursor. Stamping it here is what lets a
            # subscriber pass the last id it saw as `since` and receive exactly
            # what it missed — the property that makes this a Stream.
            event.stream_id = _as_str(stream_id)
            events.append(event)
        return events

    def wait(
        self,
        run_id: str,
        since: Optional[str] = None,
        timeout_ms: int = 500,
        count: int = 100,
    ) -> List[RunEvent]:
        """`XREAD BLOCK` for the next events, or `[]` on timeout.

        This is what makes a server-sent stream push rather than poll: the process
        sleeps in the server instead of a loop that wakes every 500 ms to ask. The
        timeout is the caller's to choose so it can also emit a heartbeat and check
        its own deadline.
        """
        # XREAD's start id is exclusive already ("greater than"), unlike XRANGE
        # which needs the `(` prefix to exclude the boundary.
        start = _as_str(since) or "0"
        response = self._client.xread(
            {self.stream_key(run_id): start}, count=count, block=int(timeout_ms)
        )
        if not response:
            return []
        events: List[RunEvent] = []
        for _key, entries in response:
            for stream_id, fields in entries:
                event = RunEvent.from_dict(_decode_fields(fields))
                event.stream_id = _as_str(stream_id)
                events.append(event)
        return events

    def close(self, run_id: str, ttl_seconds: Optional[int] = None) -> None:
        ttl = self._ttl_seconds if ttl_seconds is None else ttl_seconds
        if ttl is None:
            ttl = DEFAULT_TTL_SECONDS
        self._client.expire(self.stream_key(run_id), ttl)


def _decode_fields(fields: Mapping[Any, Any]) -> Dict[str, Any]:
    """Normalise a stream entry's fields to `str -> value`.

    Handles both `decode_responses=True` and byte-returning clients; `from_dict`
    parses the JSON-encoded payload and stringified numbers.
    """
    decoded: Dict[str, Any] = {}
    for key, value in fields.items():
        if isinstance(key, bytes):
            key = key.decode("utf-8", errors="replace")
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        decoded[key] = value
    return decoded
