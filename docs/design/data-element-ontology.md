# Domain data elements at four ontology altitudes — a brainstorm

> **Brainstorm, not a plan.** Nothing here is committed or estimated. It is the
> analysis behind [YB-055](../todos/entries/YB-055-data-element-design.md), which
> recorded that data elements and their exchanges are the one core aspect of an
> architecture with **no ontology class, no pass rule and no guard at all**. That
> entry asked a yes/no question (model it, or declare it out of scope). This
> document answers the harder follow-up: *if modelled, what does it look like at
> each altitude of the ontology?*
>
> Marked **[M]** where the point rests on a measurement, **[H]** where it is a
> hypothesis to test. Nothing here is built.
>
> Related: [YB-055](../todos/entries/YB-055-data-element-design.md),
> [YB-047](../todos/entries/YB-047-enterprise-governance-layer.md) (the governance
> layer, in progress, and the deferred architecture→governance import),
> [YB-008](../todos/entries/YB-008-team-organisational-unit.md) (ownership),
> [YB-007](../todos/entries/YB-007-prompt-scaffolding-instruction-dilution.md)
> (prompt budget), [ADR-0032](../decisions/ADR-0032-aspect-guards-container-component-integration.md)
> (the aspect audit that found this), and
> [extraction-reliability-levers.md](extraction-reliability-levers.md) §3.6, §3.17.

---

## 1. What was measured

**[M] The business half already exists and is a dead end.** The obvious assumption
— that nothing about data is modelled anywhere — is wrong:

| Layer | What already exists for data | Read or populated by |
|---|---|---|
| `sea_common` | `IdentityScope` (ENTERPRISE/INITIATIVE/DOCUMENT/RUN) | ✅ identity, `core/` |
| `enterprise_structure` | `PlatformContractType.DATA_SCHEMA` — "Shared data model or schema contract" | ❌ nothing |
| `requirements_base` | **`DomainConcept`** + `ConceptAttribute` + `ConceptRelationship` (BusinessContext subset); domain packs subclass `DomainConcept` | ⚠️ `DomainConcept` only, as the extraction **default catch-all** |
| `governance_base` | `PolicyDomain.DATA` ("classification, residency, retention and privacy"), `ControlDomain.DATA` ("data protection controls") | ❌ nothing |
| `architecture_base` | `DataStore.data_classification` (string), `DataStore.holds_sensitive_data` (bool), `Connection.carries_sensitive_data` (bool), `Interface.payload_contract` (string), `Connection.description_text` (string) | ⚠️ `description_text` in the C4 view; the rest nothing |

**[M] `DomainConcept` is the schema's default, which is why it looks populated.**
The extraction entity schema declares `ontology_class` with `default="DomainConcept"`
([knowledge_extraction/agent.py:191](../../agents/knowledge_extraction/agent.py#L191)),
so an entity the model cannot classify lands here. In the live MVP scope
(`data/sea_home_01`, `acme_pillar_01`) there are:

| Kind | Nodes |
|---|---|
| `Concept` (the graph's own last-resort bucket) | 24 |
| **`DomainConcept`** | **3** — `Country of Transaction`, `Transaction Currency`, `Payment Method Preference` |
| `ConceptAttribute` | **0** |
| `ConceptRelationship` | **0** |

The three `DomainConcept` nodes are **routing criteria**, not data entities. So the
business-level data model is declared and effectively unpopulated, and its *richest*
slots — `key_attributes`, `relationships` — have never had an instance.

**[M] Those two slots cannot be populated by the current pipeline.** They are
`inlined_as_list` nested objects (`DomainConcept.key_attributes -> ConceptAttribute`),
and the extraction contract is flat: entities plus `(subject, predicate, object)`
triples. A nested structure has no representation, which is a sufficient explanation
for 0 instances and a hard constraint on any design below.

**[M] The entire governance layer is empty.** `Policy`, `Control`, `StandardClause`,
`Principle`, `Risk`, `Strategy`: **0 nodes each**. Governance classes are declared,
documented, and never extracted. Anything proposed at the governance altitude is
therefore being proposed on top of a layer that has never produced a fact.

**[M] There is no data-entity class anywhere.** A repo-wide search for
`DataEntity`, `DataElement`, `DataExchange`, `DataClassification`, `DataSubjectArea`
across all five layers returns exactly one hit: `architecture_base.yaml:957`, the
string slot `DataStore.data_classification`. The aspect is absent, not partial.

### What the absence costs

1. **PCI-DSS data-flow review is unanswerable.** "Which links carry cardholder data,
   from where to where, and is every one of them classified?" is the question the
   payment test case exists to ask. Today `carries_sensitive_data` is a per-link
   boolean with no entity behind it, so *which* data crosses is prose in
   `Connection.description_text`.
2. **Span anchoring has no vocabulary to anchor to** (reliability §3.6). A closed
   set of data-element names is what makes "every emitted name is a substring of the
   chunk" checkable; free-text `payload_contract` cannot be anchored, covered or
   falsified, and the category-word defect ([YB-053](../todos/entries/YB-053-category-elements-and-duplicate-system.md))
   applies to data names with nothing in the way.
3. **A data-store's classification is architecture-local.** `DataStore.data_classification`
   is a string on the store, so the same entity stored in three systems can carry
   three spellings and no shared obligation — the exact failure mode
   `QualityAttribute`-as-a-node was introduced to fix for quality.
4. **The enterprise cannot ask who owns its data.** There is no slot for a system of
   record or a steward, so "where is the authoritative copy of this?" has no answer
   even in principle.

---

## 2. The precedent to reuse, and the one to avoid

The ontology already contains both shapes this problem could take, and choosing
between them is most of the design.

**Pattern A — one node, several claimants.** `QualityAttribute` is a *single* class
referenced from three altitudes:

```
NonFunctionalRequirement --realizes_attribute-->  QualityAttribute
ArchitectureElement      --satisfies_attribute--> QualityAttribute
DesignTechnique          --satisfies_attribute--> QualityAttribute
```

`ontology/README.md` gives the reason it is a class and not an enum: an enum value
"is not a thing anything can point at", so a technique could only be justified by an
attribute some requirement happened to state, and two NFRs about the same attribute
would look unrelated. Use Pattern A when the thing has **one identity** and several
parties make claims about it.

**Pattern B — two registers, one realisation link.** `Container.is_enterprise_application
-> Application`: the enterprise construct (a registered, owned, long-lived CI) and
the architecture element (a deployable unit with its own lifecycle) are **separate
nodes** joined by a realisation. Use Pattern B when the two altitudes are different
*kinds of record* with different owners and lifetimes.

**[H] Data needs both, at different seams.** The recommendation below is Pattern B
between architecture and everything above it, and Pattern A for the enterprise /
business / governance triangle:

- A wire-level field (`pan`, `amount`, `settlement_date`) is a different kind of
  record from a governed entity (`Cardholder Data`). It has an API version, a
  serialisation, and it dies when the contract changes. → **Pattern B.**
- "Cardholder Data" is one thing that the business defines, the enterprise governs,
  and a policy obliges. Three altitudes, one identity. → **Pattern A.**

Getting this backwards is the expensive mistake: one node for "the data" means a
schema change invalidates an enterprise asset, and two nodes for "Cardholder Data"
means the classification and the obligation attach to different things and stop
agreeing.

---

## 3. The four altitudes

The driver of the split is not tidiness — it is **domain neutrality**. The four base
layers "describe the *artifact* … and must stay domain-neutral" (`ontology/README.md`).
`Cardholder`, `PAN` and `Settlement` are domain-pack content. So the base layers may
declare only the *classes*, and every concrete entity is graph data or pack content.

| Altitude | Layer | Class (new unless noted) | Question it answers | What it must NOT carry |
|---|---|---|---|---|
| **Enterprise** | `enterprise_structure.yaml` | `DataEntity`, `DataSubjectArea` | Which entities does the enterprise recognise, who is the steward, and where is the system of record? | Domain vocabulary (`Cardholder`), wire formats, policy text |
| **Business** | `requirements_base.yaml` | `DomainConcept` **(exists)**, `ConceptAttribute`, `ConceptRelationship` | What does it mean to the business, what are its attributes and relationships? | Deployment, schema, classification obligations |
| **Governance** | `governance_base.yaml` | `DataClassification`, `DataHandlingObligation` | What may be done with it, under whose obligation, evidenced how? | Schema shape, which system holds it |
| **Architecture** | `architecture_base.yaml` | `DataElement`, `DataExchange` | In what shape does it cross the wire, between which elements? | Enterprise ownership, policy, business meaning |

### Enterprise — `DataEntity`

**Why it is not an `EnterpriseConstruct`.** The existing hierarchy is
`EnterpriseConstruct` → Product / SubProduct / System / Application / Platform, and
its defining property is a **lifecycle driver** (market viability, architectural
fitness, technical debt). A data entity is not owned by a product manager and does
not have a release cadence; it is *recognised* and *governed*. **[H] It should be a
new root in the enterprise layer**, mixing in `ExternallyReferenced` (a data entity
is quintessentially a registered thing — a Collibra entry, a CMDB class, a glossary
term) and `Provenanced`, exactly as `ArchitectureElement` does.

Proposed slots, in rough order of value:

| Slot | Range | Why |
|---|---|---|
| `subject_area` | `DataSubjectArea` or `string` | Coarse grouping ("Payments", "Reference Data"). Parallels `BusinessCapability`. **[H] Start as a string; promote to a class only if something needs to point at it.** |
| `authoritative_system` | `System` | The system of record. **This is the single most valuable slot** — it is what makes "where is the truth?" answerable, and it is internal to the layer so it costs no import. |
| `steward` | ⚠️ see below | Accountability. Blocked on the ownership gap. |
| `is_personal_data` | `boolean` | **[H]** Cheap, and drives GDPR-shaped questions. Risk: duplicates governance's classification — see §4. |
| `key_identifiers` | `string` (multivalued) | Natural keys a system of record joins on. Feeds `IdentityScope`. |

**The ownership problem is a real blocker, not a detail.** `steward` wants an
organisational unit, and the enterprise layer has none (`Product`, `System`,
`Application` are all constructs, not orgs). [YB-008](../todos/entries/YB-008-team-organisational-unit.md)
is the open item for that. **[H] Two ways out, and they differ in kind:**

- point `steward` at `Stakeholder` (requirements layer) — but that is a *downward*
  reference from `enterprise_structure`, so by the layer doctrine it must be declared
  in `requirements_base` as a binding class (`DataEntityStewardship`), the way
  `SystemCapabilityBinding` already is;
- wait for YB-008 and range over a real `OrganisationalUnit`.

The first is available now and is the pattern the README prescribes for exactly this
shape of problem. The second is better if ownership is going to be joined on.

### Business — the existing `DomainConcept`, finally connected

The classes exist. What is missing is (a) anything that ingests
`key_attributes`/`relationships`, and (b) **any link to the other three altitudes**.

**[H] `DomainConcept.specialises -> DataEntity`** (or `refines`) is the seam, and it
must be declared in `requirements_base.yaml` because that layer is higher in the
chain — `enterprise_structure` cannot see `requirements_base` without a cycle. This
is precisely the `SubProductScope` / `SystemCapabilityBinding` precedent.

Direction matters and is worth stating: the enterprise declares the *class*, a domain
pack or a requirements run supplies the *specialisation*. So a payments pack's
`Cardholder` is a `DomainConcept` that specialises the enterprise's `DataEntity`
— the base layer never learns the word "Cardholder".

**[H] The nested slots need a flat encoding or they stay at zero.** Three options:

1. Leave them nested and accept they are documentation, not data (current state,
   0 instances).
2. Emit `ConceptAttribute` as its own entity with an `attribute_of` triple — which
   is expressible in the flat contract today, with no schema change. **[H] Cheapest.**
3. Extend the extraction schema to carry nested records — a real cost against YB-007.

Option 2 is the one that fits what the pipeline can already do, and it would have
made the difference between 0 and something measurable.

### Governance — `DataClassification` and the obligation

This is the altitude with the strongest *existing* scaffolding and the weakest
population. `PolicyDomain.DATA` and `ControlDomain.DATA` already exist, so the
vocabulary for "this is a data policy" is declared; what is absent is a thing to
classify.

**[H] `DataClassification` should be a class, not an enum**, and the argument is the
one `QualityAttribute` already won: a classification is pointed at by several
parties (`DataEntity` classified, `Control` protecting, `Policy` obliging), and it
needs an identity so two systems' spellings of "Restricted" are one concern rather
than two. Precedent: the quality census has to reconcile `Availability` with
`High Availability`; data classification has exactly that problem with
`Confidential` / `Restricted` / `CHD` / `PCI`.

Proposed shape:

| Class | Key slots | Notes |
|---|---|---|
| `DataClassification` | `name`, `level` (enum: PUBLIC/INTERNAL/CONFIDENTIAL/RESTRICTED), `regime` (e.g. "PCI-DSS v4.0", "GDPR"), `handling_requirements` | `regime` links it to the existing `Standard`/`StandardClause` |
| `DataHandlingObligation` | `applies_to -> DataEntity`, `obligation` (encrypt/retain/reside/mask), `parameter` (e.g. "AES-256", "7 years", "EU"), `mandated_by -> StandardClause`, `discharged_by -> Control` | The "what must be done" statement, kept apart from the control that does it — the same split `Control` vs `StandardClause` already makes |

And two links declared here, since `governance_base` may reference both lower layers:

- `DataEntity --classified_as--> DataClassification`
- `Control --protects--> DataEntity` (alongside the existing `satisfies`/`mitigates`)

**[H] The cross-level guard this enables** — and it is the most valuable single
artefact in this document. Architecture already carries
`DataStore.data_classification` as a **string**. Once governance has classifications
with identity, a deterministic validator can assert that the string on a store
resolves to a declared `DataClassification`, and that every `DataExchange` carrying a
`RESTRICTED` entity has a protecting control. That is a *governance gap* found
mechanically — the same shape as `check_techniques_are_linked`, and it needs no model
call. It converts the whole four-level structure from documentation into a check.

### Architecture — `DataElement` and `DataExchange`

Two classes, one seam apart:

| Class | Meaning | Key slots |
|---|---|---|
| `DataElement` | A field/shape on the wire or in a store: `pan`, `settlement_date`, `amount` | `name`, `data_type`, `is_masked`, `carries -> DomainConcept`, `part_of -> DataElement` (nested payloads), `example_value` |
| `DataExchange` | An **edge** made first-class: this data, over this connection, in this direction | `connection -> Connection`, `payload -> DataElement` (multivalued), `direction`, `format` (JSON/ISO 8583/CSV/…), `classification -> DataClassification`⛔ |

⛔ **Blocked by the same import decision as YB-047.** `architecture_base` deliberately
does **not** import `governance_base`, so an architecture class cannot point at a
`DataClassification`. The README states the condition for changing that: "when
conformance slots land that import is added, and the prompt-budget consequence has to
be handled then."

**[H] There is a cheaper route that avoids the import entirely, and it should be
preferred first.** `DataExchange --carries--> DomainConcept` is legal today
(architecture imports requirements). Classification then arrives by **transitive
resolution in Python**, not by a slot:

```
DataExchange --carries--> DomainConcept --specialises--> DataEntity --classified_as--> DataClassification
```

`core/` can walk that at query time, exactly as `realization_report` walks
requirement→architecture bindings without the architecture schema knowing about the
report. **[H] This is the load-bearing design idea of the document:** put the edge
where the import rule allows, and resolve the rest deterministically — do not pay a
prompt-budget cost for a claim the graph can already derive.

`DataExchange` as a first-class node is deliberate, and it is the same move
`Connection` already makes (reified as a node "because protocol, style,
`via_interface` and failure handling have nowhere to live on an assertion"). A
payload has the same problem: `Connection.description_text` is prose precisely
because there was nowhere else to put it.

---

## 4. The seams, drawn out

Where each link must be **declared** is dictated by the one-way import rule, and
getting it wrong is a cycle:

```
enterprise_structure        DataEntity, DataSubjectArea, authoritative_system -> System
      ▲
      │  (may reference enterprise)
requirements_base           DomainConcept --specialises--> DataEntity        [declare here]
      ▲
      │  (may reference requirements + enterprise)
governance_base             DataEntity --classified_as--> DataClassification
                            Control --protects--> DataEntity
                            DataHandlingObligation --applies_to--> DataEntity
      ▲
      │  (architecture does NOT import governance — YB-047)
architecture_base           DataElement --carries--> DomainConcept
                            DataExchange --carries--> DomainConcept
                            DataExchange --connection--> Connection
                            DataStore --holds--> DomainConcept
```

The layer-neutrality rule bites once more: **`DataEntity` cannot name a domain
concept and `DomainConcept` cannot name a wire format.** They are joined only by the
specialisation edge, and both stay in their register.

### The five concrete slots that exist today, and what to do with them

Replacing nothing and adding a fourth model would be worse than either. Recommended
disposition:

| Existing slot | Disposition | Why |
|---|---|---|
| `Interface.payload_contract` (string) | **Keep, and add `payload -> DataElement`** | The string names the contract; the elements say what is in it. Keep the string because it is often all a document states |
| `Connection.description_text` (string) | **Keep** | Human prose is still the honest record of what a document said |
| `Connection.carries_sensitive_data` (bool) | **Keep, and cross-check** | Cheap, already ingested, and now falsifiable against the derived classification. **[H]** Its `true`-only emission means absence is ambiguous (unstated vs. classified-not-sensitive) — a validator should report the difference rather than let it read as "safe" |
| `DataStore.holds_sensitive_data` (bool) | **Keep, cross-check** | Same |
| `DataStore.data_classification` (string) | **Keep as the pointer; promote the vocabulary** | It becomes the string that must resolve to a `DataClassification`. Promoting it to an enum would be the *enum mistake* `QualityAttribute` avoided |

---

## 5. Identity — the lesson that stops a confident wrong join

`sea_common`'s `IdentityScope` exists because `FR-001` in one document is not
`FR-001` in another, and a wrong join "makes the audit wrong rather than merely
incomplete". Data-element names have exactly this hazard, worse:

- `amount` in a settlement ISO 8583 message, `amount` in a REST payload, and `amount`
  as a database column are **three different fields** with three different types,
  scales and sign conventions.
- `Customer` at enterprise level and `customer_id` in a payload are different
  registers, which is why Pattern B keeps them apart.
- `pan` and `card_number` may be the same thing, or `pan` may be the tokenised form
  and `card_number` the clear one — a semantic distinction a string match destroys.

**[H] The rule to adopt:** a `DataElement` is `DOCUMENT`-scoped (or a new
`CONTRACT`-scoped identity if one is warranted) and **must never be joined on name
across documents**; a `DataEntity` is `ENTERPRISE`-scoped and may be. Reusing the
existing enum and its documented refusal is cheaper than inventing a second identity
doctrine, and it is the one place this design should copy rather than invent.

---

## 6. Worked example — the payment case, at all four altitudes

```
ENTERPRISE   DataEntity: CardholderData          authoritative_system -> PGSP
             DataEntity: PaymentInstruction      subject_area -> "Payments"
             DataSubjectArea: Payments

BUSINESS     DomainConcept: Cardholder       --specialises--> CardholderData
             DomainConcept: Payment          --specialises--> PaymentInstruction
             ConceptAttribute: pan                                  (attribute_of Cardholder)
             ConceptAttribute: amount                               (attribute_of Payment)

GOVERNANCE   DataClassification: CHD  level=RESTRICTED  regime="PCI-DSS v4.0"
             CardholderData --classified_as--> CHD
             DataHandlingObligation: encrypt at rest  param="AES-256"
                 --applies_to--> CardholderData
                 --mandated_by--> StandardClause: PCI-DSS 3.5.1
                 --discharged_by--> Control: "PAN-Card Encryption Service"
             Control --protects--> CardholderData

ARCHITECTURE DataElement: pan     data_type=string  is_masked=true
             DataElement: amount  data_type=decimal
             DataElement: pan --carries--> DomainConcept: Cardholder
             DataExchange: PaymentOrchestrator -> Elavon
                 --connection--> Connection: PaymentOrchestrator → Elavon
                 --payload--> pan
```

The questions this makes answerable, none of which are answerable today:

1. **"Which links carry CHD?"** — derived by the four-hop walk in §3, no new
   architecture slot and no governance import.
2. **"Is every CHD link encrypted by a control that discharges a PCI clause?"** —
   the governance gap check.
3. **"Where is the authoritative copy of PaymentInstruction?"** — `authoritative_system`.
4. **"Which systems store a RESTRICTED entity without declaring it?"** —
   `DataStore.data_classification` cross-checked against the derived classification.
5. **"Which fields on the wire have no business meaning?"** — a `DataElement` whose
   `carries` edge is empty, which is the data-side twin of `check_grounded_elements`.

---

## 7. What this costs, and the constraints that decide the order

**[M] The prompt budget is the binding constraint.** `ontology/README.md` is explicit:
class names are scoped by `visible_layer_keys`, so "a layer architecture imports is a
layer every architecture prompt pays for (YB-007)". A four-level data vocabulary
added to the architecture prompt would be several dozen class names in every pass,
against a scaffolding:document ratio that is already a tested invariant
(`tests/test_prompt_budget.py`). **Any design here that puts classification or
governance vocabulary into the architecture prompt should be rejected on that ground
alone** — which is the substantive reason §3 prefers a derived edge over a slot.

**[M] Governance has never produced a fact.** Building a data-classification model on
`governance_base` means the governance extraction path has to exist first, or the
classification half stays as empty as `Policy` and `Control` are today (0 nodes).
**[H] Sequencing consequence: the architecture-level work can ship alone and be
useful; the governance half cannot.**

**[M] The flat extraction contract bounds the business half.** `key_attributes` as
`inlined_as_list` cannot be emitted by the current schema. Either those slots stay
documentation, or they are re-expressed as flat entities (Option 2 in §3).

**[H] Ownership is unsolved and is a precondition for the enterprise altitude being
useful.** `steward` has no range today; YB-008 is the item.

**[H] Two classes per altitude is a lot of new surface.** `DataEntity` +
`DataSubjectArea` + `DataClassification` + `DataHandlingObligation` + `DataElement` +
`DataExchange` is six classes for an aspect that currently has zero. The
counter-argument is that `DesignTechnique` + `ArchitecturePattern` + `ArchitectureStyle`
+ `EngineeringConvention` are four classes for design rationale and the README
defends each on "what becomes checkable". **[H] The same test should be applied
here, and any class that cannot name its check should be dropped** — by that test
`DataSubjectArea` is the first candidate to cut.

---

## 8. Sequencing, cheapest useful first

1. **Architecture only: `DataElement`**, emitted as flat entities by the existing
   architecture passes, plus `DataElement --carries--> DomainConcept`. No import
   change, no budget change beyond one class, and it gives span anchoring a
   vocabulary (§3.6) and makes `payload_contract` falsifiable. **Cheapest, and
   testable against `payment_platform_arch.md` immediately.**
2. **`DataExchange` + the derived-classification walk in `core/`.** Answers the PCI
   data-flow question with no governance import. Needs no model call at all — it is
   a `realization_report`-shaped query.
3. **`DataClassification` + the cross-level validator** (§3, the guard). Requires
   governance extraction to exist; this is where the layer's emptiness becomes the
   blocking fact.
4. **`DataEntity` + `authoritative_system`, and `DomainConcept --specialises-->` it.**
   Requires YB-008 or the binding-class workaround.
5. **`DataHandlingObligation` and the conformance links.** Most valuable at the end,
   because it is only checkable once 1–4 hold.

Steps 1 and 2 are the ones that pay, are buildable today, and would have caught the
originally-reported gap. Step 3 is where the design meets a layer that has never
worked.

---

## 9. Open questions

1. **Is `DataExchange` a node or a view?** `Connection` set the precedent for
   reification, but an exchange is arguably `(Connection, payload)` — a projection.
   Reifying it doubles the node count of the integration aspect. Which one does a
   reviewer actually want to verify?
2. **Does classification belong to the entity or to the field?** `pan` is restricted;
   is `amount`? If classification is per-`DataElement`, governance attaches to the
   architecture altitude and the enterprise `DataEntity` becomes a grouping. If it is
   per-entity, a field inside a restricted entity is restricted by default — simpler,
   but it cannot express "this field is masked so it is no longer restricted".
3. **What is the right unit for `DataSubjectArea`?** Enterprise architecture usually
   has a data-domain map; is that a class here, a lens in `app/viewpoints/`, or a
   domain-pack concept? The answer decides whether it is one of the six classes.
4. **Should a domain pack be allowed to declare `DataEntity` subclasses at all?** The
   base layers stay domain-neutral, so today the answer must be no — but then every
   concrete entity is `DataEntity` with a name, and the pack's typing value is lost.
   Is the specialisation edge enough, or does the enterprise layer need a
   pack-extensible seam the way `DomainConcept` has one?
5. **Does the graph need a `Contract` altitude?** §5 raises a `CONTRACT`-scoped
   identity for a wire field. If API contracts are going to be versioned and joined
   on, that is a sixth altitude, and it is better decided now than retrofitted.
6. **How is "this field has no business meaning" surfaced** without a new pass? It is
   the data twin of `check_grounded_elements` (design) — a validator over existing
   output, not a new model call.

---

## 10. What would make this an ADR

This document stays a brainstorm until one of these is true, at which point the
chosen slice moves to `docs/decisions/` per the TODO system's closing rule:

- **Step 1 ships** (`DataElement` + `carries`, architecture-only) with a measurement
  on the payment fixture: how many data elements are emitted, how many carry a
  business meaning, and whether span anchoring now passes on them.
- **Step 2 ships** (the derived-classification walk) with the PCI question answered
  from the real graph, including the count of `carries_sensitive_data` links whose
  derived classification disagrees with the boolean.
- **Or the opposite is chosen**: [YB-055](../todos/entries/YB-055-data-element-design.md)
  Option B — strip `payload_contract` and `carries_sensitive_data` from the ontology
  and record that data elements are out of scope. That is a legitimate ending, and
  it is better than the current state, which is slots that read as capability and
  are read by nothing.
