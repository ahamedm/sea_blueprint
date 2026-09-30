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

**Alternative evaluated (2026-09-30):** [`docs/design/terminusdb-evaluation.md`](../../design/terminusdb-evaluation.md)
— TerminusDB, on the strength of its git-for-data model. Verdict: **not a substitute**,
and it would reverse this item's central choice. Two findings decide it — the RDF document
formats (Turtle/JSON-LD/RDF-XML) and `@context` processing are **Enterprise-only**, and its
query language is **WOQL, not SPARQL**, which forecloses the Jena path this item preserves.
Its real strength (branch/merge/time-travel over a graph) lands on **YB-046**, not here.

**Gate status (2026-09-30):** the obstacle this entry names is met — `inv_ontology_class_coverage`
passes at **100%** on both `req_sample` (48/48) and `req_prd` (59/59), against a `min 80%`
fallback. Two caveats: `--validate-only` re-checks saved artifacts rather than a fresh run,
and the harness still has one unrelated gate failing (`inv_no_tech_leak`, `Quartz` as an
element). The precondition is satisfiable; declaring extraction reliability *proven* is a
judgement for the owner, not a number this entry can close on.
