# Enterprise governance layer

> **Design document** for [YB-047](../todos/entries/YB-047-enterprise-governance-layer.md).
> Implemented as `ontology/governance_base.yaml`.

---

## 1. The three ontologies, kept apart

"The ontology of the enterprise" is ambiguous, and the ambiguity is expensive:

| | What it is | Where it lives |
|---|---|---|
| **SEA meta-ontology** | the *kinds* — Container, Requirement, Regulation, Policy | `ontology/*.yaml`, ships with the platform |
| **ACME enterprise ontology** | the *instances* — ACME's policies, standards, principles, org | graph data, in the workspace-level shared scope |
| **Domain ontology** | the subject matter — payments | `ontology/domains/*.yaml` |

Curating ACME's governance is therefore **not authoring a file**; it is graph data
the platform reasons over. That requires the meta-ontology to have somewhere to put
it — which is what this layer provides. The catalogue precedent is explicit:
`catalogues/architecture_patterns.yaml` declares instances while
`architecture_base.yaml` declares the class, and the two have different edit cadences.

## 2. Why a layer, and where

Governance is neither "organisational shape" nor "requirements". It is a distinct
concern with its own lifecycle (authored → approved → in force → superseded) and its
own actors (architecture board, CISO).

Placement was decided by the one-way import rule and by what must reference what:

- A **policy mandates a requirement** → governance must be able to reference
  `Requirement`, so governance sits **above** requirements.
- A **control satisfies a standard clause** and an **architecture element conforms**
  → architecture must be able to reference governance. It does not yet; see §5.

```
common → enterprise → requirements → governance → architecture
```

`Regulation` and `Standard` deliberately stayed in `requirements_base`. They are
named, versioned things *outside* the system — the comment at
`requirements_base.yaml:860` says so — while this layer is what the enterprise has
decided. Moving them would have been a class move with `class_uri` and subset
consequences, for no gain: governance imports requirements, so
`Control.satisfies → StandardClause → Standard` resolves.

## 3. The model

**One abstract root, so a query can range over everything the enterprise has
decided:** `GovernanceInstrument` (id, name, owner, status, effective window,
version).

| Class | The question it answers |
|---|---|
| `Strategy` | where the enterprise intends to go, and what drives it |
| `Principle` | what it durably believes, and what that rules out |
| `Policy` | what it binds, over what scope, mandating which requirements |
| `Control` | what it *does* about it, and how conformance is evidenced |
| `Risk` | what it is protecting against, and what mitigates it |
| `StandardClause` | the numbered obligation a control discharges |

**`Control` is first-class, not a `ConstraintRequirement` with a `STANDARD` type.**
A requirement *states* an obligation; a control *discharges* it. Collapsing them
makes "which control satisfies clause A.8.3.1?" unanswerable, and that is the main
audit question. It also gives `verification_method` a home — and an empty one is
itself a finding ("this control cannot currently be evidenced").

**Clauses are why the layer exists.** `Standard` carries a name, body and version —
not its clauses. Without `StandardClause`, a graph can record "conforms to ISO/IEC
27001" but never "satisfies A.8.3.1", and a clause is what an audit asks for.
`ClauseLevel` (MANDATORY / RECOMMENDED / OPTIONAL) is separate from
`GovernanceStatus` because "must" and "should" are different obligations: a missing
mandatory clause is a finding, a missing recommended one is not.

## 4. What it makes decidable

- *Which systems in the CDE use a technique not on ACME's approved list?*
- *Which systems are in scope of the data-residency policy?* — derived from scope
  plus `DeploymentNode.network_zone`, `shared_across_enterprise` and the platform
  instance sharing scope.
- *Show every system in scope of standard X, the control satisfying clause Y, and the
  architecture implementing it.*
- *A policy was revised — what is affected?* — one query, not a sweep of 200 products.
- And the chain becomes walkable upward:
  `Strategy → Principle → Policy → Standard clause → Control → Requirement →
  Architecture → Evidence`, which is the difference between an architecture that is
  described and one that is justified.

## 5. The constraint this layer ran into: prompt budget

Adding the layer broke `tests/test_prompt_budget.py` — scaffolding 7,376 chars against
a 7,266-char document (1.02:1), which is YB-007's failure mode caught by its own test.

The cause was a real inconsistency: `_collect_ontology_names`
(`agents/base_agent.py:708`) injected **every class and enum in the model**, while the
predicate vocabulary was already scoped by `visible_layer_keys` (the YB-030 fix). So
any new base layer taxed every profile's prompt.

**Fixed**: class and enum names are now scoped the same way — only the layers the
entry schema imports reach the prompt. Consequences:

- A layer no profile imports costs no budget. Govern yourself accordingly.
- `architecture_base` therefore does **not** import `governance_base` yet. There is no
  conformance slot referencing it, so the import would be unused and would still put
  the policy vocabulary into every architecture prompt. When `conforms_to` /
  `realizes`-style slots land, that import is a deliberate step with a budget plan —
  most likely by scoping further (only the classes those slots reference) rather than
  by accepting the whole layer.
- The LinkML oracle test now checks each class against the layer that declares it,
  because there is no longer a single root schema that reaches every class.

## 6. What this deliberately does not do

- **No ACME content.** Classes only. ACME's policies are curated into the
  workspace-level shared scope (YB-042 §11) and changed through a `promotion_request`
  (YB-046).
- **No conformance slots yet — decided, and deferred.** An architecture element
  answers to **both** `StandardClause` and `Control`, and both are retained:

  | Claim | Slot (when wired) | The gap it finds |
  |---|---|---|
  | conformance to the external standard | `conforms_to → StandardClause` | *which clause has no control?* — a governance gap |
  | implementation of the enterprise's decision | `realizes → Control` | *which control has no implementation?* — a deployment gap |

  The second is the one that finds real problems, and it only exists because the
  two are separate claims. Wiring both is deferred because it requires
  `architecture_base` to import `governance_base`, which is exactly the prompt-budget
  cost described in §5 — so the slot shape and the budget handling should be settled
  together rather than one before the other. To revisit.
- **No scope derivation.** `Policy.applies_to_scope` is free text on purpose: scope is
  worded in more ways than an enum holds, and turning it into a membership rule is a
  separate problem.
- **No governance extraction profile.** Which profile emits these classes — a new
  governance profile over `governance_base.yaml`, or the existing ones — is undecided.

## 7. How ACME's instruments get into the graph

"Isn't ingesting governance just building an instance of `governance_base.yaml`, so
it becomes like `payment_processing.yaml`?" — **half right, and the wrong half
matters.**

### Schema versus individuals

`governance_base.yaml` and `payment_processing.yaml` are both **schemas**: LinkML
files that declare *classes*. Neither is an instance of the other, and neither is
data. What ingest produces is a third thing: **individuals** typed by those classes.

For payments, a pack is right because the subject matter has entity *types* the base
does not know: `Payment`, `Cardholder`, `Chargeback`, `Merchant`. Those must be
declared as classes, so `payment_processing.yaml` is an overlay that adds vocabulary.

ACME's governance is the opposite case. `Policy`, `Control`, `Risk`, `StandardClause`
**already exist**. ACME's content is a set of *individuals* — `POL-SEC-014`,
`NET-04`, `R-07`, `A.8.3.1`. There is no class to add; there is a node to create.
Declaring `ZeroTrustPrinciple` as a class would be a category error, and it would
destroy the ability to ask "show me all policies".

So: **instruments are data, and the pack analogy does not hold for them.**

### The right precedent is the catalogue, not the pack

`ontology/catalogues/architecture_patterns.yaml` is the shape that fits: a file of
**instances** of a class the ontology already declares. Its own header argues the
split — the ontology declares the CLASS, the catalogue holds INSTANCES, and the two
have different edit cadences.

But there is a gap to close. Today that catalogue is **not** ingested:
`core/patterns.py` loads it into the *prompt*, the model emits patterns, and ingest
writes what the model said. For governance that is backwards — ACME's policies are
authoritative, structured artefacts, and running them through a sampling model to
recover them would import exactly the non-determinism the platform exists to remove.

So governance needs a path the codebase does not have yet: **a direct load from a
curated instance file into the graph, with no model in the loop.** Same
`add_assertion` fold, same review gate, same provenance — and the provenance source
already exists: `SOURCE_IMPORTED` (`core/knowledge/model.py:97`), which says exactly
what this is. Nothing new is needed on the `source_type` axis.

### Where extraction still belongs

Not authoring the instrument — **linking** it. Which requirement a policy mandates,
which architecture element realises which control, which clause a system is in scope
of: those are claims inside other documents, and they are what a governance profile
would extract. The instrument itself stays curated.

### Where a pack *would* apply

Only if ACME needs **classes the base lacks** — an ACME-specific
`DataClassificationTier`, a `ControlFramework` (NIST CSF, ISO 27001 Annex A), a
`LandingZone`. Those are types, so they are a pack. But note the axis differs from a
domain pack:

| | Domain pack | Enterprise governance overlay |
|---|---|---|
| Answers | *what is this business about?* | *what has this organisation decided?* |
| Scope | one subject matter (payments) | one enterprise (ACME) |
| Cardinality | conditional, swappable **per Initiative** | always present **per enterprise** |
| Home | `ontology/domains/` | workspace-level shared scope (YB-042 §11) |

Both are overlays; they are not the same axis, and a governance overlay must not be
filed under `ontology/domains/` — a pack is per-Initiative by construction, and
ACME's policies are not.

### The worked shape

```
Policy POL-SEC-014  --mandates------->  FunctionalRequirement "Encrypt CHD at rest"
Policy POL-SEC-014  --enforced_by---->  Control NET-04 "CDE network segmentation"
Control NET-04      --satisfies------>  StandardClause A.8.3.1
Control NET-04      --mitigates------>  Risk R-07 "Cardholder data exposure"
Container "Payment Orchestrator" --realizes--> Control NET-04        (slots deferred)
```

The first four come from the curated catalogue. The last comes from extraction or
review, and is the deferred conformance slot from §6.

## 8. Providing an enterprise brief to the agents

*"A one-pager on the ACME enterprise, handed to the agents so they extract governance
triples — is that the way?"* It is a good idea, and it separates into two jobs that
need different answers.

### In plain terms

You hand the agent a one-page description of ACME. Three things follow, and only the
first is what I would build first:

1. **It teaches the agent ACME's words.** If the page says ACME calls it "the CDE",
   the agent stops writing "cardholder data environment" on one run and "CDE" on the
   next. Same name every time means the facts join up. This is background for *every*
   extraction, not just governance.
2. **It is not how the policy list should be entered.** A policy's number, owner and
   effective date are facts ACME already holds in its own register. Type those in;
   don't re-derive them from a summary, or the graph will quietly disagree with the
   official record.
3. **A clean run of the one-pager does not mean "all ACME's policies were found".** It
   means "this page was fully read". Those are different claims, and an audit must not
   confuse them.

The rest of this section is the reasoning behind those three.

### Two jobs, not one

| Job | Right mechanism | Why |
|---|---|---|
| **Authoring the instrument** — its id, name, owner, version, effective window, clause numbering | **Import**, if a register exists | these are facts of record. Re-deriving them through a sampling model creates a second truth that can disagree with the official one |
| | **Extract + review**, if no register exists | then prose *is* the only source, and the review gate is the control |
| **Linking the instrument** — which requirement it mandates, which element realises which control, which systems are in scope | **Extract, always** | those claims live inside other documents, which is exactly what the model is for |

So the deciding question is not "can agents extract governance?" — they can — but
**does ACME keep a system of record for its instruments?** If it is a GRC tool, a
ServiceNow register or an EA repository, the instruments should be imported and only
the links extracted. The ontology already anticipates that join:
`MANAGED_REFERENCE_TYPES` (`core/knowledge/model.py:422`) includes `EA_REPOSITORY_ID`,
`PPM_ID`, `CATALOG_ENTRY` and `TECH_REGISTRY_ID`, so an imported instrument can carry
its register key and be reconciled against it.

### Where the one-pager genuinely earns its place

**As grounding, whatever the answer above** — and this is the stronger use.

Today an agent's context is the ontology plus an optional domain pack. There is no
enterprise context: `input_data` accepts `document`, `document_type`, `domain`,
`max_chars`, `force_text_parsing` and nothing else. A brief is the missing third
input, and it is the same kind of thing as a domain pack — grounding that shapes
vocabulary — on a different axis:

| | Domain pack | Enterprise brief |
|---|---|---|
| Answers | *what is this business about?* | *who is this organisation, and what has it decided?* |
| Form | schema overlay (classes, enums) | prose one-pager |
| Scope | one subject matter | one enterprise |
| Cardinality | per Initiative | per enterprise, a placeholder until the real one lands |

Concretely it stops the extractor inventing synonyms: "CDE" vs "cardholder data
environment", "landing zone", "the payments domain", the house names for
capabilities. That is the domain-pack argument — a closed vocabulary reduces
invention — applied to the enterprise rather than the subject matter.

**As a bootstrap**, it produces a *draft* instrument set from one page, which the
review gate then confirms. That is a legitimate way to stand up an initial governance
graph, and it is how the platform already handles requirements.

### The caveat that must be written down

`completeness` is **per run and per document, not per domain**. A one-pager extracted
cleanly reports `COMPLETE` — which says the *page* was fully extracted, not that
ACME's governance has been exhausted. One policy document run in isolation would
report COMPLETE while 199 policies remain unextracted, and an audit reading that
verdict as "governance is captured" would be exactly the false assurance
[ADR-0013](../../docs/decisions/ADR-0013-requirements-completeness-reporting.md)
exists to prevent.

So a graph bootstrapped from a brief must not be read as the register. Either the
register is imported and its key is the completeness signal, or the governance
coverage question is asked as *"which policy areas have no instrument yet?"* — a
census over a closed vocabulary, not a run verdict.

## 9. Related

- [YB-047](../todos/entries/YB-047-enterprise-governance-layer.md) — the item.
- [YB-011](../todos/entries/YB-011-domain-ontology-layer.md) — the domain overlay,
  a different axis from this.
- [YB-042](workspace-structure.md) §11 — where ACME's instances live.
- [YB-046](branching-and-promotion.md) — how they change.
- [YB-007](../todos/entries/YB-007-prompt-scaffolding-instruction-dilution.md) — the
  budget this layer had to respect.
