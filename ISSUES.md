# ISSUES

Small, verified observations found while working — the things that are real but are
not decision-sized. Hand-edited, one entry per issue, stable `ISS-N` ids that are
never reused.

**What this is not.** It is not the TODO system. A TODO entry
([`docs/todos/entries/`](docs/todos/entries/)) is a decision to make or a piece of
work to size, and it carries front matter, acceptance criteria and — when it closes —
a decision record. An issue here is a fact about the current state that somebody
measured. When an issue needs a decision, a design, or an estimate, it gets promoted
to a `YB-NNN` entry and this line becomes a link to it.

**How to keep it honest.** Record the measurement, not the impression — the run id,
the file and line, the count. Close an issue by fixing it (move it to *Fixed* at the
bottom, with the test that pins it, so a report from someone using the tool can be seen
to have been answered) or by promoting it (replace the body with the `YB-NNN` link and
keep the id). If an entry has been open across several sessions without moving, it is
either not an issue or it is a TODO entry pretending to be one.

**Verifying DSL output.** The Structurizr parser is the only authority on what the
emitted notation accepts, and it disagrees with the language reference in places (see
ISS-7). A CLI jar lives at `data/scratch/structurizr/` (gitignored, downloaded from
the structurizr/cli releases) and the check is:

```sh
cd data/scratch/structurizr
java -cp ".:lib/*" com.structurizr.cli.StructurizrCliApplication validate -w workspace.dsl
java -cp ".:lib/*" com.structurizr.cli.StructurizrCliApplication export -f mermaid -w workspace.dsl -o out
```

`validate` catches the grammar and the duplicate-name/view-key rules; `export` proves
the workspace can actually be rendered.

---

## ISS-1 — Category nouns enter the graph as `Concept` nodes through attribution endpoints

**Open.** The largest single node kind in the live payment scope is the graph's own
fallback bucket.

- **Evidence.** `data/sea_home_x/payment_sys_v2.sqlite`, scope `payments_v2`, run
  `run_55151484a7f2`, document [simple_architecture_partial.md](test_data/arch/simple_architecture_partial.md)
  ("All Microservices are Stateless and Containerized with Docker."). **25 of 85 nodes
  are `Concept`**, including `All Microservices`, `Microservices`,
  `Microservice Communications`, `Microservice-to-microservice communication`, `PSPs`,
  `PGSPs`, `Financial Data`. `concept:all_microservices` is referenced only by
  attribution edges — `uses_technology`, `applies_technique`, `conforms_to` (pass
  `technology`) and `deploys_on` (pass `triples`). The run reports **COMPLETE, 0
  refusals, 0 unconsumed keys, 4/4 passes ok**.
- **Mechanism.** The architecture pass schema takes free-text element lists for
  attribution — `used_by` ([passes.py:438](agents/architecture_extraction/passes.py#L438)),
  `adopted_by` ([:448](agents/architecture_extraction/passes.py#L448)), `applies_to`
  ([:473](agents/architecture_extraction/passes.py#L473), [:552](agents/architecture_extraction/passes.py#L552))
  — with no requirement that the value be an element the structure pass declared. The
  technique description and the pass prompt invite a group label outright: *"Elements
  the technique is applied to. Usually several — statelessness and redundancy are
  platform-wide decisions"* ([:611](agents/architecture_extraction/passes.py#L611)).
  Ingest then resolves each label through `_resolve`
  ([ingest.py:367](core/knowledge/ingest.py#L367)), whose fallback kind is `Concept`,
  at [ingest.py:576](core/knowledge/ingest.py#L576), [:590](core/knowledge/ingest.py#L590),
  [:626](core/knowledge/ingest.py#L626), [:728](core/knowledge/ingest.py#L728) — one
  node per phrasing.
- **Why it is not caught.** `check_connection_endpoints`
  ([validators.py:550](agents/extraction/validators.py#L550)) enforces "both ends were
  declared" for `connections` only. Anchoring
  ([validators.py:646](agents/extraction/validators.py#L646)) passes because the phrase
  IS in the document; it catches invention, not category words. See ISS-2.
- **Expected behaviour.** A platform-wide claim belongs on the platform or the
  `ArchitectureStyle` node (`Payment Platform --follows_style--> Microservices
  Architecture` already exists), never on a node invented to stand for "all of them".
- **Fix goes.** A generalised endpoint check in `agents/extraction/validators.py`,
  wired in the architecture agent's validation step.
- **Related.** [YB-053](docs/todos/entries/YB-053-category-elements-and-duplicate-system.md) defect 1
  (same defect, measured on a different axis).

## ISS-2 — Every category guard reads `elements`, so the attribution axis is unmeasured

**Open.** This is why ISS-1 can exist while every existing check is green.

- **Evidence.** `inv_no_tech_leak` ([run_extraction_tests.py:219](scripts/run_extraction_tests.py#L219))
  and `inv_no_style_as_element` ([:226](scripts/run_extraction_tests.py#L226)) both
  inspect `out["elements"]` — see the call at
  [:237](scripts/run_extraction_tests.py#L237). `All Microservices` never appears in
  `elements`; it arrives through `design_techniques[].applies_to`. YB-053's proposed
  `check_category_names` is specified against elements too, so implementing it as
  written would report zero on the run above.
- **Fix goes.** Any category check must run over every endpoint list a pass emits, not
  only the structure pass's element list.

## ISS-3 — One document, two nodes for one container

**Open.** Identity is the exact slugified label, and the passes do not agree on wording.

- **Evidence.** The same scope holds `Container: StoreFront Management Service` **and**
  `Concept: StoreFront Management microservice` — one thing, two nodes, one run. The
  document says "Storefronts are first class entities, stored by a StoreFront
  Management microservice" (its own lowercase wording); the structure pass normalised
  it, the triples/technology passes did not.
- **Mechanism.** `make_node_id` keys on (kind, label) and `_resolve` matches labels
  exactly, so a casing or wording variant is a different thing. This is YB-053 defect 2
  within a single document rather than across REQ-G and ARC-G.
- **Fix goes.** Whatever YB-053 defect 2 decides for cross-document identity, applied
  to intra-document variants as well; or a duplicate-name finding at ingest.
- **Related.** [YB-053](docs/todos/entries/YB-053-category-elements-and-duplicate-system.md) defect 2,
  [YB-004](docs/todos/entries/YB-004-model-output-not-structurally-stable.md).

## ISS-4 — Requirements-profile findings have no renderer

**Open.** Findings are logged and counted, and there is nowhere to review them.

- **Evidence.** `contract_violations` is written by the requirements profile
  ([agent.py](agents/knowledge_extraction/agent.py)) and read nowhere in `app/`; the
  extraction harness consumes it only as a count
  ([run_extraction_tests.py:142](scripts/run_extraction_tests.py#L142),
  [:566](scripts/run_extraction_tests.py#L566)). The runtime accounting warns on every
  run that has findings — "ingest read none of the N record(s) emitted under
  'contract_violations'" — which is true and has been accepted as noise.
- **Consequence.** A reviewer never sees an unanchored name, a clause-shaped node or a
  concept-attribute finding; only a run log line does.
- **Fix goes.** Route the findings channel to the review queue (or to the run page)
  rather than registering keys as explained-unrouted. See ISS-6.

## ISS-5 — Two regression tests assert counts against fixtures that were regenerated

**Open.** A full regression is red for an environmental reason, which is how a real
failure hides.

- **Evidence.** `test_the_real_fixture_reports_both_directions`
  ([test_realization.py:452](tests/test_realization.py#L452), expects 22 requirements /
  20 unrealized / 13 obligations) and `test_the_two_lists_are_populated_from_the_same_report`
  ([test_app.py:405](tests/test_app.py#L405), expects `unrealized_count == 20`) read
  `data/output/test_req_prd.json` + `test_arch.json` (gitignored, regenerated
  2026-09-26; `.deepseek-pre-*` siblings show the regeneration history). They now yield
  20 / 17. Reports show 2 failed / 1033 passed.
- **Checked, so nobody has to re-check.** Both fail with the ConceptAttribute ingest
  change reverted to HEAD, so they are fixture drift and not that change.
- **Fix goes.** Regenerate the fixtures with the harness and re-record the numbers, or
  have the assertions derive the expected counts from the fixture instead of hardcoding
  a measurement from ADR-0006.

## ISS-6 — `attribute_findings` is an explained-unrouted key while its sibling is not

**Open, low.** An asymmetry introduced with the ConceptAttribute prototype.

- **Evidence.** `UNROUTED_OUTPUT_KEYS` ([ingest.py:82](core/knowledge/ingest.py#L82))
  lists `attribute_findings`, so the accounting records it without an alarm;
  `contract_violations` is absent from it and therefore raises the warning in ISS-4 on
  every run that has any. Two keys of the same kind, treated two ways.
- **Fix goes.** Either both are explained or neither is — decided together with ISS-4,
  because the honest outcome is a renderer for both.

---

## ISS-10 — One system, three names: the notation draws the same architecture twice

**Open.** The duplicate boxes a reader sees in the emitted DSL are not an emitter bug
now (ISS-8 and ISS-9 fixed those); they are three runs naming one architecture three
ways, and no guard reaches it.

- **Evidence.** Scope `payments_v2`, grouped by the run that first asserted each node:

  | Run | Document | What it declared |
  |---|---|---|
  | `run_c6425e740d6c` | `sample_requirements.md` (requirements) | `Platform: Payment Gateway Platform` |
  | `run_55151484a7f2` | `simple_architecture_partial.md` (architecture) | `SoftwareSystem: Payment Platform`, `Container: Payment Orchestrator Service`, `Container: Rule Engine Service`, `Component: Payment Orchestrator State Machine` |
  | `run_7a8b4f8dd35a` | `simple_architecture_increment_2.md` (architecture, PARTIAL) | `SoftwareSystem: Payment Gateway Platform`, `Container: Payment Orchestrator`, `Component: Rule Engine`, `Component: State Machine` |

  So one system appears as three boxes — `Payment Gateway Platform` (a `Platform` from
  the requirements profile), `Payment Gateway Platform` (a `SoftwareSystem` from the
  increment), `Payment Platform` (a `SoftwareSystem` from the partial) — and one
  service as two: `Payment Orchestrator` beside `Payment Orchestrator Service`, with
  `Rule Engine` as a component in one run and `Rule Engine Service` as a container in
  the other. Both architecture documents describe the same platform; the increment is
  written as a continuation of it.
- **What is fixed.** The two nodes with the *same label* are now drawn once, with the
  collision reported ([c4.py](app/viewpoints/c4.py) — `duplicate-element` gap and the
  `unique_names` check), because Structurizr refused to load the file at all (ISS-8).
- **What is not.** `Payment Platform` vs `Payment Gateway Platform`, and
  `Payment Orchestrator` vs `Payment Orchestrator Service`, are *different labels* and
  different kinds. Merging those is a confident-wrong-join decision — the failure this
  repo treats as worse than a missing join — so the view draws both and says nothing,
  and the notation shows one architecture twice with no way to tell.
- **Fix goes.** Identity, not presentation: the mapping `Platform → SoftwareSystem` and
  the reconciliation of near-duplicate labels across runs. This is
  [YB-053](docs/todos/entries/YB-053-category-elements-and-duplicate-system.md) defect 2
  (exact mechanism written up there) and [ISS-3](#iss-3--one-document-two-nodes-for-one-container)
  is the single-document version of it. A label-similarity merge is explicitly NOT the
  fix.

---

---

## ISS-12 — The two platform lenses are named in two namespaces, and neither is complete

**Open — likely TODO-sized: closing it needs an ontology decision, not a patch.**

- **Question asked.** "A Platform can be viewed through two lenses: Business (a Payment
  Platform supporting Payment Products like Card, APM) and Technical (OpenShift, Airflow,
  n8n). Does the ontology support both?"
- **Answer: both are named, asymmetrically, and nothing joins them.** The ontology says so
  itself — `Platform`'s naming note reads *"this is the COMMERCIAL sense of platform…
  It is NOT the same as an 'Enterprise Technology Platform' (OpenShift, Splunk, Grafana) —
  that concept lives in `architecture_base.SoftwareSystem.system_class`, deliberately under
  a different name so the two do not blur."*

  | Lens | Where | Shape |
  |---|---|---|
  | Business / commercial | `enterprise_structure.Platform` | A class: Product **and** System, `value_proposition`, `target_extenders`, `contracts -> PlatformContract`, `extended_by_products -> Product`, `hosted_systems -> System`, `multi_tenancy_model`, `backward_compatibility_policy`, plus free-text `platform_type` |
  | Management scope (covers both) | `architecture_base.SoftwareSystem.system_class` | `SoftwareSystemClass`: ENTERPRISE_TECHNOLOGY_PLATFORM, BUSINESS_TECHNOLOGY_PLATFORM, BUSINESS_APPLICATION, SHARED_TECHNICAL_SERVICE, INTEGRATION_PLATFORM |
  | Supporting flags | `SoftwareSystem` | `shared_across_enterprise`, `managed_by` (free text), `origin`, `deployment_model`, `vendor` |
- **What works.** The architecture lens is real and in use: live scope `payments_v2` holds
  **2 verified `system_class` facts**, both `BUSINESS_TECHNOLOGY_PLATFORM` (Payment Platform,
  Payment Gateway Platform), and the harness gates that every `SoftwareSystem` carries one
  (`inv_software_systems_classified`). `repair.system_under_design` reads it to refuse an
  enterprise technology platform as the anchor for an unplaced element — the one place the
  enum's own auditor note is implemented.
- **Gap 1 — a technology platform can only be a `SoftwareSystem`.** `system_class` is
  declared on `SoftwareSystem` alone (checked across the layer), and in the live scope
  OpenShift is a `DeploymentNode` plus a `TechnologyStack`, so **it carries no lens at
  all**. Airflow and n8n would land the same way. A technology platform is a thing with an
  owner, a lifecycle and consumers; today the ontology can only say where it *runs* or what
  something *uses*.
- **Gap 2 — two vocabularies for one axis, unreconciled.** `Platform.platform_type` is free
  text whose own examples include "infrastructure" and "integration platform", overlapping
  `SoftwareSystemClass.ENTERPRISE_TECHNOLOGY_PLATFORM` and `INTEGRATION_PLATFORM`. "Is this
  an infrastructure platform?" has two answers and no rule relating them.
- **Gap 3 — no join between the lenses.** `System.is_part_of_platform`,
  `Product.extends_platform` and `Application.consumes_platform_contracts` all sit on the
  enterprise side and all have **0 instances** in the live scope; nothing on the architecture
  side points at a `Platform`. So the commercial platform and the architecture element
  implementing it are two nodes joined only by label — the YB-053 defect 2 mechanism, which
  is also why `Payment Gateway Platform` exists twice (ISS-10).
- **Gap 4 — the commercial half is declared but unreachable, exactly like
  `ConceptAttribute` was.** `Platform.contracts -> PlatformContract` is `inlined_as_list`,
  the nested shape no pass can emit (YB-055's defect), so a platform's contracts,
  multi-tenancy and extenders cannot reach the graph. Live: 0 `Product`, 0 `SubProduct`,
  0 `PlatformContract` nodes. Ownership (`managed_by`) is free text pending YB-008.
- **On the payment-products half of the example.** The pack models methods, not products:
  `PaymentMethod` = CARD, DIRECT_DEBIT, BANK_TRANSFER, WALLET, INVOICE, OPEN_BANKING, with
  instruments as concepts (`Card`, `BankAccount`). "APM" as a category is not named — the
  pack lists the individual methods instead, consistent with its "a state is a value, not a
  concept" reasoning. And a platform does not state what it supports: `payment_method` is
  declared on the pack's root `PaymentDomainConcept`, so a domain concept carries it, not the
  `Platform`. `Product`/`SubProduct` exist for "the products a platform is extended by" and
  are empty.
- **Options, cheapest first.** (a) Make the capability-coverage audit honour the auditor note
  the enum already states (exclude ENTERPRISE_TECHNOLOGY_PLATFORM from business coverage) —
  no ontology change; today nothing in `app/` reads `system_class` except a tooltip, so the
  false-gap risk the enum warns about is still live. (b) Retire the duplicate vocabulary:
  `platform_type` as an enum, or a pointer to `SoftwareSystemClass`. (c) Declare the
  architecture→enterprise binding (`SoftwareSystem.realizes_platform -> Platform`, a binding
  class in the higher layer per the import rule) — which would also give ISS-10 a mechanical
  answer: two nodes realizing one platform ARE one thing. (d) A `TechnologyPlatform` entity
  in `enterprise_structure` (owner, criticality, consumers) realized by architecture
  elements, Pattern B — the shape `Container -> Application` already uses. Every one of these
  must name its check, and (c)/(d) cost prompt budget (YB-007).
- **Related.** YB-053 defect 2 and [ISS-10](#iss-10--one-system-three-names-the-notation-draws-the-same-architecture-twice)
  (the identity half), YB-055 and [ISS-1](#iss-1--category-nouns-enter-the-graph-as-concept-nodes-through-attribution-endpoints)
  (the nested-slot half), YB-008 (ownership).

---

## Fixed

Kept rather than deleted, because each was reported from using the tool and each now
has a test that fails if it comes back.

### ISS-7 — `{ tags "External" }` is not valid DSL (fixed 2026-10-01)

- **Reported as.** `Too many tokens, expected: softwareSystem <name> [description]
  [tags] at line 12: pgsp = softwareSystem "PGSP" "…" { tags "External" }` — the whole
  workspace fails to load on the first external system.
- **Cause.** The DSL terminates a statement at the newline, so a `}` on a statement's
  line is a syntax error. `tags` is a block child, and the block has to be multi-line
  even though the language reference writes the field as `[tags]` on the element line
  and its own "Permitted children" list includes `tags`. Verified against
  structurizr-cli v2025.11.09: inline `tags "External"` fails for `softwareSystem`,
  `person` and `container` alike; `{ tags "External" }` and `{ tags "External"\n}` both
  fail; a block with the tag on its own line passes.
- **Fixed.** [c4.py](app/viewpoints/c4.py) emits the tag as its own line, opening a
  block for it when the element has no children.
- **Pinned by** `test_an_external_system_is_tagged_in_the_form_the_parser_accepts` and
  `test_every_closing_brace_is_alone_on_its_line` (`tests/test_c4_view.py`).

### ISS-8 — A duplicate top-level name makes the whole DSL refuse to load (fixed 2026-10-01)

- **Reported as.** "Major issue with duplicate Components, Software Systems in
  Structurizr DSL."
- **Cause.** Structurizr refuses two elements of one name in one scope: *"A top-level
  element named 'Payment Gateway Platform' already exists"* is a hard error. The graph
  holds both because `Platform` (REQ-G) and `SoftwareSystem` (ARC-G) are different
  kinds for one system — [YB-053](docs/todos/entries/YB-053-category-elements-and-duplicate-system.md)
  defect 2.
- **Fixed.** The view draws the box the diagram hangs off (most descendants, then most
  facts, then lowest id — stable across runs), drops the other, and reports it as a
  `duplicate-element` gap plus a failed `unique_names` check, which the DSL header
  carries as a `// WARNING`. Connections naming the dropped node are reported as
  dangling rather than re-pointed: whether the two ARE one thing is the open decision
  in ISS-10. The graph is untouched.
- **Pinned by** `test_two_elements_of_one_name_are_drawn_once_and_reported`.

### ISS-9 — Two component views keyed `Components` (fixed 2026-10-01)

- **Found by** validating the fixed DSL: *"A view with the key Components already
  exists"* — one component view per container, and they were all called `Components`.
- **Also.** View keys are restricted to `[a-zA-Z0-9_-]`, so the readable fix
  (`"Components - Payment Orchestrator"`) is rejected for its spaces.
- **Fixed.** Keys are built from the element's own identifier:
  `Components-payment_orchestrator`.
- **Pinned by** `test_every_view_key_is_one_structurizr_accepts_and_only_once`.

### ISS-11 — The map painted a whole layer one colour (fixed 2026-10-01)

- **Reported as.** "When the Map is rendered as Graphs all class of Architecture or
  Business is filled with single colour. A differentiation of Concept by some types
  will make the Graph intuitive."
- **Measured.** Live scope `payments_v2`: **30 distinct kinds over 176 nodes, drawn in
  3 colours**, with `Concept` — the graph's own unclassified bucket — 28 of them.
- **The axis that did NOT work.** "Colour by top-level ancestor" was the obvious
  data-driven rule and it makes things worse: `OntologyModel.ancestors()` includes
  mixins, so `Provenanced` is the root of 11 kinds and 41 nodes and Containers end up
  the same colour as FunctionalRequirements.
- **Shipped (B + D):** colour = the ontology's own `in_subset` family, shape = the C4
  level, and a **Colour by: Family · Layer · Kind** control so the old layer reading is
  still one click away. Precedence is declared because a class may hold several
  subsets (`DesignTechnique` ∈ ArchitectureStructure ∧ ArchitectureRationale;
  NFR ∈ Requirements ∧ QualityAttributes). Live result: **8 swatches** — business
  context 20, requirements 12, quality attributes 17, C4 elements 23, architecture
  rationale 25, architecture structure 39, enterprise structure 5, unclassified 35 —
  and shapes System 14, Container 11, Component 3, everything else 148.
- **Vocabulary and degradation.** Families come from the ontology rather than a second
  hand-written list; a kind the base layers do not declare (a pack class, `Concept`)
  reports `unclassified`, and a map whose ontology cannot be read falls back to the
  layer rather than inventing a family. All three axis values ride on every node, so
  the payload is self-describing for the API.
- **Pinned by** `test_every_axis_is_available_and_the_key_matches_the_axis`,
  `test_an_unknown_axis_falls_back_to_the_default_rather_than_raising`,
  `test_the_family_is_read_from_the_ontologys_own_subsets`,
  `test_a_family_map_without_the_ontology_degrades_to_the_layer`,
  `test_the_colour_legend_counts_exactly_what_the_colour_colours`,
  `test_a_colour_axis_cannot_take_more_swatches_than_a_legend_can_hold`,
  `test_shape_carries_the_c4_level_and_the_stated_level_wins`,
  `test_the_shape_legend_names_only_shapes_the_graph_draws`,
  `test_the_layer_axis_still_answers_what_it_answered` (`tests/test_viewpoint_map.py`).
- **Verified by hand, because a Python test cannot see a drawn diagram.** The rendered
  inline JS passes `node --check`, and all five SVG shapes were run through node and
  produce valid path data that grows with the node's identifier count. The `kind` axis
  stays available with 30 swatches where the palette cycles by design — 30
  distinguishable hues do not exist, which is why shape carries the second channel.
- **Not moved to `docs/decisions/`.** It is an encoding change with its alternatives
  measured above, not a decision other work has to answer to; say the word if it should
  be an ADR.

---

Not listed here, deliberately: the `inv_no_tech_leak` FAIL on `Quartz` in the saved
architecture output is already recorded inside
[YB-053](docs/todos/entries/YB-053-category-elements-and-duplicate-system.md) (its defect 1
progress note), and the read-only `uv` cache under the agent sandbox is an environment
fact, not a property of this project.
