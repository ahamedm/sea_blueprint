"""
Architecture extraction passes.

Four focused passes replace one mega-prompt. Each has a single purpose, a small
schema, and a short instruction block — because the previous design's failure was
precisely that a large schema made the model silently drop whole categories.

Pass order matters only for readability of logs; passes are independent and run
against each chunk separately.
"""

from typing import Any, List, Literal, Optional
import re

from pydantic import AliasChoices, BaseModel, Field, field_validator

from ..knowledge_extraction.agent import ExtractedTriple
from ..extraction.passes import PassSpec


# ============================================================================
# Enum coercion
# ============================================================================
# Literal types are the strongest constraint available (Tier 1): on the
# STRUCTURED path the SDK rejects a bad value and the model retries. On the TEXT
# path there is no retry — a single wrong value fails the whole record and the
# entire pass is lost. Observed: c4_level emitted as "1" / "L2" killed a
# structure pass outright, taking every element with it.
#
# So coercion, not rejection: match case-insensitively, accept common aliases,
# and fall back to empty (meaning "not stated") rather than raising. That is the
# same principle as the confidence validator — normalise formatting variance,
# judge the content.

_C4_ALIASES = {
    "1": "CONTEXT", "L1": "CONTEXT", "LEVEL_1": "CONTEXT", "LEVEL1": "CONTEXT",
    "SYSTEM_CONTEXT": "CONTEXT", "SYSTEMCONTEXT": "CONTEXT",
    "2": "CONTAINER", "L2": "CONTAINER", "LEVEL_2": "CONTAINER", "LEVEL2": "CONTAINER",
    "3": "COMPONENT", "L3": "COMPONENT", "LEVEL_3": "COMPONENT", "LEVEL3": "COMPONENT",
    "4": "CODE", "L4": "CODE", "LEVEL_4": "CODE", "LEVEL4": "CODE",
}


def _match_enum(value: Any, allowed, default: str = "") -> str:
    """Case-insensitive match against a vocabulary, else the default."""
    if value is None:
        return default
    text = str(value).strip()
    if not text:
        return default
    for candidate in allowed:
        if candidate.lower() == text.lower():
            return candidate
    # Normalise separators and try again ("synchronous request response").
    squeezed = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")
    for candidate in allowed:
        if candidate.lower() == squeezed.lower():
            return candidate
    return default


def _norm_c4(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    upper = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").upper()
    if upper in _C4_ALIASES:
        return _C4_ALIASES[upper]
    return _match_enum(text, ("CONTEXT", "CONTAINER", "COMPONENT", "CODE"), "")


# ============================================================================
# 1. STRUCTURE — elements, containment, classification
# ============================================================================

class ElementRecord(BaseModel):
    """A C4 element, its place in the hierarchy, and how it is classified."""

    name: str = Field(..., description="Name of the element: a short noun phrase, not a sentence.")
    element_type: Literal[
        "SoftwareSystem", "ExternalSystem", "Person", "Container",
        "DataStore", "Component", "CodeElement", "DeploymentNode",
    ] = Field(..., description=(
        "C4 element class. A microservice or service is a Container. A datastore "
        "is a DataStore. A logical block inside a container is a Component. "
        "Deployment infrastructure (OpenShift, data centres) is a DeploymentNode."
    ))
    c4_level: str = Field(
        default="", description=(
            "C4 level: one of CONTEXT, CONTAINER, COMPONENT, CODE. "
            "EMPTY for DeploymentNode — infrastructure belongs to the deployment "
            "view, not to L1-L4."
        ),
    )
    """NOTE: deliberately `str`, not `Literal`, unlike the other vocabularies here.

    A `Literal` ships a strict `enum` in the JSON schema. When the model emits a
    value outside it — observed: `"SYSTEM"`, a plausible-but-wrong guess at an
    ABSTRACT vocabulary — the SDK rejects at the SCHEMA level, so a
    `mode="before"` coercer never runs. The model then retries, fails the same
    way, and the pass dies on the turn cap having produced nothing.

    Policy: `Literal` for concrete vocabularies the model reliably knows
    (element_type: Container, DataStore, …). Plain `str` plus coercion for
    abstract ones the model guesses at (C4 level). The description still states
    the allowed values, and a validator flags anything unrecognised — so we keep
    the guidance without the retry loop.
    """
    parent: str = Field(default="", description=(
        "Name of the containing element. A Container or DataStore belongs to its "
        "SoftwareSystem; a Component to its Container. Empty only for top-level "
        "elements (SoftwareSystem, ExternalSystem, Person)."
    ))
    system_class: Literal[
        "", "ENTERPRISE_TECHNOLOGY_PLATFORM", "BUSINESS_TECHNOLOGY_PLATFORM",
        "BUSINESS_APPLICATION", "SHARED_TECHNICAL_SERVICE", "INTEGRATION_PLATFORM",
    ] = Field(default="", description=(
        "SoftwareSystem only. ENTERPRISE_TECHNOLOGY_PLATFORM = shared cross-cutting "
        "infrastructure run by a platform team (OpenShift, Grafana, Splunk, ELK, "
        "API gateway, identity). BUSINESS_TECHNOLOGY_PLATFORM = a domain platform "
        "such as the system under design."
    ))
    origin: Literal["", "BUILT_IN_HOUSE", "VENDOR_COMMERCIAL", "OPEN_SOURCE", "HYBRID"] = Field(
        default="", description=(
            "SoftwareSystem only. Where the SOFTWARE came from: engineered here, "
            "bought from a vendor, adopted open source, or hybrid. Not where it runs."
        ),
    )
    deployment_model: Literal["", "SELF_HOSTED", "VENDOR_MANAGED_DEDICATED", "SAAS", "HYBRID"] = Field(
        default="", description=(
            "SoftwareSystem only. Who OPERATES it — separate from origin."
        ),
    )
    responsibilities: List[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("responsibilities", "responsibility",
                                      "accountabilities", "accountability"),
        description=(
            "What this element is ACCOUNTABLE for, as the document states it. Phrase as "
            "accountability, not behaviour: 'Acceptance and validation of payment "
            "requests', not 'Accepts requests'. Documents usually list these under each "
            "component — capture them all. This is the semantic content of the graph."
        ),
    )
    """Accepted under several names deliberately.

    Observed: the model emits `"responsibility": "..."` (singular string) where the
    schema declares `responsibilities` (plural list). Pydantic ignores unknown
    fields by default, so the value was SILENTLY DISCARDED — no error, no warning,
    just empty responsibilities on every element. That is the worst kind of
    failure: it looks like a model quality problem when it is a schema-vocabulary
    mismatch.

    Accept the natural variants and normalise, rather than requiring one spelling.
    """
    description: str = Field(default="", description="What the element is, briefly.")

    # Coerce rather than reject — see the enum-coercion note above. A pass is
    # worth more than the precision of one value.
    @field_validator("c4_level", mode="before")
    @classmethod
    def _coerce_c4(cls, v):
        return _norm_c4(v)

    @field_validator("element_type", mode="before")
    @classmethod
    def _coerce_element_type(cls, v):
        matched = _match_enum(v, (
            "SoftwareSystem", "ExternalSystem", "Person", "Container",
            "DataStore", "Component", "CodeElement", "DeploymentNode",
        ), "Container")
        return matched

    @field_validator("system_class", mode="before")
    @classmethod
    def _coerce_system_class(cls, v):
        return _match_enum(v, (
            "ENTERPRISE_TECHNOLOGY_PLATFORM", "BUSINESS_TECHNOLOGY_PLATFORM",
            "BUSINESS_APPLICATION", "SHARED_TECHNICAL_SERVICE", "INTEGRATION_PLATFORM",
        ), "")

    @field_validator("origin", mode="before")
    @classmethod
    def _coerce_origin(cls, v):
        return _match_enum(v, (
            "BUILT_IN_HOUSE", "VENDOR_COMMERCIAL", "OPEN_SOURCE", "HYBRID",
        ), "")

    @field_validator("deployment_model", mode="before")
    @classmethod
    def _coerce_deployment_model(cls, v):
        return _match_enum(v, (
            "SELF_HOSTED", "VENDOR_MANAGED_DEDICATED", "SAAS", "HYBRID",
        ), "")

    @field_validator("responsibilities", mode="before")
    @classmethod
    def _coerce_responsibilities(cls, v):
        """Normalise every shape the model produces into a list of strings.

        Handles: a bare string; a list of strings; a list of objects
        (`[{"responsibility": "..."}]`), which previously stringified into
        garbage like `"{'responsibility': '...'}"`; and nested lists.
        """
        if v is None:
            return []
        if isinstance(v, str):
            return [v.strip()] if v.strip() else []
        if isinstance(v, dict):
            v = [v]
        if not isinstance(v, (list, tuple)):
            return [str(v).strip()] if str(v).strip() else []

        out: List[str] = []
        for item in v:
            if isinstance(item, dict):
                # Pull whichever value the object carries.
                for key in ("responsibility", "statement", "value", "name", "text"):
                    if item.get(key):
                        out.append(str(item[key]).strip())
                        break
                else:
                    for val in item.values():
                        if isinstance(val, str) and val.strip():
                            out.append(val.strip())
                            break
            elif isinstance(item, (list, tuple)):
                out.extend(str(x).strip() for x in item if str(x).strip())
            elif item is not None and str(item).strip():
                out.append(str(item).strip())
        return out


class StructurePassResult(BaseModel):
    elements: List[ElementRecord] = Field(default_factory=list)
    triples: List[ExtractedTriple] = Field(
        default_factory=list,
        description="Containment triples: <contained> --part_of--> <container>. One per parent.",
    )


STRUCTURE_PASS = PassSpec(
    name="structure",
    schema=StructurePassResult,
    output_keys={"elements": "elements", "triples": "triples"},
    instructions="""# Task: extract C4 structure

Extract every architecture ELEMENT in this excerpt, its place in the hierarchy,
and its classification.

Rules:
1. A microservice or service is a **Container**, even if the document calls it a
   "component". Test: can it be deployed on its own? Yes -> Container.
2. A logical block inside one container is a **Component**.
3. A persistence store is a **DataStore**. An external party or system is an
   **ExternalSystem**. Infrastructure (OpenShift, data centre, cluster) is a
   **DeploymentNode** with no C4 level.
4. Set `parent` for every contained element, AND emit the matching
   `<contained> --part_of--> <parent>` triple. Both are required.
5. Capture `responsibilities` — what each element is accountable for — where the
   document states them.
6. Several SoftwareSystems is expected: the system under design plus the
   enterprise platforms it depends on. Classify each with `system_class`.

Name concepts; never emit a sentence as an element name. Do not invent elements
the excerpt does not describe.""",
)


# ============================================================================
# 2. CONNECTIONS — runtime call paths
# ============================================================================

class ConnectionRecord(BaseModel):
    source: str = Field(..., description="Name of the calling element.")
    target: str = Field(..., description="Name of the called element.")
    description: str = Field(default="", description="What flows across it.")
    protocol: Literal[
        "", "REST", "GRAPHQL", "GRPC", "SOAP", "AMQP", "KAFKA", "JMS",
        "JDBC", "SFTP", "SFTP_FILE", "WEBHOOK", "WEBSOCKET",
    ] = Field(default="", description="Wire protocol. Empty if unstated.")
    style: Literal[
        "", "SYNCHRONOUS_REQUEST_RESPONSE", "ASYNCHRONOUS_EVENT",
        "ASYNCHRONOUS_REQUEST_REPLY", "BATCH_TRANSFER", "PUBLISH_SUBSCRIBE",
        "SHARED_DATABASE",
    ] = Field(default="", description="Integration style. Empty if unclear.")


class ConnectionPassResult(BaseModel):
    connections: List[ConnectionRecord] = Field(default_factory=list)
    triples: List[ExtractedTriple] = Field(
        default_factory=list,
        description="<source> --connects_to--> <target>. One per connection.",
    )


CONNECTION_PASS = PassSpec(
    name="connections",
    schema=ConnectionPassResult,
    output_keys={"connections": "connections", "triples": "triples"},
    instructions="""# Task: extract runtime connections

Extract every RUNTIME call path between architecture elements: source, target,
protocol, and whether the call is synchronous.

Rules:
1. A connection is a runtime call. Deployment is NOT a connection — "runs on" is
   a `deploys_on` triple, not a connection.
2. Monitoring is usually pull-based or asynchronous, not synchronous
   request/response. Prometheus scraping and log shipping are not
   `SYNCHRONOUS_REQUEST_RESPONSE`.
3. Emit both the `connections` record and the matching
   `<source> --connects_to--> <target>` triple.
4. Use only the listed protocol and style values. Leave a field empty rather than
   inventing a term.""",
)


# ============================================================================
# 3. TECHNOLOGY — stacks and styles
# ============================================================================

class TechnologyStackRecord(BaseModel):
    name: str = Field(..., description=(
        "The technology as the source names it: 'Spring Boot', 'Java 21', "
        "'PostgreSQL', 'Valkey', 'Docker', 'TLS 1.2+', 'Kafka', 'REST'."
    ))
    category: Literal[
        "", "LANGUAGE", "FRAMEWORK", "LIBRARY", "TOOL", "PLATFORM",
        "DATA_STORE", "MESSAGING", "PROTOCOL", "INFRASTRUCTURE", "OBSERVABILITY",
    ] = Field(default="", description="Optional category. Empty if unsure.")
    version: str = Field(default="", description="Version if stated.")
    used_by: List[str] = Field(default_factory=list, description="Elements that use it.")


class ArchitectureStyleRecord(BaseModel):
    name: str = Field(..., description="The style as the source states it, e.g. 'Stateless Modular Microservices'.")
    style: Literal[
        "", "MONOLITH", "MODULAR_MONOLITH", "MICROSERVICES", "SERVICE_BASED",
        "SERVICE_ORIENTED", "SERVERLESS", "EVENT_DRIVEN", "LAYERED",
        "HEXAGONAL", "MICROKERNEL", "PIPELINE", "SPACE_BASED",
    ] = Field(default="", description="Normalised style.")
    adopted_by: List[str] = Field(default_factory=list)


class TechnologyPassResult(BaseModel):
    technology_stacks: List[TechnologyStackRecord] = Field(default_factory=list)
    architecture_styles: List[ArchitectureStyleRecord] = Field(default_factory=list)
    triples: List[ExtractedTriple] = Field(
        default_factory=list,
        description="uses_technology / follows_style / deploys_on triples.",
    )


TECHNOLOGY_PASS = PassSpec(
    name="technology",
    schema=TechnologyPassResult,
    output_keys={"technology_stacks": "technology_stacks",
                 "architecture_styles": "architecture_styles",
                 "triples": "triples"},
    instructions="""# Task: extract technologies and architectural styles

These are NOT architecture elements. They have no C4 level and no parent.

Extract:

**technology_stacks** — languages, frameworks, tools, platforms, protocols, data
technologies, observability tooling. Examples: Spring Boot, Java 21, Docker,
Kubernetes, OpenShift, PostgreSQL, Valkey, Kafka, REST, gRPC, TLS 1.2+, AlpineJS,
Prometheus, Grafana, ELK Stack, Splunk.

**architecture_styles** — the coarse shape of the design. Examples: Monolith,
Modular Monolith, Microservices, Event-Driven, Layered, Serverless.

Test: does it RUN, or is it USED? Things that run are elements (not your job in
this pass). Things that are used are technology stacks. Things that SHAPE the
design are architecture styles.

Emit triples: `<element> --uses_technology--> <technology>`,
`<element> --follows_style--> <style>`, `<element> --deploys_on--> <platform>`.

Preserve the source's own naming. Do not genericise 'PostgreSQL' to 'Primary
Database'.""",
)


# ============================================================================
# 4. TRACEABILITY — links back to requirements and business intent
# ============================================================================

class ReferenceRecord(BaseModel):
    """A captured link from architecture to business intent.

    Captured verbatim even when it cannot be resolved — establishing the link
    structure is this phase's job; resolving it against the requirements graph is
    a later reconciliation step. A captured-but-unresolved reference can be
    corrected by a human architect; an absent link is invisible.
    """

    element: str = Field(..., description="The architecture element making the link.")
    relationship: Literal[
        "implements_requirement", "traces_to_goal", "satisfies_quality_attribute",
        "supports_capability", "delivers_initiative", "governed_by_rule",
    ] = Field(..., description="The kind of link.")
    reference: str = Field(..., description=(
        "The target, EXACTLY as the source gives it — an identifier like "
        "'FR-PM-001' if present, otherwise the paraphrased name verbatim."
    ))
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    source_text: str = Field(default="")


class TraceabilityPassResult(BaseModel):
    references: List[ReferenceRecord] = Field(default_factory=list)
    triples: List[ExtractedTriple] = Field(default_factory=list)


TRACEABILITY_PASS = PassSpec(
    name="traceability",
    schema=TraceabilityPassResult,
    output_keys={"references": "references", "triples": "triples"},
    instructions="""# Task: extract traceability links

Link architecture elements back to business intent: requirements, goals,
capabilities, quality attributes, and initiatives (business cases / work items).

Emit a `references` record and a matching triple for each link.

**Capture the reference even when you cannot resolve it.** If the source cites an
identifier (`FR-PM-001`), capture that identifier. If it only paraphrases
("handles Request Acceptance and Validation"), capture the paraphrase verbatim.
Never drop a reference because you cannot match it to a known requirement —
recording the link is this pass's job, resolving it is a later step.

Where a link is clearly implied rather than stated, still emit it with confidence
0.5-0.8. A missing traceability edge is worse than an uncertain one: the auditor
cannot report a gap that was never recorded.

Omit this section entirely if the excerpt contains no traceability content.""",
)


ARCHITECTURE_PASSES = [STRUCTURE_PASS, CONNECTION_PASS, TECHNOLOGY_PASS, TRACEABILITY_PASS]
