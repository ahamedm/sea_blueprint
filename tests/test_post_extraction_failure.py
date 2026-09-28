"""
A paid-for run is not discarded by a post-extraction error (YB-051 defect 1).

THE DEFECT. The passes all succeeded — 12/12, 182 triples, 19 connections, 146 s of
model calls — and then a representation mismatch between two of our own stages threw
`'dict' object has no attribute 'subject'`. The job failed with nothing stored.

Extraction is the expensive part and it had already happened. So the stages after it
(merge, cross-chunk repair, validation) run under a guard: what merged is returned,
the error becomes a finding, and a failed `(post-extraction)` pass record makes the
run PARTIAL rather than letting a hole report itself as COMPLETE.

These tests pin the three properties that make that useful rather than merely
non-fatal: the facts survive, the error is visible, and the verdict is honest.
"""

from __future__ import annotations

from agents.architecture_extraction import agent as agent_module
from agents.architecture_extraction.agent import ArchitectureExtractionAgent
from agents.architecture_extraction.passes import ElementRecord, StructurePassResult
from agents.base_agent import AgentConfig
from agents.knowledge_extraction.agent import ExtractedTriple
from core.knowledge import graph_from_extraction
from core.knowledge.model import RUN_PARTIAL

DOCUMENT = "The platform contains a Payment Orchestrator owned by the Payments system."


def _agent():
    agent = ArchitectureExtractionAgent(AgentConfig(
        name="Architecture Extraction Agent", description="test",
        model_provider="openai_compatible", model_id="stub-model",
        base_url="http://127.0.0.1:9/v1", api_key="stub",
        ontology_path="ontology/architecture_base.yaml", ontology_dir="ontology",
    ))
    agent.use_domain_pack("")   # no pack: the passes get the base vocabulary

    def handler(prompt, schema):
        if schema is StructurePassResult:
            return StructurePassResult(
                elements=[
                    ElementRecord(name="Payments", element_type="SoftwareSystem"),
                    ElementRecord(name="Payment Orchestrator", element_type="Container",
                                  parent="Payments"),
                ],
                triples=[ExtractedTriple(subject="Payment Orchestrator",
                                         predicate="part_of", object="Payments",
                                         confidence=0.9)],
            )
        return schema()          # a valid empty answer for the other passes

    agent.invoke_structured = handler
    agent.invoke = lambda prompt: ""
    return agent


def _run(monkeypatch, failing_stage: str):
    def boom(*_args, **_kwargs):
        raise RuntimeError("a stage after the model calls broke")

    monkeypatch.setattr(agent_module, failing_stage, boom)
    return _agent().run({"document": DOCUMENT, "document_type": "architecture"})


def test_a_failing_repair_stage_keeps_the_facts_and_the_run(monkeypatch):
    result = _run(monkeypatch, "repair_containment")

    assert result.success, "the run is kept; only the stage failed"
    assert result.output is not None
    # 1. The facts merged before the failure survive.
    assert [t["predicate"] for t in result.output["triples"]] == ["part_of"]
    assert {e["name"] for e in result.output["elements"]} == {
        "Payments", "Payment Orchestrator",
    }


def test_the_post_extraction_error_is_a_finding_not_only_a_log_line(monkeypatch):
    result = _run(monkeypatch, "repair_containment")

    findings = result.output["findings"]
    post = [f for f in findings if f.get("kind") == "post_extraction_error"]
    assert len(post) == 1
    assert any("repair" in message for message in post[0]["messages"])
    assert result.output["statistics"]["post_extraction_errors"] == 1


def test_the_run_verdict_is_partial_not_complete(monkeypatch):
    result = _run(monkeypatch, "repair_containment")

    records = result.metadata["passes"]
    synthetic = [r for r in records if r["pass_name"] == "(post-extraction)"]
    assert len(synthetic) == 1
    assert synthetic[0]["outcome"] == "failed"
    assert "a stage after the model calls broke" in synthetic[0]["error"]

    _, run = graph_from_extraction(result.output, result.metadata,
                                   document_ref="arch.md", document_text=DOCUMENT)
    assert run.completeness == RUN_PARTIAL, (
        "a run that lost its repair stage must not report itself COMPLETE"
    )


def test_a_clean_run_carries_no_post_extraction_record(monkeypatch):
    """The guard is not always-on noise: with no failure there is nothing to report."""
    result = _agent().run({"document": DOCUMENT, "document_type": "architecture"})

    assert result.success
    assert [f for f in result.output["findings"]
            if f.get("kind") == "post_extraction_error"] == []
    assert [r for r in result.metadata["passes"]
            if r["pass_name"] == "(post-extraction)"] == []
