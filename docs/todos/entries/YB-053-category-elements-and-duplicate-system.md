---
id: YB-053
legacy: null
title: "Category elements, one system named twice, and a hub that is really a document gap"
status: open
priority: high
area: "`agents/architecture_extraction/passes.py` (structure rules), `agents/extraction/validators.py` (a name validator), `core/knowledge/ingest.py` (`_resolve` identity)"
created: 2026-09-27
updated: 2026-09-27
design: docs/design/c4-specification-view.md
record: null
superseded_by: []
related: ["ADR-0029", "YB-051", "YB-052", "YB-020", "YB-004"]
blocks: []
blocked_by: []
---

# YB-053 — Category-word elements and one system named twice

> **Open.** Found by running the MVP test case end to end against DeepSeek
> (`deepseek-v4-pro`) on a clean scope, measured with `scripts/c4_scorecard.py`. They are
> what is left of the C4 model after the earlier defects were fixed — the diagram is now
> real, and these are what still make it read wrong.
>
> Defects 1 and 2 are in the pipeline and the graph. Defects 3 and 4 are in the source
> **document**, and 3 is the reason the diagram's shape is misleading: the one container
> whose relationships the prose lists becomes the hub everything hangs off.

### Where the model stands

`data/sea_home_01` scope `acme_pillar_01`, `sample_requirements.md` (REQ-G) +
`payment_platform_arch.md` (ARC-G), `payment_processing` domain pack:

| | Local 4B (before) | DeepSeek + fixes |
|---|---|---|
| C4 arrows | **0** | **11** |
| container diagram | 0 boxes, 0 arrows | 14 boxes, 11 arrows |
| level stated by the extraction | 4 of 75 | **25 of 26** |
| reflexive / nesting / duplicate defects | 4 / 29 / 21 | **0 / 0 / 0** |
| passes failed | 2 of 12 | **0 of 12** |
| wall time | ~28 min | **146 s** |
| `sanity_check`-style readiness | `READY: NO` on four rules | `READY: NO` on two |

The remaining two rules that fail are `levels_stated` (25/26 — one element carries no
`c4_level`) and `runs_complete` (the architecture run is PARTIAL because one connections
call returned `empty`). Neither is a C4 defect; both are reported honestly and are
discussed below.

### Defect 1 — elements named after a category, drawn as boxes

The emitted diagram contains:

```
external_services = softwareSystem "External Services" "External services communicating
                    with microservices over TLS 1.2+." "TLS 1.2+" { tags "External" }
database          = container      "Database" "Database whose access is restricted
                    based on RBAC."
```

Both are placeholders, not things. `Database` and `External Services` are groups whose
actual members ARE in the graph — `PostgreSQL` and `Valkey` are containers, and `Elavon`,
`Mastercard`, `CCnet` and `PGSP` are ExternalSystems. So the diagram shows a box for the
category *and* boxes for its members, which reads as six things where there are four.

**The structure prompt already forbids this** (rule 8: "Never emit a bare category word —
'Database', 'Cache', 'Services', 'Microservices' — as an element when the document names
the specific thing"). The escape clause is the defect: *"when the document names the
specific thing"* invites the model to emit the category whenever the document itself is
generic, which it usually is. A category word is a placeholder either way.

**Shape.** Two halves, and the second is the one that catches what the prompt will still
miss:

1. **Tighten rule 8** to forbid the category word outright when it is a plural or
   mass noun denoting a group (`External Services`, `Services`, `Components`,
   `Microservices`, `Databases`), rather than only when a specific name is available.
2. **A `check_category_names` validator**, following `style_as_element` and
   `check_element_types` — a deterministic check that flags an element whose name is a
   bare category. Prompt rules are advice; a validator is a finding. The existing
   `style_as_element` is the precedent: the prompt already forbade styles as elements and
   the model emitted `Microservices` anyway, so a validator was added and it now catches
   it every run (1 style merged on the last run).

### Defect 2 — one system, two nodes

The graph holds the same system twice:

```
platform:payment_gateway_platform         kind=Platform        label="Payment Gateway Platform"
softwaresystem:payment_gateway_platform   kind=SoftwareSystem  label="Payment Gateway Platform"
```

One comes from REQ-G (the requirements profile names the system a `Platform`) and one
from ARC-G (the architecture profile names it a `SoftwareSystem`).

**The precise mechanism, corrected after checking it** (the first write-up said `_resolve`
matches "by label within a kind", which is not what the code does):

- `make_node_id(kind, label)` → `f"{slugify(kind)}:{slugify(label)}"`. Identity is the
  **pair**, and deliberately so: the docstring says "the same kind and label in a
  different run produce the same id, so re-extraction converges rather than duplicating",
  and that part works — re-ingesting one document leaves a single node per label.
- `_resolve` matches by **label only**, but it consults `by_label`, which
  `_collect_declared_nodes` builds **fresh, empty, from the current document alone** and
  never seeds from the existing graph.
- So cross-document identity rests entirely on the two profiles agreeing on the KIND. They
  do not, and `make_node_id` therefore mints two ids for one real system.

Why they cannot agree as things stand: ARC-G's `element_type` is a closed `Literal` —
`SoftwareSystem, ExternalSystem, Person, Container, DataStore, Component, CodeElement,
DeploymentNode` — which contains no `System`, `Platform` or `Application`. REQ-G's
`ExtractedEntity.entity_type` is a free `str` whose description is
`"e.g. Stakeholder, System, BusinessProcess, DomainConcept"`, i.e. unconstrained. One side
picks from a closed C4 vocabulary, the other from the open enterprise vocabulary, and the
two share no token for "the system". In the live run REQ-G chose `Platform`, which is a
*sibling* of `System` under `EnterpriseConstruct` ("Concrete subtypes: Product, SubProduct,
System, Application, Platform") and is the commercial sense of the word.

They cannot agree, and §"Why the ontology lets them disagree" below is the reason: two
disjoint abstract hierarchies, whose mapping exists only as a sentence in
`SoftwareSystem`'s description.

Consequences, all visible in the emitted DSL:

- **Two boxes** for one system, disambiguated only by the identifier suffix this item's
  predecessor added (`payment_gateway_platform` vs
  `payment_gateway_platform_softwaresystem`). A reader sees a duplicate.
- **The system choice is a coin toss.** `c4_model` picks the element with the most
  descendants, so ARC-G's node becomes *the* system and REQ-G's becomes a second
  top-level `softwareSystem` with no containers. Nothing says which is authoritative.
- **Containment can point at the wrong one.** A container whose `part_of` names the
  requirements-profile node is drawn outside the boundary that holds its siblings, and
  the `nesting` check cannot see it — the parent level is `context`, which is what a
  container expects. This is the failure mode that makes it worth fixing rather than
  tolerating: a check that passes while the diagram is wrong.

**Shape.** Identity, not presentation, and the ontology is where the missing piece is.
`SoftwareSystem` already *claims* the mapping in prose — "Maps to the enterprise `System`
construct — that linkage is what keeps ARC-G and REQ-G talking about the same thing" — but
carries `is_a: ArchitectureElement`, not `is_a: System`, and there is no `maps_to` or alias
anywhere. The intent is documented and unenforceable.

So, smallest first:

3. **Constrain REQ-G's class.** `ExtractedEntity.entity_type` is a free `str` whose
   description is `"e.g. Stakeholder, System, BusinessProcess, DomainConcept"`, so the
   requirements agent picks from the whole enterprise vocabulary while ARC-G picks from a
   closed `Literal`. It chose `Platform` — a *sibling* of `System` under
   `EnterpriseConstruct` — for the system under design.
4. **Or catch it without merging**: a validator for "the same label declared under two
   kinds that the ontology says are the same construct", reported in `/gaps`. Cheaper and
   reversible, and it is the `style_as_element` precedent.

Whatever is chosen, the review gate should see the merge, because merging two nodes is not
reversible from the UI.

### Why the ontology lets them disagree

`System` and `SoftwareSystem` are not two classes in one hierarchy — they are two disjoint
abstract roots, one per layer:

| | `System` | `SoftwareSystem` |
|---|---|---|
| File | `ontology/enterprise_structure.yaml` | `ontology/architecture_base.yaml` |
| Parent | `EnterpriseConstruct` (`abstract: true`) | `ArchitectureElement` (`abstract: true`) |
| Sense | organisational: "a coordinated set of components… may serve one or many Products… may exist independently OR be part of a Platform" | architectural: "C4 Level 1. The system being architected, seen as a single box" |
| Attributes | `architectural_pattern`, `is_part_of_platform` → `Platform`, `serves_products` → `Product`, `comprises_applications` → `Application` | `system_class` (`ENTERPRISE_TECHNOLOGY_PLATFORM` / `BUSINESS_TECHNOLOGY_PLATFORM` / `BUSINESS_APPLICATION` …), `origin` |
| Mapped to the other? | no | no — `is_a: ArchitectureElement` |

Both are visible to ARC-G (the import chain is `enterprise_structure → requirements_base →
architecture_base`, one-way), and `EnterpriseConstruct`'s own description lists the concrete
subtypes: "Product, SubProduct, System, Application, Platform". So `Platform` and
`Application` sit *beside* `System`, not under `SoftwareSystem`, and the requirements agent
has three plausible-looking siblings to choose from for one real thing.

The linkage between the two roots exists in exactly one place: the sentence in
`SoftwareSystem`'s description. Nothing parses it, so nothing can act on it.

**The ontology's author already solved this pattern once and stopped short of the second
case.** `Platform` carries an explicit naming note — this is the *commercial* sense
(Shopify, Stripe), and it is "NOT the same as an 'Enterprise Technology Platform'
(OpenShift, Splunk, Grafana)… That concept lives in `architecture_base.SoftwareSystem.
system_class`, deliberately under a different name so the two do not blur." So the
collision was recognised and fixed by naming for `Platform`, while `System`/`SoftwareSystem`
got a prose note and no mechanism. That asymmetry is the defect, not naivety.

**Also mine, and worth naming:** the C4 view papered over this by listing `System`,
`Platform` and `Application` in `_LEVEL_FROM_KIND`, flattening four unrelated classes to
"context". It reports them as `inferred-level` rather than stated, so it does say the level
was guessed — but the flatness is the view's, and `Application.implements_system` (an
attribute, not a level relation) means the diagram cannot express that an application is
not a peer of the system it implements.

### Defect 3 — the document states ONE container's relationships, so that container is the hub

Asked why `Settlement Job Orchestrator` looks like the entry point every other container
hangs off. The graph is **faithful**: all 11 arrows trace to a sentence, and the reason is
the source document, not the extractor.

`test_data/arch/payment_platform_arch.md` is 169 lines. §2.7 (`Settlement Job
Orchestrator`) is ~40 of them and ends with an explicit **"Relationships:"** bullet list —
the only place in the document where container→container edges are stated as such:

| Document line | Edge extracted |
|---|---|
| L88 "Reads and writes … in **PostgreSQL**" | → PostgreSQL (JDBC) |
| L89 "Calls the **PGSP Gateway** … to initiate settlement" | → PGSP Gateway |
| L90 "Obtains routing context from the **Payment Routing Decision Engine**" | → Routing Decision Engine |
| L91 "Receives PAN/CVV only through the **PAN-Card Encryption Service**" | → PAN-Card Encryption Service |
| L92 "Reports tenancy and storefront attribution via the **Storefront Management Service**" | → Storefront Management Service |
| L93 "the **Payment Orchestrator** may enqueue an ad-hoc capture" | Payment Orchestrator → Settlement Job Orchestrator |

The expected request path — Payment Orchestrator → Routing Decision Engine → PGSP Gateway
— is **never stated as a call anywhere**. §2.1 says the orchestrator "Handles payment
initiation"; §2.2 says the engine "Implements the Rule-Based Routing logic"; §2.3 says the
gateway "Provides a standardized API". Those are *responsibilities*, and a human reader
reconstructs the flow from them; the text does not assert it. A grep for call verbs finds
exactly one stated container→container call in the whole document (L89).

**Lever 1, and the root cause: the fixture is ours.** `test_data/arch/` is a hand-written
input, and it should state the request path the way §2.7 states the settlement
relationships — a "Relationships" list under §2.1/2.2/2.3. Then the test case exercises
flow extraction instead of demonstrating a document gap. Nothing else here fixes that.

**Lever 2: inference where the text is silent is unstable, so the flow arrives by luck.**
Same document, same model (`deepseek-v4-pro`), two runs: the first produced
`Payment Orchestrator → Routing Decision Engine`, `→ PGSP Gateway`,
`→ PAN-Card Encryption Service`, `→ Storefront Management Service` and
`Payment UI Service → Payment Orchestrator`. The second produced **none** of those five.
The edges that vary are exactly the inferred ones; the stated ones (§2.7) are stable
across both. That is [YB-004](../entries/YB-004-model-output-not-structurally-stable.md)
landing precisely where the document is silent — and it means "is this graph right?" has
a different answer per run for the part that matters most to an architect.

### Defect 4 — a PERMITTED capability became an asserted connection

`Settlement Job Orchestrator → Valkey` comes from §3: *"Valkey caching of job status **is
permitted** for read-heavy operational dashboards only."* A permission is not a fact: the
document says the platform may do this, and the graph asserts that it does, with
`style: SYNCHRONOUS_REQUEST_RESPONSE`. Its own job-store sentence says the opposite
about durability ("Valkey is **not** used as the job store").

**Shape.** `is_synchronous` is currently unset on every connection even though `style`
determines it (YB-051 decision 2). Worth deciding together with a modality: the ontology
has room for a MAY/optional distinction, and an asserted edge drawn from a permissive
sentence is the kind of thing a reviewer should be shown rather than have to notice.

### Also measured, and deliberately NOT changed

- **`empty` counts against run completeness.** One connections call answered `empty` on a
  chunk with no runtime calls in it, so the run is `PARTIAL` and `/c4` reports
  `run-completeness`. This is ADR-0013's deliberate rule — `empty` and `failed` both mean
  "content may be missing", because the pipeline cannot distinguish "nothing was there"
  from "the model missed it". Changing it would trade a false alarm for a silent miss,
  which is the wrong direction. Recorded here so the next reader does not "fix" it.
- **One element with no stated level** (`inferred-level: 1`). The C4 view infers it from
  the node kind and says so. Not a defect.
- **Four `unlevelled` DeploymentNodes.** Deployment is not L1–L4 and is reported rather
  than placed (ADR-0029, decision 5).

### Acceptance

- `test_data/arch/payment_platform_arch.md` states the request path (a "Relationships"
  list under §2.1/2.2/2.3, mirroring §2.7), and a re-run's container diagram shows
  Payment Orchestrator → Routing Decision Engine → PGSP Gateway **in every run**, not in
  one run out of two.
- No element in a re-extracted `payment_platform_arch.md` is named after a bare category:
  the `c4_scorecard.py` count of `Database`/`External Services`-style names is zero.
- No connection is asserted from a permissive sentence ("is permitted", "may").
- `Payment Gateway Platform` is one node, and the C4 model draws one system box.
- A container whose `part_of` names a system is drawn inside that system's boundary.
- The scorecard's `READY` verdict is reachable on this test case, or every remaining
  failed rule is one this entry argues is not a defect.

### Related

- [ADR-0029](../../decisions/ADR-0029-c4-specification-view.md) — the view these defects
  are visible in, and the checks that report them.
- [YB-052](../entries/YB-052-reflexive-and-duplicate-extraction.md) — the defects the same
  view found before this run; both are now fixed and both counts are zero.
- [YB-051](../entries/YB-051-connections-dropped-at-ingest.md) — the connections pass. Its
  output is what the prompt rule here is measured against.
- [YB-004](../entries/YB-004-model-output-not-structurally-stable.md) — the same document
  extracted twice produced 27 then 30 elements and 19 then 13 connections, which is the
  variance this item's prompt change has to be judged against rather than a single run.
- [Extraction and reconciliation reliability — a brainstorm of levers](../../design/extraction-reliability-levers.md)
  — the wider list this item's defects contributed evidence to. A holding pen, not a plan:
  entries get minted when something is chosen from it.
