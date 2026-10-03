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
- **Progress (2026-10-03).** The finding half is done for the drawn view: the C4 model
  reports pairs whose names are one thing under two labels as a `near-duplicate-element`
  gap and a `distinct_names_are_distinct_things` check
  ([c4.py](app/viewpoints/c4.py)) — six on the live scope, measured, with no false
  positives. It reports rather than merges, so the identity decision above is still
  open. Pinned by `test_a_second_name_for_one_thing_is_reported_not_merged` and
  `test_names_that_share_only_a_word_are_not_reported` (`tests/test_c4_view.py`).
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
- **Progress (2026-10-03).** "Says nothing" is no longer true. The C4 model reports the
  pairs as a `near-duplicate-element` gap and a `distinct_names_are_distinct_things`
  check ([c4.py](app/viewpoints/c4.py)): on the live scope it names
  `Payment Gateway Platform` / `Payment Platform`, `Payment Orchestrator` /
  `Payment Orchestrator Service`, `Rule Engine` / `Rule Engine Service`,
  `Payment UI` / `Payment-UI Service` and two more — six, all real. The rule is token
  containment over drawn elements, chosen after measuring the alternative: a string
  similarity ratio reported 141 pairs on the same graph, because `Adaptability` and
  `Availability` are close strings naming different things and every connection label
  reads `A → B`. Reported, never merged — the identity decision above is untouched.
  Pinned by `test_a_second_name_for_one_thing_is_reported_not_merged` and
  `test_names_that_share_only_a_word_are_not_reported` (`tests/test_c4_view.py`).

---

---

## ISS-12 — The two platform lenses are named in two namespaces, and neither is complete

**Promoted — see [YB-062](docs/todos/entries/YB-062-the-two-platform-lenses.md).**
Promoted to a TODO entry on 2026-10-03, jointly with
[YB-044](docs/todos/entries/YB-044-platform-instances.md), and the analysis moved there
rather than being copied: closing this needed an ontology decision about what a
technology platform IS, which is a TODO entry, not a measured observation about the
current state.

The two items were cross-referencing nothing while sharing one question. YB-044's
instance half is unblocked and its type half waits on that decision; the
`shared_across_enterprise` derivation is the seam where they must agree.


## ISS-13 — The node-naming rule runs over design traceability links, and rejects names the graph already holds

**Open.** `check_object_contract` exists to stop a behavioural clause becoming a node.
Applied to a design's reference to a requirement, it flags the requirement's own name.

- **Evidence.** `_MAX_NODE_CHARS = 40` ([validators.py:193](agents/extraction/validators.py#L193))
  and `check_object_contract` ([:196](agents/extraction/validators.py#L196)) run over the
  design profile's merged triples at
  [agent.py:188](agents/design_assistant/agent.py#L188). In `draft_20261002T183436_1143`
  (`run_eca47b9187ea`) it produced 13 pure "clause-shaped" findings, and **7 of the 13
  flagged strings are labels of nodes that already exist** in the same scope —
  `Email Frequency Limit (Normal Operations)` (41 chars),
  `Email Frequency Limit (Marketing Campaign)` (42),
  `OpenShift Container Orchestrator Platform` (41). None is a clause; each is the name
  REQ-G or the architecture already uses.
- **Consequence, measured.** The same draft reports the three email elements —
  `Email Delivery Service`, `Email Template Manager`, `Email Frequency Governor` —
  as `ungrounded`, so the part of the proposal that answers the email requirements is
  exactly the part the review page marks unsupported. Two things worth separating: the
  flag does not itself drop the link (this profile runs no repair,
  [agent.py:190](agents/design_assistant/agent.py#L190)), and the traceability pass
  emitted no `references` entry for those three names at all. The false positives are
  the confirmed defect; whether they also obscure the missing reference is not
  established by this draft.
- **Fix goes.** Either stop running the node-naming contract over traceability records,
  or exempt an endpoint whose string resolves to an existing node label — a name the
  graph already holds cannot be a clause "nothing can link to". The 40-char cap is the
  wrong instrument for that check in either form.
- **Related.** [ISS-14](#iss-14--a-design-draft-counts-every-cross-graph-link-as-unresolved-and-39-in-the-live-graph-can-never-bind)
  is the same family one step further on: references that pass this check unremarked and
  still cannot be joined to anything.

## ISS-14 — A design draft counts every cross-graph link as unresolved, and 39 in the live graph can never bind

**Open.** Corrected after checking: the 109 was expected, not a failure. What is real is
a smaller set that no matcher can ever bind.

- **Correction first, because this entry was wrong.** It claimed 109 links "should have
  bound and did not", reading `draft_20261002T183436_1143`'s `unresolved_references: 109`
  as a resolution failure. It is not one. `unresolved_references()`
  ([model.py:1002](core/knowledge/model.py#L1002)) is scoped to the graph it is called
  on, and it resolves a reference by looking for a node **in that graph**
  ([reference_targets_a_node](core/knowledge/model.py#L743)). A design proposal is its
  own graph and REQ-G is correctly absent from it, so every cross-graph link it makes is
  unresolved by construction — the state that method's own docstring calls expected.
- **Measured, which is what settles it.** Proposal alone: **109** unresolved. REQ-G
  alone: **39**. Proposal merged into REQ-G, which is what applying the draft produces
  (`merge_graphs`, [runner.py](app/runner.py)): **39** — exactly REQ-G's own count. So all
  109 bind on apply, by exact label, and none of them was broken. The misleading part is
  a number: the draft's `counts` reports 109 as though it were a defect count, and the
  design page renders it.
- **The residue that is real.** The 39 never bind, all in `SYSTEM_BASELINE`: by predicate
  `supports_capability` 9, `satisfies_quality_attribute` 8, `implements_requirement` 6,
  `governed_by_rules` 4, `traces_to_process` 4, `governed_by_rule` 3,
  `realizes_quality_attribute` 3, `traces_to_capability` 2. They name capabilities
  ("Storefront Management", "Payment Routing Decisioning"), quality attributes written as
  descriptions ("Data Confidentiality (AES-256)", "Availability (Redundancy and
  Replicas)"), rule sets and processes — none of which was ever extracted as a node, so
  no matcher can bind them. Only 5 are near-misses at 0.75 similarity
  ("Storefront Management" ~ "StoreFront Management Service", "Payment Request
  Processing" ~ "Payment Request processing services"). This is the ISS-13 family — a
  descriptive phrase where the predicate's range expects a node — but the consequence
  here is real rather than a false positive: the claim cannot count as an answer, so
  whatever it points at reads as unanswered.
- **Also found, latent, and not the cause here.** `REALIZATION_PREDICATES`
  ([realization.py:68](core/knowledge/realization.py#L68)) and `CROSS_GRAPH_PREDICATES`
  ([model.py:161](core/knowledge/model.py#L161)) disagree in both directions:
  `addresses_goals`, `satisfies_quality_attributes` and `supports_capabilities` are
  counted by the coverage report but never offered for reconciliation, while
  `delivers_initiative` and `mandated_by` are offered but not counted. None of the 39
  uses an asymmetric predicate, so nothing here changed — which is the point: it is the
  "two rules would eventually disagree" hazard that docstring warns about, sitting one
  plural away from mattering.
- **Fix goes.** Two separate things. Report a draft's unresolved count against the graph
  it will be merged into, or label it as references awaiting reconciliation rather than
  as defects. And decide what a link naming a capability, quality attribute, rule or
  process should bind TO — either those kinds get extracted as nodes, or the predicate's
  range is narrowed so the model is not asked for something nothing can join.


## ISS-15 — The design profile's prompt scaffolding sits at its ceiling, so any vocabulary growth breaks the budget guard

**Open.** One new enum name tipped a guard that was already at 100%.

- **Evidence.** `test_scaffolding_does_not_exceed_the_document`
  ([test_prompt_budget.py:96](tests/test_prompt_budget.py#L96)) measures the design
  profile's pass prompt against a synthetic 7,266-char requirement document and asserts
  `scaffolding <= document`. Adding `SharingScope` to `architecture_base.yaml` (YB-044) —
  one enum, three permissible values, and no new classes — moved it to **7,276**, so the
  test now fails by 10 bytes (1.00:1).
- **Why it is structural rather than a defect in that enum.** The scaffolding is
  dominated by the shared ontology context, which `SEABaseAgent._format_ontology_context`
  builds as the list of every class and enum NAME resolved across the layer's imports:
  **5,174 of those 7,276 chars**. The profile's own contribution (pass instructions plus
  catalogue context) is ~2,100. So the ratio is governed by the size of the vocabulary,
  and this project's vocabulary is meant to GROW — every ontology item drives the number
  up. Measured before the change, the guard had no headroom: it passed, and only just.
- **Consequence.** YB-007's ratio, as measured here, currently reports vocabulary size
  rather than prompt dilution. It cannot tell "the profile's instructions are crowding out
  the document" — the failure it exists to catch — from "the ontology gained a term",
  which is the product working as intended.
- **Options.** (a) Measure the profile's OWN scaffolding (pass instructions and
  catalogue) and assert the ratio on that, with the shared vocabulary bounded separately;
  (b) shrink the shared context, which is the dominant term and the more direct fix for
  prompt budget; (c) accept a larger allowance, which weakens the guard without answering
  what it measures.
- **Not fixed here.** The guard was left failing rather than adjusted, because redefining
  another area's budget test to accommodate a schema change is the wrong order — and the
  measurement is the point, not the green tick.
- **Update 2026-10-03: the guard passes again, and the cause has NOT changed.** Measured:
  the document it compares against grew from 7,266 to **11,221** chars, because the design
  digest now carries recorded decisions and their trade-offs (`d831adf`), while the largest
  scaffolding is 7,374 (`patterns`, 0.66:1). So the symptom is gone and the structure is
  identical: the shared ontology context is still **5,272 of that 7,374**, still the dominant
  term. The guard is a ratio against a fixture that a different feature happened to enlarge,
  which is the same conclusion as option (a) below arrived at from the other direction —
  the number moves when the document moves, not when the scaffolding does.

---

## Fixed

Kept rather than deleted, because each was reported from using the tool and each now
has a test that fails if it comes back.

### ISS-18 — An architecture ingest could never record a decision, and nothing failed when it did not (fixed 2026-10-03)

- **Reported as.** "Recent Architecture extraction job `job_e793a18aab1e` didn't get a
  DECISIONS pass."
- **Half of that was timing.** That job ran at **10:27:18**; `d831adf` — which added the
  decisions pass — landed at **12:32:15**, about two hours later. Its four passes
  (`structure`, `connections`, `technology`, `traceability`) are exactly
  `ARCHITECTURE_PASSES` as it stood then, so the run was correct for its code.
- **The other half was a real gap, and the more useful finding.**
  `d831adf`'s own message says *"Add a **Design Assistant** decisions pass"* — so the pass
  existed only in the design profile. `agents/architecture_extraction/passes.py` held
  `ARCHITECTURE_PASSES = [structure, connections, technology, traceability]`, and
  **ingest was already ready** (`architecture_decisions` was in the consumed-key whitelist
  and had been since that commit). So an `/ingest` of an architecture document could never
  emit a decision, the graph's decision count stayed at zero, and no test failed — which is
  why the measurement in YB-067 read "0 ArchitectureDecision nodes" without anyone being
  able to say whether that was the data or the profile.
- **Fix.** `ARCHITECTURE_DECISION_PASS` added to the extraction profile, with the record
  type moved to `architecture_extraction/passes.py` and imported by the design profile
  rather than declared twice. The extraction prompt differs where it must: it extracts what
  the DOCUMENT states and defaults `status` to `ACCEPTED`, because a document presenting a
  choice as settled is asserting an enterprise fact — while the assertion stays `UNVERIFIED`
  with agent provenance. Those are two axes and the prompt says so, because a document
  sounding confident must not become the platform sounding confident.
- **Guarded now by three assertions**: the extraction profile runs a `decisions` pass, that
  pass's output key is one ingest consumes, and the record's `status` cannot express a review
  state.
- **The fix above was INCOMPLETE, and a second field report caught it.** With the pass added,
  the run recorded `decisions outcome=ok triples=155` — and still produced **0
  ArchitectureDecision nodes**. The pass ran and its output was dropped: the architecture
  agent's merge block collected `structure`, `connections`, `technology` and `traceability`,
  and had **no line for `decisions`**. The design profile has one; the extraction profile did
  not, so the records never reached `graph_from_extraction`.
- **Why nothing failed, again.** `test_output_consumption` proves INGEST reads a pass's key. It
  says nothing about whether the AGENT collects it, and nothing did. The new assertion closes
  that half — every non-triple output key of every architecture pass must appear in a
  `collect(outcomes, "<pass>", "<key>")` call — and it was verified by reverting the fix, which
  produces `these pass outputs are never collected by the agent: ['decisions.architecture_decisions']`.
- **Still open, and a different question.** A decision named in a *requirements* document still
  becomes a `Concept`: `requirements_base` imports `linkml:types`, `enterprise_structure` and
  `sea_common` — not `architecture_base` — so the requirements profile cannot see
  `ArchitectureDecision` at all. Two live examples sit in `payments_v3` as `Concept` nodes
  ("Java Microservices Technology Decision", "On-Premises Deployment Decision"), asserted by the
  `triples` pass. That is a layering decision, not a bug in this fix: it is filed as YB-070.

### ISS-17 — The ontology count pins drifted apart again, one commit after the note about it (fixed 2026-10-03)

- **Evidence.** `d831adf` added `TradeOff` and took the class count 70 → 71. It updated the
  pins in `test_ontology.py`, `test_ontology_reference.py`, `test_domain_pack.py` and
  `test_layering.py` — and missed `tests/test_app.py`, which was then the only red suite.
  `test_ontology.py`'s own note from the previous occurrence reads: *"they must be updated
  TOGETHER, and the 15-minute suite is the one that gets forgotten."* It was.
- **Why it recurs.** The same number is pinned in **five** files, deliberately: a schema
  change should be acknowledged where the schema is read, not flow silently into whatever
  consumes the payload. That intent is sound and the drift is the cost of it.
- **Fix.** The pin is corrected to 71/51, and the comment now names the recurrence and the
  one-line audit that catches it before the suite does —
  `grep -rn 'classes"\] == \|stats()\["classes"\]' tests/`. A `TradeOff` class is the kind of
  addition that looks local and is not: it moves a count that four other files also assert.
- **Not fixed by one pin.** Collapsing the five into one derived count would remove the
  acknowledgement the design asks for. The cheap mitigation is the grep in the commit
  checklist, which is now written where the next person will hit it.

### ISS-16 — A named SPARQL query was silently always-empty, and nothing tested it (fixed 2026-10-03)

- **Evidence.** `QUERY_MISSING_ACTIVE` (`core/knowledge/rdf.py`) asked:
  `FILTER NOT EXISTS { ?el sea:implements_requirement ?ref . ?el rdfs:label ?ref_label . }`.
  The block shares **no variable** with `?req`, so it did not ask "does *this* requirement
  have an implementer" — it asked "does any element implement anything and have a label".
  On a fixture with one implemented and one unimplemented requirement it returned **zero
  rows**: it could not return the unimplemented requirement, which is its only job. It also
  matched abstract `sea:Requirement`, which no node is ever typed as, so it could not match
  even in principle.
- **Why it survived.** It was never registered in `QUERIES`, and
  `scripts/test_knowledge_layer.py` exercised only `unverified`. A named query that nothing
  runs and nothing tests is a landmine rather than dead code: the next person to add it to
  the allowlist would have shipped it.
- **Fix.** The body is correlated, subclass-aware (a property path, which needed `to_rdf` to
  emit `rdfs:subClassOf` — it did not), and matches a bound edge **or** a literal reference,
  because 9 of 10 real `implements_requirement` links are literals. Four checks now pin it in
  the harness, including the fixture above and the grandchild subclass it used to miss.
- **Not registered, deliberately.** It cannot separate "nothing cited this" from "cited and
  not yet bound" — `realization_report` reports those as `none` and `unresolved`, and on
  `payments_v2` the corrected query returns 15 against the projection's 3. It also has a COMPLETE-run
  precondition a query cannot enforce. Its own comment now carries all three reasons.

### ISS-1 — Category nouns enter the graph as `Concept` nodes through attribution endpoints (fixed 2026-10-03)

- **Reported as.** The largest single node kind in the live scope was the graph's own
  fallback bucket: 25 of 85 nodes in `run_55151484a7f2` were `Concept`, including
  `All Microservices`, `Microservices`, `Microservice Communications` and `PSPs` —
  one node per phrasing of one idea, on a run reporting COMPLETE with 0 refusals.
- **Cause.** The architecture pass schemas take free-text element lists for attribution
  (`used_by`, `adopted_by`, `applies_to`) with no requirement that the value be an
  element the structure pass declared, and the prompt invites a group label outright:
  *"Elements the technique is applied to. Usually several — statelessness and
  redundancy are platform-wide decisions."* Ingest then resolves each label through
  `_resolve`, whose fallback kind is `Concept`.
- **Fixed.** `check_attribution_endpoints` ([validators.py](agents/extraction/validators.py))
  applies the rule `check_connection_endpoints` already enforced for connections to
  every attribution list, and is wired into the architecture profile's validation step
  ([agent.py](agents/architecture_extraction/agent.py)). It FLAGS rather than drops: a
  platform-wide claim is legitimate content in the wrong shape — it belongs on the
  platform or the `ArchitectureStyle`, not on a node standing in for all of them — and
  a drop would silently lose the claim. The declared-name set is now computed once by
  `_declared_names` and shared with the connection check, so the two cannot drift into
  disagreeing about what was declared.
- **Still true.** A flag reports; it does not stop the node being created, and the
  triples channel (`deploys_on` was the other measured path) is not covered by this
  check. Both belong to
  [YB-053](docs/todos/entries/YB-053-category-elements-and-duplicate-system.md).
- **Pinned by** `test_an_attribution_list_naming_an_undeclared_element_is_flagged`,
  `test_every_attribution_field_is_covered`, `test_a_declared_attribution_is_not_flagged`
  and `test_attribution_endpoints_are_not_judged_without_declarations`
  (`tests/test_aspect_guards.py`).

### ISS-2 — Every category guard read `elements`, so the attribution axis was unmeasured (fixed 2026-10-03)

- **Reported as.** The reason ISS-1 could exist while every existing check was green:
  `inv_no_tech_leak` and `inv_no_style_as_element` both inspected `out["elements"]`.
- **Fixed.** `_element_reference_occurrences`
  ([run_extraction_tests.py](scripts/run_extraction_tests.py)) yields every place a
  pass names an element and which list it named it in — `elements`, both connection
  endpoints, and the four attribution lists — and both invariants read it. The style
  rule is fed those names rather than restating the vocabulary, so there is still one
  copy of it.
- **Boundary, deliberate.** Triples are NOT scanned. `X uses_technology Docker` names a
  technology legitimately, and `_TECH_LEAK` contains `docker`, `grpc` and `aes-256`
  precisely because those are used rather than run; running an element rule over triple
  endpoints would flag correct output, and a guard that reports a correct graph is
  worse than none.
- **Pinned by** `test_a_leak_through_an_attribution_list_is_caught`,
  `test_a_leak_through_a_connection_endpoint_is_caught`,
  `test_a_style_named_in_an_attribution_list_is_caught` and
  `test_the_category_guards_stay_quiet_on_a_clean_run`
  (`tests/test_extraction_harness.py`).

### ISS-5 — Two regression tests assert counts against fixtures that were regenerated (fixed 2026-10-03)

- **Reported as.** A full regression was red for an environmental reason, which is how a
  real failure hides: `test_the_real_fixture_reports_both_directions`
  ([test_realization.py](tests/test_realization.py)) expected 22 requirements / 20
  unrealized / 13 obligations, and `test_the_two_lists_are_populated_from_the_same_report`
  ([test_app.py](tests/test_app.py)) expected `unrealized_count == 20`; the fixtures
  yielded 20 / 17.
- **Cause.** Both pinned absolute counts — and one pinned two element labels — measured
  from `data/output/test_req_prd.json` + `test_arch.json`, which are gitignored and
  regenerable. The assertions were measurements of an artefact rather than of the code,
  so regenerating the fixtures reddened the suite; the same test also asserted
  `"AlpineJS UI Framework"`, a label the graph now carries as `AlpineJS`.
- **Fixed.** Both tests derive what they can and relate what they cannot. The requirement
  count is derived independently from the fixture's own entities, and the rest are
  identities the report must satisfy whatever the fixture says
  (`requirements == realized + unrealized`, `unrealized == none + unresolved`,
  `claims == obligations == unbound_claims`, `proposed + unproposed == unbound`), with
  non-vacuity assertions so they cannot hold trivially on an empty graph. The page
  assertion reads its labels back off the API instead of naming them, which makes
  page/API agreement the thing tested rather than two hardcoded strings.
- **What was lost.** The absolute numbers were a cross-check against ADR-0006's original
  measurement. Restoring that needs the fixtures either tracked — they are extraction
  OUTPUTS, and `data/` is excluded wholesale by design — or regenerated and re-recorded.
  That is a decision, not a patch.
- **Pinned by** `test_the_real_fixture_reports_both_directions`
  (`tests/test_realization.py`) and
  `test_the_two_lists_are_populated_from_the_same_report` (`tests/test_app.py`).

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
