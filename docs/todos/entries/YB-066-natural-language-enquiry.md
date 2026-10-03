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

## Recommended order

1. **The registry, with no model** — named questions over the deterministic answers that
   already exist. Useful with no NLP at all.
2. **A keyword router** over it. Falsifies the interaction model in a day.
3. **YB-010's query surface, then SPARQL-backed entries** — the four existing queries get
   their caller.
4. **Impact, last and explicitly** — the class with no precedent and the highest cost of a
   confident wrong answer.

## What closes it

A registry with tests, a router that reports `no_named_question` rather than guessing, at
least one SPARQL-backed entry reaching the four existing queries, and an impact answer that
states its scope, its unresolved-reference count and its assumptions.
