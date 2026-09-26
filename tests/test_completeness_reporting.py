"""
Requirements-run completeness — the YB-023 fix.

THE DEFECT. `ExtractionRun.compute_completeness()` returns `UNKNOWN` when a run has
no pass records, and that is right: absence of pass detail is ignorance, not
success. But it was the *permanent* answer for every requirements run, because the
requirements profile emitted no pass metadata at all — no `model_calls`, no
`failed_calls`, no `empty_calls`, no `model_id`. Ingest reconstructed records from
those counters, found nothing, and produced an empty list, so every REQ-G graph was
UNKNOWN and therefore never auditable. Observed in the wild on
`run_aa36f85b79f4`: `completeness=UNKNOWN`, `passes=0`, `model_id=''`, while the
architecture run in the same working set reported COMPLETE.

WHAT THESE TESTS PIN. Three things, in the order the data flows:

  1. the profile's per-attempt records (`KnowledgeExtractionAgent._pass_records`)
  2. the ingest reader that prefers them over the counters (`_passes_from_metadata`)
  3. the verdict, through the real `graph_from_extraction` path

The second is the one that would have caught this: two shapes of the same
information existed, and nothing checked that they agreed.
"""

import yaml

import pytest

from agents.architecture_extraction.agent import ArchitectureExtractionAgent
from agents.architecture_extraction.passes import ElementRecord, StructurePassResult
from agents.base_agent import AgentConfig, console
from agents.knowledge_extraction.agent import (
    ExtractedEntity,
    ExtractedTriple,
    KnowledgeExtractionAgent,
)
from core.knowledge import graph_from_extraction
from core.knowledge.ingest import _passes_from_metadata
from core.knowledge.model import (
    RUN_COMPLETE,
    RUN_PARTIAL,
    RUN_UNKNOWN,
    PassRecord,
)


def _agent(**config):
    agent = KnowledgeExtractionAgent.__new__(KnowledgeExtractionAgent)
    agent.config = AgentConfig(
        name="probe", description="probe",
        ontology_path="ontology/requirements_base.yaml", ontology_dir="ontology",
        model_id="test-model", **config,
    )
    agent.console = console
    agent.ontology = yaml.safe_load(open("ontology/requirements_base.yaml"))
    agent.domain_pack = None
    agent.agent = None
    agent.confidence_threshold = 0.7
    return agent


# ============================================================================
# 1. The profile's own records
# ============================================================================


def test_a_structured_run_reports_one_successful_pass():
    records = _agent()._pass_records(
        path="structured_output", structured_error=None, triples=12, entities=4
    )
    assert [(p.pass_name, p.outcome, p.path) for p in records] == [
        ("structured", "ok", "structured")
    ]
    assert records[0].triples_produced == 12


def test_a_text_fallback_run_reports_the_failure_and_the_fallback():
    """Both attempts are visible, and the reason is kept.

    Flattening this to one `ok` record would report a run whose structured call
    never worked as fully complete — the false assurance the field exists to
    prevent.
    """
    records = _agent()._pass_records(
        path="text_parsing",
        structured_error="model did not satisfy schema within turn budget",
        triples=9,
        entities=3,
    )
    assert [(p.pass_name, p.outcome, p.path) for p in records] == [
        ("structured", "empty", "none"),
        ("text_fallback", "ok", "text"),
    ]
    assert "did not satisfy schema" in records[0].error


def test_the_reason_distinguishes_an_empty_result_from_a_failed_call():
    """`invoke_structured` returns None for several different problems.

    "Came back empty" and "was cancelled / unsupported" leave the same absence
    behind, but they are different things to fix, so the record's `path` keeps them
    apart while the outcome stays `empty` for both.
    """
    empty = _agent()._pass_records(
        path="text_parsing", structured_error="structured output returned empty",
        triples=1, entities=1,
    )
    cancelled = _agent()._pass_records(
        path="text_parsing", structured_error="model did not satisfy schema within turn budget",
        triples=1, entities=1,
    )
    assert empty[0].path == "structured"
    assert cancelled[0].path == "none"
    assert empty[0].outcome == cancelled[0].outcome == "empty"


def test_a_forced_text_run_does_not_invent_a_failed_attempt():
    """`force_text_parsing` skips structured output entirely. Reporting a failed
    attempt that never happened would be a fabricated pass record."""
    records = _agent()._pass_records(
        path="text_parsing", structured_error=None, triples=5, entities=2
    )
    assert [(p.pass_name, p.outcome) for p in records] == [("text_fallback", "ok")]


def test_a_text_run_that_produced_nothing_reports_empty():
    records = _agent()._pass_records(
        path="text_parsing", structured_error=None, triples=0, entities=0
    )
    assert [p.outcome for p in records] == ["empty"]


# ============================================================================
# 2. The profile's metadata envelope
# ============================================================================


def test_the_metadata_carries_records_and_counters_that_agree(monkeypatch):
    """The end-to-end claim, with the model stubbed out.

    Records and counters are both emitted, and they have to describe the same run:
    the counters are what old consumers read, and a mismatch between the two is
    exactly the drift that hid this defect.
    """
    # A pipe-separated row is one of the formats the text parser actually handles
    # (see the docstring on `_parse_triples_from_text`), so the fallback really
    # does produce content — which is what makes the fallback a *partial* run
    # rather than an empty one.
    agent = _agent(use_structured_output=True)
    monkeypatch.setattr(agent, "invoke_structured", lambda *_a, **_k: None)
    monkeypatch.setattr(
        agent, "invoke", lambda *_a, **_k: "Payment Acceptance | requires | Validation | 0.9"
    )

    result = agent.run({"document": "Payment Acceptance requires Validation."})

    assert result.success
    meta = result.metadata
    assert meta["extraction_path"] == "text_parsing"
    # The bug: none of these existed, so the run could only ever be UNKNOWN.
    assert meta["model_id"] == "test-model"
    assert meta["model_calls"] == 2
    assert meta["text_fallback_calls"] == 1
    assert meta["empty_calls"] == 1
    assert meta["failed_calls"] == 0
    assert [p["outcome"] for p in meta["passes"]] == ["empty", "ok"]
    assert meta["passes"][0]["pass_name"] == "structured"


def test_counters_and_records_describe_the_same_run(monkeypatch):
    """A crossing check the earlier design could not have passed.

    The counters are derived FROM the records, so a disagreement is a bug in the
    derivation rather than a fact about the run — and it is the class of drift
    (two shapes, one checked) that let the requirements profile report nothing.
    """
    agent = _agent(use_structured_output=True)
    monkeypatch.setattr(agent, "invoke_structured", lambda *_a, **_k: None)
    monkeypatch.setattr(agent, "invoke", lambda *_a, **_k: "")

    meta = agent.run({"document": "nothing extractable"}).metadata
    outcomes = [p["outcome"] for p in meta["passes"]]

    assert meta["model_calls"] == len(outcomes)
    assert meta["empty_calls"] == outcomes.count("empty")
    assert meta["failed_calls"] == outcomes.count("failed")
    assert meta["text_fallback_calls"] == sum(1 for p in meta["passes"] if p["path"] == "text")


def test_a_structured_success_reports_metadata_that_yields_complete(monkeypatch):
    from agents.knowledge_extraction.agent import ExtractionResult

    payload = ExtractionResult(
        triples=[ExtractedTriple(subject="A", predicate="requires", object="B", confidence=0.9)],
        entities=[ExtractedEntity(name="A", ontology_class="FunctionalRequirement")],
    )
    agent = _agent(use_structured_output=True)
    monkeypatch.setattr(agent, "invoke_structured", lambda *_a, **_k: payload)

    meta = agent.run({"document": "A requires B."}).metadata

    assert meta["extraction_path"] == "structured_output"
    assert meta["model_calls"] == 1
    assert meta["text_fallback_calls"] == 0
    assert meta["empty_calls"] == 0
    assert [p["outcome"] for p in meta["passes"]] == ["ok"]


# ============================================================================
# 3. The ingest reader
# ============================================================================


def test_records_win_over_counters_when_both_are_present():
    """Per-attempt records are strictly richer, so they are read first."""
    metadata = {
        "model_calls": 1,
        "passes": [
            {"pass_name": "structured", "outcome": "empty", "path": "none"},
            {"pass_name": "text_fallback", "outcome": "ok", "path": "text"},
        ],
    }
    assert [(p.pass_name, p.outcome) for p in _passes_from_metadata(metadata)] == [
        ("structured", "empty"),
        ("text_fallback", "ok"),
    ]


def test_counters_are_still_read_for_output_that_predates_the_records():
    records = _passes_from_metadata({"model_calls": 3, "failed_calls": 1, "empty_calls": 1})
    assert sorted(p.outcome for p in records) == ["empty", "failed", "ok"]


def test_a_text_fallback_counter_is_not_counted_as_a_clean_pass():
    """THE SECOND SITE OF THE SAME BUG.

    The architecture profile has always emitted `text_fallback_calls`, and ingest
    never read it — so a run whose calls all fell back to text reconstructed as
    every call `ok` → COMPLETE → the audit gate opened on a run that is genuinely
    incomplete. The counter is now read as `empty`, which is what a fallback is.
    """
    records = _passes_from_metadata({"model_calls": 2, "text_fallback_calls": 2})
    assert [p.outcome for p in records] == ["empty", "empty"]
    assert all(p.path == "text" for p in records)


def test_a_pre_switch_run_reports_unknown_rather_than_complete():
    """No records and no counters is ignorance, and must stay UNKNOWN.

    The fix must not turn every silent run into an assurance — that would be the
    same falsehood pointing the other way.
    """
    assert _passes_from_metadata({}) == []
    assert _passes_from_metadata({"document_type": "requirements"}) == []


def test_an_unreadable_outcome_is_not_promoted_to_ok():
    """An outcome we cannot parse is skipped, never defaulted to success — that is
    the one reading that could open the gate on unreadable evidence."""
    records = _passes_from_metadata(
        {"model_calls": 2, "passes": [{"pass_name": "structured", "outcome": "weird"}]}
    )
    # The malformed entry is dropped; the counters still describe the run.
    assert [p.outcome for p in records] == ["ok", "ok"]


# ============================================================================
# 4. The verdict, through the real ingest path
# ============================================================================


def _run(metadata, document=""):
    output = {"triples": [], "entities": [], "relationships": []}
    graph, run = graph_from_extraction(output, metadata, document_ref="brief.md",
                                       document_text=document)
    return run


def test_a_structured_run_is_complete_and_therefore_auditable():
    run = _run({
        "document_type": "requirements",
        "model_id": "test-model",
        "model_calls": 1,
        "failed_calls": 0,
        "empty_calls": 0,
        "text_fallback_calls": 0,
        "passes": [{"pass_name": "structured", "outcome": "ok", "path": "structured"}],
    })
    assert run.completeness == RUN_COMPLETE


def test_a_text_fallback_run_is_partial_not_complete():
    """The whole point of the fix: a run that fell back says so.

    PARTIAL still refuses the audit, which is correct — the text parser does not
    cover every collection, so "some content is missing" is the truth about this
    run. What changes is that it is now *stated* rather than reported as an
    unexplainable UNKNOWN.
    """
    run = _run({
        "document_type": "requirements",
        "model_id": "test-model",
        "model_calls": 2,
        "empty_calls": 1,
        "text_fallback_calls": 1,
        "passes": [
            {"pass_name": "structured", "outcome": "empty", "path": "none"},
            {"pass_name": "text_fallback", "outcome": "ok", "path": "text"},
        ],
    })
    assert run.completeness == RUN_PARTIAL


def test_the_requirements_profile_no_longer_reports_unknown(monkeypatch):
    """The regression, as the user hit it.

    A requirements run through the real agent and the real ingest used to land on
    UNKNOWN with `passes=0` and `model_id=''`. It must now report what actually
    happened.
    """
    agent = _agent(use_structured_output=True)
    monkeypatch.setattr(agent, "invoke_structured", lambda *_a, **_k: None)
    monkeypatch.setattr(agent, "invoke", lambda *_a, **_k: "no triples here")

    result = agent.run({"document": "Payment Acceptance requires Validation."})
    run = _run(result.metadata, document="Payment Acceptance requires Validation.")

    assert run.completeness == RUN_PARTIAL
    assert len(run.passes) == 2
    assert run.model_id == "test-model"
    assert [p.outcome for p in run.passes] == ["empty", "empty"]


def test_a_bare_metadata_envelope_is_still_unknown():
    """Backward compatibility, stated as an expectation rather than assumed: the
    saved fixture predates this and will keep reading UNKNOWN until it is re-run."""
    assert _run({"document_type": "requirements"}) .completeness == RUN_UNKNOWN


# ============================================================================
# 5. The architecture profile — the last one reconstructing `(unspecified)`
# ============================================================================


def _architecture_agent(handler):
    """A real architecture agent with only the model seam replaced.

    The requirements profile above makes one structured attempt; this one runs
    four passes over every chunk. It emitted the aggregate counters but none of
    the records, so ingest reconstructed every attempt as
    `pass_name="(unspecified)"` with `triples_produced=0` — a run that could not
    say which pass lost content, or which model produced it.
    """
    agent = ArchitectureExtractionAgent(AgentConfig(
        name="Architecture Extraction Agent",
        description="test",
        model_provider="openai_compatible",
        model_id="arch-test-model",
        base_url="http://127.0.0.1:9/v1",
        api_key="stub",
        ontology_path="ontology/architecture_base.yaml",
        ontology_dir="ontology",
    ))
    agent.invoke_structured = handler          # the seam, replaced
    agent.invoke = lambda prompt: ""           # no text fallback in tests
    return agent


def _arch_handler(prompt, schema):
    """Answer the structure pass; return a valid empty answer for the other three.

    An empty result is a legitimate pass outcome, not a failure, so this stubs the
    seam without pretending the run produced more than it did.
    """
    if schema is StructurePassResult:
        return StructurePassResult(
            elements=[ElementRecord(name="Payment Gateway Platform",
                                    element_type="SoftwareSystem")],
            triples=[ExtractedTriple(subject="Payment Orchestrator", predicate="part_of",
                                     object="Payment Gateway Platform", confidence=0.9)],
        )
    return schema()


def test_the_architecture_metadata_carries_the_model_and_its_real_passes():
    result = _architecture_agent(_arch_handler).run(
        {"document": "The platform contains a Payment Orchestrator.",
         "document_type": "architecture"}
    )

    assert result.success
    meta = result.metadata
    assert meta["model_id"] == "arch-test-model"
    # The defect: this key did not exist, so no consumer could read a real pass.
    assert meta["model_calls"] == len(meta["passes"])
    assert {p["pass_name"] for p in meta["passes"]} == {
        "structure", "connections", "technology", "traceability"
    }
    assert all(p["chunk_label"] for p in meta["passes"])
    assert any(p["triples_produced"] >= 1 for p in meta["passes"])


def test_the_architecture_run_no_longer_ingests_as_unspecified():
    """The reader that matters: the real pass names survive into the stored run."""
    result = _architecture_agent(_arch_handler).run(
        {"document": "The platform contains a Payment Orchestrator.",
         "document_type": "architecture"}
    )
    records = _passes_from_metadata(result.metadata)

    assert records
    assert all(r.pass_name != "(unspecified)" for r in records)
    assert {r.pass_name for r in records} == {
        "structure", "connections", "technology", "traceability"
    }
    assert sum(r.triples_produced for r in records) == 1
