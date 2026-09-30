# Interacting with the graph — the queue, the picture, and the question

> **Analysis, not a plan.** Written in response to a reframing: the searches for a store
> engine were partly a search for *a simpler way to interact with the graph*. "Filtering
> through all 100s of entries will face resistance and bad UX. Complete graph rendered
> for traceability is not legible, not intuitive. Better views are required. Better ways
> to communicate with graph, maybe via NLP."
>
> That is a product problem, not a storage problem, and it is three problems wearing one
> coat. Conflating them is why the existing answers feel wrong. **[M]** marks what was
> measured on the live scope `data/sea_home_01` / `acme_pillar_01`.

---

## 1. The measurement that reframes it

**[M] The review queue is 486 items out of 613 active assertions.** That is the "100s of
entries". But look at what those 486 actually are:

| Bucket | Count | Does it need a human? |
|---|---|---|
| Literal, predicate **already enum-validated** (`element_type`, `c4_level`, `system_class`, `origin`, `deployment_model`) | **57** | No — `check_enum_membership` / `check_element_types` already decide it |
| Literal naming an ontology enum that is **not** in the check list (`quality_category` 29, `subcharacteristic` 28, `technique_category` 24, `technology_category` 18) | **99** | No — checkable by extending one list |
| Literal quotations (`description` 29, `description_text` 13) | **42** | No — span-anchoring (§3.6) checks a quotation against its source |
| **Relational** — points at another node | **175** | **Yes.** "Does this really implement that requirement?" is the architectural call |
| Other literals (`mechanism` prose 24, `style` 13, `requirement_type` 12, …) | ~113 | Mixed |

And the confidence spread of the undecided is **399 of 486 at `1.0`** — the extractor had
no doubt — with only 7 below `0.8`.

**So roughly 40% of the queue is transcription and classification, not judgement.** The
platform is asking a human to confirm 156 facts whose value comes from a closed vocabulary
it already holds, and 42 quotations it could check against the text it just read.

**The real defect is a conflation: the gate treats "recorded" and "vouched for" as the
same state.** An assertion is `UNVERIFIED` because no human touched it, and `VERIFIED`
because one did — with nothing in between for *"a deterministic check agrees"*. Every fact
that arrives is therefore a decision point, whether or not it is a decision.

**[M] It is already ranked, which is worth knowing.** The queue defaults to *lowest
confidence first* and carries eight chips (Needs review, Low confidence, Disputed,
Unresolved refs, Human-edited, Superseded, Removed) plus free text. So this is not a
missing-sort problem — ranking by model self-report is simply the weakest available signal,
and it puts 399 equally-certain items in an arbitrary order.

## 2. Three problems, three kinds of answer

| | Problem | Nature | The wrong answer | The right family |
|---|---|---|---|---|
| **A** | 486 decisions nobody wants to make | **Triage** | better filters over the same queue | decide what needs deciding at all |
| **B** | the full graph is not legible | **Comprehension** | more filtering, denser diagrams | project at the right altitude; prefer answers to pictures |
| **C** | asking the graph a question | **Expression** | an LLM answering from the graph | an LLM translating to a deterministic query |

### A. Triage — shrink the queue, then rank what is left

Cheapest first, and none of it needs a model:

1. **Give "machine-checked" a state.** Assertions whose classification a validator can
   decide should leave the human queue and be *recorded as checked, with the check named*.
   Not "verified" — the platform's credibility rests on a human having vouched — but a
   distinct state the audit can weigh. **[M] Extending `check_enum_membership`'s field list
   by four entries (`quality_category`, `subcharacteristic`, `technique_category`,
   `technology_category`) covers 99 of the 486 today**, and the vocabularies are already
   read from the ontology.
2. **Span-anchor quotations** (§3.6, recorded, unbuilt): a `description` that is a
   substring of its source chunk is checked; one that is not is a finding. 42 more leave
   the queue, and the ones that remain are the interesting ones.
3. **Rank by consequence, not confidence.** The reports are pure functions
   (`project_gap_report`, `project_quality_report`, `realization_report`). An assertion
   whose decision cannot move any report does not belong at the top of a queue. Cheap
   approximation: rank by whether the subject appears in a report at all, then by degree.
4. **Rank by disagreement** (reliability §3.11, recorded, unbuilt): run-to-run diff,
   critic-vs-extractor, confidence band. The queue is currently ordered by the one signal
   the model controls.

**[M] Corrected 2026-09-30, after implementing the cheap half.** This passage first claimed
"after 1 and 2 the queue is ~230". That was wrong twice, and the second error is the
interesting one:

- **Arithmetically**: 486 − 156 − 42 = **288**, not 230.
- **Substantively**: the reduction is produced by item **1** — giving *machine-checked* a
  state — not by the guards. Adding a check does not remove an assertion from the queue;
  it converts "unverified" into "unverified, and flagged if the value is wrong". Without
  that state the queue stays at 486 and only its **composition** becomes visible.

What the implemented slice measures on the live scope:

```
outstanding      486
  enum_valued    174   a guard can decide (check_enum_membership, or a Literal)
  quotation       42   the document can decide
  relational     175   a judgement
  other           95   mixed, mostly prose — a judgement
needs_judgement  270
```

So the queue is **486 assertions carrying 270 decisions**, and the page now says so. Getting
the 216 decidable ones out of the queue is YB-057's deferred half, and it is a product
decision rather than a mechanical one — see the risk section of that entry.

### B. Comprehension — altitude and answers, not density

Already filed as [YB-056](../todos/entries/YB-056-map-representation-modes.md) (tidy-tree
or layered layout, plus real filtering), and its analysis holds. Two additions that
measurement argues for:

- **Altitude is the axis, not filtering.** C4 *is* a hierarchy — context → container →
  component → code — and containment is enforced now (ADR-0032), so "show containers" and
  "show what is inside this one" are projections of a tree the graph already holds.
  Filtering a hairball by kind is not the same operation and does not compose.
- **Prefer the answer to the picture.** The gap report, the quality census and the
  unrealised-requirements list are already answer-shaped, and they are what a user
  actually opens the tool for. A graph is for exploration; a table is for decisions. The
  most legible "view" of a 147-node graph is usually the six-line list of requirements
  that have no architectural answer.
- **After a baseline, the diff is the view.** `project_delta` exists and `/changes/diff`
  renders it. "What changed since the approved baseline" is legible where "the whole
  graph" is not, and it is the question the evolution journey makes routine (ADR-0035).

### C. Expression — NLP, with one firm constraint

The honest position, and it follows from this repo's own recorded lesson rather than from
taste. [YB-010](rdf-knowledge-layer.md) already states it:

> A precise reasoning layer over a lossy graph produces **rigorously-derived wrong
> answers** — worse than fuzzy ones, because precision implies trust.

An LLM answering questions *from* the graph is that failure mode with a chat interface. It
will produce a fluent, confident, wrong architectural claim, and the user has no way to
tell. The constraint:

> **NLP translates to a deterministic query. The engine answers. The query is shown.**

That is auditable (the query is a reviewable artifact), falsifiable (the answer is a
result, not a claim), and it degrades honestly (a query that returns nothing says so).
YB-010's SPARQL projection is what makes it possible — which is a second, better argument
for that item than the audit one: **a closed query language is what makes an
LLM-to-graph interface safe.**

Three shapes, in rising cost:

1. **NLP → filters** (the wedge). "unresolved security requirements with no container"
   becomes the existing `?kind=&predicate=&only=` chips. Bounded vocabulary, no new engine,
   and it composes with §A's queue. Fails loudly when no filter matches.
2. **NLP → query** over the RDF projection: SPARQL, displayed, with `LIMIT` and a
   read-only guard. This is YB-010's surface being used for interaction rather than audit.
3. **Graph as tools for an agent** (MCP). Note both engines evaluated ship this — Omnigraph
   ships an MCP server. It is the same translator discipline: the agent calls a *function*
   that returns rows, it does not narrate the graph.

What none of these is: a chat box that answers architecture questions in prose.

## 3. What is already available to build on **[M]**

Worth listing, because most of §A and §B needs no new machinery:

| Existing | Gives |
|---|---|
| `review_progress`, `ReviewFilters`, `bulk_verify`, `bulk_apply` | the queue, its filters, and a batch path that already re-derives selection from the graph |
| Validators reading their vocabulary from the ontology | the check list §A.1 extends by four entries |
| `project_gap_report`, `project_quality_report`, `realization_report` | the pure functions that rank by consequence |
| `project_delta`, `/changes/diff` | the post-baseline view |
| `to_rdf` / `to_turtle` / `run_query` | the query surface §C.2 targets |
| `node_sides`, `reference_candidates` | the relational half of the queue, already ranked by score |

## 4. Recommendation, in order

1. **[YB-057](../todos/entries/YB-057-machine-checked-is-not-human-verified.md) — separate
   what is checkable from what is a judgement.** Extend the enum check list, add
   span-anchoring for quotations, give "machine-checked" a state, and report queue size as
   "N need a decision" rather than "N unverified". **The first, third and fourth landed
   2026-09-30** (see the correction in §2A); the state — the part that actually shrinks the
   queue — is the product decision the entry leaves open. No model calls either way.
2. **[YB-056](../todos/entries/YB-056-map-representation-modes.md) — altitude views over
   graph filtering.** Already filed; this analysis only adds the argument that altitude is
   the axis.
3. **Ask the answer-shaped questions first.** Before any query interface, list the
   questions users ask that no report answers. If the list is short, add reports — cheaper,
   testable, no hallucination surface.
4. **[YB-010](rdf-knowledge-layer.md) when its gate clears — then NLP → SPARQL, never NLP →
   answer.** The query language is what makes it safe.

## 5. What I am not claiming

- **I have not run a user study.** The split into 270 judgements and 216 decidable facts is
  a classification of the live scope, not evidence about what an architect would accept as
  checked — and it was already wrong once in this document (see §2A).
- **"Machine-checked" is a product decision, not an obvious one.** It weakens "a human
  vouched for this" unless the two states stay visibly distinct everywhere the graph is
  read — the review page, the audit trail, the reports, the exports. If they blur, the
  platform loses the property it is selling.
- **The NLP estimate is a hypothesis.** No prototype exists; the wedge shape (NLP →
  filters) is a judgement about cost and failure modes, not a measured result.
