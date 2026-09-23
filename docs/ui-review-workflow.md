# The Review Gate — Extraction Projection, Review and Change Management

**Status:** Implemented (MVP UI) · `app/` + `core/knowledge/`

This documents the first end-to-end path that actually *delivers* something:
extraction output can now be **reached, judged, and acted on** by a human. Before
this, the pipeline's only exit was a JSON file. That was the finding in
[`user-journey.md`](user-journey.md) §3: the journey was blocked at the human
review gate, not at extraction quality.

---

## 1. The three artifacts

Conflating these is what makes "verified" a flag on a mutable graph instead of a
state (`user-journey.md` gap 3). They are kept separate on disk and in the UI.

| Artifact | What it is | Lifetime | Where |
|---|---|---|---|
| **Working set** | the graph under active review | autosaved on every change | `data/sea/working.json` |
| **Revision** | a deliberate, immutable snapshot | append-only | `data/sea/revisions/<rev_id>.json` |
| **Baseline** | a revision that has been frozen | the reference later work is compared to | same file, `kind: baseline` |

A revision is never rewritten. Freezing does not copy the graph; it marks the
revision, because a baseline *is* a revision you have stopped editing.

---

## 2. The stages, and where they are in the UI

```
INGEST ──▶ REVIEW ──▶ RECONCILE ──▶ COMMIT ──▶ FREEZE ──▶ (AUDIT)
  │           │           │            │          │
/ingest    /review    /reconcile   /changes   /changes
```

| Stage | Page | What the human does |
|---|---|---|
| **Project** | `/` | see the state of the graph: counts, completeness, what still needs attention |
| **Ingest** | `/ingest` | upload or paste a document; choose requirements vs architecture |
| **Review** | `/review` | confirm, correct, dispute or reopen individual assertions |
| **Reconcile** | `/reconcile` | bind unresolved cross-graph references to the nodes they mean |
| **Change management** | `/changes` | commit revisions, freeze a baseline, promote to baseline, read the audit trail |
| **Diff** | `/changes/diff` | see what a run added, changed or removed before accepting it |
| **Gaps** | `/gaps` | unresolved references, dangling assertions, completeness — and whether absence is even meaningful |
| **C4 view** | `/c4` | the architecture as a C4 viewpoint (context / container / component) |

`/ontology` is deliberately not in that table: it is a **reference**, not a stage
of the workflow. It describes the vocabulary rather than the work — see §8a.

---

## 3. Decision semantics

Every decision is recorded as a `Decision` carrying what changed, who, when and
why. That log is the answer to *"why does the graph look like this?"* — the
question an auditor actually asks.

| Action | Effect | Notes |
|---|---|---|
| **Verify** | `status → VERIFIED`, provenance flips to `HUMAN_REVIEWER` | the human vouches for the agent's claim |
| **Correct** | writes a *new* assertion, marks the old `SUPERSEDED` with `superseded_by` | see below |
| **Dispute** | `status → DISPUTED`, stays in the graph | silently deleting a rejected claim is worse than keeping it flagged |
| **Reopen** | `status → UNVERIFIED`, provenance returns to the agent | undoing a decision is itself a decision |
| **Bulk verify** | verifies many at once, by selection or by confidence threshold | selection is **re-derived from the graph**, so a stale page cannot verify assertions the reviewer never saw |
| **Promote to baseline** | `scope INITIATIVE_PROPOSAL → SYSTEM_BASELINE` for reviewed facts | a baseline does not absorb unchecked extraction |

### Correction is supersession, not mutation

Assertion identity is content-addressed (`make_assertion_id` over
`subject|predicate|object|value`), so changing a target changes the assertion's
id. Correcting therefore **writes a new assertion and supersedes the old one**.

Mutating in place would:

- erase the fact that a human changed their mind, and
- break the re-extraction merge — the agent would re-assert the old fact and,
  with no superseded record, it would fold back in alongside the correction.

One edge case is handled explicitly: correcting a target to *the same value*
folds onto the original assertion, so the record must not point at itself.

---

## 4. Reconciliation — binding unresolved references

An **unresolved reference** is an assertion whose predicate points into the other
graph (`implements_requirement`, `traces_to_goal`, `supports_capability`, …) and
whose target was kept as a literal, because ingest refuses to invent a node for
it. `/reconcile` binds that literal to a node that already exists, producing the
object-valued assertion the link always claimed to be.

**Resolution is not `review.correct()`.** `correct()` deliberately will not turn a
cross-graph reference into a node — doing that silently would erase the
difference between "resolved" and "referenced but not yet reconciled". Resolution
is the opposite act: a deliberate, recorded decision that the referent is known.

### The machine proposes, the human decides

Candidate matching is deterministic and explainable, with the reason recorded:
`exact`, `external_ref` (the document's own stable key), `contains`, `tokens`.
Nothing is bound without an explicit threshold, and **never across kinds**.

Kind scoping is not optional. Measured on the real ARC-G output, unscoped
best-match picks `Concept:'Card Payment Processing'` (0.94) over the correct
`BusinessCapability:'Unified Payment Processing'` (0.92) for
`supports_capability → 'Payment Processing'`. Lexical similarity alone prefers the
wrong kind of thing. Each predicate therefore declares the kinds it may point at
(`EXPECTED_TARGET_KINDS`); a candidate outside them is shown as a near miss that a
human must override deliberately, and bulk resolve ignores them entirely.

### What bulk resolve reports

`bulk_resolve` binds every reference whose best *expected-kind* candidate clears
the threshold, and returns what it declined:

| Outcome | Meaning |
|---|---|
| `resolved` | bound, audited, `VERIFIED`, superseding the literal |
| `below_threshold` | a candidate exists but is weak — left for a human |
| `no_candidate` | nothing of the expected kind at all |
| `unknown_ids` | the caller's selection included references no longer open (stale page) |

The default threshold is 0.75 and deliberately conservative: a wrong traceability
link is worse than a missing one, because it makes the downstream audit
confidently wrong.

### The useful failure: `mislabel_suspected`

A strong match in the *wrong* kind usually means the predicate is wrong, not that
the target is missing. On the real ARC-G output at the default threshold, two
`traces_to_goal` references have no `BusinessGoal` candidate at all but score 0.90
and 0.86 against `FunctionalRequirement`s — the extractor attached a goal
predicate to a function name. Four more sit at 0.42–0.62 in the wrong kind and
appear if the threshold is lowered. Reporting this is more useful than "no target
found", because the fix is upstream.

### Measured on the real fixture

| | |
|---|---|
| Unresolved references | 13 |
| Resolvable at 0.75 | 1 |
| Below threshold | 3 |
| No candidate of the expected kind | 9 |
| Predicate looks wrong | 2 |

This is the honest state of the project's own data, and it is the point. The
saved fixture carries **zero** `requirement_id` values (0 of 54 entities) and no
`references` entries at all — it predates the ID-preservation work. Reconciliation
cannot invent what extraction never emitted.

### A bug this feature exposed

The requirements profile emits `requirement_id`; ingest read only the architecture
profile's `external_references`, so the document's own stable key never reached
the graph. **An identifier the graph does not record cannot be matched on** — the
strongest signal was structurally unreachable, whatever the extractor did. Fixed
in `ingest._external_refs`. It is forward-looking for the saved fixture, and
`test_a_preserved_id_joins_two_documents_end_to_end` proves the path works.

---

## 5. Re-extraction is merge + diff, never replace

`merge_graphs(base, incoming)` routes every incoming fact through
`KnowledgeGraph.add_assertion`, which already encodes the fold rule:

- re-observing the same fact **raises confidence** and keeps the fuller source
  text, rather than creating a parallel duplicate;
- a **human assertion outranks an agent re-observation**, so corrections survive.

This is the "sleeper" problem from
[`architecture-review.md`](architecture-review.md) §3.3, handled at the point of
write rather than as an afterthought. `/changes/diff` exists so a re-run can be
reviewed as a change set instead of silently replacing what a human approved.

---

## 6. Why the audit gate refuses

`user-journey.md` §4 holds that an audit over **unverified** knowledge reports
extraction artifacts as architecture gaps — confidently wrong, which is worse
than no audit. Two independent conditions therefore close the gate:

1. **Nothing outstanding.** Every assertion has a human decision
   (`review_progress().is_auditable`).
2. **Extraction is COMPLETE.** `PARTIAL` / `FAILED` / `UNKNOWN` mean absence of a
   fact is *not* evidence of its absence. `UNKNOWN` is deliberately distinct from
   both `FAILED` (a false alarm) and `COMPLETE` (a false assurance).

`RevisionStore.freeze` raises `BaselineNotReady` on condition 1, with an explicit
`allow_unverified` override so bypassing the gate is a deliberate act, not an
accident. Note the gate checks the **revision**, not the working set: reviewing
after committing does not retroactively review the snapshot, so the flow is
review → **commit** → freeze.

---

## 7. Module map

| Module | Responsibility |
|---|---|
| `core/knowledge/model.py` | canonical graph: nodes, assertions, runs, provenance, delta |
| `core/knowledge/ingest.py` | extraction output → graph; `merge_graphs` graph → graph |
| `core/knowledge/serialise.py` | graph ↔ JSON, field-complete round trip |
| `core/knowledge/review.py` | decisions, audit trail, progress, baseline promotion |
| `core/knowledge/reconcile.py` | reference candidates (match + kind scoping), resolve, bulk resolve |
| `core/knowledge/store.py` | working set, revisions, freezing, diffing |
| `core/knowledge/rdf.py` | graph → RDF (plain triples + assertion resources) |
| `core/ontology.py` | **schema reader** — LinkML to a resolved model; no graph, no Flask |
| `app/projections.py` | **graph projection** — notation-agnostic rows, counters, edges, deltas |
| `app/viewpoints/c4.py` | **architecture viewpoint** — C4 levels and element selection |
| `app/ontology_reference.py` | **ontology reference** — the schema as a browsable structure |
| `app/__init__.py` | routes: orchestration only, no knowledge logic |
| `app/templates/`, `app/static/` | Jinja + HTMX + D3, no client build step |

The separation is deliberate: the CLI reviewer (`scripts/review_assertions.py`)
and the web gate must produce **identical graphs from identical decisions**, or
"verified" means different things depending on which door you came in through.

`core/` is the domain layer. **Agents and app may import core; core imports
neither** — which is what keeps the knowledge model usable without an LLM and the
web layer replaceable without touching the model. `tests/test_layering.py` holds
that line, and the CLI reviewer above is the proof it matters: it is a second
consumer of the same decisions.

---

## 8. Three kinds of view — do not fuse them

All three are loosely called "a view". Two of them were fused in one module until
they were split, and the fusion caused a real confusion: *projecting the knowledge
graph* and *projecting the architecture as a C4 view* are different things. Adding
the ontology reference makes a third, and it is the one most easily mistaken for
one of the others, because it is also "a graph".

| | Graph projection | Architecture viewpoint | Ontology reference |
|---|---|---|---|
| **Module** | `app/projections.py` | `app/viewpoints/` | `core/ontology.py` + `app/ontology_reference.py` |
| **Reads** | the instance graph (ABox) | the instance graph + a notation | the LinkML schemas (TBox) |
| **Question** | "make the graph readable and judgeable" | "describe the architecture in a recognised notation" | "what concepts exist, and how do they relate?" |
| **Knows about** | assertions, confidence, provenance, filters, deltas | C4 levels, element kinds, element detail | classes, slots, enums, `is_a`, mixins, imports |
| **Changes when** | the knowledge model changes | the notation, or the views offered, changes | the schema changes |
| **Page** | `/review`, `/gaps`, `/changes` | `/c4` | `/ontology` |
| **Domain-specific** | no | yes | the domain *is* the schema |

**The dependency direction is one way**: a viewpoint composes the projection
primitives (`node_records`, `edge_records`, `literal_facts`) and then *selects*; the
ontology reference imports neither, because it must stay usable by agents that have
no graph at all. Four tests hold the line —
`test_projection_layer_does_not_own_architecture_notation`,
`test_the_viewpoint_composes_projection_primitives`,
`test_the_loader_reads_schemas_and_not_the_graph`, and
`test_the_reference_view_is_duck_typed_on_the_graph`.

The schema and the instance graph meet in exactly one place: the `instances` column
on `/ontology`, a deliberately labelled cross-reference counting nodes per class in
the working set. That join lives in the *view* layer, where both sides happen to be
available, so the loader never learns about graphs. It is also the most useful
column on the page — a class showing 0 instances explains why an empty view
elsewhere is empty, without implying the schema is incomplete.

### Why the distinctions have consequences

A viewpoint is a **deliberate reduction**. C4's context level shows software systems
and the people who use them; a `Container` inside a system is real, is in the graph,
and is *not drawn* at that level. That omission is the viewpoint working — so the
view reports `excluded_kinds`, and a reader should never mistake "not at this level"
for "not in the graph".

Fusing them invites the opposite mistake: treating "the graph view" and "the C4
view" as one feature, so a change to C4 would be made by widening the projection
module, and the next notation (deployment, data flow, a Structurizr import) would
have no home. Calling the diagram "the graph" is the same confusion in a URL — the
old `/graph` is now `/c4`, with `/graph` kept as a redirect.

---

## 8a. What the ontology reference shows

`/ontology` exists because a list of class names is not comprehension. It presents
the schema structurally:

- **The import chain.** Four layers, one-way, with counts and the reason each layer
  exists. This is the ontology's own architecture: `requirements_base` may reference
  `enterprise_structure` and never the reverse, which is why enterprise constructs are
  defined once and both graphs speak one vocabulary.
- **`is_a` versus `mixins`.** The taxonomy versus the cross-cutting aspects. Each
  layer's *abstract root* (`EnterpriseConstruct`, `Requirement`, `ArchitectureElement`)
  mixes in `ExternallyReferenced` and `Provenanced`, so every class beneath inherits
  identity and provenance — but a mixin is not a taxonomy edge and is not drawn as one.
- **Inheritance, resolved.** `NonFunctionalRequirement` carries 33 slots of which 7
  are its own. Without resolving `is_a` a reader sees a fifth of the truth, so the slot
  table shows **own** slots and **inherited** slots separately, each inherited slot
  naming the class that declares it.
- **Relationships as opposed to hierarchy.** Slots whose `range` is another class are
  the actual concept-to-concept links (`BusinessRequirement.traces_to_goals →
  BusinessGoal`); primitive-ranged slots are not.
- **The binding classes.** `SubProductScope` and `SystemCapabilityBinding` exist only
  to break a circular import, and saying so is more useful than silently omitting them.
- **Integrity findings.** Unresolved supertypes and slot ranges whose type is neither a
  class, an enum, nor a primitive are reported rather than assumed. Currently: zero.

The focus view is laid out in **semantic rows** rather than by a force simulation —
supertypes above, subtypes below, relationship targets and incoming references further
out. Position therefore *means* something; a hairball of 61 classes teaches nothing,
and a physics layout teaches less because the arrangement is arbitrary.

### Measured state of the four schemas

| | |
|---|---|
| Classes | 61 (4 common, 7 enterprise, 29 requirements, 21 architecture) |
| Enums | 42 |
| Subsets | 13 |
| Slots declared | 504 |
| Slots whose range is another class | 162 |
| Abstract / mixin classes | 3 / 2 |
| Unresolved supertypes, unresolved ranges, duplicate names | 0 / 0 / 0 |

The loader's own resolution is checked against **LinkML's authoritative parser** for
every one of the 61 classes — ancestors and effective slot sets
(`test_resolution_matches_linkml`). Asserting against hand-written expectations would
only prove the loader is consistently wrong.

---

## 9. Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SEA_DATA_DIR` | `data/sea` | where the working set and revisions live |
| `SEA_REVIEWER` | `architect` | attribution for decisions in the audit trail |
| `SEA_INITIATIVE` | `INIT-MVP-001` | default Living System scope for ingests |
| `SEA_SECRET_KEY` | dev constant | Flask session key — **set this in any shared deployment** |
| `SEA_ONTOLOGY_DIR` | `ontology` | where the foundational LinkML schemas live — and where domain packs are discovered (`ontology/domains/`) |
| `SEA_DOMAIN_PACK` | *(empty)* | fallback domain pack for runs that do not select one; empty means none, which is a supported state |

---

## 10. Deliberately not built yet

Recorded so the gaps are choices rather than oversights.

- **Authentication / multi-user review.** One reviewer identity from config. A
  real deployment needs per-user attribution and role separation, since
  `Reviewer/Auditor` may be a distinct persona (open question 1 in
  `user-journey.md`).
- **Reconciliation at scale.** `/reconcile` binds references to nodes that already
  exist, one accepted match at a time. It does not create a missing target, does not
  invert the direction (requirements with no architectural answer), and its matching is
  lexical — no embeddings or model assistance, so a paraphrase with no shared vocabulary
  will never surface. On the real fixture that leaves 9 of 13 references unresolvable.
- **Semantic audit.** All checks are structural. "Does this design answer this
  requirement?" is not implemented (TODO item 9).
- **Correction merge conflicts.** While one document owns a graph, corrections are
  recorded as supersessions. The first time knowledge arrives from two sources,
  the overlay/conflict-resolution design (TODO item 9b) becomes necessary.
- **Only one viewpoint.** C4 is the only architecture notation implemented, and it
  is read-only: no layout persistence, no manual arrangement, no write-back from the
  canvas, and no parsing of structured C4 sources (Structurizr/PlantUML/Mermaid,
  TODO item 12). The viewpoint layer exists so the next notation has a home.
- **Concurrent writers.** Reads and writes go to disk per request. Fine for a
  single-process MVP; a multi-worker deployment needs locking or a real store.

---

## 11. Tests

`tests/` runs the real knowledge and web paths against a **fake extractor**, so
the suite needs no model server.

```
.venv/bin/python -m pytest tests/ -q      # 246 tests
```

| File | Covers |
|---|---|
| `test_serialise.py` | field-level round trip; `superseded_by` / `status` / provenance survival |
| `test_merge.py` | human corrections survive re-extraction; folding does not duplicate |
| `test_review.py` | decision semantics, supersession edge cases, bulk selection, promotion gate |
| `test_reconcile.py` | matching, kind scoping, resolve/bulk semantics, the `requirement_id` path |
| `test_store.py` | working set vs revision vs baseline; the freeze gate; ordering within one second |
| `test_projections.py` | graph projection, filters and the primitives viewpoints compose |
| `test_viewpoint_c4.py` | C4 level selection and what the view reports it is hiding |
| `test_ontology.py` | the schema loader, checked against LinkML across all 61 classes |
| `test_ontology_reference.py` | the reference view, and the layer-boundary guards |
| `test_app.py` | routes, HTMX partials, and the ingest→graph handoff regression |
