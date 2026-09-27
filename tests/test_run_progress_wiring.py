"""
The producer chain, end to end: agent → journal → run record.

The pieces were built separately — `run_passes` emits, `JournalProgress` stamps and
appends, ingest mints the run record — and this file pins the joins between them, which
is where a progress feature usually turns out to be wired to nothing:

  1. **The agent forwards an attached sink.** `input_data["progress"]` reaches
     `run_passes`; without that, every sink is silent and no test of `run_passes` alone
     would notice.
  2. **The run id correlates.** Events are published while extraction runs, but the run
     record is created afterwards, and `_run_id` includes the current time — so the id is
     unpredictable. A caller must be able to mint it and pass it through, or the live
     stream and the stored run can never be joined.
  3. **The verdict is published after it is persisted.** `finished(completeness)` takes
     the verdict from the run record, not from a guess.
"""

from __future__ import annotations

from agents.architecture_extraction.agent import ArchitectureExtractionAgent
from agents.architecture_extraction.passes import ElementRecord, StructurePassResult
from agents.base_agent import AgentConfig
from agents.extraction.progress import JournalProgress
from core.events import PASS_FINISHED, PASS_STARTED, RUN_FINISHED, RunEvent
from core.knowledge import graph_from_extraction

RUN_ID = "run_fixed_for_the_test"
DOCUMENT = "The platform contains a Payment Orchestrator and a PostgreSQL database."


class FakeJournal:
    """Appends in order and replays per run, like the real Stream does."""

    def __init__(self):
        self.rows: list[RunEvent] = []

    def append(self, event: RunEvent) -> str:
        self.rows.append(event)
        return f"{len(self.rows)}-0"

    def read(self, run_id: str, since=None):
        rows = [e for e in self.rows if e.run_id == run_id]
        if since is not None:
            cursor = int(str(since).split("-")[0])
            rows = [e for e in rows if e.seq > cursor]
        return rows

    def wait(self, run_id, since=None, timeout_ms=500, count=100):
        # A journal that retains events would block here; the fake has nothing new
        # between calls, so it returns immediately like `NullJournal`.
        return []

    def close(self, run_id: str, ttl_seconds=None) -> None:
        return None


def _agent():
    agent = ArchitectureExtractionAgent(AgentConfig(
        name="Architecture Extraction Agent", description="test",
        model_provider="openai_compatible", model_id="stub-model",
        base_url="http://127.0.0.1:9/v1", api_key="stub",
        ontology_path="ontology/architecture_base.yaml", ontology_dir="ontology",
    ))

    def handler(prompt, schema):
        if schema is StructurePassResult:
            return StructurePassResult(elements=[
                ElementRecord(name="Payment Gateway Platform", element_type="SoftwareSystem"),
            ])
        return schema()   # a valid empty answer for the other three passes

    agent.invoke_structured = handler      # the only seam replaced
    agent.invoke = lambda prompt: ""
    return agent


def test_the_agent_forwards_an_attached_sink_and_the_run_id_correlates():
    journal = FakeJournal()
    sink = JournalProgress(journal, run_id=RUN_ID, scope_id="arc")

    result = _agent().run({
        "document": DOCUMENT,
        "document_type": "architecture",
        "progress": sink,          # the whole point: does it reach run_passes?
    })

    # 1. The sink received a started/finished pair per pass (four passes, one chunk).
    kinds = [event.kind for event in journal.rows]
    assert kinds.count(PASS_STARTED) == 4, kinds
    assert kinds.count(PASS_FINISHED) == 4, kinds
    # Ordered, monotonic, and stamped with the journal's identity rather than the
    # pipeline's — the pipeline names a transition, it does not own the envelope.
    assert [event.seq for event in journal.rows] == list(range(1, len(journal.rows) + 1))
    assert {event.run_id for event in journal.rows} == {RUN_ID}
    assert {event.scope_id for event in journal.rows} == {"arc"}

    # 2. The run record takes the caller's id, so the live stream can be joined to it
    #    after the fact.
    _, run = graph_from_extraction(
        result.output, {**result.metadata, "run_id": RUN_ID},
        document_ref="payment_platform_arch.md", document_text=DOCUMENT,
    )
    assert run.id == RUN_ID

    # 3. The verdict is published from the record, after the record exists.
    sink.finished(run.completeness)
    terminal = journal.rows[-1]
    assert terminal.kind == RUN_FINISHED
    assert terminal.payload["completeness"] == run.completeness
    assert terminal.run_id == run.id


def test_a_run_that_never_gets_a_finished_event_is_not_reported_as_finished():
    """The reason `finished()` demands a verdict: a stream that merely ends is
    indistinguishable from success unless the terminal event says otherwise."""
    journal = FakeJournal()
    sink = JournalProgress(journal, run_id=RUN_ID, scope_id="arc")

    _agent().run({"document": DOCUMENT, "document_type": "architecture",
                  "progress": sink})

    assert not any(event.is_terminal for event in journal.rows)


def test_a_run_with_no_sink_still_produces_its_record():
    """Progress is optional by construction: an MVP install with no journal behaves
    exactly as before."""
    result = _agent().run({"document": DOCUMENT, "document_type": "architecture"})

    assert result.success
    _, run = graph_from_extraction(result.output, result.metadata,
                                   document_ref="doc.md", document_text=DOCUMENT)
    # No caller-minted id, so ingest mints one — and it is still a usable run.
    assert run.id.startswith("run_")
    assert run.completeness


def test_two_runs_started_in_the_same_second_get_different_ids():
    """The id is the KEY of `graph.runs`, so a collision is a silent overwrite.

    `new_run_id` hashed `utc_now()`, which has second resolution — so minting two ids
    in one second returned the same string, and ingesting two documents back to back
    made the second run's `ExtractionRun` replace the first. That loses the first run's
    completeness and per-pass outcomes, which is what `/c4`'s run-completeness gap and
    the run page read. `new_revision_id` beside it already added a uuid4 for exactly
    this reason; this asserts the run id does too.
    """
    from core.knowledge.ingest import new_run_id

    ids = [new_run_id() for _ in range(200)]

    assert len(set(ids)) == len(ids)
    assert all(i.startswith("run_") for i in ids)


def test_two_runs_in_one_second_keep_both_records():
    """The consequence, stated as behaviour rather than as an id property."""
    from core.knowledge.ingest import new_run_id

    first, second = new_run_id(), new_run_id()
    graph = {}
    for run_id in (first, second):
        _, run = graph_from_extraction(_agent().run(
            {"document": DOCUMENT, "document_type": "architecture"}).output,
            {"run_id": run_id}, document_ref="doc.md", document_text=DOCUMENT)
        graph[run.id] = run

    assert set(graph) == {first, second}
