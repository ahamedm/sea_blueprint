"""
The extraction pipeline's progress seam, and the journal-backed sink (YB-036).

WHAT THESE TESTS PIN, AND WHY
-----------------------------
YB-036 turns live progress into a first-class channel: the pipeline emits a
`RunEvent` per state transition and a sink appends it. The properties that are
easy to get wrong, and expensive because they are silent, are these:

  1. **Order and sequence.** A subscriber reconstructs a run by replaying events;
     a missing or repeated `seq` makes that reconstruction a guess.
  2. **One pair per chunk x pass.** The pair is the unit a progress bar and a
     "which pass is slow" question are both built from. A dropped `PASS_STARTED`
     leaves a pass that never appears to have started.
  3. **Transitions, never content.** The journal is a notification log. If
     extracted content can travel on it, the wire grows with the document and the
     record stops being the one source of truth. The payload key set is asserted
     closed, and the model's own answer is asserted absent.
  4. **A dead subscriber cannot break a run.** YB-036's failure contract: only
     *live* progress degrades. A sink that raises must leave every outcome intact.
  5. **The terminal event states a verdict.** End of stream is not completeness;
     a stream that merely stops must never read as finished.

The model is replaced at the one seam `run_passes` calls — `invoke_structured` —
so everything below it (prompt assembly, timing, outcome shaping, the fallback
path's shape) runs for real, as in `test_design_agent.py`.
"""

from __future__ import annotations

import json

import pytest

from agents.architecture_extraction.agent import ArchitectureExtractionAgent
from agents.base_agent import AgentConfig
from agents.extraction.chunking import Chunk
from agents.extraction.passes import PassSpec, run_passes
from agents.extraction.progress import PROGRESS_PAYLOAD_KEYS, JournalProgress
from core.events import (
    ERROR,
    PASS_FINISHED,
    PASS_STARTED,
    RUN_FAILED,
    RUN_FINISHED,
    RUN_STARTED,
    NullJournal,
    RunEvent,
    RunJournal,
)

# A marker standing in for "the extracted content". Nothing on the progress
# channel may carry it; the run record is where it belongs.
SECRET = "PAYMENT-ORCHESTRATOR-MUST-NOT-LEAK"


# ============================================================================
# The seam: a real agent with only the model call replaced
# ============================================================================


class TripleResult:
    """A stand-in pass result carrying `triples`, the only content a pass yields."""

    def __init__(self, triples=None):
        self.triples = list(triples or [])


def _agent(handler) -> ArchitectureExtractionAgent:
    """A real architecture agent whose model seam is replaced.

    Mirrors `test_completeness_reporting._architecture_agent`: the pipeline is
    exercised for real and only `invoke_structured` is stubbed, so nothing here
    depends on a model server.
    """
    agent = ArchitectureExtractionAgent(AgentConfig(
        name="Architecture Extraction Agent",
        description="test",
        model_provider="openai_compatible",
        model_id="progress-test-model",
        base_url="http://127.0.0.1:9/v1",
        api_key="stub",
        ontology_path="ontology/architecture_base.yaml",
        ontology_dir="ontology",
    ))
    agent.invoke_structured = handler      # the seam, replaced
    agent.invoke = lambda prompt: ""       # no text fallback in these tests
    return agent


def _specs():
    return [
        PassSpec(name="alpha", schema=TripleResult, instructions="Extract alpha."),
        PassSpec(name="beta", schema=TripleResult, instructions="Extract beta."),
    ]


def _chunks():
    return [
        Chunk(index=0, total=2, text="first chunk", heading_path="One"),
        Chunk(index=1, total=2, text="second chunk", heading_path="Two"),
    ]


def _ok_handler(prompt, schema):
    """One triple, whose subject is the content that must not reach the journal."""
    return TripleResult(triples=[{
        "subject": SECRET, "predicate": "is", "object": "content", "confidence": 0.9,
    }])


# ============================================================================
# An in-memory journal
# ============================================================================


class FakeJournal:
    """Records appended events, in order, and satisfies `RunJournal`.

    The in-memory counterpart to `NullJournal`: it retains what a real journal
    would, so order and sequence can be asserted without Valkey.
    """

    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    def append(self, event: RunEvent) -> str:
        self.events.append(event)
        return f"{len(self.events)}-0"

    def read(self, run_id: str, since: str | None = None):
        return [e for e in self.events if e.run_id == run_id]

    def close(self, run_id: str, ttl_seconds: int | None = None) -> None:
        return None


class RecordingSink:
    """A plain callable sink, as `run_passes` accepts: it just keeps events."""

    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    def __call__(self, event: RunEvent) -> None:
        self.events.append(event)


def test_the_fake_journal_is_a_journal():
    assert isinstance(FakeJournal(), RunJournal)
    assert isinstance(NullJournal(), RunJournal)


# ============================================================================
# 1. Order and sequence
# ============================================================================


def test_events_are_appended_in_order_with_a_monotonic_sequence():
    journal = FakeJournal()
    progress = JournalProgress(journal, run_id="run_1", scope_id="ARC-G")

    run_passes(_agent(_ok_handler), _specs(), _chunks(), progress=progress)

    assert [e.seq for e in journal.events] == list(range(1, len(journal.events) + 1))
    assert [e.seq for e in journal.events] == sorted(e.seq for e in journal.events)
    # The sink stamps the run identity, so a subscriber can filter one run out of
    # a shared journal without the pipeline having known the run id.
    assert {e.run_id for e in journal.events} == {"run_1"}
    assert {e.scope_id for e in journal.events} == {"ARC-G"}
    assert all(not e.is_terminal for e in journal.events)
    assert progress.seq == len(journal.events)


def test_a_plain_callable_sink_receives_the_envelopes():
    sink = RecordingSink()
    run_passes(_agent(_ok_handler), _specs(), _chunks(), progress=sink)
    assert sink.events
    assert all(isinstance(e, RunEvent) for e in sink.events)


def test_no_progress_sink_is_the_default_and_changes_nothing():
    outcomes = run_passes(_agent(_ok_handler), _specs(), _chunks())
    assert len(outcomes) == 4
    assert all(o.ok for o in outcomes)


# ============================================================================
# 2. One PASS_STARTED / PASS_FINISHED pair per chunk x pass
# ============================================================================


def test_a_started_and_finished_pair_appears_per_chunk_and_pass():
    journal = FakeJournal()
    run_passes(
        _agent(_ok_handler), _specs(), _chunks(),
        progress=JournalProgress(journal, run_id="run_2"),
    )

    kinds = [e.kind for e in journal.events]
    assert kinds == [PASS_STARTED, PASS_FINISHED] * 4

    started, finished = journal.events[0::2], journal.events[1::2]
    assert [(e.payload["pass_name"], e.payload["chunk_label"]) for e in started] == [
        ("alpha", "chunk 1/2"),
        ("beta", "chunk 1/2"),
        ("alpha", "chunk 2/2"),
        ("beta", "chunk 2/2"),
    ]
    # ...and each finish names the same transition its start did.
    assert [(e.payload["pass_name"], e.payload["chunk_label"]) for e in finished] == [
        (e.payload["pass_name"], e.payload["chunk_label"]) for e in started
    ]
    assert {e.payload["outcome"] for e in finished} == {"ok"}
    assert {e.payload["path"] for e in finished} == {"structured"}
    assert {e.payload["triples_produced"] for e in finished} == {1}


def test_a_failed_pass_emits_started_then_error():
    def handler(prompt, schema):
        raise RuntimeError("model exploded")

    journal = FakeJournal()
    outcomes = run_passes(
        _agent(handler), _specs(), _chunks(),
        progress=JournalProgress(journal, run_id="run_3"),
        allow_text_fallback=False,
    )

    assert [e.kind for e in journal.events] == [PASS_STARTED, ERROR] * 4
    errors = [e for e in journal.events if e.kind == ERROR]
    assert all(e.payload["outcome"] == "failed" for e in errors)
    assert all("model exploded" in e.payload["error"] for e in errors)
    # The failure is recorded on the run as before; progress did not alter it.
    assert all(o.error for o in outcomes)


# ============================================================================
# 3. Transitions, never extracted content
# ============================================================================

# The exact key set each non-terminal kind may carry. `PASS_STARTED` names what
# is about to happen; `PASS_FINISHED`/`ERROR` report what changed. Nothing else.
EXPECTED_KEYS = {
    PASS_STARTED: {"pass_name", "chunk_label"},
    PASS_FINISHED: {
        "pass_name", "chunk_label", "outcome", "elapsed", "triples_produced", "path",
    },
    ERROR: {
        "pass_name", "chunk_label", "outcome", "elapsed", "triples_produced",
        "path", "error",
    },
}

# The terminal kinds carry the verdict the pipeline cannot know; `RUN_FAILED`
# also carries the cause.
EXPECTED_TERMINAL_KEYS = {
    RUN_FINISHED: {"completeness"},
    RUN_FAILED: {"completeness", "error"},
}


def test_the_payload_vocabulary_is_exactly_the_documented_transition_set():
    documented = set().union(*EXPECTED_KEYS.values(), *EXPECTED_TERMINAL_KEYS.values())
    assert PROGRESS_PAYLOAD_KEYS == documented


def test_no_event_payload_carries_extracted_content():
    sink = RecordingSink()
    run_passes(_agent(_ok_handler), _specs(), _chunks(), progress=sink)

    for event in sink.events:
        # Keys are exactly the allowed set for the kind, so a content field
        # (`triples`, `elements`, `text`, ...) cannot be smuggled onto the wire.
        assert set(event.payload) == EXPECTED_KEYS[event.kind]
        assert set(event.payload) <= PROGRESS_PAYLOAD_KEYS

    # And the model's actual answer appears nowhere in the serialised payloads.
    wire = json.dumps([e.to_dict() for e in sink.events])
    assert SECRET not in wire
    # The counter says how much arrived without saying what it was.
    assert {e.payload.get("triples_produced") for e in sink.events if e.kind == PASS_FINISHED} == {1}


def test_a_failed_pass_still_leaks_no_content():
    def handler(prompt, schema):
        raise RuntimeError("model exploded")

    sink = RecordingSink()
    run_passes(
        _agent(handler), _specs(), _chunks(), progress=sink, allow_text_fallback=False,
    )
    for event in sink.events:
        assert set(event.payload) <= PROGRESS_PAYLOAD_KEYS
    assert all(set(e.payload) == EXPECTED_KEYS[e.kind] for e in sink.events)


# ============================================================================
# 4. A dead subscriber cannot break a run
# ============================================================================


def test_a_raising_sink_does_not_break_run_passes():
    """The failure contract: only live progress degrades, never the run."""
    calls = []

    def dead_sink(event):
        calls.append(event.kind)
        raise RuntimeError("subscriber is dead")

    log_lines = []
    outcomes = run_passes(
        _agent(_ok_handler), _specs(), _chunks(),
        progress=dead_sink, log=log_lines.append,
    )

    # Every pass still ran and every outcome is exactly what it would have been.
    assert len(outcomes) == 4
    assert all(o.ok for o in outcomes)
    assert [o.pass_name for o in outcomes] == ["alpha", "beta", "alpha", "beta"]
    # The sink was called and failed on every transition — twice per pass.
    assert calls == [PASS_STARTED, PASS_FINISHED] * 4
    assert any("progress sink failed" in line for line in log_lines)


def test_a_sink_that_raises_on_only_some_events_is_equally_harmless():
    calls = []

    def flaky_sink(event):
        calls.append(event.kind)
        if event.kind == PASS_STARTED:
            raise ValueError("started events are poison")

    outcomes = run_passes(_agent(_ok_handler), _specs(), _chunks(), progress=flaky_sink)
    assert all(o.ok for o in outcomes)
    assert len(calls) == 8


# ============================================================================
# 5. The terminal event states a verdict explicitly
# ============================================================================


def test_finished_requires_and_records_the_completeness_verdict():
    journal = FakeJournal()
    progress = JournalProgress(journal, run_id="run_4", scope_id="ARC-G")
    progress.started()
    progress.finished("PARTIAL")

    terminal = journal.events[-1]
    assert terminal.kind == RUN_FINISHED
    assert terminal.is_terminal
    assert terminal.payload["completeness"] == "PARTIAL"
    assert terminal.completeness == "PARTIAL"
    assert journal.events[0].kind == RUN_STARTED
    # Sequencing continues across the terminal event; the sink is one run's writer.
    assert [e.seq for e in journal.events] == [1, 2]


def test_completeness_is_positional_so_it_cannot_be_forgotten():
    """End of stream is not completeness: a caller must state the verdict."""
    with pytest.raises(TypeError):
        JournalProgress(FakeJournal(), run_id="run_5").finished()


def test_failed_is_terminal_and_also_carries_a_verdict():
    journal = FakeJournal()
    progress = JournalProgress(journal, run_id="run_6", scope_id="ARC-G")
    progress.started()
    progress.failed("the model vanished")

    terminal = journal.events[-1]
    assert terminal.kind == RUN_FAILED
    assert terminal.is_terminal
    assert terminal.completeness, "a failed run must never read as an unstated end"
    assert terminal.payload["completeness"] == terminal.completeness
    assert "the model vanished" in terminal.payload["error"]


def test_a_null_journal_is_a_valid_sink_that_retains_nothing():
    """The CLI and unwatched-run case: emit unconditionally at no cost."""
    progress = JournalProgress(NullJournal(), run_id="run_7")
    assert progress.started() == ""
    assert progress.finished("COMPLETE") == ""
    assert progress.seq == 2
    assert isinstance(progress.journal, NullJournal)
