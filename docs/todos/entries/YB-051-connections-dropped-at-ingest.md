---
id: YB-051
legacy: null
title: "Connections are extracted and then discarded — the graph has no C4 arrows"
status: in-progress
priority: high
area: "`core/knowledge/ingest.py` (does not read `connections`), `app/viewpoints/merged.py` (kind registry), tests"
created: 2026-09-27
updated: 2026-09-27
design: docs/design/async-run-progress.md
record: null
superseded_by: []
related: ["YB-025", "YB-012", "YB-006", "YB-024", "YB-007"]
blocks: []
blocked_by: []
---

# YB-051 — Connections are extracted and then discarded

> **In progress.** Found while assessing C4 rendering ([YB-025](../entries/YB-025-c4-specification-view.md)):
> the diagram's boxes are all there and its arrows are not, because a whole extraction
> pass produces facts that never reach the graph.

### The problem

The architecture profile runs a `connections` pass per chunk and emits the records
under their own output key:

```python
# agents/architecture_extraction/agent.py:236
"connections": [self._as_output_dict(c) for c in connections],
```

`graph_from_extraction` reads **eleven** keys — `elements`, `triples`, `references`,
`technology_stacks`, `architecture_styles`, `design_techniques`,
`engineering_conventions`, `quality_scenarios`, `architecture_patterns`,
`entities`, `initiatives` — and **`connections` is not one of them.** The records are
dropped between the model and the graph, by construction, every run.

Measured on the live architecture graph (`data/sea-deepseek`, scope `async`):

| What | Count |
|---|---|
| Active assertions | 810 |
| `c4_level` literals (CODE 23 / CONTAINER 17 / CONTEXT 12 / COMPONENT 7) | 59 |
| `element_type` literals | 64 |
| `part_of` (containment) | 73 |
| **Element→element connection predicates** | **0** |

So the graph can say what the boxes are and what they contain, and cannot say what
talks to what.

### Why this is not the ontology's fault

`ontology/architecture_base.yaml` already declares the class, and describes it as
exactly what is missing:

```yaml
Connection:
  description: >-
    A runtime call path from one element to another, with the technology used.
    This is the "arrow" in a C4 diagram — and the edge the auditor walks to find
    coupling and single points of failure.
  attributes:
    source: {range: ArchitectureElement, required: true}
    target: {range: ArchitectureElement, required: true}
    via_interface: {range: Interface}
    style: {range: IntegrationStyle}
    protocol: {range: IntegrationProtocol}
    is_synchronous: {range: boolean}
    carries_sensitive_data: {range: boolean}
    failure_handling: {range: string}
    description_text: {range: string}
```

The model was designed. The plumbing was never written.

### Why it matters beyond C4

- **The auditor's edge does not exist.** "Coupling and single points of failure" is a
  question about connections, and the graph cannot be asked it.
- **A model call per chunk is paid for and thrown away** — the pass is not free.
- **The C4 view is unbuildable as a specification.** Boxes and containment render; a
  diagram with no arrows is a box chart, and the notation would be emitted without the
  part that carries meaning.

### Second, separate finding — the pass itself returned empty

On the live architecture run the `connections` pass reported
`outcome=empty, triples=0` for **all three chunks**, while `structure` and
`technology` succeeded in structured mode (`traceability` succeeded on one chunk via
the text fallback). That is why that run is `PARTIAL`. It needs its own diagnosis —
the baseline (`docs/reference/extraction-baseline.md`) recorded 26 connections on an
earlier document, so the pass *can* work. Fixing ingest alone will not put arrows in
this graph; it will put arrows in the *next* one. Recorded here so the two are not
confused when the diagram is still empty.

### Checked, and *not* the same defect: the requirements profile's edge key

The requirements profile emits its edges under `relationships` (the profile hook
returns `{"nodes": "entities", "edges": "relationships"}`) and ingestion ignores that
key too — so this looked like the same bug on the other profile. It is not.
`ExtractedRelationship` is a **predicate vocabulary**:

```python
class ExtractedRelationship(BaseModel):
    """An extracted relationship type (predicate)."""
    relationship_type: str
    description: str = ""
```

It has no source and no target, and when the model does not state the types the
profile derives them *from* the triple predicates — "the predicate IS the
relationship". The triples are ingested, so nothing is lost. The architecture
profile's `connections` is a different record: it carries real endpoints, a protocol
and a style. Only that one was being thrown away.

### Shape

Reify each connection as a **`Connection` node**, not a bare element→element edge —
the ontology's `protocol`, `style`, `is_synchronous`, `failure_handling` and
`via_interface` have nowhere to live on the assertion model, which is
`(subject, predicate, object|value)` and has no room for edge attributes. This follows
the precedent already in ingest for `QualityAttribute`: materialise the thing as a
node, then assert about it.

```
Connection "Payment Orchestrator → Transaction Store"
    source           -> Payment Orchestrator      (node)
    target           -> Transaction Store         (node)
    protocol         = "JDBC"                     (literal)
    style            = "SHARED_DATABASE"          (literal)
    description_text = "persists"                 (literal)
```

An endpoint that does not resolve to a declared element becomes a `Concept`
placeholder through the existing `_resolve`, so the connection stays visible and shows
up as an unresolved reference rather than silently vanishing — the same rule every
other undeclared referent follows.

### Acceptance

- A connection in the extraction output becomes a `Connection` node with `source` and
  `target` edges to the elements it names.
- `protocol`, `style` and the description travel with it as literals.
- A connection whose endpoint names nothing declared is represented (placeholder), not
  dropped.
- `Connection` is in the architecture kind registry, so a view includes it
  deliberately rather than treating it as unmapped.
- The map's architecture lens shows the arrow (a Connection node with its two edges),
  and no existing view breaks.

### Decisions this item has to make

1. **Does the Design Assistant's digest need connections?** `core/knowledge/digest.py`
   groups kinds into sections and has none for `Connection`, so the design input would
   still not see coupling. Probably yes — but it changes the digest's size, so it is a
   decision rather than a default.
2. **Is `is_synchronous` derived from `style` or asked for?** The pass does not return
   it; `style` distinguishes `SYNCHRONOUS_*` from `ASYNCHRONOUS_*`, so it is derivable
   and should not be invented as a second fact if the first can answer it.
3. **What does a connection between a container and a datastore mean at L1?** A C4
   view has to decide whether to roll connections up to the level it is drawing, or
   only draw the ones whose endpoints are both present. That belongs to YB-025.

### Related

- [YB-025](../entries/YB-025-c4-specification-view.md) — the view that needs the arrow.
- [YB-012](../entries/YB-012-c4-notation-parser.md) / [YB-006](../entries/YB-006-c4-structurizr-importer.md)
  — notation *in*; the round-trip needs the same shape.
- [YB-007](../entries/YB-007-prompt-scaffolding-instruction-dilution.md) — the pass that
  returns empty is a prompt/instruction question, and this is a second measurement of it.
