# SEA Base Requirements Ontology

**Version:** 0.2.0  
**Last Updated:** September 19, 2026

A generic, domain-agnostic ontology for structured business requirement analysis. This ontology supports both **standalone** and **platform-centric** enterprise topologies through a topology-aware design.

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

This base ontology is designed to be **extended** by domain-specific ontologies:

- **Payment Processing Ontology:** Adds domain concepts (Transaction, Merchant, Payout), specialized business rules, payment-specific constraints
- **Healthcare Ontology:** Adds HIPAA-specific constraint types, clinical workflow patterns
- **E-commerce Ontology:** Adds product catalog concepts, shopping cart patterns, fulfillment workflows

Domain ontologies **inherit** from this base and add:
- Domain-specific `DomainConcept` instances
- Domain-specific `BusinessRule` types
- Domain-specific enums and constraints
- Domain-specific requirement patterns

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
├── requirements_base.yaml    # This file — the base ontology
└── README.md                  # This documentation
```

Future extensions:
```
ontology/
├── requirements_base.yaml
├── domains/
│   ├── payment_processing.yaml
│   ├── healthcare.yaml
│   └── ecommerce.yaml
└── README.md
```

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
