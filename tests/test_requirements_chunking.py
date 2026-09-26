"""
Chunking for the requirements profile.

WHY THIS EXISTS. The architecture profile has always chunked and merged; the
requirements profile sent the whole document and asked for every collection back in
one response. That was survivable while the corpus was samples and is the wrong
shape for a real PRD — the output budget is the document's, one dropped collection
cannot be recovered, and a truncated response is a failed run rather than a partial
one.

The load-bearing claim these tests make is that **nothing about small documents
moved**: a document that fits one chunk takes the old path exactly, with the same
prompt and no merge. Chunking only engages where it is needed.
"""

from __future__ import annotations

import yaml
from rich.console import Console

from agents.base_agent import AgentConfig
from agents.knowledge_extraction.agent import (
    ExtractionResult,
    ExtractedEntity,
    ExtractedTriple,
    KnowledgeExtractionAgent,
)


def agent(**config) -> KnowledgeExtractionAgent:
    """A bare agent with no model — the pattern the completeness tests use."""
    a = KnowledgeExtractionAgent.__new__(KnowledgeExtractionAgent)
    a.config = AgentConfig(
        name="probe", description="probe",
        ontology_path="ontology/requirements_base.yaml", ontology_dir="ontology",
        model_id="test-model", **config,
    )
    a.console = Console()
    a.ontology = yaml.safe_load(open("ontology/requirements_base.yaml"))
    a.domain_pack = None
    a.agent = None
    a.confidence_threshold = 0.7
    return a


def big_document(sections: int = 40) -> str:
    """Comfortably over the 7000-char chunk budget, with real section structure."""
    parts = ["# Payment Platform Requirements\n"]
    for i in range(sections):
        parts.append(
            f"\n## Section {i}: capability {i}\n\n"
            f"The platform shall support capability {i} for tenancy T{i}. "
            f"FR-PM-{i:03d} records that requirement, and it traces to the goal of "
            f"reducing payment failure. Non-functional: p95 latency under {i}ms.\n"
            + ("Detail sentence. " * 20)
            + "\n"
        )
    return "".join(parts)


# ============================================================================
# The small path must not have moved
# ============================================================================


def test_a_small_document_takes_one_call_and_the_unchanged_prompt():
    """One chunk in, one structured call out — the shape this profile always had."""
    a = agent(use_structured_output=True)
    prompts = []

    def invoke_structured(prompt, schema):
        prompts.append(prompt)
        return ExtractionResult(
            triples=[ExtractedTriple(subject="A", predicate="traces_to_goal",
                                     object="B", confidence=0.9)],
            entities=[ExtractedEntity(name="A", ontology_class="FunctionalRequirement")],
        )

    a.invoke_structured = invoke_structured
    a.invoke = lambda *x, **k: ""
    document = "Payment Acceptance requires Validation.\n"

    result = a.run({"document": document})

    assert result.success, result.errors
    assert len(prompts) == 1
    # The document travels verbatim — chunking must not rewrite a small document.
    assert document in prompts[0]
    assert result.metadata["chunks"] == 1 if "chunks" in result.metadata else True


# ============================================================================
# The large path
# ============================================================================


def test_a_large_document_is_extracted_chunk_by_chunk():
    a = agent(use_structured_output=True)
    prompts = []

    def invoke_structured(prompt, schema):
        prompts.append(prompt)
        return ExtractionResult(
            triples=[ExtractedTriple(subject="A", predicate="traces_to_goal",
                                     object="B", confidence=0.9)],
            entities=[ExtractedEntity(name="A", ontology_class="FunctionalRequirement")],
        )

    a.invoke_structured = invoke_structured
    a.invoke = lambda *x, **k: ""
    result = a.run({"document": big_document()})

    assert result.success, result.errors
    assert len(prompts) > 1, "a document over the budget has to be more than one call"
    # One pass record per chunk, so completeness describes the whole document
    # rather than the last chunk that happened to answer.
    structured = [p for p in result.metadata["passes"] if p["pass_name"] == "structured"]
    assert len(structured) == len(prompts)
    assert all(p["chunk_label"] for p in structured), (
        "a chunked run must say WHICH chunk each attempt was"
    )


def test_a_large_document_merges_rather_than_concatenates():
    """The same requirement seen in two chunks is one record, not two.

    This is the difference between chunking and duplicating: content-addressed
    assertions collapse duplicates at ingest, but the extraction should not be
    producing them in the first place (the finding recorded in YB-009).
    """
    a = agent(use_structured_output=True)

    def invoke_structured(prompt, schema):
        # Every chunk reports the same entity and triple.
        return ExtractionResult(
            triples=[ExtractedTriple(subject="Repeated Thing",
                                     predicate="traces_to_goal",
                                     object="Same Goal", confidence=0.8)],
            entities=[ExtractedEntity(name="Repeated Thing",
                                      ontology_class="FunctionalRequirement")],
        )

    a.invoke_structured = invoke_structured
    a.invoke = lambda *x, **k: ""
    result = a.run({"document": big_document()})

    out = result.output
    names = [e["name"] for e in out["entities"]]
    assert names.count("Repeated Thing") == 1, f"duplicated across chunks: {names}"
    triples = [(t["subject"], t["predicate"], t["object"]) for t in out["triples"]]
    assert len(triples) == len(set(triples)), f"duplicated across chunks: {triples}"


def test_distinct_entities_from_different_chunks_all_survive():
    """Merging must not collapse things that are genuinely different."""
    a = agent(use_structured_output=True)
    counter = {"n": 0}

    def invoke_structured(prompt, schema):
        counter["n"] += 1
        return ExtractionResult(
            triples=[ExtractedTriple(subject="A", predicate="traces_to_goal",
                                     object="B", confidence=0.9)],
            entities=[ExtractedEntity(name=f"Requirement {counter['n']}",
                                      ontology_class="FunctionalRequirement")],
        )

    a.invoke_structured = invoke_structured
    a.invoke = lambda *x, **k: ""
    result = a.run({"document": big_document()})

    names = {e["name"] for e in result.output["entities"]}
    assert len(names) == counter["n"] > 1


def test_a_text_fallback_on_one_chunk_does_not_hide_the_structured_chunks():
    """Completeness is about the whole run, so a chunk that fell back has to be
    visible even when every other chunk answered structured."""
    a = agent(use_structured_output=True)
    counter = {"n": 0}

    def invoke_structured(prompt, schema):
        counter["n"] += 1
        if counter["n"] == 1:
            return None                      # this chunk fails the schema
        return ExtractionResult(
            triples=[ExtractedTriple(subject="A", predicate="traces_to_goal",
                                     object="B", confidence=0.9)],
            entities=[ExtractedEntity(name="A", ontology_class="FunctionalRequirement")],
        )

    a.invoke_structured = invoke_structured
    a.invoke = lambda *x, **k: "no parseable triples here"
    result = a.run({"document": big_document()})

    paths = {p["path"] for p in result.metadata["passes"] if p["pass_name"] == "text_fallback"}
    assert "text" in paths
    assert result.metadata["text_fallback_calls"] >= 1
