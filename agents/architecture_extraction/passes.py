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
    container_type: str = Field(default="", description=(
        "Container and DataStore only: the KIND of running thing this is. One of "
        "WEB_APPLICATION, API_SERVICE, WORKER, BATCH_JOB, DATABASE, CACHE, "
        "MESSAGE_BROKER, GATEWAY, UI_COMPONENT, FILE_STORE, SCHEDULER. Empty for "
        "every other element type."
    ))
    """`container_type` is `required: true` on Container in the ontology and was
    emitted by nothing: not in this schema, not in ingest, not in any validator — so
    a required slot the pipeline never produced was invisible, because an absent
    field and an unasked question look identical.

    Deliberately `str`, not `Literal`, following the policy stated for `c4_level`
    below: `ContainerType` is a vocabulary the model guesses at ("SERVICE"), and a
    strict `enum` in the JSON schema makes a near-miss fail at the SCHEMA level, so
    the pass retries and dies on the turn cap having produced nothing. The allowed
    values are still named here for guidance, and `check_enum_membership` flags
    anything outside `ContainerType` — so the guidance is kept without the retry loop.
    """
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

    # --- quality attributes this element delivers ---
    #
    # Distinct from a requirement reference. `requirement_refs` records what the
    # document SAYS this element answers; this records what quality property it
    # actually provides, which is the half that survives an attribute nobody
    # wrote an NFR for. Redundancy is worth recording even when no availability
    # requirement exists — and that gap is what the auditor needs to see.
    satisfies_attributes: List[str] = Field(
        default_factory=list,
        description=(
            "Quality ATTRIBUTES this element delivers, named with the standard's "
            "own terms: 'Availability', 'Time Behaviour', 'Scalability', "
            "'Confidentiality'. Never a mechanism — 'Redundancy' and 'Replication' "
            "are techniques that deliver Availability, not attributes. Leave empty "
            "where the document states no quality property."
        ),
    )
    quality_category: str = Field(
        default="",
        description=(
            "For an element delivering a quality property: the ISO 25010:2023 "
            "top-level characteristic, e.g. RELIABILITY, PERFORMANCE_EFFICIENCY, "
            "SECURITY, FLEXIBILITY. Empty if not applicable."
        ),
    )
    subcharacteristic: str = Field(
        default="",
        description=(
            "The precise ISO 25010:2023 sub-characteristic the element delivers, "
            "where clear — AVAILABILITY, TIME_BEHAVIOUR, SCALABILITY, "
            "CONFIDENTIALITY. Empty if only the top-level characteristic is clear."
        ),
    )
    # ---- DeploymentNode slots (YB-044) ----
    #
    # The first DeploymentNode slots this pass has ever emitted. Until now a
    # deployment node arrived as a bare name plus `element_type`, because NONE of
    # the class's own fields (infrastructure_type, environment, region,
    # network_zone, hosted_on, runs_containers, parent_system) existed on this
    # record — and `ingest.py`'s structure-fact whitelist is a literal tuple, so a
    # field here that is not added there reaches the output dict and never the
    # graph. Both ends move together or the new slots become the
    # "declared-but-unemitted" defect a third time (ConceptAttribute,
    # `Platform.contracts`).
    platform_type: str = Field(
        default="",
        description=(
            "For a DeploymentNode: the platform TYPE this node is an instance of, "
            "as the source names it — 'OpenShift', 'Airflow', 'n8n'. The TYPE is "
            "never an element: do not also emit it as one. Empty for anything that "
            "is not a platform instance."
        ),
    )
    sharing_scope: str = Field(
        default="",
        description=(
            "For a DeploymentNode: how widely THIS instance is shared — "
            "ENTERPRISE, BUSINESS_UNIT or DEDICATED. A property of the instance, "
            "not of the type: one platform type is routinely deployed all three "
            "ways at once. Empty where the document does not say."
        ),
    )
    serves: List[str] = Field(
        default_factory=list,
        description=(
            "For a DeploymentNode: the SoftwareSystems this instance serves. "
            "Multivalued because a shared cluster serves many — the singular "
            "`parent_system` on the ontology class cannot express that. Names must "
            "match SoftwareSystem elements emitted in this run: the ontology ranges "
            "this slot over SoftwareSystem, so a Container, Component or product "
            "name here is an edge the schema does not permit."
        ),
    )

    # Coerce rather than reject — see the enum-coercion note above. A pass is
    # worth more than the precision of one value.
    @field_validator("c4_level", mode="before")
    @classmethod
    def _coerce_c4(cls, v):
        return _norm_c4(v)

    @field_validator("parent", "description", "container_type", mode="before")
    @classmethod
    def _coerce_optional_text(cls, v):
        """`None` means "not stated", which is what the empty string already means.

        Observed on a live Design Assistant run: the model emitted `"parent": null`
        for top-level elements. Pydantic rejects `None` for a `str` field at the
        SCHEMA level, so the structured call failed, the model was re-prompted, and
        the pass spent three turns arriving at what `""` already said. Same posture
        as the enum coercers: normalise formatting variance, judge the content.
        """
        return "" if v is None else str(v).strip()

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
   **DeploymentNode** with no C4 level. When the document names the platform a
   node is an instance OF ("the OpenShift cluster"), put that platform's name in
   `platform_type`, and set `sharing_scope` when the document says how widely
   this instance is shared (ENTERPRISE, BUSINESS_UNIT, DEDICATED). List the
   systems one shared instance serves in `serves`. The platform TYPE is never an
   element: emit the cluster, not the cluster and its type.
4. Set `parent` for every contained element, AND emit the matching
   `<contained> --part_of--> <parent>` triple. Both are required. If this
   excerpt does not say where a Container, Component or DataStore sits, attach
   it to the system under design — never leave `parent` empty, because a
   contained element with no parent cannot be placed in the hierarchy.
5. Capture `responsibilities` — what each element is accountable for — where the
   document states them.
6. Several SoftwareSystems is expected: the system under design plus the
   enterprise platforms it depends on. Classify each with `system_class`. Do not
   emit one platform both ways in the same run: a cluster or runtime the document
   deploys ON is the DeploymentNode of rule 3, not also a SoftwareSystem.
7. An architectural STYLE or pattern the document names (microservices, layered,
   event-driven, hexagonal, modular monolith, SOA) is NOT an element — do not
   emit it here. A named technology, framework, tool or platform is a
   TechnologyStack, also not an element.
8. Use the concrete name the document gives (PostgreSQL, Valkey, OpenShift).
   Never emit a bare category word — "Database", "Cache", "Services",
   "Microservices" — as an element when the document names the specific thing.
   Use ONE name for one thing throughout: where the document calls it "the
   OpenShift cluster" in one place and "OpenShift" in another, that is one
   element, not two.
9. Set `container_type` on every Container and DataStore — the KIND of running
   thing it is (WEB_APPLICATION, API_SERVICE, WORKER, BATCH_JOB, DATABASE, CACHE,
   MESSAGE_BROKER, GATEWAY, UI_COMPONENT, FILE_STORE, SCHEDULER). The ontology
   marks it required, so a Container without one classifies as nothing. Leave it
   empty for every other element type.

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
    carries_sensitive_data: bool = Field(default=False, description=(
        "True when regulated or sensitive data crosses this connection — cardholder "
        "data, credentials, personal data. Set it only where the document states or "
        "clearly implies it; leave false otherwise. Drives PCI scope decisions."
    ))
    failure_handling: str = Field(default="", description=(
        "What happens when this call fails, as the document states it — 'retry x3 then "
        "DLQ', 'circuit breaker', 'no handling'. Empty if unstated."
    ))


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
   inventing a term.
5. BOTH ENDPOINTS MUST BE A NAMED ELEMENT: a Container, Component, DataStore,
   ExternalSystem or SoftwareSystem that the document names — use the SAME concrete
   name the structure pass uses ("Valkey", not "Cache"; "PostgreSQL", not
   "Database"; "Elavon", not "External Services").
6. These are NOT connection endpoints, and a connection that names one cannot be
   drawn: an architecture style or pattern ("Microservices", "Event-Driven"), a
   quality attribute ("High Availability"), a design technique ("Performance
   Monitoring"), a category word ("Database", "Cache", "Services", "Critical
   Components"), or a group of things ("External Services", "External PGSP/PSP").
   If the excerpt names no specific pair of elements, emit an EMPTY list — an empty
   answer is a real answer, and a connection between two things the architecture
   does not declare is a fact about nothing.
7. Set `carries_sensitive_data` when regulated or sensitive data crosses the link
   (cardholder data, credentials, personal data) — PCI scope is decided from this.
   Set `failure_handling` where the document states it ("retry x3 then DLQ",
   "circuit breaker"). Leave both empty/false rather than guessing.""",
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


class TradeOffRecord(BaseModel):
    """A structured cost accepted with a design choice: what it buys, what it costs.

    Maps to the `TradeOff` ontology class. A bare string cost is still accepted by
    ingest (kept as a description-only node), but the structured form is what makes
    a trade-off queryable — "which choices bought Availability at the cost of
    Flexibility?".
    """

    name: str = Field(..., description=(
        "The trade-off as stated, e.g. 'Consistency over availability'."
    ))
    gains: List[str] = Field(default_factory=list, description=(
        "Quality attributes the choice BUYS, named as the standard does — "
        "'Consistency', 'Availability'."
    ))
    sacrifices: List[str] = Field(default_factory=list, description=(
        "Quality attributes the choice COSTS, named the same way."
    ))
    rationale: str = Field(default="", description=(
        "Why this cost is acceptable in this design."
    ))


class ArchitectureStyleRecord(BaseModel):
    name: str = Field(..., description="The style as the source states it, e.g. 'Stateless Modular Microservices'.")
    style: Literal[
        "", "MONOLITH", "MODULAR_MONOLITH", "MICROSERVICES", "SERVICE_BASED",
        "SERVICE_ORIENTED", "SERVERLESS", "EVENT_DRIVEN", "LAYERED",
        "HEXAGONAL", "MICROKERNEL", "PIPELINE", "SPACE_BASED",
    ] = Field(default="", description="Normalised style.")
    adopted_by: List[str] = Field(default_factory=list)
    trade_offs: List[TradeOffRecord] = Field(default_factory=list, description=(
        "Structured costs accepted with the style — what it buys versus what it costs."
    ))


class DesignTechniqueRecord(BaseModel):
    """A verifiable mechanism the architecture uses to achieve a quality attribute.

    Deliberately separate from `ArchitectureStyleRecord`: a style is the coarse
    shape of the design, a technique is the mechanism inside it. "Stateless
    Modular Microservices" is the style; "Stateless Services" and
    "Redundancy / Replicas" are the techniques, and they are what answers an
    availability or scalability NFR.
    """

    name: str = Field(..., description=(
        "The mechanism as the source states it: 'Stateless Services', "
        "'Redundancy / Replicas', 'Active-Active Multi-DataCentre', "
        "'Connection Pooling', 'Asynchronous Offload'. "
        "Name the mechanism, NOT the quality attribute it targets — "
        "'High Availability' is an NFR, not a technique."
    ))
    technique_category: Literal[
        "", "STRUCTURAL", "INTEGRATION", "DATA", "RESILIENCE", "SECURITY",
        "DEPLOYMENT", "OBSERVABILITY", "AVAILABILITY", "SCALABILITY",
        "PERFORMANCE", "STANDARDS_CONFORMANCE",
    ] = Field(default="", description="Which family of technique. Empty if unsure.")
    applies_to: List[str] = Field(
        default_factory=list,
        description="Elements the technique is applied to. Usually several — "
                    "statelessness and redundancy are platform-wide decisions.",
    )
    realizes_quality_attributes: List[str] = Field(
        default_factory=list,
        description="Names or ids of the NFRs this technique is the mechanism for. "
                    "Emit the link even when the NFR is only implied.",
    )
    quality_category: Literal[
        "", "FUNCTIONAL_SUITABILITY", "PERFORMANCE_EFFICIENCY", "COMPATIBILITY",
        "INTERACTION_CAPABILITY", "RELIABILITY", "SECURITY", "MAINTAINABILITY",
        "FLEXIBILITY", "SAFETY", "REGULATORY_COMPLIANCE",
    ] = Field(default="", description=(
        "ISO 25010:2023 characteristic this technique TARGETS. FLEXIBILITY, not "
        "PORTABILITY — the 2023 revision replaced Portability with Flexibility, "
        "and scalability lives there. Set this when the attribute cannot be named; "
        "it is what lets the auditor check the technique is aimed at the right "
        "family."
    ))
    subcharacteristic: Literal[
        "", "FUNCTIONAL_COMPLETENESS", "FUNCTIONAL_CORRECTNESS", "FUNCTIONAL_APPROPRIATENESS",
        "TIME_BEHAVIOUR", "RESOURCE_UTILIZATION", "CAPACITY",
        "CO_EXISTENCE", "INTEROPERABILITY",
        "APPROPRIATENESS_RECOGNIZABILITY", "LEARNABILITY", "OPERABILITY",
        "USER_ERROR_PROTECTION", "USER_ENGAGEMENT", "INCLUSIVITY",
        "USER_ASSISTANCE", "SELF_DESCRIPTIVENESS",
        "FAULTLESSNESS", "AVAILABILITY", "FAULT_TOLERANCE", "RECOVERABILITY",
        "CONFIDENTIALITY", "INTEGRITY", "NON_REPUDIATION", "ACCOUNTABILITY",
        "AUTHENTICITY", "RESISTANCE",
        "MODULARITY", "REUSABILITY", "ANALYSABILITY", "MODIFIABILITY", "TESTABILITY",
        "ADAPTABILITY", "SCALABILITY", "INSTALLABILITY", "REPLACEABILITY",
        "OPERATIONAL_CONSTRAINT", "RISK_IDENTIFICATION", "FAIL_SAFE",
        "HAZARD_WARNING", "SAFE_INTEGRATION",
    ] = Field(default="", description=(
        "The precise sub-characteristic the technique delivers, where clear — "
        "Redundancy is AVAILABILITY, Statelessness is SCALABILITY. Empty if only "
        "the characteristic is clear."
    ))
    satisfies_attributes: List[str] = Field(
        default_factory=list,
        description=(
            "Quality ATTRIBUTES this technique delivers, named as the standard "
            "does — 'Availability', 'Scalability', 'Time Behaviour'. Distinct "
            "from `realizes_quality_attributes`, which names the NFRs: this "
            "survives an attribute no requirement ever stated."
        ),
    )
    mechanism: str = Field(default="", description=(
        "How it works, concretely enough to check: 'any replica serves a request; "
        "session state is externalised to Valkey, so losing a pod loses no "
        "session'. Not 'the services are stateless'."
    ))
    trade_offs: List[TradeOffRecord] = Field(default_factory=list, description=(
        "Structured costs accepted with the technique — what it buys versus what it costs."
    ))


class EngineeringConventionRecord(BaseModel):
    """An organisation-specific rule about how things are built, named or documented.

    Named conventions such as `<company>-<product>-<web>` are the highest-value
    case: they are verifiable against element names the graph already holds.
    """

    name: str = Field(..., description="Short name, e.g. 'Container naming', 'Service naming'.")
    convention_type: Literal[
        "", "NAMING", "STRUCTURE", "VERSIONING", "INTERFACE", "ERROR_HANDLING",
        "CONFIGURATION", "SECURITY", "OBSERVABILITY", "DEPLOYMENT",
        "DOCUMENTATION", "CODE_STANDARD",
    ] = Field(default="", description="What the convention constrains.")
    pattern: str = Field(default="", description=(
        "The convention as a machine-checkable template with angle-bracket "
        "placeholders: '<company>-<product>-<web>', '<company>-<product>-<api>'. "
        "Capture verbatim; do not invent placeholder values."
    ))
    examples: List[str] = Field(
        default_factory=list,
        description="Conforming instance names from the source, e.g. 'acme-payments-web'.",
    )
    applies_to: List[str] = Field(
        default_factory=list,
        description="Elements the convention governs, where the source names them.",
    )
    enforcement: Literal[
        "", "MANDATORY", "RECOMMENDED", "ADVISORY", "TOOL_ENFORCED", "LEGACY_EXEMPT",
    ] = Field(default="", description="How binding the convention is.")
    rationale: str = Field(default="", description="Why the organisation adopted it.")


class TechnologyPassResult(BaseModel):
    technology_stacks: List[TechnologyStackRecord] = Field(default_factory=list)
    architecture_styles: List[ArchitectureStyleRecord] = Field(default_factory=list)
    design_techniques: List[DesignTechniqueRecord] = Field(default_factory=list)
    engineering_conventions: List[EngineeringConventionRecord] = Field(default_factory=list)
    triples: List[ExtractedTriple] = Field(
        default_factory=list,
        description="uses_technology / follows_style / deploys_on / realizes_quality_attribute triples.",
    )


TECHNOLOGY_PASS = PassSpec(
    name="technology",
    schema=TechnologyPassResult,
    output_keys={"technology_stacks": "technology_stacks",
                 "architecture_styles": "architecture_styles",
                 "design_techniques": "design_techniques",
                 "engineering_conventions": "engineering_conventions",
                 "triples": "triples"},
    instructions="""# Task: extract technologies, styles, techniques and conventions

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
design are architecture styles. Things that MAKE A QUALITY ATTRIBUTE HAPPEN are
design techniques.

**design_techniques** — the verifiable mechanisms the design uses to achieve a
quality attribute: Stateless Services, Redundancy / Replicas, Active-Active
Multi-DataCentre, Health-Checked Removal from Rotation, Connection Pooling, Read
Replicas, Caching, Asynchronous Offload, Batching, Partitioning.

A technique is NOT a style and NOT a technology:
- "Stateless Modular Microservices" is the **style** (the shape).
- "Stateless Services" and "Redundancy / Replicas" are the **techniques** inside
  it (the mechanisms).
- "Spring Boot" is a **technology** (what it is built from).

For each technique, set `applies_to` (usually several elements — statelessness
and redundancy are platform-wide), and record `realizes_quality_attributes` or at
minimum `quality_category`. **This link is the point of the class**: a High
Availability NFR and a description of redundancy with no edge between them is the
gap it closes. Always capture `mechanism` — how it works, not what it achieves.
"High Availability" is the NFR, not a technique; do not emit it as one.

**engineering_conventions** — the organisation's own rules about how things are
built, named or documented. Naming conventions are the most valuable: record the
`pattern` with its placeholders exactly as stated
(`<company>-<product>-<web>`, `<company>-<product>-<api>`) and every conforming
`examples` name you can see. If the document names the convention but not the
elements it governs, still record it — an unchecked convention is still a
convention. Do NOT invent placeholder values that the source does not give.

Emit triples: `<element> --uses_technology--> <technology>`,
`<element> --follows_style--> <style>`, `<element> --deploys_on--> <platform>`,
`<technique> --realizes_quality_attribute--> <nfr>`,
`<element> --applies_technique--> <technique>`,
`<element> --conforms_to--> <convention>`.

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


class ArchitectureDecisionRecord(BaseModel):
    """A decision the DOCUMENT records, mirroring the ontology's `ArchitectureDecision`.

    Shared with the Design Assistant, which proposes decisions; this profile EXTRACTS
    the ones a document states. The record is identical because the graph node is —
    what differs is who is claiming it, and that lives in the provenance, not here.
    """

    title: str = Field(..., description=(
        "Short name for the decision, e.g. 'Synchronous gateway calls for "
        "authorizations'. A decision names a CHOICE, not an element."
    ))
    context: str = Field(default="", description=(
        "The forces at play when the decision was made — the requirements, quality "
        "attributes or constraints the document gives as its reason."
    ))
    decision: str = Field(default="", description="What was decided.")
    consequences: List[str] = Field(default_factory=list, description=(
        "What follows from the decision, good and bad, as the document states it."
    ))
    alternatives_considered: List[str] = Field(default_factory=list, description=(
        "The options the document says were rejected, with the reason where it gives one."
    ))
    decided_date: str = Field(default="", description="When it was decided, if the document says.")
    status: str = Field(default="ACCEPTED", description=(
        "What the DOCUMENT says about the decision's own lifecycle: ACCEPTED when it "
        "presents the choice as settled, PROPOSED when under consideration, SUPERSEDED, "
        "REJECTED or DEPRECATED when it says so. This is NOT the review state — the "
        "assertion is UNVERIFIED and carries agent provenance however settled the "
        "document sounds, and conflating the two axes is how a document's confidence "
        "becomes the platform's."
    ))
    affects_elements: List[str] = Field(default_factory=list, description=(
        "The elements this decision governs, by the exact name of an element you "
        "extracted. Leave it empty rather than inventing an element."
    ))
    supersedes: List[str] = Field(default_factory=list, description=(
        "An earlier decision this one replaces, by its exact title. Empty when new."
    ))


class DecisionPassResult(BaseModel):
    """Decisions only — deliberately NO `triples`.

    Ingest derives every edge a decision has from these fields: `affects_element` from
    `affects_elements` and `supersedes` from `supersedes` (`core/knowledge/ingest.py`).
    Asking the model to restate them as triples duplicated work it had already done, and
    the duplication was not free: on a 3.3k-character document the pass spent 58s and
    returned nothing schema-valid at all, losing every decision — while the same pass on
    a smaller document emitted 155 triples that ingest ignored.
    """

    architecture_decisions: List[ArchitectureDecisionRecord] = Field(default_factory=list)


ARCHITECTURE_DECISION_PASS = PassSpec(
    name="decisions",
    schema=DecisionPassResult,
    output_keys={"architecture_decisions": "architecture_decisions"},
    instructions="""# Task: extract the architecture DECISIONS the excerpt records

A decision is the WHY — a choice and the forces behind it. It is not an element and
not a requirement. Extract only decisions the excerpt actually states or clearly
implies; a document that records no rationale yields no decisions, and an empty
result is a correct answer.

Rules:
1. `title` names the CHOICE, not a thing. Good: "Synchronous calls for
   authorizations". Bad: "Payment Gateway Platform".
2. `context` gives the forces the document cites — the requirement, quality
   attribute or constraint that made one option win. Quote its reason where it
   gives one; do not invent a rationale the excerpt does not contain.
3. `decision` is what was chosen, in one sentence.
4. `consequences` and `alternatives_considered` come from the excerpt. Both matter:
   a decision with no rejected alternative and no consequence usually means the
   excerpt did not state a decision, only a fact.
5. `status` is what the DOCUMENT says — ACCEPTED for a settled choice, PROPOSED for
   one still under consideration, SUPERSEDED / REJECTED / DEPRECATED where stated.
   This is the decision's lifecycle in the enterprise, NOT the review state: every
   assertion here is UNVERIFIED and agent-attributed until a human vouches for it.
6. `affects_elements` names the elements this decision governs, by the exact name
   of an element in this excerpt. Empty rather than invented.
7. `supersedes` names an earlier decision this one replaces, by its exact title.
8. Do not restate a requirement or a quality attribute as a decision. "The system
   shall route transactions" is a requirement; "route by rule engine rather than
   round-robin" is a decision.
9. Record at most TEN decisions — the ones that shape the architecture. An empty list
   is a correct and complete answer for an excerpt that records no rationale, and a
   short list is better than a padded one. Do NOT emit triples: the links this pass
   needs (`affects_elements`, `supersedes`) are read from those fields, so restating
   them is work thrown away.

Omit this section entirely if the excerpt records no decisions.""",
)


ARCHITECTURE_PASSES = [
    STRUCTURE_PASS,
    CONNECTION_PASS,
    TECHNOLOGY_PASS,
    TRACEABILITY_PASS,
    ARCHITECTURE_DECISION_PASS,
]
