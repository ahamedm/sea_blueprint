# Natural-language enquiry over the graph — the questions, and which engine answers each

> **Design document.** Fleshes out §C of
> [graph-interaction.md](graph-interaction.md) ("Expression — NLP, with one firm
> constraint"), whose §5 records the honest state of it: *"The NLP estimate is a
> hypothesis. No prototype exists."* This replaces the hypothesis with a routed design
> and the measurements that decide the routing. Status is tracked in
> [YB-066](../todos/entries/YB-066-natural-language-enquiry.md).
>
> **[M]** marks what was measured, on `payments_v2` (201 nodes, 781 assertions) and
> `payments_v3` (58 nodes, 120 assertions).

---

## 1. What is being asked, and what already answers it

The request names three kinds of enquiry. They are not equally served today, and saying
which is which is most of the design:

| Enquiry | Example | Served today by | Gap |
|---|---|---|---|
| **Structure** | "what is inside the Payment Gateway Platform?", "which requirements have no architecture?" | `project_gap_report`, the C4 view, `/map` lenses, and YB-056's altitude work | the answer exists; getting *to* it is manual |
| **Decisions needed** | "what still needs a human?", "which facts are unresolved?" | §A of graph-interaction, YB-057, and the four named queries in `core/knowledge/rdf.py` | the queries exist and **nothing calls them** |
| **Impact of a change** | "if I retire this container, what breaks?", "what depends on this requirement?" | **nothing** | no reachability surface at all |

So two of the three are a *front door* problem — deterministic answers already exist and
are reached by clicking — and the third is a genuinely missing capability. That asymmetry
is what the plan below is built around, because it means the NLP layer's first job is
**routing, not reasoning**.

## 2. The constraint, inherited rather than re-argued

graph-interaction §C states it and it is not negotiable here:

> **NLP translates to a deterministic query. The engine answers. The query is shown.**

with the reason from [YB-010](rdf-knowledge-layer.md):

> A precise reasoning layer over a lossy graph produces **rigorously-derived wrong
> answers** — worse than fuzzy ones, because precision implies trust.

An LLM narrating the graph is that failure mode with a chat box. Everything below is an
attempt to get the useful part — you can *ask* instead of navigating — without it.

## 3. The routing table

Every enquiry is classified, then executed by one of four engines. The classification is
the only model call, and it is cheap and checkable because it selects from a registry
(§4) rather than generating anything.

| Class | Engine | Why that engine | Evidence |
|---|---|---|---|
| Structure, within a hierarchy | **projection** (`part_of` closure) | it is a containment question and containment is a property, not an edge | YB-056's tree work; 15 `part_of` assertions on v2 [M] |
| Structure, "what is missing" | **projection** (`project_gap_report`) | already answer-shaped, already tested | graph-interaction §2B |
| Decisions needed | **SPARQL over the RDF projection** | a closed-world filter over a closed vocabulary — exactly what SPARQL is for, and absence is meaningful | 4 named queries already written [M] |
| Compliance / gap audit | **SHACL** (via YB-010) | closed-world: absence is a violation | rdf-knowledge-layer's SHACL/OWL trap |
| Impact — reachability | **indexed traversal** | needs "everything that reaches X"; a cached adjacency index is 2.1× today's rebuild-and-walk | bench below [M] |
| Impact — structural position | **networkx algorithms** | cut vertices, paths between, centrality: algorithms worth not hand-rolling | 0.16–1.60 ms on the live scope [M] |
| Anything else | **say so** | the honest path, and the common one | §4.3 |

**Not on this list: an LLM answering from the graph.** No engine above may be replaced by
a model reading retrieved rows and summarising them into an architectural claim.

## 4. The central decision: a registry of named questions, not generated queries

The tempting design is "NL → SPARQL, run it". It is the wrong one here, for the reason
this repo keeps rediscovering: a generated query that is *nearly* right returns a
confident, well-formed, wrong answer, and nothing in the pipeline notices.

The alternative is a **registry**: each answerable enquiry is a named, typed entry —
parameters, the engine it runs on, the question it answers, and what it cannot express.
The model's job shrinks to *classify and fill slots*, which is checkable; the query text
is a reviewable artifact; and the tests are ordinary unit tests over named entries.

Three consequences worth stating, because each is a design constraint:

1. **The registry is the product surface.** "What can I ask?" is answerable — enumerate
   the registry. A free-form interface cannot answer that, and its failures are
   indistinguishable from its successes.
2. **An unanswerable question must be a first-class outcome**, not a low-confidence
   answer. `no_named_question` is a result the UI renders, with the closest entries
   listed.
3. **Nothing is generated at request time.** If the registry has to be extended, that is
   a code change with a test — which is the point, not a limitation.

This is the same shape as the existing canned SPARQL queries and the filter chips: a
bounded vocabulary, named, with the fallback stated.

## 5. Linkage to YB-010 (rdflib, SPARQL)

YB-010 is the load-bearing dependency, and this feature is the strongest argument for
finishing it. The coupling is concrete:

| YB-010 provides | This feature uses it for |
|---|---|
| `core/knowledge/rdf.py` — `to_rdf`, `to_turtle`, `run_query`, 255 lines [M] | the query surface |
| Four named queries — `unverified`, `missing_active`, `human_overrides`, `partial_runs` [M] | the "decisions needed" class, already written |
| ARC-G ⇄ REQ-G as a SPARQL join (YB-005) | impact questions that cross the two graphs |
| SHACL assertion of the ontology, closed-world | gap and compliance enquiries, where absence *is* the answer |
| Named graphs per revision (YB-004) | "impact of a change *since the baseline*" |

Two facts make this timely rather than speculative:

- **The gate is met.** `rdf-knowledge-layer.md` says "do not start this until extraction
  reliability is proven"; YB-010's 2026-09-30 update records `inv_ontology_class_coverage`
  at **100%** on both `req_sample` (48/48) and `req_prd` (59/59), with two caveats
  (`--validate-only` re-checks artifacts; one unrelated harness gate still fails).
- **The query surface has no caller.** `run_query` and all four named queries are invoked
  only from `scripts/test_knowledge_layer.py` [M]; the only product path to RDF is
  `/export/graph.ttl`. The capability is written, tested, and unreachable — the
  declared-but-unused shape this repo keeps finding, one layer up.

So the honest framing is not "build an NLP feature on top of RDF". It is: **finish
YB-010's query surface, and this feature is what makes it a product rather than an
export.** That ordering matters, because §C's §3 wedge (NLP → existing filters) can ship
before YB-010 and is the cheap falsification of the whole idea.

**The OWL warning applies directly.** An impact question is an entailment question, and
open-world semantics mean "nothing depends on this" is a claim the graph usually cannot
support. Impact must be phrased as *"these are the dependents the graph records"*, never
as *"nothing else depends on it"*.

## 6. Linkage to networkx — measured, and narrower than expected

`networkx>=3.2` is a declared dependency used in **one** place: `scripts/bench_traversal.py`
[M]. That bench already answers the question, and the answer splits:

**[M] Traversal — networkx loses.** At 5,000 nodes / 9,995 assertions:

| Operation | Hand-rolled | networkx | Verdict |
|---|---|---|---|
| Build adjacency (all edges) | 1.98 ms | 6.83 ms | dict, 3.5× |
| Build children (`part_of` only) | 0.32 ms | 2.39 ms | dict, 7.5× |
| 500 neighbour queries | 8.92 ms (scan) | 0.01 ms (degree) | **index** — 1,145× the scan; a dict index is 2,930× |
| 50 transitive `part_of` | 32.55 ms (rebuild) | 121.03 ms | **cached dict walk**, 15.58 ms — networkx is 0.3× *today's* code |

The lesson is not "avoid networkx", it is **the win is an index, not a graph library**, and
the library's own traversals are slower than a cached dict walk at this size.

**[M] Algorithms — networkx earns its place.** On the live `payments_v3` graph (52 nodes,
69 edges), for questions this feature must answer:

| Algorithm | Result | Time | Hand-rolling it would be |
|---|---|---|---|
| `articulation_points` | 15 cut vertices | 0.81 ms | Tarjan's — real work, easy to get subtly wrong |
| `all_simple_paths` (cutoff 4) | works | 0.98 ms | enumerated DFS with a cutoff |
| `betweenness_centrality` | 52 scored | 1.14 ms | Brandes — real work |
| `shortest_path` | works | 1.60 ms | BFS |
| `weakly_connected_components` | 1 | 0.16 ms | union-find |

**[M] One gap to decide on: `numpy` is not a dependency**, so `nx.pagerank` raises
`ModuleNotFoundError`. Any plan that reaches for networkx's linear-algebra surface
(PageRank, HITS, some centrality) needs `numpy` added, and that is a dependency decision
rather than a code one. Everything in the table above works without it.

**The routing rule that follows:** traversal stays hand-rolled (indexed); networkx is
reached for *algorithms* — and only where the graph's own structure is the answer, since
a centrality score over a graph with 39 unresolved references [M, `payments_v2`] measures
the extraction, not the architecture.

## 7. Impact of a change — the class with no existing answer

This is the one enquiry with nothing behind it, so it needs the most care.

An impact question decomposes into four sub-questions, and they have different answers and
different reliability:

| Sub-question | Engine | Reliability |
|---|---|---|
| What is structurally inside / containing X? | indexed containment closure | high — containment is enforced (ADR-0032) |
| What references X, and what does X reference? | index over edges + **resolved** references | medium — see the caveat below |
| What would break if X went away? | cut vertices, path enumeration | medium — structural, not semantic |
| What is the *architectural* consequence? | **a human** | not answerable by this feature |

**[M] The caveat that must be displayed with every impact answer: the graph is a lossy
witness.** On `payments_v2`, **39 cross-graph references are unresolved** — they exist as
text, not as edges — so any reachability answer *underestimates* impact by exactly the
part of the graph that has not been reconciled. On `payments_v3` the number is 0, because
that scope is a requirements-only ingest with almost no architecture to reference; an
impact answer there is not "reliable", it is **vacuous**, and the two states must not look
alike. Similarly, the graph is not connected — 27 components on `payments_v2` [M] — so
"nothing reaches X" frequently means "X is in another island", not "X is unused".

That is why impact must answer with its own assumptions attached: which scope, which
revision or baseline, how many references are unresolved, and which components were
searched. It is the same discipline the Quality page already applies with *"Read this
beside the numbers"*, one question further on.

## 8. Decisions to make

1. **The registry's home.** A table in `app/` (near the other projections) or a
   declarative file the ontology knows about. Everything else depends on this.
2. **Whether the classifier is a model call at all, for the wedge.** §C's first shape
   (NL → existing filters) can be done with a keyword/synonym map and no model, which is
   falsifiable in a day. The model call is only needed for free-form phrasing.
3. **`numpy`, or no linear-algebra algorithms.** §6's table works without it.
4. **Does an impact answer require a baseline?** Impact is only well-defined against a
   frozen revision. Today no frozen baseline exists on either live scope [M] — all
   revisions are `draft` — so impact against `working` is a moving target and should
   probably say so rather than pretend otherwise.
5. **Where the answer is rendered.** A new page, or the existing report surfaces. §2B's
   argument ("prefer the answer to the picture") suggests reports first.

## 9. Recommended order

1. **The registry, with no model.** Named questions that already have deterministic
   answers (gaps, quality, unresolved, next decisions), exposed behind one interface.
   This is useful with no NLP at all, and it is what §C.1's wedge needs.
2. **A keyword router over the registry.** "Ask" becomes classification over a bounded
   vocabulary. Falsifies the interaction model cheaply, and fails loudly by construction.
3. **YB-010's query surface, then SPARQL-backed entries.** Only then does the "decisions
   needed" class get its real engine, and the four existing queries get their caller.
4. **Impact, last and explicitly.** It is the class with no deterministic precedent, the
   most sensitive to graph completeness, and the one where a confident wrong answer costs
   most.

## 10. The agent shape — and what "given the graph" has to mean

A QnA agent with a chat interface and tools over the graph is the right shape, and §C.3 of
graph-interaction already names it: *"the agent calls a **function** that returns rows, it
does not narrate the graph."* The front-door framing is what makes it worth building **and**
what shrinks the job.

**Why the framing justifies it.** The answers exist across roughly a dozen surfaces — the gap
report, the quality census, the realisation report, the review queue, the map, the C4 view,
the diff, reconcile. Nobody knows which one answers their question, so they filter a
486-item queue instead. That is a routing problem, and a chat front door is a legitimate
answer to a routing problem. It is also a much smaller claim than "answers architecture
questions": the agent's job is **select the tool, show the result, carry the caveat** — never
derive the fact.

**"Given the graph" splits three ways, and only two are safe:**

| Reading | Verdict |
|---|---|
| The graph as *context* — nodes/edges retrieved into the prompt, then narrated | **No.** This is YB-010's "rigorously-derived wrong answers", and it does not fit anyway: `payments_v2` is 201 nodes and 781 assertions |
| The graph as *tools* — functions returning rows | **Yes.** This is the design, and it is what makes an answer falsifiable |
| The **ontology** as context | **Yes, and this is the useful context.** The vocabulary is what maps a user's words ("service", "gateway", "platform") onto the graph's classes, and it is small — `_format_ontology_context` already ships it to every agent: a flat name list plus the ISO 25010 model, ~5,174 chars |

That third row is the non-obvious one: give the agent the **schema**, not the **instance
data** — and it already gets the schema.

### What follows, and each is a requirement rather than a preference

1. **Read-only, enforced.** `run_query` today takes a raw SPARQL string and applies no guard
   at all [M] — the design already asks for "a read-only guard and `LIMIT`", and handing that
   path to an LLM makes it mandatory rather than prudent. This platform's selling property is
   that agents *propose and never write*; a QnA agent must not be the exception that erodes it.
2. **The caveats must survive the prose.** Every surface here carries its state — "not
   auditable yet", "Read this beside the numbers", "0 hierarchy edge(s)", the scope, the 39
   unresolved references. A fluent summary that drops them is *less* honest than the page it
   summarises. So the caveat belongs to the **tool's return value**, not to the model's
   discretion: an answer that cannot state its own assumptions is not renderable.
3. **Scope and revision are inputs, not assumptions.** "Impact of X" is only well defined
   against a scope *and* a revision. No frozen baseline exists on either live scope [M], so
   the agent must either ask or state which it used.
4. **The answer is a pointer, not a parallel account.** The chat should deep-link to the
   deterministic surface it summarised. Two reasons: the page is where the reviewer acts
   (verify, dispute, reconcile), and the chat log is not an audit trail — the review log is.
5. **The tools are the existing functions.** `project_gap_report`, `project_quality_report`,
   `realization_report`, `project_delta`, the review queue and its filters, `to_rdf` +
   `run_query` (YB-010), and for impact the indexed traversal plus the networkx algorithms of
   §6. Each is already tested, which is the whole reason to route through them.

### The honest counter-point

If the answers already exist and are reachable, a chat interface is only worth building if
the routing problem is real. graph-interaction's §4.3 says the cheaper move comes first:
*"Before any query interface, list the questions users ask that no report answers. If the list
is short, add reports — cheaper, testable, no hallucination surface."* So §9.2's first step is
also the falsification: log what is searched and asked, and see whether it is routing or
missing reports. Nothing above is a reason to skip that measurement.

## 11. What I am not claiming

- **No prototype exists.** Every number here is a measurement of the *substrate* —
  projection functions, SPARQL surface, traversal cost, algorithm cost — not of an
  end-to-end question-answering feature, and not of whether an architect prefers asking
  to clicking. graph-interaction §5's disclaimer stands.
- **The classifier is unmeasured.** "Classify into a bounded registry" is a judgement
  about failure modes, not a result. It could route badly; that is what step 2 tests.
- **The routing split is an argument from cost and semantics, not from a user study.** The
  claim that SPARQL suits "decisions needed" and networkx suits "impact" follows from what
  each engine is, plus the measurements above. It has not been validated on real questions.
- **`payments_v3` is too small to extrapolate from.** 58 nodes and no architecture makes
  its algorithm timings reassuring and its impact answers meaningless.
