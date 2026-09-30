# TerminusDB as an RDF knowledge layer — an evaluation against YB-010

> **Evaluation, not a plan.** Nothing here is committed or scheduled. It answers one
> question: does [TerminusDB](https://github.com/terminusdb/terminusdb) simplify what
> [YB-010](../todos/entries/YB-010-rdf-knowledge-layer.md) is trying to do, or what the
> repo is already hand-rolling elsewhere?
>
> **Evidence discipline.** Claims about TerminusDB are from its own README and docs,
> fetched 2026-09-30, and are *vendor-stated* — not independently verified here. Claims
> about this repo were run against the code and are marked **[M]** where measured.

---

## 1. What YB-010 actually asks for

Not "use a graph database". Straight from
[`rdf-knowledge-layer.md`](rdf-knowledge-layer.md):

- make the **audit deterministic** while leaving extraction as-is, with the value being
  **diagnostic** — "a fixed query over a varying graph does not hide extraction
  non-determinism, it **exposes** it";
- **ARC-G ⇄ REQ-G as a SPARQL join** (YB-005);
- **named graphs for revision diffing** (YB-004);
- `rdflib` + `pySHACL` now, `Jena Fuseki` later via `SPARQLWrapper`, chosen for
  **"no service boundary and no second toolchain"**;
- the trap it names: **"SHACL is closed-world (absence is a violation — right for gap
  auditing); OWL is open-world (absence entails nothing — a gap query finds nothing,
  ever, silently). SHACL for the audit, OWL for entailment."**

## 2. Two decisions that already bound this

Worth stating first, because TerminusDB pushes against both.

**[M] The store shape is settled.** `graph-store-schema.md` §6 weighs three shapes and
recommends normalised tables; the triple/quad table is recorded as *"that is the RDF
store, and it is deferred"*. §5 goes further:

> **RDF.** Materialised *from* these tables per revision (N-Quads/Turtle) into the same
> artifact store. **RDF is a projection; the tables are the record.** Jena, when it
> arrives, reads the artifacts.

**[M] The RDF projection already exists.** `core/knowledge/rdf.py` ships `to_rdf`,
`to_turtle`, `to_jsonld` and `run_query` — raw SPARQL or a named query — over `rdflib`.
YB-010 is therefore *finishing* a layer whose substrate is chosen, not choosing one.

**[M] Branching is designed and unbuilt.** `branching-and-promotion.md` (YB-046) already
specifies a branch axis, a three-way merge, and the observation that content-addressed
assertion ids make most of a merge converge for free.

## 3. What TerminusDB is

From its README and the "What is TerminusDB?" page:

| Capability | Vendor's description |
|---|---|
| Model | JSON/JSON-LD **documents** in a schema-enforced graph; decomposed into RDF triples internally |
| Version control | commits, **branch, merge, diff, clone, push/pull**, time-travel to any commit |
| Storage | immutable, content-addressed **delta layers**; never mutates in place; periodic delta rollup |
| Transactions | ACID; schema validated on every write; lock-free reads |
| Semantics | **closed-world assumption**, stated as a deliberate contrast with SPARQL triple stores' open-world |
| Query | **WOQL** (Datalog with unification, path queries, rule inference), GraphQL, REST document API — **not SPARQL** |
| Schema | its **own JSON schema language**, not LinkML |
| Extras (v12) | arbitrary-precision `xsd:decimal`, Allen interval algebra / temporal reasoning, range queries over succinct data |
| Shape | a **server** (Docker/snap, `localhost:6363`, admin password, Python/JS/Rust clients) |
| Licence | **Apache-2.0** core; "DFRNT assumed stewardship in 2025"; Enterprise edition alongside |

## 4. Fit, requirement by requirement

### 4.1 The core requirement — a standard RDF/SPARQL audit surface: **poor fit**

Two independent problems, and either alone would decide it.

**(a) The OSS edition does not give you RDF documents.** The README lists *"Full JSON-LD,
Turtle, and RDF/XML documents"* under **Enterprise**, and the Enterprise page confirms it:
**Multi-format document API**, **W3C JSON-LD `@context` processing**, **Turtle
serialization** and **RDF/XML serialization** are all enterprise-only features. The
Apache-2.0 core stores RDF *internally* and exposes JSON documents.

That is the inverse of what YB-010 needs. Its whole purpose is an RDF surface for
auditing — Turtle/N-Quads artifacts that Jena can later read. In TerminusDB's OSS core
"the RDF layer is an implementation detail", which is the vendor's own phrase. Adopting
it would mean paying for Enterprise, or writing our own RDF serialiser against its HTTP
API — **which is what `rdflib` already does for free**.

**(b) The query language is WOQL, not SPARQL.** The vendor is explicit: *"WOQL vs
SPARQL — TerminusDB uses WOQL rather than SPARQL. WOQL is more expressive for recursive
graph traversal and reasoning; SPARQL has a larger ecosystem and standardisation."*
YB-010's plan is SPARQL now and the Jena path later, precisely so the choice is not
foreclosed. WOQL forecloses it, and trades a W3C standard for a single-vendor language
with a fraction of the ecosystem.

### 4.2 Closed-world semantics: **strong alignment, no new capability**

This is the most interesting convergence. YB-010 names open-world entailment as *the*
trap ("a gap query finds nothing, ever, silently"). TerminusDB reaches the same
conclusion independently and markets it: *"Traditional triple stores (and SPARQL) use
open-world semantics: the absence of a fact does not imply it is false. TerminusDB uses
a closed-world assumption… This makes schema validation, constraint checking, and
application development far more predictable."*

That is **corroboration worth quoting** — but the repo already gets closed-world
semantics from SHACL, which is the W3C standard for exactly this and is already analysed
in YB-010 (LinkML generates SHACL including `sh:closed true`). TerminusDB offers it
through a *different, proprietary* schema language, so using it would mean translating
LinkML → TerminusDB schema: a second schema to keep aligned, which is the drift
`check_schema_consistency` and the validators module exist to prevent. LinkML → SHACL
generation has the opposite property: both come from one source.

### 4.3 Revision diffing and branching (YB-004, YB-046): **genuinely strong, but points elsewhere**

Version control is TerminusDB's actual competency — immutable delta layers, branch,
merge, diff, time-travel, clone/sync, lock-free concurrency. It is unambiguously better
than what this repo hand-rolled for YB-004, and better than the designed-but-unbuilt
branch model in `branching-and-promotion.md`.

Two things temper it:

- **Our diff is semantic and domain-aware.** `compute_graph_delta` resolves labels,
  classifies added/changed assertions and feeds a review page. TerminusDB diffs documents
  and triples at field level. It would not delete that work without also moving the
  review UI onto TerminusDB diffs.
- **Our history is not its history.** The repo's revisions carry `scope`
  (`INITIATIVE_PROPOSAL` / `SYSTEM_BASELINE`), review status, per-assertion provenance and
  `superseded_by` lineage; its audit trail is the `ReviewLog` of *human decisions*.
  TerminusDB's commit log would be a **parallel** history over the same facts — two
  records of one truth, which is the failure mode this codebase spends the most effort
  avoiding.

So: if collaborative branching per Initiative becomes a real product requirement, this is
the thing to look at. It is not a reason to change YB-010.

### 4.4 Reasoning and datalog: **real, but overlapping what exists**

WOQL gives unification, path queries and rule inference. The repo's audit is
deterministic validators plus report projections, several of which began life as
"RDF-shaped" questions in `rdf.py`'s named queries. If rule-based inference over the
knowledge graph becomes a goal (rather than hand-written validators), datalog is a
capability — but `pyshacl` over `rdflib` covers the validation half already, and the
validators are written, tested and wired.

### 4.5 Decimal precision, interval algebra, succinct range queries: **attractive, off the critical path**

This is the most tempting under-analysed corner. Payment amounts are `xsd:decimal`
throughout the domain pack, ADR-0034 added `representment_deadline` and
`response_deadline` dates, and Allen interval algebra would answer things like *"which
settlements overlap this dispute window?"* over a succinct range index.

But the platform **reasons about architecture; it does not compute balances**. Exact
decimal arithmetic and temporal classification are capabilities for a financial
processing engine, not for a graph that holds "the gateway shall authorise before
capture". This is worth remembering if the product ever moves toward numeric
reconciliation — not before.

### 4.6 Operating model: **a second stateful service**

YB-010 recorded its reason for `rdflib` first in one phrase: *"no service boundary and no
second toolchain."* TerminusDB is a server: Docker or snap, `localhost:6363`, an admin
password, built from source with `make dev`. Today the graph store is a file or SQLite
inside the Flask process, and the suite runs offline (Valkey-backed tests skip cleanly
when it is absent).

Adding a graph *server* is a different class of dependency from adding a library — for a
store that is not optional, on the read path of every page.

### 4.7 Licensing and stewardship

Apache-2.0 core, which permits a fork and is the main mitigation for the rest. Two things
worth recording because they are the kind of risk that is invisible until it matters:

- **The RDF surface is paywalled** (4.1a) — the specific capability this analysis was
  asked about.
- **Stewardship changed in 2025** ("DFRNT assumed stewardship", and the Enterprise page
  is now titled *"DFRNT TwinfoxDB Enterprise Edition"*). Not a criticism — Apache-2.0
  keeps the code available — but a platform dependency on a project whose commercial
  centre of gravity is moving toward a paid edition deserves a decision recorded on
  purpose rather than by default.

## 5. Verdict

**TerminusDB is not a substitute for YB-010's plan, and adopting it would reverse the
analysis's central choice.** YB-010 deliberately picks a *standard, open, library-shaped*
RDF surface — rdflib, SHACL, SPARQL, Jena-compatible — so the audit is deterministic and
the toolchain has no service boundary. TerminusDB offers a *proprietary-schema,
server-shaped* database that treats RDF as an internal detail, queries it in a
single-vendor language, and charges for the RDF document formats. Those are opposite
stances, not different points on one axis.

Its real strength — git-for-data over a graph — lands on **YB-046 (branching and
promotion)** and **YB-004 (revision diffing)**, not on YB-010.

**Recommendation: keep YB-010 as written.** Concretely:

1. **Continue rdflib → SHACL → SPARQL, Jena later.** Nothing in this evaluation changes
   the choice.
2. **Cite TerminusDB in `branching-and-promotion.md`** as the reference implementation of
   branch/merge/time-travel over a graph. It independently validates the model the doc
   designs, and it is where to look if multi-architect branching becomes real.
3. **Quote the closed-world corroboration.** A vendor whose product is a graph database
   independently concluding that CWA is what makes constraint checking reliable is a
   better argument for "SHACL for the audit, OWL for entailment" than the repo's own
   assertion.
4. **Revisit only on a trigger**, not on a review: multi-architect branching becomes a
   priority; or the RDF document formats land in the OSS edition; or numeric/temporal
   reasoning moves onto the roadmap.

**If evidence is wanted rather than argument**, the cheap experiment is a timeboxed
spike: `docker compose up`, export one revision's `to_turtle` output, load it, and ask the
REQ-G ⇄ ARC-G join in both WOQL and SPARQL. The deliverable would be the query-language
comparison, not a migration. **[M] But not now** — see below.

## 6. What changed since YB-010 was written

**[M] The gate YB-010 set for itself is now met.** It says:

> Do not start this until extraction reliability is proven by the harness… **The `req_prd`
> `ontology_class` coverage invariant is currently failing and is the gate.**

Run today against the committed artifacts:

```
req_sample  [PASS] inv_ontology_class_coverage  100% within band 90%–110%  (48/48)
req_prd     [PASS] inv_ontology_class_coverage  100% within band 90%–110%  (59/59)
req_prd     gates 4/4   budgets 3/3 in band
```

So the specific obstacle named is gone: coverage is **100%** on both requirement cases,
against a `("min", 80.0)` fallback and a ±10% band around a 100% baseline.

Two honest caveats before anyone treats YB-010 as unblocked:

- `--validate-only` re-checks **saved** artifacts; it is not a fresh live extraction. The
  number is real for the committed runs, not a claim about the current model.
- The harness is **not** green: one gate still fails, `inv_no_tech_leak` — `Quartz`
  extracted as an element. That is a different invariant from the one YB-010 named, but
  "extraction reliability is proven" is a judgement about the whole harness, not one
  budget. The precondition is *satisfiable*; declaring it satisfied is the owner's call.

## 7. Sources

- TerminusDB README — <https://github.com/terminusdb/terminusdb> (Apache-2.0; v12; Enterprise feature list)
- What is TerminusDB? — <https://terminusdb.org/docs/terminusdb-explanation/> (delta layers, CWA, WOQL vs SPARQL, comparison table)
- Enterprise Edition — <https://terminusdb.org/docs/enterprise/> (multi-format document API, JSON-LD contexts, Turtle/RDF-XML: enterprise-only)

Repo side: [`rdf-knowledge-layer.md`](rdf-knowledge-layer.md),
[`graph-store-schema.md`](graph-store-schema.md) §5–6,
[`branching-and-promotion.md`](branching-and-promotion.md),
`core/knowledge/rdf.py`, `scripts/run_extraction_tests.py`.
