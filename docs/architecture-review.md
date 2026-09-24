# SEA Architecture Review — 20 September 2026

Two design discussions, recorded together because they converge on the same
conclusion: **the knowledge model is the architecture**, and several questions
that look like tooling choices are really data-model decisions.

---

## Part 1 — Findings from probing the rdflib/Jena proposal

Testing the proposal surfaced two silent bugs in the ontology. Both were found
only because a stricter parser (LinkML) rejected what `yaml.safe_load` had been
quietly mangling.

### 1.1 Seventeen enum descriptions were silently truncated

Written in YAML flow style:

```yaml
MICROSERVICES: { description: Independently deployable services, own data, network calls. }
```

In **flow mapping, commas separate entries**, so this parsed as:

```python
{'description': 'Independently deployable services',
 'own data': None,
 'network calls.': None}
```

The description was cut at the first comma and the remainder became phantom keys.
`yaml.safe_load` accepted it; LinkML rejected it. Affected 13 descriptions in
`architecture_base.yaml` and 4 in `requirements_base.yaml`.

**Fixed** by quoting the descriptions. Verified: no phantom keys remain.

### 1.2 Fifty-eight classes used the wrong key for subsets

The ontology used `subsets:` on class definitions. The LinkML metaslot is
`in_subset:`. Every subset assignment across all four layers was therefore inert —
the subsets have never existed as far as any tooling is concerned.

**Fixed** by renaming (schema-level `subsets:` declarations left alone, since
those are correct). 58 renames across the four files.

### 1.3 LinkML → SHACL fails on any layer with imports

```
sea_common.yaml            (no imports)    OK      8,326 chars SHACL
enterprise_structure.yaml  (1 import)     FAILED  KeyError: 'sea-common'
requirements_base.yaml     (2 imports)    FAILED  KeyError: 'enterprise-structure'
architecture_base.yaml     (3 imports)    FAILED  KeyError: 'requirements-base'
```

Not import *depth* — imports at all. `SchemaView.schema_map` is not populated with
imported schemas, so `get_uri` cannot determine which schema a class belongs to.

**Workaround verified:**

```python
sv = SchemaView("architecture_base.yaml")
sv.merge_imports()
ShaclGenerator(sv.schema).generate()   # → 242,561 chars
```

Output is real SHACL, including closed-world validation:

```turtle
sea-arc:ArchitectureSpecification a sh:NodeShape ;
    sh:closed true ;
    sh:property [ sh:class sea-arc:Person ; sh:path sea-arc:persons ],
        [ sh:datatype xsd:string ; sh:maxCount 1 ; sh:path sea-arc:version ],
        [ sh:class sea-arc:Container ...
```

**This is the most important finding of the two discussions**: the ontology
converts into validation shapes almost for free. The hand-written Python
validators can become declarative SHACL derived from the same source of truth,
which solves the schema/validator drift problem properly.

---

## Part 2 — Analysis: rdflib / Jena proposal

**Proposal:** use Apache Jena to reason over extracted knowledge, to reduce
non-determinism in semantic auditing and to store generated knowledge.

### 2.1 The determinism claim is right, but scoped

Jena will **not** reduce non-determinism in *extraction* — the graph going in is
still produced by a sampling LLM. It makes **the audit** deterministic, requiring
audits to be expressed as queries and shapes.

The second-order value matters more than the first:

> **A fixed query over a varying graph does not hide extraction non-determinism —
> it exposes it.** Two runs disagreeing on a deterministic query means the graph
> changed, which localises blame to extraction.

Only the **structural** audit class becomes deterministic: traceability gaps,
orphans, containment, cardinality, enum conformance, coverage. Those become
*definitions* rather than opinions — "requirements with no implementing element"
stops being a judgement.

The **semantic** class ("does this architecture satisfy this requirement?") stays
LLM work, but Jena narrows it: the model judges only the related pairs the graph
identifies, not all pairs.

### 2.2 The storage claim is real but undersold

Saving triples is not the win — JSON files work. Three things are:

- **The graph persists as a graph**, rather than existing only as a Python object
  during a run.
- **Named graphs** map onto the PRD's "requirements and architecture evolve"
  requirement — a graph per revision, diffed. This is the requirement-diffing
  risk (YB-004) solved structurally.
- **ARC-G ⇄ REQ-G becomes a query.** Item 5's cross-verification — two graphs
  sharing 4 of 60 node names — becomes a SPARQL join across named graphs instead
  of bespoke matching code.

### 2.3 The "not graph storage" concern is narrower than it sounds

RDF **is** a graph; Jena **is** graph storage. The precise issue is that it is not
a **property graph**. What bites is specific: **every fact we extract carries
metadata** — `confidence`, `source_text`, `ontology_class`, provenance. Attaching
properties to a *statement* in RDF needs one of:

| Approach | Cost |
|---|---|
| Reification (RDF 1.1 singleton) | 4 triples per assertion, verbose, clunky SPARQL |
| RDF-star | cleaner; Jena support newer, SPARQL-star varies |
| Model the assertion as a resource | verbose but explicit and queryable |

**The ontology already anticipated this**: `RequirementRealization` and
`Provenance` are deliberately modelled as first-class resources rather than bare
triples. The shape is compatible; the question is how much else needs the same.

### 2.4 Depiction is a view concern, not a storage concern

Graphs are the intuitive depiction, and RDF visualises fine in any graph viewer.
The friction is that our edges carry attributes, so a naive viewer shows
unlabelled edges. That is a **projection layer** problem: flatten the reified
assertion into a labelled edge for display. Keep storage optimised for query and
inference; materialise views on top. Choosing a database for its UI is how this
goes wrong.

### 2.5 The tool decision is separable from the modelling decision

Measured availability:

```
rdflib              7.6.0   already a dependency, full SPARQL 1.1
SPARQLWrapper       2.0.0   already installed — talks to Jena Fuseki over HTTP
pyshacl             —       would be needed
owlrl               —       would be needed
```

**rdflib + pySHACL delivers most of the benefit at a fraction of the integration
cost** — same language as the pipeline, no service boundary, no second toolchain.
Jena earns its place when reasoners, TDB2 scale, or the Fuseki endpoint are
actually needed. And because `SPARQLWrapper` is already present, the bridge to
Fuseki later is cheap — choosing rdflib now does not foreclose Jena.

**Adopt the modelling decision (RDF) now; stage the tool decision.**

### 2.6 The trap to avoid: SHACL vs OWL

- **SHACL is closed-world.** Absence is a violation. "This requirement has no
  implementing element" is *reportable*.
- **OWL is open-world.** Absence entails nothing. The same query finds
  **nothing, ever** — silently.

**Use SHACL for the audit. Use OWL for entailment** (transitive `part_of`, inverse
properties, subclass closure). Mixing them naively gives a reasoner that
confidently reports no gaps because it cannot prove any.

### 2.7 Ordering

A precise reasoning layer over a lossy graph produces **rigorously-derived wrong
answers**. This session found, in extraction alone: traceability predicates
silently dropped, technologies resurfacing as elements, responsibilities
vanishing, monitoring platforms disappearing, requirement IDs lost on one path,
and `responsibility` (singular) discarded without error.

Feeding that into Jena would give confidently wrong audits — worse than fuzzy
ones, because precision implies trust.

**Fix extraction reliability before adding precision downstream.**

### 2.8 Verdict

Directionally right, worth doing, with three refinements:

1. **Scope the determinism claim** — it is the *audit* that becomes deterministic,
   and its biggest value is diagnostic.
2. **Separate modelling from tooling** — RDF now via rdflib + pySHACL; Jena when
   it earns its place.
3. **SHACL for validation, OWL for entailment** — not one reasoner for both.

**The proposal has already paid for itself** by surfacing two silent ontology bugs
that would otherwise have gone on quietly corrupting descriptions and ignoring
every subset assignment.

---

## Part 3 — Analysis: SEA system architecture sanity check

Proposed moving parts:

> Set of Agents · Orchestrator or Workflow · Web UI for agent interaction ·
> Web UI for semantic validation/correction · MCP for document access ·
> MCP for accessing Jena · API exposing semantic validation for business requirements

### 3.1 What is right

**Two UIs, correctly separated.** "Talk to the agents" and "review/correct
extracted knowledge" are different jobs with different users and rhythms.
Conflating them is the common mistake and it has not been made here.

**MCP as the access layer.** Documents and the knowledge store as MCP servers
means agents do not hardcode access and both are swappable.

**The API named as the product.** "Expose semantic validation of architecture for
business requirements" is the deliverable. Everything else is machinery that
exists to produce it.

### 3.2 The gap that matters most

The list names interfaces between components but **never names where the
knowledge lives or what its canonical form is.** For a system whose entire value
is the knowledge it produces, the data model *is* the architecture.

Five representations are already in play:

```
documents      → Markdown
extraction     → Pydantic models (per-pass, per-chunk)
ontology       → LinkML
storage        → RDF (Jena)
UI             → graph views
```

No component owns the transformations between them. There is an implied gap:

```
extraction output → ??? → Jena → SPARQL → UI
```

**That `???` is a Knowledge Serialisation layer, and it is the highest-risk
component in the system** — every extraction change breaks it, it is where
confidence/source-text must land on reified assertions, and it is the only place
that can guarantee the graph is well-formed. It needs to be a named part with
tests.

### 3.3 Three unassigned responsibilities

**1. Identity resolution.** Item 5 — joining ARC-G and REQ-G. The platform's core
value, with nowhere to live in the list. Deserves its own service: it is what the
product is *for*.

**2. Revisions.** The PRD says requirements and architecture evolve. Nothing diffs
or versions anything. Named graphs would carry it; no component owns it.

**3. Human corrections vs re-extraction — the sleeper.**

The correction UI edits extracted knowledge. Then someone re-runs extraction with
a better prompt, a different model, or a new document revision. **Do the
corrections survive?**

If extraction replaces the graph, human work is silently destroyed. If it does
not, corrections go stale against new content. Neither is a detail; both force a
decision that is expensive to retrofit:

- corrections as a **separate overlay layer** that outranks agent assertions
- corrections as **provenance-marked assertions** that win on conflict
- re-extraction as **diff-and-review** rather than replace

Underneath all three is one requirement: **the system must distinguish "asserted
by an agent" from "confirmed by a human," structurally, in the graph.** That is
the architectural reason for `Provenance` and `VerificationStatus` — not tidiness.

### 3.4 Incompleteness must be representable

Partial extraction treated as complete means **the auditor reports extraction
artifacts as architectural gaps** — worse than no audit, because confidently
wrong.

"This pass failed", "this chunk produced nothing", "this element has no
responsibilities" must be first-class graph state, not a log line. Arguably the
correctness property of this system, and absent from the list.

### 3.5 Validation must be one service

The validation UI and the API both need validation results. If each computes them,
**they will diverge** — the schema/validator drift problem at system scale.

One validation engine, consumed by both, with rules **generated from the ontology**
(the SHACL path demonstrated in Part 1.3) rather than hand-written twice.

### 3.6 Orchestrator *or* workflow — choose workflow

| | Orchestrator (dynamic) | Workflow (fixed) |
|---|---|---|
| Routing | model decides | declared |
| Auditability | needs trace explanation | intrinsic |
| Human gates | ad hoc | explicit steps |
| Determinism | adds a variable | removes one |

For a system selling *verifiable* traceability, **a dynamic router adds
non-determinism to the thing being sold as deterministic.** The PRD already
describes a fixed 8-step process. Model it as a workflow with explicit human
gates; keep the intelligence inside the agents.

### 3.7 Agents stateless; the graph is the memory

Worth committing deliberately rather than by accident: agents as **stateless
functions over the graph** — no private memory, no cross-run state. Then the graph
is the single source of truth rather than five private contexts, agents stay
replaceable and testable, and re-running is safe.

The prototype already behaves this way.

### 3.8 Write path: stage → validate → commit

Direct writes from agents to Jena mean partial and failed extractions pollute the
graph and re-runs duplicate. Instead:

```
agents → staging → serialise + validate → commit (with provenance + revision)
```

The commit step is where provenance, revision and review state are attached — and
where a graph failing SHACL never reaches production.

### 3.9 Recommended sequencing

The tempting order is UIs first, because they are visible and low-risk. **That is
the trap**: building them first locks in data-model decisions that should be
deliberate.

1. **Canonical model + serialisation layer.** Nothing else is testable without it.
2. **Validation as a service**, rules generated from the ontology.
3. **Correction write path + merge semantics.** Hardest, most likely to force
   redesign — so before UI depends on it.
4. **Validation/correction UI.** Needs 2 and 3.
5. **Workflow.** Can be scripted initially; four passes do not need an engine.
6. **MCP servers.** Good encapsulation, not load-bearing early.
7. **Interaction UI.** Last — least risky, most likely to change.

### 3.10 Verdict

The shape is sound — layered, sensibly separated, product surface named honestly.
Two things are load-bearing rather than optional:

- **A canonical knowledge model with an owned serialisation layer.** Without it,
  five representations drift.
- **A structural distinction between agent-asserted and human-confirmed
  knowledge, plus representable incompleteness.** Without it, corrections are
  destroyed on re-extraction and audits report extraction failures as
  architecture gaps.

And one thing to do before more design: **the prototype already answers several of
these questions.** The passes are a workflow, the validators are a validation
engine, the harness is the eval loop, the agents are already stateless. Deriving
the architecture *from* the working code will be more accurate than designing it
ahead of it — the lesson from the Jena detour, and from this whole session.
