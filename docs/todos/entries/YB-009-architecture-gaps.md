---
id: YB-009
legacy: "9"
title: "Architecture gaps — canonical model, correction merge, incompleteness"
status: open
priority: high
area: "`core/knowledge/`, `core/ontology.py`, `docs/architecture-review.md`"
created: 2026-09-20
updated: 2026-09-24
design: null
record: null
superseded_by: []
related: ["YB-023", "YB-018", "ADR-0015"]
blocks: []
blocked_by: []
---

# YB-009 — Architecture gaps — canonical model, correction merge, incompleteness

> **Open work.** This file is the source of truth for this item; `TODO.md` is generated from it.

**Legacy status:** Analysed, not started
**Legacy priority:** High — these are load-bearing, not polish

---

**Full analysis:** [`docs/architecture-review.md`](../../../docs/architecture-review.md)

A sanity check of the proposed system architecture (agents / workflow / two UIs /
MCP servers / API) surfaced two gaps that force decisions everything else depends
on. Recorded in full in the linked document; the actionable core:

**9a. Canonical knowledge model + owned serialisation layer. — ✅ DONE**
`core/knowledge/` implements the model, the ingest transform and RDF emission.
28/28 checks pass (`scripts/test_knowledge_layer.py`). View projection layer
proven via CLI table view (`scripts/review_assertions.py`) — exposes all assertions
with provenance for human review. Still to do: wiring the agents to emit the
canonical model rather than JSON dicts, and C4 / gap-report view models.

Verified on real output: ARC-G ingests to 31 nodes / 156 assertions / 0 dangling /
13 unresolved cross-graph references; 2,030 RDF triples. Two findings from the
exercise are below.

*Finding — the requirements extraction emits 3.5x duplication.* 169 triples
collapse to **48 unique facts**, each repeated up to 4x. The architecture agent
deduplicates via its chunk/pass merge; the requirements agent is still single-call
and has no merge, so duplicates survive. Content-addressed assertion ids collapse
them at ingest, but the extraction itself should stop producing them.

*Finding — completeness must be UNKNOWN, not FAILED, when pass data is absent.*
The requirements output carries no per-pass metadata, and treating that as FAILED
is a false alarm — while treating it as COMPLETE would be a false assurance, which
is worse. `RUN_UNKNOWN` is now distinct from both, and reaches the RDF so a
consumer can honour it. The requirements agent should emit per-pass records.

**9a-i. Canonical knowledge model + owned serialisation layer.**
Five representations are already in play (Markdown → Pydantic → LinkML → RDF →
UI views) and no component owns the transformations between them. The implied gap
is:

```
extraction output → ??? → Jena → SPARQL → UI
```

That `???` is the highest-risk component in the system: every extraction change
breaks it, it is where `confidence`/`source_text` must land on reified
assertions, and it is the only thing that can guarantee a well-formed graph. A
named part with tests, not an implementation detail.

**9b. Human corrections vs re-extraction — ✅ PROVEN**
The canonical model structurally distinguishes agent-asserted from human-confirmed
knowledge via `Provenance.source_type` and `Assertion.status`. The merge logic in
`add_assertion()` ensures human corrections survive agent re-observations. Proven
via `scripts/review_assertions.py` — interactive CLI allows marking assertions as
VERIFIED or CORRECTED, with corrections persisting in the graph. Next: wire this
into a proper UI and implement the diff-and-review workflow for re-extractions.

**9c. Incompleteness must be representable. — ✅ IMPLEMENTED**
`ExtractionRun.completeness` tracks COMPLETE / PARTIAL / FAILED / UNKNOWN per run.
`PassRecord` captures per-chunk outcomes. This prevents partial extractions from
being mistaken for complete ones, which would cause the auditor to report missing
content as architectural gaps. Verified: completeness state reaches the RDF so
consumers can honour it.

**9d. Revisions.** The PRD requires evolving requirements/architecture. Nothing
diffs or versions. Named graphs would carry it; no component owns it.

**Also worth settling:** orchestrator vs workflow (recommendation: **workflow**
with explicit human gates — a dynamic router adds non-determinism to a product
selling determinism), agents stateless with the graph as memory, one validation
service consumed by both UI and API, and a stage→validate→commit write path.

### Sequencing — REORDERED by the user journey

`docs/user-journey.md` reorders this. The journey is blocked at the **human
review gate**, not at extraction: knowledge can be produced but has no route to a
person and no route onward. The pipeline's only exit is a JSON file that only the
test harness reads (verified).

Blocking, in dependency order:

1. **View projection layer** — nothing to look at; every downstream step needs it
2. **Correction write path** — the review gate has nowhere to record a decision
3. **Revision / baseline** — without it "verified" is a flag on a mutable graph
4. **Reconciliation** — the core job; 13 unresolved refs already surface and go nowhere
5. **Audit engine + gap report** — the product

Demoted: the requirements pass-split was going to be next. It improves step 2,
which already works. Quality, not blocking.

### Original sequencing (superseded)

1. Canonical model + serialisation layer — nothing else is testable without it
2. Validation as a service, rules **generated from the ontology**
3. Correction write path + merge semantics — hardest, so before UI depends on it
4. Validation/correction UI
5. Workflow (scriptable first; four passes do not need an engine)
6. MCP servers — good encapsulation, not load-bearing early
7. Interaction UI — last, least risky, most likely to change

**Build UIs last.** They are the most tempting to start with and the most likely
to lock in data-model decisions that should be deliberate.

---

### Added by ADR-0015 (retiring extracted facts)

`retire` (ADR-0015) removes a fact from the working set and survives re-extraction.
It deliberately does **not** retract a fact from a frozen revision: snapshots are
immutable by design, so a promoted fact that is later removed is still asserted by
the baseline revision it was promoted into. Expressing a *retraction* — rather than
an addition — as a change relative to a baseline is this item's territory, and it is
the same correction-merge problem seen from the other side: the overlay has to be
able to say "this baseline fact is now wrong", not only "here is something new".
