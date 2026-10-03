---
id: YB-066
legacy: null
title: "Ask the graph: a named-question registry, then a router, then SPARQL — never a model narrating the graph"
status: open
priority: medium
area: "`app/` (the registry and its route), `core/knowledge/rdf.py` (the query surface YB-010 already wrote), `app/projections.py` (the answer-shaped functions the registry wraps), `scripts/bench_traversal.py` (the networkx measurements)"
created: 2026-10-03
updated: 2026-10-03
design: docs/design/natural-language-enquiry.md
record: null
superseded_by: []
related: [YB-010, YB-056, YB-057, YB-005]
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

## What closes it

A registry with tests, a router that reports `no_named_question` rather than guessing, at
least one SPARQL-backed entry reaching the four existing queries, a decision index that
answers for **recorded** decisions and not only proposed ones, and an impact answer that states
its scope, its unresolved-reference count and its assumptions.
