---
id: YB-052
legacy: null
title: "Reflexive `part_of` and concepts extracted twice — the two defects the C4 view found"
status: open
priority: high
area: "`agents/architecture_extraction/` (triples/connections passes), `core/knowledge/ingest.py` (reflexive guard), `core/knowledge/review.py` (bulk-verify guard), tests"
created: 2026-09-27
updated: 2026-09-27
design: docs/design/c4-specification-view.md
record: null
superseded_by: []
related: ["ADR-0029", "YB-051", "YB-004", "YB-020", "YB-007"]
blocks: []
blocked_by: []
---

# YB-052 — Reflexive `part_of` and concepts extracted twice

> **Open.** Both found by the C4 specification view on its first live run
> ([ADR-0029](../../decisions/ADR-0029-c4-specification-view.md), [ADR-0029](../../decisions/ADR-0029-c4-specification-view.md)).
> Neither was visible anywhere else in the app: deciding which node kinds are
> architecture elements — which is what reducing a graph to C4 means — is what makes a
> duplicate or an impossible containment show up at all.

### What was measured

Against `payment_platform_arch.md` in `data/sea-deepseek` scope `async`, via `/api/c4`:

| Defect | First run | After a second, independent run |
|---|---|---|
| `X part_of X` — an element that contains itself | 3 | **4** |
| Elements at the code level with no component to contain them | 23 | 23 |
| The same label extracted as a structural element *and* as a concept | 12 | **21** |

The second column is `run_da71f1dfac87`, a re-extraction of the same document into the
same scope. It is not a duplicate count: the graph holds only **one** structural label
appearing on more than one node, so `_resolve` deduplicated as designed. The fourth
self-loop is a **different system** — `Settlement Platform`, a name the first run never
used — which means the reflexive containment is reproducible across independent runs and
not a one-off artefact of one sampling. Extracting the same document twice separately
also reclassified existing elements: context-level 16 → 12, container-level 17 → 24.

### Defect 1 — `X part_of X`, and it survived review

```
Payment Gateway Platform                        part_of  Payment Gateway Platform
Payment Processing Platform                     part_of  Payment Processing Platform
Payment Settlement and Processing Platform      part_of  Payment Settlement and Processing Platform
Settlement Platform                             part_of  Settlement Platform      # second run
```

All four are `VERIFIED`, all carry
`source_type=HUMAN_REVIEWER, pass_name=triples, correction_note="bulk verify (selected)"`
and `derived_from=payment_platform_arch.md`.

Two separate things went wrong, and the second is the more serious:

1. **The extraction asserted a reflexive containment.** The `triples` pass emitted
   `X part_of X` for the document's own system. That is the
   [YB-004](../entries/YB-004-model-output-not-structurally-stable.md) family: a 4B local
   model producing a structurally impossible fact. The prompt names the top-level system
   as the subject of many facts, and "the system is part of …" with no other candidate
   resolves to itself.
2. **Review passed it.** A bulk verify selected and verified them. A reflexive assertion
   is *never* valid for any predicate with an irreflexive range — `part_of`, `hosts`,
   `depends_on`, `implements` — and it is checkable without knowing anything about the
   domain. So this was a review gate that accepted something no reviewer could have
   intended, which is the failure the gate exists to prevent.

**Why it matters beyond tidiness.** In the C4 model a self-referential `part_of` makes
the element a containment *root*, so it silently becomes a boundary in the notation
rather than a child of one. In the emitted Structurizr DSL that is a system declaring
itself inside itself — the file is rejected. And on the graph side, a self-loop means
`repair_containment` and every ancestor walk have to be cycle-safe or they hang: the
`_ancestors` helper in `app/viewpoints/c4.py` is written cycle-safe *because of these
facts*, which is a workaround standing in for a guard. It stays: defensive code should
outlive the reason it was written.

### Defect 2 — the same thing extracted under two kinds

Twelve labels exist twice in the graph, once as an element and once as a concept — 21
after the second run, which is the same defect at a different sampling rather than a new
one:

| Label | Also extracted as |
|---|---|
| Stateless Modular Microservices | `ArchitectureStyle` |
| Stateless Services, Redundancy, Health-Checked Removal from Rotation, Bounded Batch Processing with Checkpoints, Idempotent Job Execution, Asynchronous Offload of Payment State Transitions | `DesignTechnique` |
| Container naming, Service naming, Environment naming, Semantic versioning, Package structure | `EngineeringConvention` |

There is a second, larger half: **all 23 elements at the code level are quality
attributes, design techniques, conventions, security measures and one protocol.** Not one
is a class, a function or a module — `Access Control`, `Encryption`, `Firewall
Configuration`, `Data Integrity`, `High Availability`, `TLS 1.2+`, `Architecture
Document`. So the `structure` pass is classifying non-code concepts as `CodeElement` with
`c4_level: CODE`, and the concepts also arrive correctly through their own passes. The
result is the same fact in the graph twice, once at L4 where it makes no sense.

### Shape

Two guards and one prompt question, in that order — the guards are cheap and general, the
prompt fix is neither:

1. **Reject a reflexive assertion where it enters.** `core/knowledge/ingest.py` is the one
   place every extracted fact passes through, so a `subject == object` assertion for an
   irreflexive predicate is refused there, with a warning naming the predicate and the
   source text. It must be refused rather than silently dropped: silently dropping is how
   this went unnoticed in the first place.
2. **Refuse to verify it in the review gate.** `core/knowledge/review.py` already has
   `BULK_ACTIONS` and `_bulk_eligible`. A bulk action that would verify an irreflexive
   assertion should skip it and report the skip, because bulk verify is exactly how a
   wrong fact acquires human authority.
3. **Then the extraction.** `element_type` should not be `CodeElement` for a quality
   attribute; the `structure` pass needs the L4 definition in the prompt, and the
   duplicate is probably the same pass emitting both the concept and a placeholder
   element for it. This is the part that needs a measurement first: the 12 duplicates and
   the 23 misclassified elements are the evidence, and the fix should be checked against
   both counts.

### Open decisions

1. **Refuse, repair, or both?** Refusing a reflexive assertion loses a fact the model
   meant to state (the system belongs to nothing) — but it stated nothing useful. Repair
   would mean dropping the assertion, which is the same thing without a warning. Leaning
   refuse-and-warn; the decision is whether the *subject* element still needs a parent
   assigned to it elsewhere.
2. **Is `CodeElement` the right kind for conceptual artifacts at all?** The ontology's
   `CodeElement` exists for L4. If the extraction cannot tell an L4 element from a
   quality attribute, the honest answer may be that L4 should not be extracted from prose
   at all — which is the [YB-012](../entries/YB-012-c4-notation-parser.md) argument in a
   different form.
3. **Where does the misclassification get reported?** `/gaps` is the natural home for
   graph-hygiene findings, and it does not currently report either of these. `/c4` does,
   but only because it decided what an element is. Duplicating the check in both is
   acceptable only if the rule lives in one place.

### Acceptance

- No irreflexive assertion can enter the graph from an extraction, and the refusal is
  reported rather than silent.
- A bulk review action cannot verify one, and says which it skipped.
- The `reflexive` and `acyclic` checks in `/c4` pass against a re-extracted
  `payment_platform_arch.md`.
- The count of elements at the code level that are concepts drops — measured before and
  after, with both numbers recorded in the closing record.
- The `_ancestors` cycle guard stays: it is correct defensive code and should exist for
  a reason that is no longer load-bearing.

### Related

- [ADR-0029](../../decisions/ADR-0029-c4-specification-view.md) / [ADR-0029](../../decisions/ADR-0029-c4-specification-view.md)
  — the view that found both, and where the checks now live.
- [YB-051](../entries/YB-051-connections-dropped-at-ingest.md) — the same "extracted and
  then lost or mis-shaped" family, one layer up.
- [YB-004](../entries/YB-004-model-output-not-structurally-stable.md) /
  [YB-020](../entries/YB-020-structured-path-budget.md) — a local 4B model producing
  structurally impossible output.
- [YB-007](../entries/YB-007-prompt-scaffolding-instruction-dilution.md) — the prompt side
  of defect 2.
