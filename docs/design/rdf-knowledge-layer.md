# Adopt RDF for the knowledge layer (rdflib first, Jena later)

> **Design document** for `YB-010`. Status is tracked in that entry.
> Preserved verbatim from `TODO.md` v1 (YB-010).

---

**Full analysis:** [`docs/architecture-review.md`](../../docs/architecture-review.md) Part 2

**Why:** makes the *audit* deterministic while leaving extraction as-is, and its
biggest value is diagnostic — a fixed query over a varying graph does not hide
extraction non-determinism, it **exposes** it. Also makes ARC-G ⇄ REQ-G a SPARQL
join (YB-005) and gives named graphs for revision diffing (YB-004).

**Tool:** `rdflib 7.6.0` and `SPARQLWrapper 2.0.0` are already installed.
`pyshacl` would be needed. rdflib + pySHACL delivers most of the benefit with no
service boundary and no second toolchain; `SPARQLWrapper` already bridges to a
Jena Fuseki endpoint later, so choosing rdflib now does not foreclose Jena.

**Verified:** LinkML generates SHACL from our ontology (242,561 chars from
`architecture_base.yaml`), including `sh:closed true`. **Caveat:** generation
fails on any layer with `imports:` (`KeyError` on the imported schema name);
workaround is `SchemaView(...).merge_imports()` before generating.

**The trap:** SHACL is closed-world (absence is a violation — right for gap
auditing); OWL is open-world (absence entails nothing — a gap query finds nothing,
ever, silently). **SHACL for the audit, OWL for entailment.**

### Do not start this until

Extraction reliability is proven by the harness. A precise reasoning layer over a
lossy graph produces **rigorously-derived wrong answers** — worse than fuzzy ones,
because precision implies trust. **The `req_prd` `ontology_class` coverage
invariant is currently failing and is the gate.**
