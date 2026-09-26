"""
Prompt budget for the Design Assistant.

YB-007 measured the architecture prompt at 15,903 characters — 11,361 of scaffolding
against a 4,542-character document, a 2.5:1 ratio — and showed the consequence:
adding one section silently broke a rule that had been working, because the
instruction was crowded out rather than wrong.

The Design Assistant is the first profile written since, so it is the first one that
can be held to a number. Two properties keep it under control, and both are asserted
here rather than asserted in prose:

1. **The catalogue never enters the prompt in bulk.** Names and categories only;
   mechanisms and trade-offs are resolved deterministically (see `core.patterns`).
2. **The digest is the document, and it degrades rather than ballooning.** When it
   is over budget the renderer drops content in a documented order and says so.

The ratio target is <= 1.0: scaffolding no larger than the document it instructs.
"""

from __future__ import annotations

from agents.design_assistant.passes import design_passes
from agents.base_agent import AgentConfig
from agents.design_assistant import DesignAssistantAgent
from core.knowledge import graph_from_extraction
from core.knowledge.digest import design_input
from core.patterns import load_pattern_catalogue, pattern_prompt_context


def big_requirement_graph(count: int = 60):
    """A requirement set large enough to make the digest the dominant term."""
    entities = []
    for index in range(count):
        if index % 3 == 0:
            entities.append({
                "name": f"Non Functional Requirement {index}",
                "ontology_class": "NonFunctionalRequirement",
                "requirement_id": f"NFR-PM-{index:03d}",
                "quality_attribute": "Time Behaviour",
            })
        else:
            entities.append({
                "name": f"Functional Requirement {index}",
                "ontology_class": "FunctionalRequirement",
                "requirement_id": f"FR-PM-{index:03d}",
            })
    graph, _run = graph_from_extraction(
        {"entities": entities},
        {"model_id": "fake", "document_type": "requirements"},
        document_ref="req.md",
        document_text="req body",
    )
    return graph


def agent_for_budget() -> DesignAssistantAgent:
    return DesignAssistantAgent(AgentConfig(
        name="Design Assistant Agent",
        description="test",
        model_provider="openai_compatible",
        model_id="stub",
        base_url="http://127.0.0.1:9/v1",
        api_key="stub",
        ontology_path="ontology/architecture_base.yaml",
        ontology_dir="ontology",
        pattern_catalogue="ontology/catalogues/architecture_patterns.yaml",
    ))


def test_the_catalogue_contributes_names_not_substance():
    catalogue = load_pattern_catalogue()
    context = pattern_prompt_context(catalogue)
    assert len(context) < 1500, len(context)
    # Mechanisms live in `core.patterns`, not in the prompt.
    assert "fail fast once an error threshold" not in context


def test_scaffolding_does_not_exceed_the_document():
    """The YB-007 ratio, measured for this profile rather than asserted about it."""
    from agents.extraction.passes import build_pass_prompt

    graph = big_requirement_graph()
    digest = design_input(graph, initiative_id="INIT-1")
    agent = agent_for_budget()
    shared = agent._format_ontology_context()
    catalogue_context = pattern_prompt_context(load_pattern_catalogue())

    document = len(digest.text)
    for spec in design_passes(catalogue_context):
        prompt = build_pass_prompt(spec, type(
            "C", (), {"text": digest.text, "header_note": lambda self: "",
                      "label": "chunk 1/1"}
        )(), shared)
        scaffolding = len(prompt) - document
        assert scaffolding <= document, (
            f"{spec.name}: scaffolding {scaffolding} exceeds the document {document} "
            f"({scaffolding / document:.2f}:1) — YB-007's failure mode"
        )


def test_the_digest_degrades_rather_than_growing_without_bound():
    """A large requirement set must not become an unbounded prompt."""
    graph = big_requirement_graph(400)
    digest = design_input(graph, initiative_id="INIT-1")
    assert digest.caveats, "a cut of this size must be reported"
    assert digest.counts["requirements"] == 400
    # The digest is bounded by the two section budgets plus the headers.
    assert len(digest.text) < 25000, len(digest.text)
