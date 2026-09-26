"""
Design Assistant passes.

Six focused passes over ONE chunk — the REQ-G + baseline digest — because a design
is a global act: splitting it across chunks would let one call choose a monolith
and the next a microservice. The budget (see `core.knowledge.digest`) is what keeps
one chunk viable, and its cuts are reported rather than taken silently.

Four of the schemas are the architecture profile's, reused verbatim. That is the
point of the framing in YB-035: the Design Assistant proposes the same KIND of
thing an architecture document describes, so it should not invent a second
vocabulary for Containers, Components or DesignTechniques. Only the two collections
the architecture profile has no record for — architecture patterns and quality
scenarios — are new here.

EVERY PASS IS TOLD WHAT IT IS DOING. It is PROPOSING a design against requirements
it did not write, not extracting one that exists. The instructions say so, because
an "extract what the document says" framing on a document of requirements produces
a design that merely restates them.
"""

from typing import List, Literal

from pydantic import BaseModel, Field

from ..architecture_extraction.passes import (
    ConnectionPassResult,
    DesignTechniqueRecord,
    StructurePassResult,
    TraceabilityPassResult,
)
from ..extraction.passes import PassSpec
from ..knowledge_extraction.agent import ExtractedTriple

# The pattern families, matching `PatternCategory` in the ontology. Duplicated as a
# `Literal` for the same reason the architecture profile duplicates it: a Literal
# ships a strict enum in the JSON schema, which is the strongest constraint
# available on the structured path. `check_schema_consistency` asserts the two
# agree, so the duplication cannot drift unnoticed.
PatternCategoryLiteral = Literal[
    "", "STRUCTURAL", "INTEGRATION", "DATA", "RESILIENCE", "SECURITY", "DEPLOYMENT",
    "OBSERVABILITY", "AVAILABILITY", "SCALABILITY", "PERFORMANCE",
    "STANDARDS_CONFORMANCE",
]


# ============================================================================
# 1. STRUCTURE — the proposed elements
# ============================================================================
# Schema reused from the architecture profile. The instruction block is the new
# part, and it is where "propose" differs from "extract".

DESIGN_STRUCTURE_PASS = PassSpec(
    name="structure",
    schema=StructurePassResult,
    output_keys={"elements": "elements", "triples": "triples"},
    instructions="""# Task: propose the CORE architecture for these requirements

You are DESIGNING, not extracting. Input 1 is what the system must do; Input 2 is
the architecture that already exists. Propose the smallest set of elements that
would satisfy the requirements.

Rules:
1. Propose top-level **SoftwareSystem** elements only for the system under design.
   Everything a party outside it owns is an **ExternalSystem** (a payment scheme, a
   bank, an identity provider).
2. Propose **Container** elements for anything independently deployable — a service,
   an application, a job runner. A **DataStore** for each place state persists. A
   **Component** only for a logical block INSIDE one container.
3. **Reuse an existing element by its exact name** (Input 2) wherever it already
   carries a responsibility the requirements need. A new element with a new name for
   something that already exists is the most expensive mistake you can make here.
4. Set `parent` for every contained element and emit the matching
   `<contained> --part_of--> <parent>` triple. Both are required.
5. Capture `responsibilities` — what each element is accountable for — phrased as
   accountability, not behaviour.
6. Set `satisfies_attributes` with the standard's own terms (Availability, Time
   Behaviour, Scalability, Confidentiality). Never a mechanism: "Redundancy" is a
   technique that delivers Availability, not an attribute.
7. Name concepts; never emit a sentence as an element name. Emit nothing you cannot
   justify from Input 1 — an element answering no requirement is a finding, not a
   contribution. The traceability pass records which requirement each element
   answers; your job here is the elements themselves.""",
)


# ============================================================================
# 2. CONNECTIONS — how the proposed elements talk
# ============================================================================

DESIGN_CONNECTION_PASS = PassSpec(
    name="connections",
    schema=ConnectionPassResult,
    output_keys={"connections": "connections", "triples": "triples"},
    instructions="""# Task: propose the runtime connections

For every call path between the elements you proposed (or reused from Input 2),
record source, target, protocol and integration style.

Rules:
1. A connection is a RUNTIME call. "Runs on" and "is part of" are not connections.
2. Prefer an explicit style for every link: `SYNCHRONOUS_REQUEST_RESPONSE` for a
   call that waits, `ASYNCHRONOUS_EVENT` or `PUBLISH_SUBSCRIBE` for one that does
   not. A design that makes every hop synchronous has chosen a latency and an
   availability profile whether it meant to or not.
3. Emit both the `connections` record and the matching
   `<source> --connects_to--> <target>` triple.
4. Use only the listed protocol and style values. Leave a field empty rather than
   inventing a term.""",
)


# ============================================================================
# 3. TECHNIQUES — the mechanisms that deliver the quality attributes
# ============================================================================


class TechniquePassResult(BaseModel):
    design_techniques: List[DesignTechniqueRecord] = Field(default_factory=list)
    triples: List[ExtractedTriple] = Field(
        default_factory=list,
        description="realizes_quality_attribute and applies_technique triples.",
    )


DESIGN_TECHNIQUE_PASS = PassSpec(
    name="techniques",
    schema=TechniquePassResult,
    output_keys={"design_techniques": "design_techniques", "triples": "triples"},
    instructions="""# Task: propose the mechanisms that deliver the quality attributes

For each quality attribute Input 1 states for this system, propose the concrete
mechanism the architecture uses to deliver it. A stated quality with no mechanism is
the gap this pass exists to close.

Rules:
1. Name the MECHANISM, not the attribute: "Stateless Services", "Redundancy /
   Replicas", "Active-Active Multi-DataCentre", "Connection Pooling", "Read
   Replicas", "Caching", "Asynchronous Offload", "Batching", "Transactional Outbox".
   "High Availability" is a requirement, not a technique — do not emit it as one.
2. `applies_to` names the elements the technique is applied to; usually several.
3. `realizes_quality_attributes` names the NFR (by identifier where Input 1 gives
   one) this technique is the mechanism for. Emit the link even when the NFR is only
   implied, with a lower confidence.
4. Set `quality_category` and `subcharacteristic` from the ISO 25010 model shown in
   the ontology context. Set `mechanism` — how it works, concretely enough to check.
5. Do not propose a technique for a quality attribute nobody asked for. Answer the
   attributes in Input 1.""")


# ============================================================================
# 4. PATTERNS — named solutions chosen from the catalogue
# ============================================================================


class ArchitecturePatternRecord(BaseModel):
    """A named solution adopted from the catalogue."""

    name: str = Field(..., description=(
        "The pattern's name. Use a catalogue name verbatim where one fits; a pattern "
        "the catalogue does not contain may still be proposed under its own name."
    ))
    category: PatternCategoryLiteral = Field(default="", description=(
        "The catalogue category. Copy it from the catalogue entry; empty if unsure."
    ))
    rationale: str = Field(default="", description=(
        "Why this pattern rather than another, tied to a requirement or quality "
        "attribute in Input 1."
    ))
    mechanism: str = Field(default="", description=(
        "HOW it delivers its quality attribute, in one clause, concretely enough to "
        "check against a quality scenario's response measure."
    ))
    trade_offs: List[str] = Field(default_factory=list, description=(
        "The costs accepted by adopting it. State them honestly — a pattern proposed "
        "with no cost is a pattern that has not been thought about."
    ))
    applies_to: List[str] = Field(default_factory=list, description="Elements it governs.")
    realizes_quality_attributes: List[str] = Field(default_factory=list, description=(
        "The NFRs this pattern exists to satisfy, by identifier or name."
    ))
    mandated_by: List[str] = Field(default_factory=list, description=(
        "A requirement that MANDATES this pattern, where Input 1 states one. Leave "
        "empty when the pattern is your proposal rather than a stated obligation."
    ))


class PatternPassResult(BaseModel):
    architecture_patterns: List[ArchitecturePatternRecord] = Field(default_factory=list)
    triples: List[ExtractedTriple] = Field(default_factory=list)


# THE ONE PASS THAT RUNS WARMER THAN THE PROFILE DEFAULT (0.3).
#
# Everything else in this profile transcribes: which containers the requirements
# imply, what talks to what, which mechanism delivers an attribute, which
# requirement justifies an element. Those produce NAMES that merge into the graph
# and are diffed between runs, so their variance is a correctness cost. The
# patterns pass is the one act here that is a genuine CHOICE among alternatives
# rather than a reading of the input, and its names are pinned by the catalogue —
# the model copies "Circuit Breaker" verbatim, so a warmer sample cannot corrupt
# the identifier the way it could a container name.
#
# This is a HYPOTHESIS, not a measurement. YB-020's outstanding acceptance is
# exactly this kind of re-measurement, and the counter-argument is real: on a
# small model a higher temperature buys incoherence as readily as it buys
# diversity. If the patterns pass starts failing the schema, this is the first
# number to put back.
PATTERN_PASS_TEMPERATURE = 0.6


def design_pattern_pass(catalogue_context: str) -> PassSpec:
    """The patterns pass, with the catalogue's names interpolated.

    Built per run rather than written as a module constant because the catalogue is
    configuration, and because only THIS pass needs it — putting the catalogue in
    the shared context would repeat it in all six prompts (YB-007).
    """
    return PassSpec(
        name="patterns",
        schema=PatternPassResult,
        temperature=PATTERN_PASS_TEMPERATURE,
        output_keys={"architecture_patterns": "architecture_patterns", "triples": "triples"},
        instructions="""# Task: choose the named patterns this design adopts

A pattern is a published SOLUTION adopted as a whole (Circuit Breaker, Saga, CQRS).
It is not a technique (a mechanism the design exhibits) and not a style (the coarse
shape).

Rules:
1. Adopt only patterns the requirements actually justify. Two well-chosen patterns
   beat eight decorative ones.
2. Use a catalogue name verbatim. A pattern the catalogue does not have may still be
   proposed under its own name — it is recorded as unresolved for review rather than
   renamed to the closest match.
3. Set `rationale` (why this one), `mechanism` (how it works), and the honest
   `trade_offs` you accept. `trade_offs` is not optional decoration: a pattern with
   no cost has not been assessed.
4. `applies_to` names the elements it governs. `realizes_quality_attributes` names
   the NFR it serves.
5. `mandated_by` names a requirement that MANDATES the pattern, where Input 1 says
   so. Leave it empty for a pattern you are proposing on your own judgement — the
   difference between "required" and "suggested" is what a reviewer checks.
""" + catalogue_context,
    )


# ============================================================================
# 5. SCENARIOS — making each quality attribute testable
# ============================================================================


class QualityScenarioRecord(BaseModel):
    """An ATAM quality scenario: stimulus -> environment -> response -> measure."""

    name: str = Field(..., description="Short name for the scenario.")
    attribute: str = Field(..., description=(
        "The quality attribute it operationalises, named as Input 1 names it "
        "(e.g. 'Time Behaviour', 'Availability'). Without this the scenario cannot "
        "be matched to the concern it measures."
    ))
    stimulus_source: str = Field(..., description="Who or what generates the stimulus.")
    stimulus: str = Field(..., description="The event or condition.")
    environment: str = Field(..., description="The operating conditions, e.g. peak load.")
    artifact: str = Field(default="", description="The part of the system affected.")
    response: str = Field(..., description="The required system response.")
    response_measure: str = Field(..., description=(
        "How the response is quantified — a number and a unit, e.g. 'p95 < 500ms'. "
        "A measure with no number makes the requirement unfalsifiable, which is the "
        "one thing a scenario exists to prevent."
    ))
    description: str = Field(default="")


class ScenarioPassResult(BaseModel):
    quality_scenarios: List[QualityScenarioRecord] = Field(default_factory=list)
    triples: List[ExtractedTriple] = Field(default_factory=list)


DESIGN_SCENARIO_PASS = PassSpec(
    name="scenarios",
    schema=ScenarioPassResult,
    output_keys={"quality_scenarios": "quality_scenarios", "triples": "triples"},
    instructions="""# Task: make each stated quality attribute testable

For every quality attribute Input 1 states, write ONE concrete scenario in the ATAM
form: source -> stimulus -> environment -> response -> measure.

Rules:
1. The `response_measure` must carry a NUMBER and a unit ("p95 latency < 500ms",
   "99.95% monthly availability"). A scenario without one does not make the
   requirement falsifiable, which is the only reason to write it.
2. Take the target from Input 1 where it states one and do not weaken it. Where
   Input 1 states no target, propose one and mark the scenario's description as a
   PROPOSAL so a reviewer knows the number is yours, not theirs.
3. `attribute` names the quality attribute exactly as Input 1 names it, so the
   scenario attaches to the concern it measures rather than to a near match.
4. Skip an attribute you cannot write a measurable scenario for, rather than writing
   a vague one. An untestable scenario is worse than an absent one: it looks like
   coverage.""")


# ============================================================================
# 6. TRACEABILITY — what each proposed element answers
# ============================================================================

DESIGN_TRACEABILITY_PASS = PassSpec(
    name="traceability",
    schema=TraceabilityPassResult,
    output_keys={"references": "references", "triples": "triples"},
    instructions="""# Task: link the proposed architecture back to the requirements

For every element you proposed, record what in Input 1 justifies it.

Rules:
1. Capture the reference EXACTLY as Input 1 gives it — the identifier
   (`FR-PM-001`) where one exists, otherwise the requirement's name verbatim. Never
   paraphrase an identifier.
2. An element that answers no requirement may still be right (infrastructure,
   observability, a gateway). Record it against the goal or capability it serves, or
   leave it unlinked and accept that it will be reported as ungrounded — that report
   is a finding for the reviewer, not a failure.
3. Use `implements_requirement` for a functional requirement,
   `satisfies_quality_attribute` for an NFR, `traces_to_goal` and
   `supports_capability` for business intent, `delivers_initiative` for the
   Initiative.
4. Emit a `references` record AND the matching triple for each link. Where the link
   is clearly implied rather than stated, still emit it with confidence 0.5-0.8: the
   auditor cannot report a gap that was never recorded.""")


def design_passes(catalogue_context: str = "") -> List[PassSpec]:
    """The design pass list, with the pattern catalogue bound into the one pass that needs it."""
    return [
        DESIGN_STRUCTURE_PASS,
        DESIGN_CONNECTION_PASS,
        DESIGN_TECHNIQUE_PASS,
        design_pattern_pass(catalogue_context),
        DESIGN_SCENARIO_PASS,
        DESIGN_TRACEABILITY_PASS,
    ]
