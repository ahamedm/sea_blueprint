---
id: YB-055
legacy: null
title: "Domain data elements and their exchanges — the architecture aspect with no model"
status: in-progress
priority: high
area: "`ontology/architecture_base.yaml` (a `DataElement`/`DataExchange` class), `agents/*/passes.py` (a pass or rules), `agents/extraction/validators.py` (guards), `core/knowledge/ingest.py` (the seam)"
created: 2026-09-28
updated: 2026-10-01
design: docs/design/data-element-ontology.md
record: null
superseded_by: []
related: [YB-047, YB-008, YB-053, YB-004, YB-007, YB-051, ADR-0029, ADR-0030, ADR-0032]
blocks: []
blocked_by: []
---

# YB-055 — Domain data elements and their exchanges

> **Open. Filed from the aspect audit that produced
> [ADR-0032](../../decisions/ADR-0032-aspect-guards-container-component-integration.md).**
> The audit asked, for each core aspect of an architecture, what shapes it: the
> ontology, the pass schema, the prompt, the validators, or refusal at the write
> boundary. Four of the five aspects had an answer. This one had none, and it is
> the only one of the five where that is true — so it is written down as a design
> decision to make rather than a defect to fix. The other four were fixed.

### What was measured

The five aspects an architecture is made of, and what holds each one in shape:

| Aspect | Ontology class | Pass rule | Deterministic guard |
|---|---|---|---|
| Container | `Container`, `DataStore` | yes | `check_containment`, `check_enum_membership` |
| Component | `Component` | yes | `check_containment_kinds` (added in ADR-0032) |
| Design techniques | `DesignTechnique` | yes, closed-list | three design validators |
| Integration to external systems | `Connection`, `Interface` | yes, with a negative list | `check_connection_endpoints` (added in ADR-0032) |
| **Domain data elements / exchanges** | **none** | **none** | **none** |

Searched across all five ontology layers for `DataElement`, `DataEntity`,
`Exchange`, `DataFlow`, `payload`, `data flow`: **no such class exists.** A domain
data element is not a thing this graph can hold.

### What represents it today, and why that is not enough

Data exchange is representable only as unconstrained free text on slots that
belong to other concepts:

| Slot | Class | Type | Read by anything? |
|---|---|---|---|
| `payload_contract` | `Interface` | `string` — "Data contract exchanged (schema name, DTO, event name)" | **nothing** |
| `description_text` | `Connection` | `string` — "Human description of what flows across this connection" | only the C4 view label (`app/viewpoints/c4.py:327`) |
| `carries_sensitive_data` | `Connection` | `boolean` | **nothing** |
| `holds_sensitive_data`, `data_classification` | `DataStore` | `boolean`, `string` | **nothing** |

`payload_contract` and `carries_sensitive_data` are verified unread by a repo-wide
grep for their names in `core/`, `agents/` and `app/`. They are declared and
unreachable: no pass emits them, no ingest path stores them, no validator checks
them. (ADR-0032 wires `carries_sensitive_data` and `Connection.failure_handling`
at the ingest seam, which is the plumbing half; the modelling half is this item.)

### Why the absence has consequences

1. **A question the platform exists to answer is unanswerable.** "What data does
   the Settlement container exchange with Elavon, and what classification is it?"
   has no graph representation, so no view, no gap report and no auditor pass can
   answer it. This is the same class of gap `DesignTechnique` was introduced to
   close for quality attributes ([architecture_base.yaml:1454](../../../ontology/architecture_base.yaml)),
   one layer over.
2. **PCI-DSS scoping is the test case and it needs this.** The MVP domain is card
   payments; the reliability measurement ran against `payment_platform_arch.md`.
   Cardholder-data flow is exactly a data-element-and-exchange question, and today
   it can only be answered from `DataStore.holds_sensitive_data` — which is a
   property of where data rests, not of where it moves.
3. **Span anchoring has no vocabulary to anchor to.** The reliability brainstorm's
   §3.6 requires every emitted value to be a substring of the source chunk, and
   §3.17 wants a completeness contract per document type ("an architecture document
   must state its request path"). Both presuppose a closed vocabulary for the
   *payload* of a connection. A free-text `payload_contract` cannot be validated
   for presence, coverage or invention — the category-word defect of
   [YB-053](YB-053-category-elements-and-duplicate-system.md) applies to data names
   and nothing currently stops it.
4. **The second half of an integration is missing.** ADR-0032 makes connection
   *endpoints* checkable. A connection without a payload is half a fact: the
   auditor can see that two elements talk and not what crosses between them, which
   is precisely the part a security or data-residency review turns on.

### The decision to make

Not a bug fix — a modelling choice with real cost on both sides, which is why it
is an entry and not a patch. **[The four-altitude analysis is in
`docs/design/data-element-ontology.md`](../../design/data-element-ontology.md)** —
what exists at each layer, what is missing, the import-direction constraints on
where each link must be declared, and a recommended sequencing. Its findings that
change the shape of this decision:

- **The business half already exists and is a dead end.** `DomainConcept`,
  `ConceptAttribute` and `ConceptRelationship` are declared in
  `requirements_base`; `ConceptAttribute` and `ConceptRelationship` have
  **0 instances**, because their slots are `inlined_as_list` and the extraction
  contract is flat. `DomainConcept` looks populated only because it is the
  schema's **default** `ontology_class`.
- **The governance layer has never produced a fact.** `Policy`, `Control`,
  `StandardClause`, `Principle`, `Risk`, `Strategy`: 0 nodes each. Any design that
  hangs data classification off governance is building on an empty layer.
- **`architecture_base` does not import `governance_base`** ([YB-047](YB-047-enterprise-governance-layer.md)),
  so classification can arrive at architecture only by deterministic resolution
  through `DomainConcept --specialises--> DataEntity --classified_as--> …`, not by
  a slot — which avoids the prompt-budget cost ([YB-007](YB-007-prompt-scaffolding-instruction-dilution.md))
  that importing the layer would impose on every architecture prompt.
- **Ownership has no range.** A data entity's steward cannot be expressed until
  [YB-008](YB-008-team-organisational-unit.md) lands or a binding class is declared
  in `requirements_base`.

**Option A — introduce the vocabulary.** A `DataElement` class (a domain-meaningful
unit: "Cardholder Data", "Settlement Report", "Payment Instruction") plus an
exchange that binds (connection or interface) → (data elements), with a
`classification` enum and a `direction`. Cost: a new pass or new rules on the
connections pass, a validator for name grounding, a view, and a completeness
contract. Benefit: closes the last aspect, and makes PCI data-flow review a graph
query. Risk: another collection the model must populate, against
[YB-007](YB-007-prompt-scaffolding-instruction-dilution.md) — every added section
dilutes the ones already there.

**Option B — declare it out of scope, explicitly.** Drop `payload_contract` and
`carries_sensitive_data` from the ontology, and state in the architecture README
that data elements and exchanges are deliberately not modelled — the platform
reasons about structure and integration, not about data. Cost: the PCI question
stays unanswerable. Benefit: the ontology stops claiming a capability the pipeline
does not have, which is the same posture ADR-0032 took toward the dead `tools`
field.

Option B is the honest minimum and should be the fallback if A is not scheduled.
What is **not** acceptable is the current state: slots that read as capability,
that nothing populates and nothing checks.

### What has been prototyped so far (2026-10-01)

**`ConceptAttribute` is reachable.** Not a `DataElement`, and not acceptance for
Option A — the cheapest slice of the same absence, and the one that needed no new
class. The logical data model already had a class and a vocabulary
(`name`, `data_type`, `is_required`, `constraints`); what it did not have was a
shape the pipeline could emit, so it had **0 instances in every graph**.

What changed:

| Layer | Change |
|---|---|
| `ontology/requirements_base.yaml` | `ConceptAttribute.concept -> DomainConcept` (the flat back-link; the nested `key_attributes` stays for hand-written models); new enum `ConceptAttributeDataType` — ten LOGICAL types, no physical ones |
| `agents/knowledge_extraction/agent.py` | `ExtractedConceptAttribute` + `ExtractedEntity.attributes`. Nested on the owning entity, so the owner is structural and cannot be got wrong. Text fallback carries it too. The worked example no longer teaches an unowned `ConceptAttribute` triple object |
| `core/knowledge/ingest.py` | Materialises the node, qualified by its owner (`Customer.Email`), plus `attribute_of`, `data_type`, `is_required` (true-only) and `constraint` facts. Created with `add_node`, **not** `_resolve`: `_resolve` is kind-blind, so a document naming a concept `Customer.Email` would have handed that concept back as the field |
| `agents/extraction/validators.py` | `check_concept_attributes` — unowned, unnamed, duplicated, unanchored, physically-typed, or on a non-concept. Vocabulary read from the ontology; a domain pack's own subclasses are not flagged |
| `tests/test_concept_attributes.py` | 16 tests, in the `ingest-seams` area |

Measured, and why the shape is nested rather than a new top-level key: the fields
ride inside `entities`, so ADR-0030's per-key accounting already covers them, and
the prompt cost is **+1,350 characters of scaffolding (~337 tokens) per
requirements chunk** — the worked example grew 292, the instruction block 1,058.
That is a real cost against [YB-007](YB-007-prompt-scaffolding-instruction-dilution.md)
and it is the kind of number that should be re-measured rather than assumed when
the next section is added.

Findings get their own `attribute_findings` key rather than joining
`contract_violations`, because that list is read by the extraction harness as a
ratio over triples ("clause-shaped nodes < 20%") and a field finding is not a
clause-shaped node — counting them together would move a calibrated metric by an
amount unrelated to what it measures.

**Still open, and not implied by the above:** `DataElement`/`DataExchange` (the
data that moves), the derived-classification walk, the physical binding of a
logical entity to a `DataStore`, `DataEntity`/`DataClassification`, and any view
that answers the PCI question. `ConceptRelationship` remains in the same shape
`ConceptAttribute` was in: declared, nested, unpopulatable.

### Acceptance

Either of:

- **A:** a `DataElement` (or equivalent) class in `architecture_base.yaml` with a
  classification enum; a pass or rule set that emits it; a deterministic guard for
  name grounding resolved from the ontology (not hardcoded, per
  `agents/extraction/validators.py`'s single-source-of-truth rule); ingest wiring
  under `INGESTED_OUTPUT_KEYS`, so ADR-0030's consumption accounting covers it;
  and a view or report that answers one real question ("which connections carry
  regulated data, and are they all classified?"). Pinned by a test that a
  data element with no classification is a finding.
- **B:** `payload_contract` and `carries_sensitive_data` removed from
  `architecture_base.yaml`, the scope decision recorded in the architecture
  README, and no dangling slot left claiming the capability.

### The lesson worth keeping

The aspect audit found this in minutes, and only because it asked the same
question of every aspect in turn. An ontology slot with no reader is invisible to
a review that looks at behaviour: nothing errors, no test fails, no run reports
PARTIAL. A field's existence reads as a capability. The check that finds it is
mechanical — for each declared slot, name the code that reads it — and it is worth
running over the whole ontology, not only this aspect.

**Full context:** the aspect audit and the four fixes are in
[ADR-0032](../../decisions/ADR-0032-aspect-guards-container-component-integration.md).
The reliability motivation for anchoring and completeness contracts is
[extraction-reliability-levers.md](../../design/extraction-reliability-levers.md) §3.6 and §3.17.
