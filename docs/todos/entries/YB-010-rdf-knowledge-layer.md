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
