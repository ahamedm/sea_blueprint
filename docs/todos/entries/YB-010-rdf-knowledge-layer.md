---
id: YB-010
legacy: "10"
title: "Adopt RDF for the knowledge layer (rdflib first, Jena later)"
status: open
priority: medium
area: "`core/knowledge/serialise.py`, `core/knowledge/rdf.py`"
created: 2026-09-20
updated: 2026-09-30
design: docs/design/rdf-knowledge-layer.md
record: null
superseded_by: []
related: ["YB-004", "YB-005", "YB-046"]
blocks: []
blocked_by: []
---

# YB-010 — Adopt RDF for the knowledge layer (rdflib first, Jena later)

> **Open work.** This file is the source of truth for this item; `TODO.md` is generated from it.

**Legacy status:** Analysed, not started
**Legacy priority:** Medium — staged behind extraction reliability

**Full analysis:** [`docs/design/rdf-knowledge-layer.md`](../../design/rdf-knowledge-layer.md)

The complete write-up for this item lives in the design document above, preserved verbatim from `TODO.md` v1 (YB-010).

**Alternatives evaluated (2026-09-30):**

- [`docs/design/terminusdb-evaluation.md`](../../design/terminusdb-evaluation.md) —
  TerminusDB, on the strength of its git-for-data model. **Not a substitute**, and it would
  reverse this item's central choice. Two findings decide it: the RDF document formats
  (Turtle/JSON-LD/RDF-XML) and `@context` processing are **Enterprise-only**, and its query
  language is **WOQL, not SPARQL**, which forecloses the Jena path this item preserves.
- [`docs/design/omnigraph-evaluation.md`](../../design/omnigraph-evaluation.md) — Omnigraph,
  raised as coming close on time-travel *and* ontology. The premise splits: it is the
  **strongest of the three on time-travel and collaboration** (branch-per-agent,
  review-and-merge, `--if-commit`, static per-query `reads`/`writes`) and the **weakest on
  ontology** — no RDF, no SPARQL, and no class hierarchy, so the LinkML ontology is not
  expressible in its schema language (`is_a`, `abstract`, `mixins` and polymorphic `range`
  are all absent, and edges take fixed concrete endpoints). Carries the head-to-head table.

Both are relevant to **YB-004 and YB-046**, not to this item: they answer *"how do I version
and collaborate on a graph?"* where YB-010 asks *"how do I reason over one
deterministically?"* — and that answer is still rdflib, SHACL and SPARQL.

**Gate status (2026-09-30):** the obstacle this entry names is met — `inv_ontology_class_coverage`
passes at **100%** on both `req_sample` (48/48) and `req_prd` (59/59), against a `min 80%`
fallback. Two caveats: `--validate-only` re-checks saved artifacts rather than a fresh run,
and the harness still has one unrelated gate failing (`inv_no_tech_leak`, `Quartz` as an
element). The precondition is satisfiable; declaring extraction reliability *proven* is a
judgement for the owner, not a number this entry can close on.

**A second caller, and a better argument for this item (2026-10-03).** The query surface
this entry specifies is written, tested and **unreachable**: `run_query` and the four named
queries in `core/knowledge/rdf.py` are called only from
`scripts/test_knowledge_layer.py`, and the sole product path to RDF is `/export/graph.ttl`.
[YB-066](YB-066-natural-language-enquiry.md) proposes to make asking the graph a
first-class interaction by translating natural language into *named* deterministic queries
— which gives those four queries a caller and makes the closed query language the reason
the interface is safe, not only the reason the audit is. Design:
[natural-language-enquiry.md](../../design/natural-language-enquiry.md). §C of
[graph-interaction.md](../../design/graph-interaction.md) reached the same conclusion from
the UX side, independently.

## SHACL: state today, and the trap this item must not walk into (2026-10-03)

**SHACL is not in use anywhere.** `pyshacl` is neither installed nor declared; no ontology
YAML declares `shapes:`, `rules:` or any `sh:` term; there is no generated shapes file; and
nothing validates the graph against shapes. SHACL appears only in prose — this entry, the
legacy TODO, the two engine evaluations, and YB-066.

**It is reachable, and generation still works** (re-verified against the current ontology,
which is now five layers and 70 classes — the design document's 242,561-char figure predates
that):

| Entry schema | Generated SHACL | NodeShapes | `sh:closed` |
|---|---|---|---|
| `architecture_base.yaml` | 295,405 chars | 63 | 63 |
| `requirements_base.yaml` | 178,447 chars | 42 | 42 |
| `governance_base.yaml` | 202,856 chars | 49 | 49 |

Every generated `NodeShape` is closed. `SchemaView(...).merge_imports()` is required first —
the same workaround, for the same reason, that `tests/test_ontology.py::test_resolution_matches_linkml`
already applies.

**What runs instead: 15 hand-rolled validators reading the same ontology.** `check_enum_membership`,
`check_element_types`, `check_object_contract`, `check_reference_kinds`, and eleven more, making
**25** calls into the ontology reader (`ontology_enum`, `ontology_classes`,
`ontology_slot_range`, `ontology_subclasses`). So the closed-world check already exists; it is
implemented directly rather than expressed in SHACL.

**The distinction is the surface, not the capability.** Those validators run on a **run's
output** — they are called inside the architecture-extraction and design agents, against that
run's `triples` and `elements` — and there is no graph-level validation entry point at all (no
validate module under `core/knowledge/`). So today's checks are an **ingest gate**; SHACL would
be a **graph audit** over the RDF projection, cross-graph joins included.

**The trap:** adopting SHACL as a *second* implementation of the same closed-world check
creates two mechanisms for one fact — the defect this repo repeatedly finds
(`shared_across_enterprise`'s two mechanisms; "four layers" in prose beside five cards). So
SHACL must either **replace** the subset of validators whose semantics it captures, or be
**asserted equivalent** to them. The house pattern for that second option is already here:
`test_resolution_matches_linkml`, whose docstring says it plainly — *"Asserting against my own
expectations would only prove I am consistently wrong."* The same test shape would hold the
validators and the generated shapes together.

The gains SHACL actually offers are therefore **(a) the surface** — the accumulated graph rather
than one run's output; **(b) the standard** — an external tool (Jena, later) can execute the same
shapes; and **(c) one source** — the ontology, already the source both would read. Not "validation",
which is present and tested.

## The query surface gained the class hierarchy, 2026-10-03

`to_rdf` now emits `rdfs:subClassOf` for every declared `is_a` (26 pairs), read from
`OntologyModel` rather than restated. **This was a precondition, not a nicety**: the
ontology's `Requirement` is abstract, nodes are typed with their concrete class, and the
hierarchy is two levels deep (`PlatformMultiTenancyRequirement` → `NonFunctionalRequirement`
→ `Requirement`). So before this, `?req a sea:Requirement` matched nothing, and the
workaround — a `UNION` over the subclasses an author knows about — is a second copy of the
ontology that silently misses every subclass added later. Measured: a `UNION` over the four
direct subclasses of `Requirement` finds **14** rows where `?kind rdfs:subClassOf*
sea:Requirement` finds **15**.

`is_a` only. `mixins` are supertypes for slot inheritance but cross-cutting aspects rather
than a taxonomy, and asserting them would put `ExternallyReferenced` and `Provenanced` in
every subclass closure — the distinction the ontology viewer keeps visible.

## `missing_active` was silently always-empty, and is not registered

Its `FILTER NOT EXISTS` shared **no variable** with `?req`:

```sparql
FILTER NOT EXISTS { ?el sea:implements_requirement ?ref . ?el rdfs:label ?ref_label . }
```

so it asked "does *any* element implement *anything*" and returned **zero rows whenever one
did** — on a fixture with one implemented and one unimplemented requirement, `[]`. It also
matched abstract `sea:Requirement`, and nothing tested it: `scripts/test_knowledge_layer.py`
exercised only `unverified`.

The body is now correct — correlated, subclass-aware, and matching a bound edge **or** a
literal reference (`?x = ?req || ?x = ?label`, because 9 of 10 real `implements_requirement`
links are literals) — and it remains **out of `QUERIES`**, for three measured reasons
recorded in its own comment:

1. **It cannot separate the states the platform distinguishes.** On `payments_v2` the
   corrected body returns **15** where `realization_report` reports **3** with no claim and
   **10** unresolved —
   because binding a reference is `reference_targets_a_node`'s job, not a SPARQL pattern's.
   The platform's answer stays in `core.knowledge.realization`.
2. **Its precondition is a COMPLETE run**, which a query cannot enforce; on a PARTIAL graph
   it reports extraction failures as architectural gaps.
3. **`include_superseded=False` is part of its meaning** — the plain triple form is emitted
   regardless of status, so a retired implementer counts unless the caller asks for the
   current-state graph. That cannot be fixed from inside the query.

In its place the harness gained four checks that would have caught all of it: the hierarchy
is emitted, the query finds exactly the uncited requirements (including a grandchild
subclass), a superseded implementer stops counting only in the current-state graph, and
`realization_report` reports more states than the query can.
