# SEA Base Requirements Ontology

**Version:** 0.2.0  
**Last Updated:** September 19, 2026

A generic, domain-agnostic ontology for structured business requirement analysis. This ontology supports both **standalone** and **platform-centric** enterprise topologies through a topology-aware design.

> **Reading this?** There is a browsable reference view of all four base layers in the
> MVP UI at **`/ontology`** (route `ontology`). It shows the import chain, the class
> hierarchy with `is_a` and mixins kept distinct, own versus inherited slots, enums
> with their permissible values, and how many instances of each class exist in the
> current working set. The same page also reports the **active domain pack**
> separately, with a coverage census showing which domain concepts the working set
> actually mentions — a domain class with zero instances is a finding, not a blank.
>
> The tables below are hand-maintained and have drifted from the schemas — the live
> view cannot. The class counts quoted in older notes (58 classes, 37 enums, 13
> subsets, 457 slots across four layers) cover the base layers only; they do not
> include the domain packs under `domains/`.

---

## Design Principles

### 1. Topology-Aware, Not Topology-Prescriptive

The ontology defines constructs (Product, System, Application, Platform) and their relationships, but **does not mandate** which constructs must be instantiated. The same schema supports:

- **Standalone topology:** Product → System → Application (no Platform)
- **Platform topology:** Product builds_on Platform ← System ← Application
- **Hybrid topology:** Some products/systems are platform-based, others are standalone

### 2. Traceability by Construction

Every requirement carries structural traceability links:
- **Vertical:** `traces_to_goals`, `traces_to_capabilities`, `traces_to_processes`
- **Horizontal:** `depends_on`, `conflicts_with`, `refines`
- **Cross-altitude:** `binds_to_product`, `binds_to_system`, `binds_to_application`, `binds_to_platform`
- **Derivation:** `derives_from` (e.g., BusinessRequirement → NFR at system level)

### 3. Quality Attribute Rigor

Aligned with **ISO/IEC 25010:2023** for quality attributes. Non-functional requirements use the **ATAM quality scenario pattern** (stimulus → environment → response → measure) to make them testable, not aspirational.

### 4. Design Choices Are Typed, Not Folded Into One Bucket

Four different questions about how an architecture is shaped, kept apart because
merging them destroys the property that makes each useful:

| Class | Question | Example | Judged by |
|---|---|---|---|
| `ArchitectureStyle` | What **shape** is the design? | Stateless Modular Microservices | comparability — is this system microservices or monolith? |
| `ArchitecturePattern` | Which **named solution** was adopted? | Circuit Breaker, Saga, CQRS | whether the published pattern is present |
| `DesignTechnique` | What **mechanism** delivers a quality attribute? | Stateless Services, Redundancy / Replicas | whether the NFR it claims is actually met |
| `EngineeringConvention` | What **rule** does the organisation impose on how things are built? | `<company>-<product>-<web>` | **conformance** — does the name match the rule? |

The distinction pays for itself in what becomes checkable. A technique carries
`realizes_quality_attributes → NonFunctionalRequirement`, which supplies the edge
that turns *"High Availability is required"* plus *"the platform is replicated"*
into a stated, verifiable mechanism instead of two unrelated facts. A convention
carries a machine-checkable `pattern` plus the elements it governs, which makes
naming drift **derivable** — a document that records a standard rarely also
reports its own violations.

Conventions are deliberately *not* modelled as techniques: a technique is
justified by a quality attribute and needs judgement to assess, whereas a
convention is justified by consistency and is decided mechanically against data
the graph already holds (element names, containment, technology stacks).

**Where the pattern names come from.** This layer declares the CLASS
(`ArchitecturePattern`) and the vocabulary it ranges over (`PatternCategory`); it
does not enumerate the published patterns. Those live in
[`catalogues/architecture_patterns.yaml`](catalogues/architecture_patterns.yaml),
read by `core/patterns.py` and used by the Design Assistant
([ADR-0017](../docs/decisions/ADR-0017-design-assistant-proposes-arc-g.md)). A
catalogue of INSTANCES is a different artefact from a schema — adding a pattern is
routine, changing a class is a schema version — and keeping them apart leaves this
four-file chain and the domain-pack discovery untouched. `SEA_PATTERN_CATALOGUE`
points at an organisation's own library instead.

### 5. The Quality Model Is ISO/IEC 25010:2023, in Two Levels

Two enums and one class:

| | |
|---|---|
| `QualityAttributeCategory` | the **nine characteristics** — Functional Suitability, Performance Efficiency, Compatibility, Interaction Capability, Reliability, Security, Maintainability, Flexibility, Safety — plus `REGULATORY_COMPLIANCE`, which is enterprise governance and labelled as such via `QualityConcernClass` |
| `QualitySubcharacteristic` | the **forty sub-characteristics** the standard actually classifies at |
| `QualityAttribute` | a **node**, so a quality concern has an identity rather than being a bare enum value |

The version matters. This is strictly 2023: Usability became **Interaction
Capability**, and Portability was replaced by **Flexibility** — which is why
`FLEXIBILITY` is present, `PORTABILITY` is not, and **Scalability** lives under
Flexibility rather than under a superseded characteristic.

**Why two levels and not one.** "within 500ms" and "1000 TPS with horizontal
scaling" are both `PERFORMANCE_EFFICIENCY` at the top level, but they are
`TIME_BEHAVIOUR` and `SCALABILITY` — and they are satisfied by entirely different
techniques (caching versus statelessness plus replication). A model that stops at
the characteristic cannot tell them apart, so it cannot check that a technique is
aimed at the right concern.

**Why `QualityAttribute` is a class.** An enum value is not a thing anything can
point at. Without the class, `DesignTechnique.realizes_quality_attributes` and
`ArchitectureElement.satisfies_attributes` could only range over
`NonFunctionalRequirement` — a *documented instance* — with three consequences: a
technique could only be justified by an attribute some requirement happened to
state; two NFRs about the same attribute looked unrelated; and `quality_category`
was a string with nowhere to hang a scenario, evidence or verdict.

With the node, all three resolve:

```
NonFunctionalRequirement --realizes_attribute-->  QualityAttribute
ArchitectureElement      --satisfies_attribute--> QualityAttribute
DesignTechnique          --satisfies_attribute--> QualityAttribute
```

That is what makes *"which elements and techniques deliver Availability?"*
answerable — including when **no requirement states Availability at all**, which
is the coverage gap the auditor exists to find.

`QualityScenario` (the ATAM pattern: stimulus → environment → response → measure)
is declared and currently **never populated**; no extraction pass emits it. It is
kept because it is the intended way to make an NFR falsifiable, and its own
description says so rather than implying it works.

---

## Identity: Scope Decides What May Be Joined On

`sea_common` records external identifiers through the `ExternallyReferenced`
mixin (on `Requirement`, `ArchitectureElement`, and the other roots) and the
`ExternalReference` class. What makes that class usable rather than decorative is
**`IdentityScope`**, which says how far an identifier's authority reaches:

| Scope | Meaning | May be matched on? |
|---|---|---|
| `ENTERPRISE` | Held in a system of record — ServiceNow CI, Jira key, LeanIX id | **Yes**, globally |
| `INITIATIVE` | Unique within one Initiative | Only within that Initiative |
| `DOCUMENT` | A label local to one source document | Only inside that document |
| `RUN` | Local to one extraction run | No |

**Why this matters more than it looks.** A requirements document numbering its
requirements `FR-001`, `FR-002` is a near-universal convention, and those numbers
carry no meaning beyond their document. Treating `FR-001` from one document as a
match for `FR-001` in another produces a **confident wrong join** — worse than a
missing join, because it makes the audit wrong rather than merely incomplete.

So the two cases are recorded differently:

- An identifier from a **system of record** is `ENTERPRISE`-scoped and is the
  strongest reconciliation signal available — an exact key match.
- A label read out of a **source document** is `DOCUMENT`-scoped, with the
  document named as its `system`. It is kept because it is how a human finds the
  requirement in its source, and it *does* match inside that same document, but
  it is refused as a cross-document join.

`reference_type` alone cannot separate the two — `REQUIREMENT_KEY` is a Jira or
DOORS key, which *is* an enterprise identity, while a markdown heading is not. The
`system` is what distinguishes them: a document label is typed `OTHER`, and an
identifier naming a managed system is inferred `ENTERPRISE`.

---

## Ontology Structure

### Core Layers

| Layer | Purpose | Key Classes |
|-------|---------|-------------|
| **Specification** | Document boundary | `RequirementSpecification`, `Assumption`, `GlossaryTerm` |
| **Business Context** | The "why" and "what" | `Stakeholder`, `BusinessGoal`, `BusinessCapability`, `BusinessProcess`, `DomainConcept`, `BusinessRule` |
| **Requirements** | The core hierarchy | `Requirement` (abstract), `BusinessRequirement`, `FunctionalRequirement`, `NonFunctionalRequirement`, `ConstraintRequirement` |
| **Use Cases** | Interaction modeling | `UseCase`, `Actor`, `UseCaseStep`, `AlternativeFlow` |
| **Quality Attributes** | NFR scenarios | `QualityScenario` |
| **Enterprise Structure** | Organizational constructs | `EnterpriseConstruct` (abstract), `Product`, `SubProduct`, `System`, `Application` |
| **Platform Model** | Platform-specific (optional) | `Platform`, `PlatformContract`, `PlatformExtensibilityRequirement`, `PlatformMultiTenancyRequirement`, `PlatformCompatibilityRequirement` |
| **Architecture Rationale** | How the design is shaped and why | `ArchitectureStyle` (shape), `ArchitecturePattern` (named solution), `DesignTechnique` (mechanism delivering a quality attribute), `EngineeringConvention` (organisation's own rule) |
| **Architecture Decisions** | Recorded choices | `ArchitectureDecision` (ADR), `RequirementRealization` (REQ → ARC join) |

---

## Enterprise Structure: Topology-Aware Modeling

### The Four Base Constructs

| Construct | Owner | Concern | Lifecycle Driver |
|-----------|-------|---------|------------------|
| **Product** | Product Manager | Market fit, revenue, positioning | Market viability |
| **SubProduct** | Product Manager | Feature scoping, tiering, pricing | Segment strategy |
| **System** | Enterprise/System Architect | Capability boundaries, integration, quality | Architectural fitness |
| **Application** | Engineering Lead | Code, deployment, runtime, ops | Technical debt, platform shifts |

### The Optional Platform Construct

**Platform** is a **topology option**, not a mandatory layer. It's instantiated only when:
- The enterprise offers a foundation that other products/systems build on
- There's a need to model extensibility, multi-tenancy, or contract stability
- Products and Systems have a "builds on" relationship (not just "uses")

When Platform is present:
- `Product.extends_platform` → references the Platform
- `System.is_part_of_platform` → references the Platform
- `Application.consumes_platform_contracts` → references PlatformContracts
- Platform-specific requirement types become applicable

When Platform is absent:
- All platform-related fields remain empty
- Standard requirement types are sufficient
- The ontology behaves like a traditional Product-System-Application model

### Relationship Types

| Relationship | Meaning | Topology |
|--------------|---------|----------|
| `Product.uses → System` | Product depends on System for capability | Both |
| `Product.extends_platform → Platform` | Product builds on Platform | Platform only |
| `System.is_part_of_platform → Platform` | System is a platform component | Platform only |
| `Application.consumes_platform_contracts → PlatformContract` | App uses platform APIs/SDKs | Platform only |
| `System.comprises → Application` | System is implemented by Apps | Both |
| `SubProduct.scopes → Product.requirements` | SubProduct selects requirement subset | Both |

---

## Requirement Types

### Standard Requirements (Always Applicable)

| Type | Altitude | Purpose |
|------|----------|---------|
| `BusinessRequirement` | Product | Strategic need, with justification & impact |
| `FunctionalRequirement` | System/Application | System behavior, with pre/postconditions |
| `NonFunctionalRequirement` | System | Quality attributes (ISO 25010 aligned) |
| `ConstraintRequirement` | Any | Hard limits (regulatory, technical, contractual) |

### Platform-Specific Requirements (Only When Platform Exists)

| Type | Purpose |
|------|---------|
| `PlatformExtensibilityRequirement` | Platform's extensibility surface (APIs, SDKs, plugins) |
| `PlatformMultiTenancyRequirement` | Tenant isolation, data segregation, resource sharing |
| `PlatformCompatibilityRequirement` | Backward compatibility, contract stability, deprecation policy |

---

## Usage Examples

### Example 1: Standalone Topology (Non-Platform Enterprise)

A retail company with an online store.

```yaml
RequirementSpecification:
  id: "SPEC-001"
  name: "Online Store Requirements"
  topology_type: STANDALONE
  
  products:
    - id: "PROD-001"
      name: "Online Store"
      value_proposition: "Direct-to-consumer retail channel"
      
  systems:
    - id: "SYS-001"
      name: "Order Management System"
      capability_boundary:
        - "Order Processing"
        - "Inventory Management"
        
  applications:
    - id: "APP-001"
      name: "order-service"
      implements_system: "SYS-001"
      tech_stack: ["Go", "PostgreSQL"]
      
  platforms: []  # No platform in this topology
  
  requirements:
    - id: "BR-001"
      name: "Increase conversion rate"
      requirement_type: BUSINESS
      binds_to_product: "PROD-001"
      
    - id: "FR-001"
      name: "System shall process orders within 2 seconds"
      requirement_type: FUNCTIONAL
      binds_to_system: "SYS-001"
      derives_from: "BR-001"
```

### Example 2: Platform Topology (Platform-Centric Enterprise)

A company offering a payment platform (like Stripe).

```yaml
RequirementSpecification:
  id: "SPEC-002"
  name: "Payment Platform Requirements"
  topology_type: PLATFORM_CENTRIC
  
  platforms:
    - id: "PLAT-001"
      name: "Payment Platform"
      value_proposition: "Payment infrastructure as a service"
      contracts:
        - id: "CONTRACT-001"
          name: "Payment API v2"
          contract_type: API
          stability_level: stable
          
  products:
    - id: "PROD-002"
      name: "Payment Gateway"
      extends_platform: "PLAT-001"
      
  systems:
    - id: "SYS-002"
      name: "Transaction Processing System"
      is_part_of_platform: "PLAT-001"
      
  applications:
    - id: "APP-002"
      name: "transaction-service"
      implements_system: "SYS-002"
      consumes_platform_contracts:
        - "CONTRACT-001"
        
  requirements:
    - id: "PER-001"
      name: "Platform shall expose REST API for payments"
      requirement_type: NON_FUNCTIONAL
      binds_to_platform: "PLAT-001"
      # Platform-specific requirement
      extends: PlatformExtensibilityRequirement
      target_platform: "PLAT-001"
      extensibility_mechanism: API
      
    - id: "PMTR-001"
      name: "Platform shall isolate tenant data"
      requirement_type: NON_FUNCTIONAL
      binds_to_platform: "PLAT-001"
      extends: PlatformMultiTenancyRequirement
      isolation_level: logical isolation
      data_segregation_model: shared database with tenant_id column
```

---

## Extension Points

This base ontology is **extended** by domain packs under `domains/`. One is built:

- **`domains/payment_processing.yaml`** — the parties (Merchant, Cardholder,
  Acquirer, Issuer, PaymentGateway), the instruments (PaymentInstrument → Card,
  BankAccount, Wallet), the operations (`Payment` plus the abstract
  `PaymentOperation` and its subclasses — Authorization, Capture, Refund,
  Chargeback, Dispute) and the lifecycle state machine as a closed enum.
  Settlement, Payout, Mandate and Reconciliation are deliberately *not*
  `PaymentOperation`s: they act over a batch, an account or an authority rather
  than against one payment, and each carries its own link to what it concerns.

A pack **inherits** from this base — it is a *schema*, not a set of instances — and adds:

- Domain classes subclassing `DomainConcept`, so instances are ordinary graph nodes
- Domain-specific `BusinessRule` references and slot ranges into the base layer
- Domain-specific enums (lifecycle states, methods, schemes, reason categories)
- An optional `worked_example` annotation that replaces the subject-neutral example
  in the extraction prompt, keeping domain guidance **in the pack** rather than
  hard-coded in `agents/`

> **A note on `DomainConcept`.** Without a pack in force, extraction collapses every
> business entity into the `DomainConcept` catch-all — which is why frameworks like
> `Spring Boot` were previously filed as domain concepts and ~43% of entities carried
> no ontology class at all. That is the placeholder a pack is meant to replace, not a
> category to target. Selecting a pack is what makes a mis-classification visible.

---

## Alignment with Standards

| Standard | Alignment |
|----------|-----------|
| **ISO/IEC 25010:2023** | Quality attribute categories (10 categories from Functional Suitability to Regulatory Compliance) |
| **BABOK (Business Analysis)** | Business goals, capabilities, processes, stakeholders |
| **TOGAF (Enterprise Architecture)** | Enterprise constructs (Product, System, Application), architecture layers |
| **ATAM (Architecture Tradeoff Analysis)** | Quality scenario pattern (stimulus → environment → response → measure) |
| **LinkML** | Schema representation language |

---

## File Structure

```
ontology/
├── sea_common.yaml            # Common layer — identity and provenance
├── enterprise_structure.yaml  # Enterprise layer — organizational constructs
├── requirements_base.yaml     # Requirements layer — REQ-G
├── architecture_base.yaml     # Architecture layer — ARC-G
├── domains/                   # Domain packs — the vocabulary of the SUBJECT MATTER
│   └── payment_processing.yaml
└── README.md                  # This documentation
```

### The four base layers and the domain packs are different kinds of thing

The four base layers above are **fixed**: they are present for every Initiative,
they describe the *artifact* (what a requirement is, how architecture is
described), and they must stay domain-neutral. They are loaded once and in a
strict one-way chain.

A **domain pack** under `domains/` is **conditional and swappable**: it describes
the *subject matter* (Payment, PAN, Merchant, Chargeback), it is selected per
Initiative, and it is an overlay that imports `requirements_base` to subclass
`DomainConcept`. It is deliberately **not** a fifth chain layer.

```
sea_common → enterprise_structure → requirements_base → architecture_base
                                            ▲
                                            └── domains/payment_processing.yaml
```

A pack is loaded by `core.ontology.load_domain_pack`, discovered by
`discover_domain_packs`, selected at `/ingest` (or via `SEA_DOMAIN_PACK`), and
recorded in every assertion's provenance as `stem@version` so a vocabulary change
is never mistaken for a content change. **Selecting no pack is a supported
state** — the base layers alone are a complete vocabulary for the artifact.

Adding a domain requires **no code changes**: drop a YAML file in `domains/` that
imports `requirements_base`, subclasses `DomainConcept`, and declares a `version`
and a unique `id`. See [`docs/domain-ontology-integration.md`](../docs/domain-ontology-integration.md).

---

## Layer Architecture (Import Direction)

The ontology is split into layers with a **strict one-way import rule**. This
keeps each layer reusable and prevents circular dependencies.

```
sea_common.yaml                    (identity and provenance; no imports but linkml:types)
        ▲
        │  imports
enterprise_structure.yaml          (Product, System, Application, Platform)
        ▲
        │  imports
requirements_base.yaml             (may reference Product/System/Application/Platform)
        ▲
        │  imports
governance_base.yaml               (may reference requirements)
        ▲
        │  imports
architecture_base.yaml             (may reference enterprise + requirements)
```

| Layer | Contains | May reference |
|-------|----------|---------------|
| `sea_common.yaml` | Identity, Provenance, ExternalReference + their enums | nothing external |
| `enterprise_structure.yaml` | Product, SubProduct, System, Application, Platform, PlatformContract + topology/contract/lifecycle enums | sea_common |
| `requirements_base.yaml` | Requirements hierarchy, business context, quality model | enterprise_structure |
| `governance_base.yaml` | Strategy, Principle, Policy, Control, Risk, StandardClause + their enums | enterprise_structure + requirements_base |
| `architecture_base.yaml` | C4-aligned elements, patterns, deployments, RequirementRealization | enterprise_structure + requirements_base |

**Governance is a peer of architecture, not a layer beneath it.** A policy mandates
a requirement (`Policy.mandates`), and a control discharges a standard clause
(`Control.satisfies`) — but an architecture element does not yet reference them, so
`architecture_base` does not import `governance_base`. When conformance slots land
that import is added, and the prompt-budget consequence has to be handled then:
class names are scoped by `visible_layer_keys`, so a layer architecture imports is a
layer every architecture prompt pays for (YB-007).

`governance_base.yaml` declares the **classes**, not ACME's content. The enterprise's
actual policies, standards and controls are graph data in the workspace-level shared
scope, the same split `catalogues/architecture_patterns.yaml` argues for.

### Why the split exists

Enterprise constructs (Product / System / Application / Platform) are needed by
**both** the Requirements Graph (REQ-G) and the Architecture Graph (ARC-G).
Defining them once in a base layer means both graphs speak the same vocabulary —
so the Semantic Auditor can compare them without mapping translation.

### The circular-dependency boundary

Some links cross the boundary in the "wrong" direction — e.g. `System` wanting
to point at a `BusinessCapability`, or `SubProduct` wanting to point at a
`Requirement` (both live in the requirements layer). Declaring those inside
`enterprise_structure.yaml` would create a cycle.

The rule is: **declare the link in the higher layer, never the lower one.**

Those links are therefore expressed as explicit *binding classes* in
`requirements_base.yaml`:

| Binding class | Links | Purpose |
|---------------|-------|---------|
| `SubProductScope` | SubProduct → Requirement | Which requirements a sub-product includes/excludes |
| `SystemCapabilityBinding` | System → BusinessCapability | Which capabilities a system is accountable for |

Directional slots on `Requirement` (`binds_to_product`, `binds_to_system`,
`binds_to_application`, `binds_to_platform`, `traces_to_capabilities`) cover the
reverse direction, so full traceability is preserved either way.

---

## Validation

The ontology is written in **LinkML** and can be validated using the LinkML framework:

```bash
# Install LinkML
pip install linkml

# Validate the schema
linkml-validate ontology/requirements_base.yaml

# Generate documentation
genmarkdown ontology/requirements_base.yaml > ontology/docs.md

# Generate JSON Schema
genjsonschema ontology/requirements_base.yaml > ontology/schema.json
```

---

## License

MIT
