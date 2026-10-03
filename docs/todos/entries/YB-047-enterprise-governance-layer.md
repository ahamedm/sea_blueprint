---
id: YB-047
legacy: null
title: "Enterprise governance layer — Policy, Principle, Control, Risk, and Standard clauses"
status: in-progress
priority: critical
area: "`ontology/requirements_base.yaml`, `ontology/enterprise_structure.yaml`, extraction grounding, shared workspace scope"
created: 2026-09-26
updated: 2026-09-26
record: null
superseded_by: []
related: ["YB-011", "YB-042", "YB-044", "YB-008"]
blocks: []
blocked_by: []
---

# YB-047 — Enterprise governance layer

> **In progress.** This file is the source of truth for this item; `TODO.md` is generated from it.
> **Design:** [`docs/design/enterprise-governance-layer.md`](../../design/enterprise-governance-layer.md)

### First slice landed (2026-09-26)

`ontology/governance_base.yaml` is a **fifth base layer**, placed above
`requirements_base` and beside `architecture_base`:

| Added | What |
|---|---|
| `GovernanceInstrument` (abstract) | id, name, owner, `GovernanceStatus`, effective window, version — the shared shape so a query can range over "everything the enterprise has decided" |
| `Strategy` | horizon, drivers, the capabilities it directs |
| `Principle` | rationale, implications, the policies that operationalise it |
| `Policy` | domain, statement, scope, and the two joins that matter: `mandates` → `Requirement`, `enforced_by` → `Control` |
| `Control` | nature (preventive/detective/corrective), domain, verification method, `satisfies` → `StandardClause`, `mitigates` → `Risk` |
| `Risk` | level, likelihood, impact, `mitigated_by` → `Control` |
| `StandardClause` | clause id, text, `level` (must/should/may), `part_of` → `Standard` |
| 6 enums, 2 subsets | `GovernanceStatus`, `PolicyDomain`, `ControlNature`, `ControlDomain`, `RiskLevel`, `ClauseLevel`; `Governance`, `Compliance` |

`Control` is **first-class**, not a `ConstraintRequirement` variant: the requirement
states the obligation, the control discharges it, and collapsing them makes "which
control satisfies this clause?" unanswerable — the main audit question.

The external referents (`Regulation`, `Standard`) deliberately stayed in
`requirements_base`: they are named things *outside* the system, while this layer is
what ACME itself has decided. Governance imports requirements, so
`Control.satisfies → StandardClause → Standard` resolves.

**A real constraint surfaced.** Adding the layer broke
`tests/test_prompt_budget.py` — scaffolding 7,376 chars against a 7,266-char document
(1.02:1, YB-007's failure mode). The cause was that `_collect_ontology_names` injected
**every** class in the model, unlike the predicate vocabulary, which is already scoped
by `visible_layer_keys`. That is now fixed: class and enum names are scoped to the
layers the entry schema imports, so a layer no profile imports costs no prompt. The
same fix is why `architecture_base` does **not** import `governance_base` yet — it has
no conformance slot referencing it, and an unused import would tax every architecture
prompt. Adding that import is a deliberate step with a budget plan, not a free one.

### Decision (2026-09-26) — both conformance targets are kept

An architecture element answers to **both** `StandardClause` and `Control`, and both
stay. They are different claims:

| Claim | The gap it finds |
|---|---|
| `conforms_to → StandardClause` — conformance to the external standard | *which clause has no control?* — a governance gap |
| `realizes → Control` — implementation of the enterprise's own decision | *which control has no implementation?* — a deployment gap |

The second only exists because the two are separate, which is the reason not to
collapse them into one "compliance" relation.

**Wiring is deferred, deliberately.** Adding the architecture-side slots requires
`architecture_base` to import `governance_base`, and that import is what put the
architecture scaffolding over the document in the first slice (1.02:1, YB-007). The
slot shape and the prompt-budget handling should be settled together, so this waits
rather than being half-done. Revisit later.

### How ACME's instruments get in — curated, not extracted

`governance_base.yaml` is a **schema**; ACME's policies are **individuals** typed by
it. That is not the `payment_processing.yaml` case: a domain pack adds *classes* the
base lacks (`Payment`, `Cardholder`), whereas `Policy`/`Control`/`Risk`/`StandardClause`
already exist, so there is a node to create and no class to declare.

The right precedent is the **catalogue** (`ontology/catalogues/architecture_patterns.yaml`
— instances of a class the ontology declares), with one gap to close: today a catalogue
only reaches the *prompt*, and the model re-emits. For governance that is backwards.
Policies are authoritative structured artefacts, and recovering them through a
sampling model would import the non-determinism the platform exists to remove.

So the next slice is a path the codebase does not have: **a direct load from a curated
instance file into the graph, no model in the loop** — same `add_assertion` fold, same
review gate, provenance source `IMPORTED` — `SOURCE_IMPORTED` already exists at
`core/knowledge/model.py:97`, so nothing new is needed on the provenance axis.

Extraction then does what it is good at: **linking**, not authoring — which requirement
a policy mandates, which architecture element realises which control. The instrument
itself stays curated and reviewable.

A pack is only warranted if ACME needs classes the base lacks (a `ControlFramework`, a
`DataClassificationTier`). Note the axis differs from a domain pack — that one is
per-Initiative subject matter, this would be per-enterprise decisions — so it does not
belong under `ontology/domains/`.

### An enterprise brief is the missing context input

A one-pager on ACME handed to the agents is worth having, and it splits into two jobs:
**authoring** the instruments (import from a register if one exists, extract + review if
not) and **linking** them (always extract — those claims live in other documents).

The stronger use is as **grounding**: today an agent gets the ontology plus an optional
domain pack, and nothing describing the enterprise. `input_data` accepts `document`,
`document_type`, `domain`, `max_chars`, `force_text_parsing` — no enterprise context.
A brief is the third input, sibling to a domain pack on a different axis (per-enterprise
rather than per-subject-matter, prose rather than classes), and it stops the extractor
inventing synonyms for the house vocabulary.

**Caveat to carry:** `completeness` is per run and per document, not per domain. A
clean one-pager run reports `COMPLETE` while 199 policies remain unextracted. A graph
bootstrapped from a brief must not be read as the register.

Next: the curated-load path above; the enterprise brief as a context input; the
shared-scope placement for those instances; then the deferred conformance slots and a
governance linking profile.

**A demand signal, and a reason to state the absence (2026-10-03).** "Which principles does this
design satisfy or contradict?" is currently unanswerable *and unstated*: 0 governance nodes exist
in either live scope, nothing imports `governance_base`, and no registry can route the question.
[YB-066](YB-066-natural-language-enquiry.md) plans to seed it as a DECLARED entry returning
`substrate_absent` and pointing here, and to log the asks — so if that question is common, the
import and the curated load above get promoted on evidence rather than on a guess. It also gives
this item's premise a test: a `substrate_absent` answer that names this item is the honest state,
and an empty result presented as "no violations" would be the vacuous-answer failure the platform
is built to avoid.

### The three ontologies, kept apart

| | What it is | Where it lives |
|---|---|---|
| **SEA meta-ontology** | the *kinds* — Container, Requirement, Regulation, Standard | `ontology/*.yaml`, ships with the platform |
| **ACME enterprise ontology** | the *instances* — ACME's policies, standards, principles, org, systems | graph data, in a shared scope |
| **Domain ontology** | the subject matter — payments | `ontology/domains/*.yaml` (YB-011) |

"Writing an ACME ontology" is therefore not authoring a file. It is **curating
ACME's governance as graph data** the platform can reason over — which requires the
meta-ontology to have somewhere to put it. That is what this item is about.

### What already exists

- `Regulation` (`requirements_base.yaml:866`) — law/directive with `jurisdiction`,
  `version`; explicitly a `DomainConcept`, not a requirement.
- `Standard` (`:895`) — conformance target with `issuing_body`, `version`. The
  description already anticipates the organisation's **own architecture board** as an
  issuing body.
- `BusinessRule` (`:971`), `BusinessGoal` (`:677`), `BusinessCapability` (`:713`).
- `ConstraintRequirement` with `ConstraintType` = REGULATORY / TECHNICAL / BUSINESS /
  CONTRACTUAL / STANDARD (`:259`), and `governed_by_rules` (`:1074`).
- Architecture side: `mandated_by` on styles, techniques, conventions and patterns
  (`architecture_base.yaml:1362,1406,1528,1655`), `contractual_obligation` (`:865`).
- `PlatformContract` (`enterprise_structure.yaml:406`).

### What is missing — and why it matters

| Missing | Consequence today |
|---|---|
| **Policy** | no way to say "ACME's data-residency policy governs these systems" |
| **Principle** | "zero trust", "least privilege", "no shared databases" have no home, so they cannot be applied or audited consistently |
| **Strategy** | `BusinessGoal` is a goal; nothing expresses a multi-year technology or sourcing direction that initiatives answer to |
| **Control** | a `ConstraintRequirement` is the *requirement*; the control that satisfies it is a different thing and has no node |
| **Risk** | nothing to link a control to, so "which risk does this mitigate?" is unanswerable |
| **Standard *clauses*** | `Standard` carries a name, body and version — **not its clauses**. You can say "conforms to ISO/IEC 27001" but not "satisfies A.8.3.1" |
| **Applicability / scope rules** | a policy that means "all CDE systems" must be enumerated by hand |
| **OrganisationalUnit / Team** | YB-008 is **parked**; `SoftwareSystem.managed_by` is free text pending it |

The sharpest of these is the third-from-last. An audit asks for a *clause*, and today
there is nowhere to record that an architecture satisfies one.

### The value — what becomes decidable

Each of these is a question the platform cannot answer now and could with this layer:

1. **Grounding.** "PCI-DSS", "zero trust", "least privilege" currently land in
   `Concept` unless the model happens to choose `Regulation`/`Standard`. The ontology
   comments at `:855-858` state the cost precisely: a coverage census then "reports
   the opposite of the truth", and "a legal obligation vanishes into the same bucket
   as a vocabulary slip". A closed governance vocabulary fixes it measurably, the way
   the domain pack did for payments (YB-011).
2. **Conformance as a query, not an assertion.** `mandated_by` and `conforms_to` exist
   with nothing authoritative to point at. *"Which systems in the CDE use a technique
   that is not on ACME's approved list?"*
3. **Scope derived, not enumerated.** *"Which systems are in scope of the
   data-residency policy?"* — computed from scope rules plus existing signals
   (`DeploymentNode.network_zone`, `shared_across_enterprise`, and YB-044's instance
   sharing scope), rather than maintained as a list.
4. **The justification chain, walkable end to end.** Today the chain starts at
   Initiative → Requirement → Architecture. With the enterprise layer it extends
   upward: **Strategy → Principle → Policy → Standard/Control → Requirement →
   Architecture → Evidence**. That is the difference between an architecture that is
   *described* and one that is *justified* — and it is what an auditor actually asks
   for ("why does this exist?").
5. **Change impact — the living system.** When a policy is revised or a regulation
   lands, *"what is affected?"* is a query over 200 products, not a manual sweep.
6. **Cross-product coherence.** One enterprise vocabulary instead of 200 dialects —
   the "shared reference frame" the domain layer exists for, and the content of the
   shared scope in [`workspace-structure.md`](../../design/workspace-structure.md) §11.
7. **Consistency of human judgement.** 50 architects apply the same principles
   because the principles are reviewable nodes, not tribal knowledge.
8. **Audit evidence.** *"Show every system in scope of standard X, the control
   satisfying clause Y, and the architecture implementing it."*

### What this is not

- **Not a document repository.** A glossary of terms adds little; the value is in
  **relations and constraints** — a policy *governs* a scope, a standard *has* clauses,
  a control *mitigates* a risk, a requirement is *mandated by* a policy. Structure is
  what makes it reasoned over rather than read.
- **Not the domain ontology.** ACME's policies are not payment subject matter; both
  are needed and merging them loses the distinction between "what the business is
  about" and "what the business has decided".

### Shape, and where it lives

- Extend `requirements_base.yaml` / `enterprise_structure.yaml` with `Policy`,
  `Principle`, `Strategy`, `Control`, `Risk`, and give `Standard` a `Clause`
  (or `Section`) part with an identifier, so conformance can be claimed per clause.
- ACME's **instances** are curated into the workspace-level **shared scope**
  (YB-042 §11) — enterprise-wide, referenced by every product, promoted only through a
  `promotion_request` (YB-046).
- Grounding: a governance vocabulary the extractor is given, so policies and standards
  are classified rather than invented.

### Acceptance

- A named internal standard can carry clauses, and an architecture can claim
  conformance to a specific clause with evidence.
- A policy can state its scope once, and the affected systems are derived.
- "Which systems are non-conformant to policy P?" is answerable as a query.
- A revised policy yields an impact list without re-reading any document.
- Enterprise-scope facts live in the shared scope, not duplicated per product.
