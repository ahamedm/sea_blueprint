"""
The Design Assistant agent.

NO MODEL. `conftest`'s rule — "a test suite that needs a model server is a test
suite nobody runs" — is followed here by replacing `invoke_structured`, which is
the single seam `run_passes` calls. Everything below that seam is exercised for
real: the digest is built, the passes run, the collections merge, the patterns are
resolved against the catalogue, the validators fire, and the output is ingested.

The end-to-end test at the bottom is the one that matters most: it proves a
proposal becomes graph facts with the right node kinds, the right provenance, and
without a model ever being contacted.
"""

from __future__ import annotations

import pytest

from agents.architecture_extraction.passes import (
    ConnectionPassResult,
    ConnectionRecord,
    DesignTechniqueRecord,
    ElementRecord,
    ReferenceRecord,
    StructurePassResult,
    TraceabilityPassResult,
)
from agents.base_agent import AgentConfig
from agents.design_assistant import DesignAssistantAgent
from agents.design_assistant.passes import (
    PatternPassResult,
    ScenarioPassResult,
    TechniquePassResult,
)
from core.knowledge import SOURCE_DESIGN_ASSISTANT, graph_from_extraction
from core.patterns import load_pattern_catalogue

CATALOGUE = "ontology/catalogues/architecture_patterns.yaml"


def make_agent(handler) -> DesignAssistantAgent:
    agent = DesignAssistantAgent(AgentConfig(
        name="Design Assistant Agent",
        description="test",
        model_provider="openai_compatible",
        model_id="stub-model",
        base_url="http://127.0.0.1:9/v1",
        api_key="stub",
        ontology_path="ontology/architecture_base.yaml",
        ontology_dir="ontology",
        pattern_catalogue=CATALOGUE,
    ))
    agent.invoke_structured = handler          # the seam, replaced
    # The text fallback is a second seam. `run_passes` reaches for it whenever the
    # structured call returns None, so a test that does not stub it makes a real
    # network call. An empty string makes the fallback yield nothing, which is the
    # honest "this attempt produced nothing" outcome.
    agent.invoke = lambda prompt: ""
    return agent


def requirement_graph():
    graph, _run = graph_from_extraction(
        {
            "initiatives": [{"id": "INIT-1", "name": "Payments"}],
            "entities": [
                {
                    "name": "Payment Request Validation",
                    "ontology_class": "FunctionalRequirement",
                    "requirement_id": "FR-PM-001",
                },
                {
                    "name": "Payment Authorization Latency",
                    "ontology_class": "NonFunctionalRequirement",
                    "requirement_id": "NFR-PS-001",
                    "quality_attribute": "Time Behaviour",
                },
            ],
        },
        {"model_id": "fake", "document_type": "requirements"},
        document_ref="req.md",
        document_text="req body",
    )
    return graph


def good_handler(prompts: list | None = None):
    """A model that answers every pass sensibly."""

    def handler(prompt, schema):
        if prompts is not None:
            prompts.append((schema, prompt))
        if schema is StructurePassResult:
            return StructurePassResult(
                elements=[
                    ElementRecord(name="Payment Gateway Platform",
                                  element_type="SoftwareSystem",
                                  responsibilities=["Acceptance of payment requests"]),
                    ElementRecord(name="Payment Orchestrator", element_type="Container",
                                  parent="Payment Gateway Platform",
                                  satisfies_attributes=["Time Behaviour"]),
                    ElementRecord(name="Scheme Switch", element_type="ExternalSystem"),
                ],
                triples=[{"subject": "Payment Orchestrator", "predicate": "part_of",
                          "object": "Payment Gateway Platform", "confidence": 0.9,
                          "ontology_class": "Container"}],
            )
        if schema is ConnectionPassResult:
            return ConnectionPassResult(connections=[
                ConnectionRecord(source="Payment Orchestrator", target="Scheme Switch",
                                 protocol="REST", style="SYNCHRONOUS_REQUEST_RESPONSE"),
            ])
        if schema is TechniquePassResult:
            return TechniquePassResult(design_techniques=[
                DesignTechniqueRecord(
                    name="Connection Pooling",
                    technique_category="PERFORMANCE",
                    applies_to=["Payment Orchestrator"],
                    realizes_quality_attributes=["NFR-PS-001"],
                    quality_category="PERFORMANCE_EFFICIENCY",
                    subcharacteristic="TIME_BEHAVIOUR",
                    satisfies_attributes=["Time Behaviour"],
                    mechanism="Reuses upstream connections so a cold connect is not on "
                              "the request path.",
                ),
            ])
        if schema is PatternPassResult:
            return PatternPassResult(architecture_patterns=[
                {"name": "Circuit Breaker", "category": "RESILIENCE",
                 "rationale": "A failing scheme switch must not cascade.",
                 "mechanism": "Fail fast past an error threshold.",
                 "trade_offs": ["Thresholds need tuning"],
                 "applies_to": ["Payment Orchestrator"],
                 "realizes_quality_attributes": ["NFR-PS-001"]},
            ])
        if schema is ScenarioPassResult:
            return ScenarioPassResult(quality_scenarios=[
                {"name": "Scheme switch timeout",
                 "attribute": "Time Behaviour",
                 "stimulus_source": "cardholder",
                 "stimulus": "submits an authorization while the switch is slow",
                 "environment": "peak load",
                 "artifact": "Payment Orchestrator",
                 "response": "the authorization is decided",
                 "response_measure": "p95 < 500ms"},
            ])
        if schema is TraceabilityPassResult:
            return TraceabilityPassResult(references=[
                ReferenceRecord(element="Payment Orchestrator",
                                relationship="implements_requirement",
                                reference="FR-PM-001"),
                ReferenceRecord(element="Payment Gateway Platform",
                                relationship="delivers_initiative",
                                reference="INIT-1"),
                ReferenceRecord(element="Scheme Switch",
                                relationship="supports_capability",
                                reference="Payment Processing"),
            ])
        return None

    return handler


# ============================================================================
# The run
# ============================================================================


def test_a_run_proposes_every_collection_the_plan_promised():
    agent = make_agent(good_handler())
    result = agent.run({"graph": requirement_graph(), "initiative_id": "INIT-1"})

    assert result.success, result.errors
    output = result.output
    assert output["elements"], "no elements"
    assert output["connections"]
    assert output["design_techniques"]
    assert output["architecture_patterns"]
    assert output["quality_scenarios"]
    assert output["references"]
    assert output["statistics"]["total_elements"] == 3


def test_the_digest_is_the_prompt_and_it_is_one_chunk():
    """A design is a global act; one chunk is a design decision, not an omission."""
    prompts = []
    agent = make_agent(good_handler(prompts))
    result = agent.run({"graph": requirement_graph(), "initiative_id": "INIT-1"})

    assert result.success
    assert result.output["statistics"]["chunks"] == 1
    # Six passes, six calls, each carrying the same digest.
    assert len(prompts) == 6
    for _schema, prompt in prompts:
        assert "FR-PM-001" in prompt, "the requirement identifiers must travel"
        assert "REQ-G" in prompt
    # The catalogue appears in the patterns prompt ONLY — that is what keeps the
    # other five from paying for it (YB-007).
    pattern_prompts = [p for s, p in prompts if s is PatternPassResult]
    assert "Circuit Breaker" in pattern_prompts[0]
    others = [p for s, p in prompts if s is not PatternPassResult]
    assert all("Circuit Breaker" not in p for p in others)


def test_the_run_provenance_says_architecture_but_the_proposal_is_marked():
    agent = make_agent(good_handler())
    result = agent.run({"graph": requirement_graph()})
    assert result.metadata["document_type"] == "architecture"
    assert result.metadata["design_base"]
    assert result.metadata["pattern_catalogue"] == CATALOGUE


def test_pass_records_are_real_and_completeness_is_not_a_guess():
    """ADR-0013's leftover: the architecture profile reconstructs `(unspecified)`."""
    agent = make_agent(good_handler())
    result = agent.run({"graph": requirement_graph()})
    passes = result.metadata["passes"]
    assert [p["pass_name"] for p in passes] == [
        "structure", "connections", "techniques", "patterns", "scenarios", "traceability",
    ]
    assert all(p["outcome"] == "ok" for p in passes)
    assert result.metadata["failed_calls"] == 0


def test_only_the_generative_pass_runs_warmer_than_the_profile_default():
    """The split, as a test.

    Five of the six passes produce NAMES that merge into the graph and get diffed
    between runs, so their variance is a correctness cost and they inherit the
    profile's 0.3. The patterns pass is the one act here that is a choice among
    alternatives, and its names are pinned by the catalogue — so it is the one
    place a warmer sample is cheap.
    """
    from agents.design_assistant.passes import (
        PATTERN_PASS_TEMPERATURE,
        design_passes,
    )

    specs = {spec.name: spec for spec in design_passes("")}
    assert specs["patterns"].temperature == PATTERN_PASS_TEMPERATURE == 0.6
    for name in ("structure", "connections", "techniques", "scenarios", "traceability"):
        assert specs[name].temperature is None, (
            f"{name} must inherit the profile default — it emits merge names"
        )


def test_the_run_record_says_which_pass_was_warm():
    """A proposal whose quality is questioned should be traceable to its sampler,
    and the temperature is the one sampling knob that varies WITHIN a run."""
    agent = make_agent(good_handler())
    result = agent.run({"graph": requirement_graph()})

    temperatures = {p["pass_name"]: p.get("temperature") for p in result.metadata["passes"]}
    assert temperatures["patterns"] == 0.6
    assert temperatures["structure"] is None
    assert temperatures["traceability"] is None


def test_the_patterns_pass_actually_runs_at_its_temperature():
    """End to end: the override is IN FORCE during the patterns call and gone for
    the passes around it — not merely declared on the spec.

    Reads the live model config from inside the call, which is the only vantage
    point where "is the sampler actually set" and "was it put back" are both
    observable. A spec field that `run_passes` never applied would pass every
    other test in this file.
    """
    observed = {}
    agent = make_agent(lambda prompt, schema: None)     # replaced below

    def handler(prompt, schema):
        params = agent.agent.model.config.get("params") or {}
        observed[schema] = params.get("temperature")
        return good_handler()(prompt, schema)

    agent.invoke_structured = handler
    result = agent.run({"graph": requirement_graph()})

    assert result.success, result.errors
    assert observed[PatternPassResult] == 0.6
    assert observed[StructurePassResult] is None
    assert observed[TraceabilityPassResult] is None
    # ...and the model was handed back to the profile default afterwards.
    assert (agent.agent.model.config.get("params") or {}).get("temperature") is None


def test_a_failed_pass_does_not_discard_the_others():
    """One pass coming back empty must not cost the run its other five."""
    def handler(prompt, schema):
        if schema is ConnectionPassResult:
            return None                      # this pass produces nothing
        return good_handler()(prompt, schema)

    agent = make_agent(handler)
    result = agent.run({"graph": requirement_graph()})

    assert result.success
    assert result.output["elements"], "the structure pass still landed"
    assert result.output["architecture_patterns"], "the patterns pass still landed"
    assert result.output["connections"] == []
    states = {p["pass_name"]: p["outcome"] for p in result.metadata["passes"]}
    assert states["connections"] == "empty"
    assert states["structure"] == "ok"
    assert result.metadata["empty_calls"] == 1


def test_a_failing_pass_records_the_error_and_the_run_continues():
    def handler(prompt, schema):
        if schema is ScenarioPassResult:
            raise RuntimeError("model exploded")
        return good_handler()(prompt, schema)

    agent = make_agent(handler)
    result = agent.run({"graph": requirement_graph()})
    assert result.success
    states = {p["pass_name"]: p for p in result.metadata["passes"]}
    assert states["scenarios"]["outcome"] == "failed"
    assert "model exploded" in states["scenarios"]["error"]


def test_no_graph_fails_loudly():
    agent = make_agent(good_handler())
    result = agent.run({})
    assert result.success is False
    assert any("knowledge graph" in e for e in result.errors)


# ============================================================================
# Findings — the silent failures this profile has to surface
# ============================================================================


def test_an_unresolved_pattern_is_reported_not_renamed():
    def handler(prompt, schema):
        if schema is PatternPassResult:
            return PatternPassResult(architecture_patterns=[
                {"name": "Quantum Flux Balancer", "category": "RESILIENCE",
                 "mechanism": "balances flux", "trade_offs": ["none known"]},
            ])
        return good_handler()(prompt, schema)

    agent = make_agent(handler)
    result = agent.run({"graph": requirement_graph()})
    kinds = {f["kind"] for f in result.output["findings"]}
    assert "pattern" in kinds
    resolution = result.output["pattern_resolutions"][0]
    assert resolution["resolved"] is False
    assert resolution["name"] == "Quantum Flux Balancer"
    assert result.output["statistics"]["patterns_resolved"] == 0


def test_a_known_pattern_resolves_and_carries_its_catalogue_detail():
    agent = make_agent(good_handler())
    result = agent.run({"graph": requirement_graph()})
    resolution = result.output["pattern_resolutions"][0]
    assert resolution["resolved"] is True
    assert resolution["matched"] == "Circuit Breaker"
    assert resolution["category"] == "RESILIENCE"
    assert "fail fast" in resolution["mechanism"]


def test_a_scenario_without_a_number_is_flagged():
    def handler(prompt, schema):
        if schema is ScenarioPassResult:
            return ScenarioPassResult(quality_scenarios=[
                {"name": "Vague", "attribute": "Time Behaviour",
                 "stimulus_source": "user", "stimulus": "acts",
                 "environment": "normal", "response": "it responds",
                 "response_measure": "fast enough"},
            ])
        return good_handler()(prompt, schema)

    agent = make_agent(handler)
    result = agent.run({"graph": requirement_graph()})
    finding = next(f for f in result.output["findings"] if f["kind"] == "scenario")
    assert "no number" in finding["reasons"][0]


def test_a_scenario_missing_an_atam_field_is_flagged():
    """Checked at the validator, because the schema already refuses this shape.

    `QualityScenarioRecord` marks the ATAM fields required, so on the structured
    path Pydantic rejects an incomplete scenario before any validator sees it. The
    check still matters for the TEXT path, where records are assembled from parsed
    output and nothing enforces the shape.
    """
    from agents.design_assistant import check_scenario_shape

    flags = check_scenario_shape([
        {"name": "Incomplete", "attribute": "Time Behaviour",
         "stimulus": "acts", "response_measure": "p95 < 500ms"},
    ])
    assert flags
    assert "stimulus_source" in flags[0].reasons[0]


def test_an_element_answering_nothing_is_flagged_as_ungrounded():
    def handler(prompt, schema):
        if schema is TraceabilityPassResult:
            return TraceabilityPassResult(references=[])
        return good_handler()(prompt, schema)

    agent = make_agent(handler)
    result = agent.run({"graph": requirement_graph()})
    ungrounded = {f["subject"] for f in result.output["findings"] if f["kind"] == "ungrounded"}
    assert "Payment Orchestrator" in ungrounded


def test_a_technique_named_after_a_requirement_is_flagged():
    """Found on the live model: the techniques pass echoed requirement names.

    A technique that restates the requirement adds a node and no information, and
    the quality census would then report the attribute as having a realizing
    technique on the strength of a renamed requirement.
    """
    def handler(prompt, schema):
        if schema is TechniquePassResult:
            return TechniquePassResult(design_techniques=[
                DesignTechniqueRecord(name="Payment Request Validation",
                                      mechanism="Validates requests."),
                DesignTechniqueRecord(name="Connection Pooling",
                                      mechanism="Reuses connections."),
            ])
        return good_handler()(prompt, schema)

    agent = make_agent(handler)
    result = agent.run({"graph": requirement_graph()})
    mechanisms = [f for f in result.output["findings"] if f["kind"] == "mechanism"]
    assert [f["subject"] for f in mechanisms] == ["Payment Request Validation"]
    assert "MECHANISM" in mechanisms[0]["reasons"][0]


def test_a_name_that_belongs_to_another_kind_is_flagged():
    """The `_resolve`-by-label hazard: a Container named after a requirement merges
    into the requirement node instead of creating one, silently."""
    def handler(prompt, schema):
        if schema is StructurePassResult:
            return StructurePassResult(elements=[
                ElementRecord(name="Payment Request Validation", element_type="Container"),
            ])
        return good_handler()(prompt, schema)

    agent = make_agent(handler)
    result = agent.run({"graph": requirement_graph()})
    collision = next(f for f in result.output["findings"] if f["kind"] == "collision")
    assert "FunctionalRequirement" in collision["reasons"][0]


# ============================================================================
# Schema robustness — observed on a live run, fixed in the shared record
# ============================================================================


def test_a_null_parent_is_read_as_no_parent():
    """The model emits `null` for a top-level element; the schema must read it.

    Pydantic rejects `None` for a `str` field at the schema level, so the
    structured call failed and the model was re-prompted — three turns to say what
    the empty string already says. `ElementRecord` is the architecture profile's
    record, reused here, so the fix benefits both.
    """
    element = ElementRecord(name="Payment Gateway Platform",
                            element_type="SoftwareSystem", parent=None,
                            description=None)
    assert element.parent == ""
    assert element.description == ""


# ============================================================================
# YB-038 — the techniques pass answers a closed list, not an open question
# ============================================================================


def test_the_techniques_pass_is_told_which_attributes_to_answer():
    """The closed list IS the fix.

    Asked for an open-ended list of "mechanisms", the live model returned the
    requirements' own names eight times out of eight with every quality field empty.
    Binding the attributes REQ-G states into the prompt turns that into one bounded
    task — one mechanism per attribute — so a missing attribute shows up as a
    missing list item instead of as silence.
    """
    prompts = []
    agent = make_agent(good_handler(prompts))
    agent.run({"graph": requirement_graph()})

    technique_prompts = [p for schema, p in prompts if schema is TechniquePassResult]
    assert len(technique_prompts) == 1
    prompt = technique_prompts[0]

    assert "Quality attributes stated by REQ-G" in prompt
    # The attribute the fixture's NFR states, listed as a thing to answer.
    assert "Time Behaviour" in prompt
    # And the failure mode named explicitly, since it is the one the model chose.
    assert "may never be a requirement's name" in prompt
    assert "realizes_quality_attributes` MUST contain" in prompt


def test_the_stated_attributes_are_read_through_the_census_not_off_the_nodes():
    """One concern, one list item.

    `quality_report` is what already decides that `Availability` and
    `High Availability` are one concern. Reading NFR labels directly would list the
    same attribute twice under two spellings and the pass would answer it twice.
    """
    from agents.design_assistant.agent import DesignAssistantAgent

    graph, _ = graph_from_extraction(
        {"entities": [
            {"name": "Availability NFR", "ontology_class": "NonFunctionalRequirement",
             "quality_attribute": "Availability"},
            {"name": "Uptime NFR", "ontology_class": "NonFunctionalRequirement",
             "quality_attribute": "High Availability"},
        ]},
        {"document_type": "requirements", "model_id": "t"},
        document_ref="req.md",
    )
    assert DesignAssistantAgent._stated_quality_attributes(graph) == ["Availability"]


def test_a_technique_linked_to_no_quality_concern_is_flagged():
    """The other half of the live failure: every quality field left empty."""
    from agents.design_assistant import check_techniques_are_linked

    flags = check_techniques_are_linked([
        {"name": "Connection Pooling", "realizes_quality_attributes": ["Time Behaviour"]},
        {"name": "Caching", "subcharacteristic": "TIME_BEHAVIOUR"},
        {"name": "Read Replicas", "quality_category": "PERFORMANCE_EFFICIENCY"},
        {"name": "Something Vague"},
    ])
    assert [f.subject for f in flags] == ["Something Vague"]
    assert flags[0].kind == "unlinked"


def test_an_unlinked_technique_reaches_the_run_findings():
    """Visible on the page, not only in a validator nobody calls."""
    def handler(prompt, schema):
        if schema is TechniquePassResult:
            return TechniquePassResult(design_techniques=[
                DesignTechniqueRecord(name="Connection Pooling",
                                      realizes_quality_attributes=["Time Behaviour"]),
                DesignTechniqueRecord(name="Read Replicas"),
            ])
        return good_handler()(prompt, schema)

    agent = make_agent(handler)
    result = agent.run({"graph": requirement_graph()})

    unlinked = [f for f in result.output["findings"] if f["kind"] == "unlinked"]
    assert [f["subject"] for f in unlinked] == ["Read Replicas"]


# ============================================================================
# End to end: a proposal becomes graph facts, without a model
# ============================================================================


def test_a_proposal_ingests_with_the_right_kinds_and_provenance():
    agent = make_agent(good_handler())
    result = agent.run({"graph": requirement_graph(), "initiative_id": "INIT-1"})
    assert result.success, result.errors

    graph, run = graph_from_extraction(
        result.output,
        result.metadata,
        document_ref="design:INIT-1@working",
        document_text="digest",
        initiative_id="INIT-1",
        source_type=SOURCE_DESIGN_ASSISTANT,
    )

    kinds = {n.kind for n in graph.nodes.values()}
    assert {"ArchitecturePattern", "QualityScenario", "DesignTechnique"} <= kinds
    assert run.completeness == "COMPLETE", [p.outcome for p in run.passes]
    assert all(a.provenance.source_type == SOURCE_DESIGN_ASSISTANT for a in graph.active())
    assert not any(a.is_human for a in graph.active())

    # And the quality census now sees a scenario, which nothing produced before.
    from core.knowledge import quality_report

    assert quality_report(graph)["summary"]["states"]["has_quality_scenario"] >= 1


def test_a_text_fallback_run_reports_partial_not_complete():
    """The fallback cannot parse pattern/scenario records, so the run must say so."""
    def handler(prompt, schema):
        return None                          # structured path yields nothing

    agent = make_agent(handler)
    agent.invoke = lambda prompt: "some free-form answer with no parseable records"
    result = agent.run({"graph": requirement_graph()})
    graph, run = graph_from_extraction(
        result.output, result.metadata,
        document_ref="design:INIT-1@working", document_text="digest",
    )
    assert run.completeness in ("PARTIAL", "UNKNOWN", "FAILED")
    assert run.completeness != "COMPLETE"


def test_a_model_that_loops_is_reported_as_a_loop_not_as_empty():
    """The live failure: the model restated its plan instead of answering.

    Recorded as `empty` this reads as "the model had nothing to say", which points
    the next reader at the prompt. The truth was a degenerate generation, and the
    cause was downstream — sampling parameters that never reached the server. The
    outcome name is what makes that debuggable, so it is asserted here rather than
    left to the log.
    """
    from tests.test_generation_bounds import LOOPING_ANSWER

    def handler(prompt, schema):
        return None                          # structured path yields nothing

    agent = make_agent(handler)
    agent.invoke = lambda prompt: LOOPING_ANSWER
    result = agent.run({"graph": requirement_graph()})

    states = {p["pass_name"]: p for p in result.metadata["passes"]}
    looped = [p for p in states.values() if p["outcome"] == "failed"]
    assert looped, [p["outcome"] for p in states.values()]
    assert "repeated itself" in looped[0]["error"]
    assert result.metadata["empty_calls"] == 0
