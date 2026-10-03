---
id: YB-066
legacy: null
title: "Ask the graph: a named-question registry, then a router, then SPARQL — never a model narrating the graph"
status: open
priority: medium
area: "`core/questions.py` + `ontology/catalogues/questions.yaml` (the registry), `core/qna/` (router, log), `agents/qna/` (classifier, agent), `app/` (the `/ask` route), `core/knowledge/rdf.py` (the query surface YB-010 wrote — allowlist, no raw SPARQL), `app/projections.py` (the answer-shaped functions the registry wraps), `scripts/bench_traversal.py` (the networkx measurements)"
created: 2026-10-03
updated: 2026-10-03
design: docs/design/natural-language-enquiry.md
record: null
superseded_by: []
related: [YB-010, YB-056, YB-057, YB-005, YB-047]
blocks: []
blocked_by: []
---

# YB-066 — Ask the graph

> **Open.** Fleshes out §C of [graph-interaction.md](../../design/graph-interaction.md), whose
> §5 records that the NLP estimate was a hypothesis with no prototype. The design doc
> above turns it into a routed design with measurements; this entry is the work.

## The request

Interact with the graph in natural language, for three kinds of enquiry: **structure**,
**what needs a decision**, and **impact of a change**.

## What the survey found

Two of the three are a front-door problem rather than a capability gap:

- **Structure** is already answerable — `project_gap_report`, the C4 view, the map lenses
  — it is just reached by clicking through the right page.
- **Decisions needed** is answerable *and already written*: `core/knowledge/rdf.py` holds
  four named SPARQL queries (`unverified`, `missing_active`, `human_overrides`,
  `partial_runs`) and a `run_query`, and **nothing in the product calls any of them** —
  only `scripts/test_knowledge_layer.py` does. The only reachable RDF path is
  `/export/graph.ttl`.
- **Impact of a change** has nothing behind it at all. That is the real gap.

## The shape (added 2026-10-03, from the agent question)

A QnA agent with a chat interface and tools over the graph is the right shape — §C.3 of
graph-interaction names it ("the agent calls a *function* that returns rows, it does not
narrate the graph"), and the front-door framing is both the justification and the limit: the
agent **selects a tool, shows the result and carries the caveat**; it does not derive the
fact. Three readings of "given the graph" split, and only two are safe — graph as *context*
to narrate is the YB-010 failure mode and does not fit (201 nodes / 781 assertions); graph as
*tools* is the design; and the **ontology** as context is the useful one, because the
vocabulary is what maps a user's words onto the graph's classes, and every agent already
receives it (`_format_ontology_context`, ~5,174 chars).

Four requirements follow, and each is a requirement rather than a preference: **read-only,
enforced** (`run_query` applies no guard today, and handing it to an LLM makes the guard
mandatory — this platform's property is that agents propose and never write); **the caveats
live in the tool's return value**, not the model's discretion, because a summary that drops
"not auditable yet" or "39 unresolved references" is less honest than the page it summarises;
**scope and revision are inputs**, since no frozen baseline exists on either live scope; and
**the answer is a pointer** to the deterministic surface, because that is where the reviewer
acts and a chat log is not an audit trail.

The counter-point stands and is recorded with it: if the answers already exist, chat is only
worth building if the routing problem is real, so §9.2's first step is also the falsification.

## The constraint

Inherited from YB-010 and not negotiable:

> **NLP translates to a deterministic query. The engine answers. The query is shown.**

An LLM narrating the graph is the "rigorously-derived wrong answers" failure with a chat
box. So the model's job is **classify and fill slots against a registry of named
questions**, never generate a query and never summarise rows into an architectural claim.
`no_named_question` is a first-class outcome that lists the nearest entries.

## How it links to YB-010 (rdflib, SPARQL)

Concretely, not aspirationally: YB-010 supplies the query surface, the four queries already
written, the ARC-G ⇄ REQ-G join (YB-005), SHACL for closed-world gap auditing, and named
graphs per revision for "since the baseline". And its gate is **met** — the 2026-09-30
update records `inv_ontology_class_coverage` at 100% on `req_sample` (48/48) and `req_prd`
(59/59), with two caveats recorded there.

The relation runs both ways, which is the useful part: **this item is the strongest reason
to finish YB-010**, because it is what turns a written-and-tested query surface into a
product surface. It also inherits YB-010's OWL warning — an impact question is an
entailment question, so "nothing depends on this" is a claim the graph usually cannot
support and must be phrased as "these are the dependents it records".

## How it links to networkx

`networkx>=3.2` is a declared dependency used only by `scripts/bench_traversal.py`. That
bench, re-run for this analysis, splits the answer:

- **Traversal: networkx loses.** At 5k nodes it builds 3.5–7.5× slower than a plain dict
  index, and its transitive `part_of` walk is **121 ms against 15.6 ms for a cached dict
  walk** — 0.3× *today's* code. The win is an *index*, not a graph library.
- **Algorithms: networkx earns its place.** On the live scope, `articulation_points` (15 cut
  vertices, 0.81 ms), `all_simple_paths`, `betweenness_centrality` and `shortest_path` all
  work cheaply, and hand-rolling Tarjan or Brandes is real work with real failure modes.
- **One gap to decide:** `numpy` is not a dependency, so `nx.pagerank` raises
  `ModuleNotFoundError`. The linear-algebra surface needs a dependency decision; the table
  above does not.

Routing rule: **traversal stays hand-rolled and indexed; networkx is reached for
algorithms** — and a centrality score over a graph with unresolved references measures the
extraction, not the architecture, so it must be presented with the same caveats as impact.

## The impact caveat, which is the item's hard part

Every impact answer must carry its assumptions: which scope, which revision or baseline,
how many references are unresolved, and which components were searched. Measured on
`payments_v2`: **39 unresolved cross-graph references** and **27 components**, so a
reachability answer underestimates impact by exactly the unreconciled part, and "nothing
reaches X" often means "X is in another island". On `payments_v3` the unresolved count is
0 — and an impact answer there is not reliable, it is **vacuous**; those two states must
not look alike.

## Corrected, 2026-10-03 — the decision/trade-off substrate, verified

`d831adf` (*"record decisions and structured trade-offs in the graph"*, now HEAD) added
`ArchitectureDecision` ingest, `TradeOff` nodes, the design digest's rendering of both, and
`tests/test_decisions.py`. That genuinely moves the impact class, and it is why this section
exists. Four specifics in the re-reasoning needed correcting against the shipped code, and two
of them change what stage 4 is.

| Stated | Shipped |
|---|---|
| `affects_element` | **`affects_elements`** — plural, multivalued, range `ArchitectureElement` |
| trade-offs linked "from the owner by `has_trade_off`" | `has_trade_off` is real, but written by ingest for **technique, pattern, style** only (`ingest.py:649`, `:693`, `:710`), matching the three ontology `trade_offs` slots. **No `trade_offs` on `ArchitectureDecision`** |
| "38 ADRs… carrying `affects_element` and `supersedes`" | ADRs carry **neither** |
| "I populated precisely the substrate" | Implemented and committed; **0 `ArchitectureDecision` and 0 `TradeOff` nodes** in all five revisions of both scopes [M] |

**The correction that matters.** `core/knowledge/decisions.py` states it outright:

> `consequences`, `alternatives_considered`, `affects_elements` and `supersedes` are **NOT
> inferred from prose** — those richer links come from the Design Assistant's decisions pass,
> which is a proposal, not an extraction.

So the 38 recorded human decisions are **disconnected from the element graph**: the edges exist
only for *proposed* decisions. "Which decisions govern X?" would answer from the proposals and
return nothing for the record — and the record is the authority. That inverts who the answers
come from, which is the opposite of an improvement.

**The traversal is not decision → trade-off.** It is two paths that meet at the *element*:

```
X ←── affects_elements ── proposed decisions          (ADR decisions: no edge at all)
X ──→ style | pattern | technique ──→ trade_offs ──→ gains / sacrifices
```

"Changing X reverses these recorded decisions and their trade-offs" is therefore, today,
"…reverses these *proposed* decisions; and separately, X's style/pattern/technique bought these
attributes at the cost of those."

**Architectural consequence is unchanged** — recording *why* moves the line; the *rightness* of
a trade-off stays the person's call. That part of the re-reasoning stands, and the
same-graph property of `affects_elements`/`supersedes`/`has_trade_off` (nothing routes them
cross-graph) confirms it is indexable rather than requiring the SPARQL surface.

Two sharpening notes from the same verification:

- **Every one of the 38 ADRs is `status: accepted`** [M], so filtering by status does not
  discriminate *among records* — it discriminates records from **proposals**, which do carry
  non-accepted statuses. Provenance (`HUMAN_ARCHITECT`) marks the same split, but status is the
  richer signal because a proposed decision can be rejected while remaining a proposal.
- `decision` is set to the ADR **title** and `context` to a ≤300-char first paragraph [M]; for
  ADRs, `consequences` and `alternatives_considered` are declared in the schema and **not
  ingested at all**. The shipped record is thinner than the schema implies.
- The loader is sound on the real corpus — **38 files, 38 records, 0 skipped** [M] — but
  `test_the_real_adr_directory_parses` asserts only `len(records) >= 10`, and skipping is
  *silent by design* ("a file with no parseable frontmatter is skipped rather than guessed").
  Twenty-eight ADRs could stop parsing without a test failing. The bound should be the corpus.

**So stage 4 is not "expose a projection over data that exists"** — it is *close the
record → element link first*. That is [YB-067](YB-067-adr-frontmatter-declares-the-elements-it-governs.md),
and it is a decision about ADR frontmatter rather than a coding task.

## The plan, preserved (2026-10-03)

Kept here rather than in a conversation thread: the stages below are the work item, and
everything above is only their argument. Deltas forced by the RDF fixes are marked
**[e2bd3eb]**.

**1. The registry — a declarative catalogue of named questions**

- `core/questions.py`: a frozen `QuestionEntry` (`id`, `intent`, `question`, `params`,
  `engine`, `caveats`, `pointer`) plus `QuestionRegistry`, `load_question_registry(path)` and
  `question_prompt_context(registry)` — mirroring `core/patterns.py`'s `PatternCatalogue`, the
  proven "bounded vocabulary + resolution" shape. **[e2bd3eb]** the entry also needs
  `needs_hierarchy: bool` and `current_state: bool`; see stage 2.
- `ontology/catalogues/questions.yaml` as the seed. That directory already exists
  (`architecture_patterns.yaml`), so this is an established home rather than a new one.
- A missing or empty registry degrades to "no questions" rather than crashing, as a missing
  pattern catalogue does.

**2. The engine layer — one uniform, read-only tool contract**

- `AnswerShaped(state, result, caveats, pointer, assumptions)`; engine signature
  `(graph, scope_id, ref, params) -> AnswerShaped`. **[principles]** `state` is
  `answered` | `substrate_absent` | `no_named_question` | `out_of_scope`, and
  `substrate_absent` names what is missing and the item that owns it.
- Adapters over the existing projections so `gap` / `quality` / `realization` / `delta` speak
  one return shape.
- ~~The decisions index (`decisions_for(element)` over `affects_element` + `has_trade_off` +
  `supersedes`)~~ — **deferred to [YB-067](YB-067-adr-frontmatter-declares-the-elements-it-governs.md)**.
  It cannot be built as written: the slot is `affects_elements`, decisions own no trade-offs,
  and the recorded decisions have no edge into the element graph at all.
- `run_named_query(graph, name, limit, *, current_state, ontology_dir)`, accepting **only
  `QUERIES` keys** — the allowlist is what forbids a model emitting raw SPARQL. **[e2bd3eb]**
  - `current_state` comes from the ENTRY, because `include_superseded` is part of a query's
    meaning: a retired implementer is not an implementer.
  - It must **refuse to answer** when a `needs_hierarchy` entry finds no class hierarchy.
    Measured: a property-path query returns 0 rows with no ontology and 1 with it, so "0" would
    read as "nothing is missing".
  - **`missing_active` is NOT registered.** It is fixed and deliberately unregistered, with the
    three measured reasons in its own comment.

**3. Router and classifier — select an entry, never generate a query**

- `core/qna/router.py`: a keyword/synonym map to `(intent_id, confidence)` or `None`, returning
  nearest entries on a miss. The day-1 falsification, and the fallback.
- `agents/qna/classifier.py`: `ClassifiedQuestion{intent_id, slots, confidence}` and
  `NoNamedQuestion{nearest}`, produced through `invoke_structured` (exists, `base_agent.py:981`)
  from `question_prompt_context(registry)` + the ontology context + the question. It selects and
  fills slots; it is never given query text to emit. **[e2bd3eb]** the router should SHORTLIST and
  the classifier choose among the shortlist, so registry growth does not grow the prompt linearly.

**4. The agent loop**

- `agents/qna/agent.py`: `QnAAgent.run({question, scope_id, ref, initiative_id})`.
- Resolve the graph from `ref` the way the design path does — `resolve_design_baseline(store,
  graph)` (`app/runner.py:279`): a frozen `load_revision(ref)`, or the promoted `SYSTEM_BASELINE`,
  or `load_working()` — and record which in `assumptions`, because an impact answer that cannot
  name its revision is vacuous.
- Loop: keyword router → classifier on a miss or low confidence → dispatch the entry's `engine`
  with the filled `slots` → `AgentResult` carrying
  `{answer, caveats, pointer, matched_intent, confidence, assumptions}`.
- `no_named_question` is a first-class output: nearest entries plus a log event, never a
  low-confidence guess. **[risk]** the classifier must degrade to the keyword router on timeout
  or error, or the whole front door depends on the LLM being up.

**5. CAN'T ANSWER log, route, tests**

- `core/qna/log.py`: one JSON line per unanswered question — `{ts, question, matched_intent,
  confidence, nearest_entries, scope_id, ref, initiative_id}` — to a **gitignored log under the
  store root** (`<store_root>/.qna/unanswered.jsonl`), not the repo root, so it follows
  `SEA_DATA_DIR` and does not mix workspaces.
- `POST /ask` taking question + scope/ref, rendering the answer with its caveats and a deep link
  to the deterministic surface via `pointer` — so the chat log is never the audit trail.
- `tests/test_qna.py`: registry resolution, router, classifier (mocked `invoke_structured`), tool
  dispatch, the `run_named_query` allowlist, the JSONL log, `no_named_question` — registered under
  a **new area** in `scripts/test_report.py` rather than widening an existing one.
- **[e2bd3eb] two guards, each generalising a defect found while verifying**: every registered
  query returns ROWS on a fixture (ISS-16 was a query nothing ran and nothing tested, so a name in
  an allowlist with no positive test is a landmine), and every entry's `pointer` resolves route
  and params (six broken relative links survived in this repo because the check validated anchors
  and never paths, and a declarative catalogue of deep links is where that recurs).

**Assumptions carried:** the registry lives in a declarative file (design §8 decision 1) rather
than an `app/` table; `pointer` is a route plus params; read-only is structural — the engines are
projections and allowlisted queries, and `to_rdf` builds a derived copy; and promotion to a
registry entry stays a human-reviewed code-plus-test change, never auto-registration.

## Recommended order

1. **The registry, with no model** — named questions over the deterministic answers that
   already exist. Useful with no NLP at all.
2. **A keyword router** over it. Falsifies the interaction model in a day.
3. **YB-010's query surface, then SPARQL-backed entries** — the four existing queries get
   their caller, and `missing_active` gets registered in `QUERIES` (defined, never named [M]).
4. **[YB-067](YB-067-adr-frontmatter-declares-the-elements-it-governs.md) — the record → element
   link**, then a decision index over it (element → decisions, decision → `supersedes`), exposed
   as an answer-shaped function. The *nodes* exist; the *edges from the record* do not.
5. **Impact, last and explicitly** — the class with no precedent and the highest cost of a
   confident wrong answer, and the one whose substrate turned out to be half-wired.

## Which engine owns "does this requirement have an architecture?", 2026-10-03

Settled by measurement while preparing the SPARQL class, and it changes the registry seed:

- **`realization_report` owns it.** It reports four coverage states — `none` 3, `unresolved`
  10, `partial` 0, `full` 3 on `payments_v2` — plus the counts, the caveats and the
  completeness gate. A SPARQL query for the same question cannot reproduce them, because
  binding a reference is `reference_targets_a_node`'s job and not a graph pattern's.
- **A gap query is the EDGE form only.** The corrected `QUERY_MISSING_ACTIVE` returns **15**
  on that scope against the projection's **3** with no claim, because it merges "nothing
  cited this" with "something cited it and reconciliation has not bound it". Two findings,
  two fixes. If such a query is ever registered it must be named for what it answers
  (`has_bound_implementer`) and carry that limitation as a caveat.
- **`missing_active` stays unregistered**, and its body is now correct rather than silently
  empty — see [YB-010](YB-010-rdf-knowledge-layer.md) and [ISS-16](../../../ISSUES.md#iss-16--a-named-sparql-query-was-silently-always-empty-and-nothing-tested-it-fixed-2026-10-03). Registering it would have
  made an always-empty result the authoritative answer to the platform's headline question.

So the registry's seed for this class points at `realization_report`, `project_gap_report`
and the four named queries *minus* `missing_active` — not at a new SPARQL query. Query text
is only ever reached through the allowlist, and `run_named_query`'s allowlist is what keeps a
model from emitting raw SPARQL.

## Reviewed against `e2bd3eb`, 2026-10-03 — what the RDF fixes changed for this plan

The RDF layer landed (`to_rdf` emits `rdfs:subClassOf`; `missing_active` fixed but
unregistered; every registered query has a fixture — ISS-16, ISS-17). Four consequences, and
one of them is a risk the fix *introduced*.

**1. "Register `missing_active`" is DELETED from the plan.** It is fixed and deliberately
unregistered, with the three measured reasons in its own comment. The decisions-needed class
therefore routes to `realization_report`, `project_gap_report` and the **three** registered
queries — `unverified` (the queue), `human_overrides` (the audit trail) and `partial_runs`
(run metadata, not a decision at all). Of the original four, only `unverified` is really a
"what needs a decision" question.

**2. Subclass-aware SPARQL now works, but only through a property path.**
`?x a sea:Requirement` still matches nothing, because `Requirement` is abstract and nodes
carry concrete classes; `?x a ?k . ?k rdfs:subClassOf* sea:Requirement` now works. Only four
classes are abstract (`ArchitectureElement`, `EnterpriseConstruct`, `GovernanceInstrument`,
`Requirement`), and none of the three registered queries touches one — so the hierarchy
benefits future entries, not the current ones. **A lint should hold the line**: no registered
query may mention an abstract class without a property path, since the failure is a silent
empty result.

**3. NEW RISK THE FIX INTRODUCED: an absent hierarchy is a silent-empty path.** `to_rdf`
degrades to no hierarchy when the ontology cannot be read, and a property-path query then
returns **0 rows** — measured, against **1** with the ontology present. That is
indistinguishable from "nothing is missing", which is the ISS-16 failure class one layer down
and now reachable through a supported path. So each entry needs `needs_hierarchy`, and
`run_named_query` must **refuse to answer** rather than answer "none" when the hierarchy is
absent. An empty result is only trustworthy when the substrate that could have produced a row
was present.

**4. `include_superseded` is part of a query's meaning, so the ENTRY declares it.**
`run_named_query` must take the graph form from the registry, not default it: an "active"
answer needs `include_superseded=False` (a retired implementer is not an implementer), a
lineage answer needs `True`. It cannot be decided inside the query.

Two guards the fixes argue for, each generalising a defect found while verifying:

- **Every registered query returns ROWS on a fixture.** ISS-16 was a query nothing ran and
  nothing tested; a name in an allowlist with no positive test is a landmine. Test
  non-emptiness, not merely the absence of an error.
- **Every registry entry's `pointer` resolves.** Six broken relative links survived in this
  repo because the check validated anchors and never paths. A declarative catalogue of deep
  links is exactly where that recurs, so the registry needs a test that each pointer's route
  exists and accepts its params — which also forces the `/quality` and `/gaps` gap below to be
  faced rather than assumed.

**Unchanged by the RDF work**: the decisions index (Correction 1, blocked on
[YB-067](YB-067-adr-frontmatter-declares-the-elements-it-governs.md)), the missing deep-link
params on `/quality` and `/gaps` (Correction 2), and the CAN'T ANSWER log's location
(Correction 3).

## The principles question — plan the BOUNDARY prior, not the wiring (2026-10-03)

The finding is correct, and verified: `Principle --informs--> Policy --enforced_by--> Control
--satisfies--> StandardClause` (plus `Control --mitigates--> Risk`) exists in
`governance_base.yaml`; the ArchitectureRationale quartet is `ArchitectureStyle`,
`ArchitecturePattern`, `DesignTechnique` and `EngineeringConvention`; and **no slot links an
architecture element to any governance class** — every governance range is inside the layer.
The nearest existing thing is `EngineeringConvention.conformance`, a STRING on the rationale
side (`"conformant"` / `"non_conformant"`), which cannot join to a governance node.

So *"which principles does this design satisfy or contradict?"* is unanswerable today. But the
measured reason is deeper than the missing slot, and it decides the sequencing:

| | |
|---|---|
| governance nodes in either live scope | **0** (`Principle`, `Policy`, `Control`, `Risk`, `Strategy`, `StandardClause`) |
| profiles importing `governance_base` | **none** — deliberately, see below |
| why | the import is what put the architecture scaffolding over its document (1.02:1, YB-007) |
| governance corpus in `test_data/` | **none** (only `arch/`, `prd/`, `test_cases/`) |

[YB-047](YB-047-enterprise-governance-layer.md) has already decided **both** conformance
targets — `conforms_to → StandardClause` and `realizes → Control`, kept separate because they
find different gaps — and deferred the wiring precisely because it requires
`architecture_base` to import `governance_base`. It also plans an ingestion path this codebase
does not have: a direct curated load with no model in the loop, because policies are
authoritative structured artefacts and recovering them by sampling would import the
non-determinism the platform exists to remove.

**Building this PRIOR would therefore be wrong.** It sequences this item behind a
prompt-budget-sensitive import, a new ingestion path, and two slots — and the import is the
one thing YB-047 says must be settled with a budget plan, not taken free.

**What the finding does add, and it is worth doing prior because it is cheap:** this plan has
no vocabulary for *"the platform is silent"* versus *"the graph is silent"*. Principles are the
clearest case — the domain can pose the question, the ontology anticipates it, and the graph
cannot answer because nothing was ever ingested — but they are not the only one. The same
`0 nodes` shape was measured this session for the hierarchy on `payments_v3` (0 containment
edges), its open references (0), and the decisions index. Reported as an answer, each reads as
"none", which is the vacuous-answer failure this whole design exists to prevent.

So, prior and cheap:

1. **`AnswerShaped` gains `state`** (see stage 2 in the preserved plan): `answered` |
   `substrate_absent` | `no_named_question` | `out_of_scope`, where `substrate_absent` names
   what is missing and the item that owns it.
2. **The registry seeds the principles question as a DECLARED entry returning
   `substrate_absent`**, pointing at YB-047 — not omitted. The answer becomes informative ("no
   governance instruments are in this scope yet — YB-047") instead of silence.
3. **The CAN'T ANSWER log records it**, so demand for the governance wiring is measured rather
   than guessed. That is design §4.3's own argument ("list the questions users ask that no
   report answers") applied to the one class where the answer is known to be absent.

## Landed 2026-10-03 — the model-free core (stages 1, 2, 3-router, 5-log)

The slice the design argues for first: everything here is useful with no model at all,
and it is the falsification the plan asks for before any classifier exists.

| Module | What it is |
|---|---|
| `core/questions.py` | The registry: frozen `QuestionEntry` / `QuestionRegistry`, `load_question_registry`, `validate_question_registry`, `question_prompt_context` — mirroring `core/patterns.py`, including that a missing file degrades to empty with findings |
| `ontology/catalogues/questions.yaml` | 12 entries: 9 answerable (gap, quality, realization, containment, delta, review queue, and the three registered queries) and **3 declared absent** |
| `core/qna/answers.py` | `AnswerShaped(state, result, caveats, assumptions, detail, blocked_by, source, truncated)` and `AnswerContext`; the four states, with `compose` attaching the ENTRY's caveats and pointer so an engine cannot drop them |
| `core/qna/router.py` | The keyword router — the wedge and the classifier's fallback. Phrase keywords match ALL their words across intervening ones |
| `core/qna/log.py` | The CAN'T ANSWER log: one JSON line per event under the store root, append-only, malformed lines kept as findings |
| `app/qna/engines.py` | Eight engines and the dispatcher, plus `run_named_query` — the allowlist that refuses query text |
| `tests/test_qna.py` | 32 tests, in a new `qna` area: "Can the graph be asked something in words without a model narrating it?" |

**What the tests are mostly about is refusal**, because that is where this could go wrong:
an unanswerable question is not guessed at (and the refusal lists what the vocabulary does
cover), `run_named_query` raises on raw SPARQL and on `missing_active`, a
hierarchy-dependent query refuses rather than reporting empty when `rdfs:subClassOf` is
absent, every registered query is asserted to return ROWS on a fixture built for it (the
ISS-16 guard, generalised), and every entry's pointer is asserted to resolve to a route
that accepts its params — which is what pins the measured `/quality` and `/gaps` gap.

**Routing, measured** on ten real phrasings: 10/11 route as intended, and the eleventh is
a deliberate tie broken by vocabulary ("assertions" belongs to the query entry, "review"
to the queue). Two misses found while building it are worth recording because they are
the wedge's whole risk: requiring contiguous phrase matches lost the registry's own
canonical question ("no architectural answer" does not contain "no answer"), and
"decision" alone routed to the review queue when it means a recorded decision. Both are
fixed, and both are pinned by tests.

**Not in this slice:** the `/ask` route and page, the LLM classifier and agent loop
(stage 3-LLM and 4), the impact engine (stage 4, last by design), and the store-root
wiring for the log path (the module takes a path; nothing calls it from the app yet).

## What closes it

A registry with tests, a router that reports `no_named_question` rather than guessing, at
least one SPARQL-backed entry reaching the four existing queries, a decision index that
answers for **recorded** decisions and not only proposed ones, and an impact answer that states
its scope, its unresolved-reference count and its assumptions.
