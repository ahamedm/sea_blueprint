"""
A progress sink that journals run events (YB-036).

WHAT THIS IS
------------
`run_passes` emits a `RunEvent` per state transition and knows nothing about who
is listening. This module is the durable listener: it wraps a `RunJournal`, owns
the run identity and the monotonic sequence the envelope requires, and appends
each event. It is a plain callable, so it plugs straight into the pipeline:

    progress = JournalProgress(journal, run_id="run_abc", scope_id="ARC-G")
    progress.started(pass_name="structure")
    outcomes = run_passes(agent, passes, chunks, progress=progress)
    progress.finished("PARTIAL", chunks=len(chunks))

WHY SEQUENCING LIVES HERE, NOT IN THE PIPELINE
----------------------------------------------
`run_id`, `scope_id` and `seq` are journal concerns, not extraction concerns. The
pipeline should not have to be handed a run id to report that a pass began, and a
shared counter in the pipeline would be wrong the moment two runs interleave. So
the pipeline names the transition and this sink stamps the envelope.

WHY THE TERMINAL HELPERS TAKE A VERDICT
---------------------------------------
A stream that *ends* looks finished whether or not it was — the false assurance
ADR-0013 exists to prevent. `finished()` therefore requires `completeness`
positionally: a caller cannot end a run on this channel without saying whether it
was COMPLETE, PARTIAL or UNKNOWN. `failed()` always records a verdict too, so a
dropped terminal event can never be read as success. The verdict is persisted in
the run record *before* it is published; losing this journal degrades live
progress only, never correctness.

THE PAYLOAD IS TRANSITIONS, NOT CONTENT
---------------------------------------
`PROGRESS_PAYLOAD_KEYS` is the whole vocabulary this channel may carry: names,
labels, counters, outcome, timing, path, a diagnostic error string, and the
terminal verdict. Extracted content stays in the run record, which is the source
of truth. Keeping the key set closed is what makes "no extracted content on the
wire" checkable rather than merely intended.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from core.events import (
    RUN_FAILED,
    RUN_FINISHED,
    RUN_STARTED,
    RunEvent,
    RunJournal,
)

__all__ = ["PROGRESS_PAYLOAD_KEYS", "JournalProgress"]

_log = logging.getLogger(__name__)


PROGRESS_PAYLOAD_KEYS = frozenset({
    # identity of the transition
    "pass_name",
    "chunk_label",
    # what changed
    "outcome",
    "elapsed",
    "triples_produced",
    "path",
    # diagnostics and the terminal verdict
    "error",
    "completeness",
    # run-level identity, carried once on `run.started`. Without these a viewer
    # knows a run began but not what it is reading or how much of it there is —
    # and per-key closure is what makes "no extracted content on the wire"
    # checkable, so the run-level names belong here rather than in a free payload.
    "document_ref",
    "document_type",
    "domain_pack",
    "chunks",
    "pass_count",
})
"""Every key a progress payload may carry. Deliberately closed: a key outside
this set is either a new transition field that belongs here, or extracted
content that does not belong on this channel at all."""


class JournalProgress:
    """A `RunJournal`-backed progress sink for `run_passes(..., progress=...)`.

    Not thread-safe and not meant to be shared between runs: one instance is one
    run's writer, which is what makes a plain incrementing counter a correct
    sequence.
    """

    def __init__(self, journal: RunJournal, run_id: str, scope_id: str = "") -> None:
        self.journal = journal
        self.run_id = run_id
        self.scope_id = scope_id
        self._seq = 0

    @property
    def seq(self) -> int:
        """The sequence number of the last event appended (0 before any)."""
        return self._seq

    # -- the pipeline-facing seam -------------------------------------------

    def __call__(self, event: RunEvent) -> str:
        """Accept a pipeline event, restamp identity and sequence, append it.

        The pipeline builds the event to name a transition; the *journal's* run
        identity and this sink's counter are authoritative, so the incoming
        envelope's `run_id`/`scope_id`/`seq` are not trusted. That keeps the
        stream monotonic even if an event is reused or a caller builds a partial
        envelope by hand.
        """
        return self._append(event.kind, event.payload)

    # -- terminal helpers ---------------------------------------------------

    def started(self, **payload: Any) -> str:
        """Append `RUN_STARTED`. Payload: document/scope identity and the plan."""
        return self._append(RUN_STARTED, payload)

    def finished(self, completeness: str, **payload: Any) -> str:
        """Append the terminal `RUN_FINISHED`, verdict required.

        `completeness` is positional and required on purpose: end of stream is
        not completeness, so a caller cannot finish a run on this channel without
        stating whether it was COMPLETE, PARTIAL or UNKNOWN.
        """
        return self._append(RUN_FINISHED, {**payload, "completeness": completeness})

    def failed(self, error: Any, completeness: Optional[str] = None, **payload: Any) -> str:
        """Append the terminal `RUN_FAILED`, carrying a verdict and the cause.

        The verdict defaults to `UNKNOWN` — a run that died did not establish
        completeness — and is never allowed to be absent, so a reader cannot
        mistake a failed stream for a finished one.
        """
        if completeness is None:
            # Local import, following passes.py: keeps agents off core's heavier
            # import path and reuses the one verdict vocabulary.
            from core.knowledge.model import RUN_UNKNOWN

            completeness = RUN_UNKNOWN
        return self._append(
            RUN_FAILED,
            {**payload, "completeness": completeness, "error": str(error)[:200]},
        )

    # -- lifecycle ----------------------------------------------------------

    def close(self, ttl_seconds: Optional[int] = None) -> None:
        """Release the run's journal, retaining it for `ttl_seconds` at most.

        Best-effort like every other write here: a run is finished whether or not
        anyone is still holding the log.
        """
        try:
            self.journal.close(self.run_id, ttl_seconds=ttl_seconds)
        except Exception as exc:  # noqa: BLE001 - see `_append`
            _log.warning("run journal close failed for %s: %s", self.run_id, exc)

    # -- internals ----------------------------------------------------------

    def _append(self, kind: str, payload: Dict[str, Any]) -> str:
        """Restamp one transition and append it, never raising.

        `run_passes` guards its own calls (`emit_progress`), but a caller that
        drives this sink directly — the web routes do — must get the same
        guarantee, because the sink's contract is that losing the journal degrades
        *live progress only*. A failing append is logged and returns "", so a dead
        or misconfigured journal can never 500 a page or fail a run.
        """
        self._seq += 1
        event = RunEvent(
            run_id=self.run_id,
            scope_id=self.scope_id,
            seq=self._seq,
            kind=kind,
            payload=dict(payload),
        )
        try:
            return self.journal.append(event)
        except Exception as exc:  # noqa: BLE001 - a dead journal degrades progress
            _log.warning(
                "run journal append failed for %s (%s): %s", self.run_id, kind, exc
            )
            return ""
