---
id: YB-051
legacy: null
title: "Connections are extracted and then discarded — the graph has no C4 arrows"
status: in-progress
priority: high
area: "`core/knowledge/ingest.py` (does not read `connections`), `app/viewpoints/merged.py` (kind registry), tests"
created: 2026-09-27
updated: 2026-09-28
design: docs/design/async-run-progress.md
record: null
superseded_by: []
related: ["ADR-0029", "ADR-0030", "YB-012", "YB-006", "YB-024", "YB-007", "YB-054"]
blocks: []
blocked_by: []
---

# YB-051 — Connections are extracted and then discarded

> **In progress.** Found while assessing C4 rendering ([ADR-0029](../../decisions/ADR-0029-c4-specification-view.md)):
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

### Second finding, diagnosed: the pass is intermittent — and reported it as "empty"

The run in that scope reports `connections outcome=empty, triples=0` for **all three
chunks**, while `structure` and `technology` succeeded in structured mode. That run
read **exactly this document** (`payment_platform_arch.md`, hash `3e7b5cc59fc82a11`,
14,893 chars, 3 chunks), so the pass was reproduced against it directly:

| Attempt | Outcome | Path | Triples | Time |
|---|---|---|---|---|
| 1 | ok | structured | 8 | 12.3 s |
| 2 | **failed** | none | 0 | **250.3 s** |
| 3 | ok | structured | 16 | 24.2 s |

Two things follow, and neither is a prompt defect:

1. **The pass is intermittent on the local 4B model, and the variance is large** —
   8 triples or 16 from identical input, and one attempt burning 250 s before
   answering nothing. That is
   [YB-004](../entries/YB-004-model-output-not-structurally-stable.md) and
   [YB-020](../entries/YB-020-structured-path-budget.md) with a concrete measurement.
   The failing attempt died in the text fallback with a `ValidationError`, so the
   model produced prose that could not be read as the schema either.
2. **The failure was labelled `empty`, which is why nobody looked.** `empty` was
   computed as "the structured call returned no object" — a failure to *answer* — and
   read as "the model had nothing to say", so a whole missing pass looked benign.
   Fixed: the three outcomes now mean what ADR-0013 says they mean.
   - `ok` — a valid result carrying something;
   - `empty` — a valid result carrying nothing ("nothing here", which is an answer);
   - `failed` — no valid result from either path, recorded **with a cause** naming
     what was tried and how long it took.

   Verdicts are unchanged (`failed or empty` both yield `PARTIAL`), with one honest
   exception: a run whose every pass answers nothing is now `FAILED`, not `PARTIAL`.

### Corrected: `connects_to` is *not* an ontology gap

This entry first claimed the pass demanded a predicate the vocabulary does not
declare. It does not declare it — but it does not declare `part_of` or
`uses_technology` either, and those work. The ontology models **classes and typed
attributes** (`depends_on_components`, `hosted_on`, `implements_interfaces`,
`protocol`, `style`), not a closed list of graph predicates; free-form predicates are
minted by the passes and stored by ingestion. The missing declaration was never the
cause, and the reproduction proves it: the same prompt produced `connects_to` records
fine.

### Now open: the edge exists twice

With both halves fixed, a successful connections pass yields **two** representations of
one fact — the `connects_to` triples the pass is instructed to emit, and the
`Connection` node ingestion materialises from the records. They are not equivalent
(the node carries protocol, style and failure handling; the edge is the topology every
existing consumer uses — `repair_containment`, `edge_records`, the exclusion filters),
but two representations is exactly the drift this codebase keeps refusing. Decide:
derive the edge from the record in ingestion and stop asking the model for the triple,
or keep both and say why.

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
3. ~~**What does a connection between a container and a datastore mean at L1?**~~ **Settled**
   by [ADR-0029](../../decisions/ADR-0029-c4-specification-view.md) and §3 of
   `docs/design/c4-specification-view.md`: a relationship whose endpoints sit below the
   drawn level is re-pointed at the ancestors that *are* drawn and counted, and one whose
   two ends collapse to the same ancestor is dropped, because that is coupling inside a
   single box. The count travels with the diagram, so a rolled-up arrow says so.

### Still needed to close this — and the closing condition FAILED its first test

The two shapes above are decided, and the emission fix has landed — `/c4` emits
`Connection` nodes as arrows and `to_payload` reports a relationship per pair. What was
not demonstrated end to end was a **live run whose connections pass succeeded**, so that
was written as the closing condition: re-extract `payment_platform_arch.md` and confirm
`/api/c4` reports a non-zero `relationships` count.

**Re-run, 2026-09-27** (`run_da71f1dfac87`, same document, same scope, 3 chunks, ~28 min
wall): the run succeeded as a run — `PARTIAL`, and `structure`/`technology`/
`traceability` all produced triples — and the connections pass produced **nothing usable
in any chunk**:

| Chunk | Outcome | Path | Elapsed | Triples |
|---|---|---|---|---|
| 1/3 | **failed** | none | 256.5 s | 0 |
| 2/3 | **empty** | none | 255.8 s | 0 |
| 3/3 | ok | structured | **1.9 s** | **0** |

So `relationships: 0` on `/api/c4` is current, not stale. Three things follow.

**1. `ok` with zero records is a distinct outcome from `empty`, and neither is a
connection.** Chunk 3 returned a *valid structured result carrying nothing* in 1.9 s,
where `technology` on the same chunk took 50.3 s for 8 triples and `structure` on chunk 2
took 23.7 s for 15. A pass that answers a schema-shaped "nothing" almost instantly is
being asked something it can decline — the prompt or the schema, not the model's speed.
`empty` and `ok`/0 are both silent here, and only the count distinguishes them, which is
why the count belongs in the run record (it does, via `triples_produced`).

**2. The ~255-second cluster is systematic and is not model slowness.** Every
failed-or-empty attempt in this run landed between 255.8 s and 282.9 s — chunk 1
`structure` 264.8 s, chunk 1 `connections` 256.5 s, chunk 1 `traceability` 267.3 s,
chunk 2 `connections` 255.8 s, chunk 2 `traceability` 282.9 s — while every successful
structured call took 1.9–88.3 s. Three different passes converging on the same ~4-minute
ceiling is a budget being exhausted (timeout or retry ceiling), not a model that was
thinking hard. The earlier 250.3 s failure recorded above is the same number. This is
[YB-020](../entries/YB-020-structured-path-budget.md) with a measurement that names the
mechanism, and it is the most actionable thing in this entry: whatever the budget is, it
is being spent in full and returning nothing.

**3. Chunk 1's `structure` pass also came back `empty` after 264.8 s, and the run merged
anyway.** A missing structure pass is a missing set of boxes, not just missing arrows, and
`PARTIAL` is the only thing that said so.

### Progress 2026-09-28 — the guard landed, the closing condition is half met

Two of this entry's own consequences were built out and recorded in
[ADR-0030](../../decisions/ADR-0030-boundary-refusals-and-output-accounting.md):

- **A second occurrence is now visible.** Every emitted output key is accounted for, and
  `run.unconsumed_keys` / `run.output_counts` report "this many records, no reader" —
  the static half (a seam test over every `PassSpec.output_keys`) plus the runtime half.
  It caught [YB-054](../entries/YB-054-unrouted-requirements-output-keys.md) on its first
  real output.
- **The run can no longer be discarded after the model calls.** Merge, repair and
  validation each run under a guard: a failure keeps the facts and appends a failed
  `(post-extraction)` record, so the verdict is PARTIAL rather than a job failure with
  nothing stored. §1's defect 1 — 12/12 passes and 146 s thrown away — cannot recur in
  that shape.

**The closing condition, re-tested.** "Re-extract `payment_platform_arch.md` and confirm
a non-zero relationship count." A live `deepseek-v4-pro` run on 2026-09-28 (4 chunks, 16
calls, 15 ok / 1 empty / 0 failed, 188 s) produced **15 connections**, including the
request path the edited fixture now states. The *extraction* half passes; `/api/c4`'s
`relationships` count was not re-measured, because that run was not ingested into a
workspace store. What remains genuinely open is the entry's own decision — the edge
exists twice (`connects_to` triples and a `Connection` node) — plus the deterministic
`is_synchronous` derivation (decision 2), re-measured on the same run as `style` set on
15/15 and `is_synchronous` on none.

### Related

- [ADR-0029](../../decisions/ADR-0029-c4-specification-view.md) — the view that needs the arrow.
- [YB-012](../entries/YB-012-c4-notation-parser.md) / [YB-006](../entries/YB-006-c4-structurizr-importer.md)
  — notation *in*; the round-trip needs the same shape.
- [YB-007](../entries/YB-007-prompt-scaffolding-instruction-dilution.md) — the pass that
  returns empty is a prompt/instruction question, and this is a second measurement of it.
- [YB-020](../entries/YB-020-structured-path-budget.md) — the ~255 s cluster is its
  measurement, not a coincidence.
- [YB-004](../entries/YB-004-model-output-not-structurally-stable.md) — the second run also
  reclassified existing elements: the scope's context-level count moved 16 → 12 and
  container-level 17 → 24 across two runs of the same document.
