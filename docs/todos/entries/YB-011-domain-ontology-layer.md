---
id: YB-011
legacy: "11"
title: "Domain ontology layer — the ontology of the SUBJECT MATTER, not the artifact"
status: in-progress
priority: high
area: "`ontology/domains/`, `core.ontology` overlay loader, extraction grounding"
created: 2026-09-21
updated: 2026-09-24
design: docs/domain-ontology-integration.md
record: null
superseded_by: []
related: ["YB-005", "YB-018"]
blocks: []
blocked_by: ["YB-009"]
---

# YB-011 — Domain ontology layer — the ontology of the SUBJECT MATTER, not the artifact

> **In progress.** This file is the source of truth for this item; `TODO.md` is generated from it.

**Legacy status:** Steps 1–3 **implemented** · step 5 (coverage audit) waits on item 9
**Legacy priority:** High
**Legacy area:** `ontology/domains/`, `core.ontology` overlay loader, extraction grounding

**Design:** [`docs/domain-ontology-integration.md`](../../domain-ontology-integration.md)

The layer definition is in the design document above; the original write-up is in [`legacy-todo-v1.md`](../legacy-todo-v1.md) (YB-011).

---

> **Implemented 23 Sep 2026 — steps 1, 2 and 3.**
>
> | Step | State |
> |---|---|
> | 1. `ontology/domains/payment_processing.yaml` | ✅ 20 classes, 7 enums, 3 subsets, 2 abstract; lifecycle state machine as a closed enum; `worked_example` annotation |
> | 2. Per-Initiative overlay loader | ✅ `load_domain_pack` / `discover_domain_packs` / `resolve_domain_pack_path` / `pack_for_graph` in `core/ontology.py`; pack id recorded in `Provenance.domain_pack` and set on `/ingest`; `SEA_DOMAIN_PACK` fallback |
> | 3. Extraction grounding | ✅ pack vocabulary injected as a separate `_format_ontology_context` block; the **payment worked example removed from the base prompt** and replaced by a subject-neutral one the pack overrides |
> | 4. `/ontology/domain` view | ⬜ not started — the picker on `/ingest` exists, the concept-map view does not |
> | 5. Coverage audit (both legs) | ⬜ blocked on the audit engine (YB-009) |
>
> **The silent-import bug is fixed.** `SEABaseAgent._collect_ontology_names` walked
> `imports:` relative to the *importing file's* directory and skipped a miss with a bare
> `continue` — so a pack under `ontology/domains/` importing `requirements_base` inherited
> **nothing**, with no error. Resolution now delegates to the shared loader, and a missing
> import (or an unresolvable slot range, or a pack specialising nothing) is fatal.
> `tests/test_domain_pack.py` holds that as a named regression.
>
> **Notable finding:** `realised_by` (the ARC-G ⇄ DOMAIN leg) is a **string reference, not a
> class range**. Ranging it at `ArchitectureElement` would make the domain layer depend on
> the architecture layer and invert the one-way import rule. It stays an unresolved
> cross-graph reference, like `CROSS_GRAPH_REFERENCE` predicates in the knowledge model.
>
> **Design:** [`docs/domain-ontology-integration.md`](../../../docs/domain-ontology-integration.md).
> **Tests:** `tests/test_domain_pack.py` (50 tests) — pack resolution, integrity failures,
> "no pack" as a first-class state, provenance round-trip, and the prompt-grounding claim.

### What is missing

We built the ontology **of requirements**, not the ontology of the **domain being
required**. `requirements_base` describes the engineering artifact — Requirement,
Goal, Capability, Stakeholder, Process. A Payment Processing domain ontology
describes the subject matter — Payment, Card, PAN, Authorization, Capture,
Settlement, Chargeback, Merchant, Acquirer, Token.

One is the vocabulary of *what we build and how we reason about it*. The other is
the vocabulary of *what the business is actually about*. The meta-level exists;
the content does not.

### Evidence it is structurally absent, not merely unfinished

```
PRD agent #2     Domain Context Agent — "propose ontological structures for a
                 new domain", "suggest initial concepts and relationships"
PRD NFR 5.4      adding a new domain "requires only the creation of a new Domain
                 Context Agent specialization and a corresponding set of LinkML
                 modules"
Built            sea_common, enterprise_structure, requirements_base,
                 architecture_base
Not built        domains/<name>.yaml, and agent #2 itself
```

**The tell is in the flow:** `domain` is read from `input_data`, logged, and
written to metadata by **both** extraction agents — and never reaches a prompt or
a schema. It does nothing. That is the signature of a layer that was assumed
rather than built: the interface exists, the substance does not.

**And `DomainConcept` is the catch-all it was meant to replace.** From the real
requirements output:

```
DomainConcept  contains:  Tenancy Identifier, Payment Request Status,
                          Primary Payment Gateway, Secondary Payment Gateway,
                          Spring Boot, AlpineJS
```

Two **frameworks** classified as domain concepts. A generic placeholder absorbs
whatever does not fit, and in doing so hides mis-classification. Separately, **20
of 47 entities (43%) carry no ontology class at all.** With a domain vocabulary
to map onto, both become findings rather than silence.

### Why this is critical — the coverage question

A requirements document implicitly claims to cover a domain. Whether it does is
**currently unmeasurable**, because there is no reference frame. The auditor can
find gaps *internal* to the requirements (orphans, broken traceability) because
those are relations within one graph. It cannot ask "does this requirement set
cover the subject matter?" because the subject matter is nowhere represented.

With a domain ontology, four questions become measurable — and **the mismatches
run both ways**:

| | Finding |
|---|---|
| Domain concept present in the requirements | covered |
| Domain concept absent | deliberate exclusion, or an oversight |
| Requirement references a concept not in the domain ontology | ontology incomplete, **or** the requirement is confused |
| Requirement references a control as if it were a domain entity | mis-classification (the `Spring Boot` case) |

The third and fourth are the ones usually missed: requirements also **invent**
things the domain does not have, and conflate controls with entities. Both
directions are findings, and neither is expressible today.

### It makes reconciliation triangular, not pairwise

```
REQ-G  ⇄  ARC-G    does the design answer the requirement?          (YB-005)
REQ-G  ⇄  DOMAIN   does the requirement set cover the subject matter?    (new)
ARC-G  ⇄  DOMAIN   which domain concepts has the design ignored?         (new)
```

The third leg is the valuable one, and it is unreachable by any other means.
*"You have built authorisation and capture, but nothing anywhere handles
chargebacks"* is a **domain-coverage** finding, not a traceability finding — no
amount of REQ⇄ARC reconciliation surfaces it, because nothing in the requirements
ever mentioned chargebacks. There is no requirement to be unimplemented.

### Where it sits

Topmost, most-specific layer. It must import `requirements_base` to subclass
`DomainConcept`:

```
sea_common → enterprise_structure → requirements_base → architecture_base
                                                             ↑
                                       domains/payment_processing.yaml
```

Extraction then points at the domain ontology instead of the base and inherits
everything below by import — which the existing import-resolution in
`_collect_ontology_names()` already handles. **No restructuring required.**

### What it takes

1. **`ontology/domains/payment_processing.yaml`** — domain concepts subclassing
   `DomainConcept`, their relationships and constraints, and the payment
   lifecycle state machine (PENDING → AUTHORIZED → CAPTURED → SETTLED).
2. **The Domain Context Agent** (PRD #2) — the component that *proposes* a domain
   ontology from business context. Without it, every new domain is hand-authored.
3. **Wire `domain` through** — inject the domain vocabulary into the extraction
   prompt so concepts map to real classes instead of collapsing to a placeholder.
4. **A coverage audit** — the third reconciliation leg plus the both-ways
   mismatch report.

### Ordering note

Its value is realised **through the coverage audit**, which needs the audit engine
that does not yet exist (YB-009, gap 5). So building the domain ontology before
the audit engine produces a vocabulary nothing consumes.

**Recommended:** record now (done), build after the review gate and audit engine
— unless domain grounding is wanted earlier purely for extraction precision, in
which case step 3 alone is independently useful and much smaller than the whole.

### Why it lingered

**Its absence causes imprecision, not error.** Nothing fails. Extraction works;
the graph is simply coarser than it should be, and coverage questions silently
cannot be asked. The same silent-absence pattern as the corrupted enum
descriptions and the inert subsets: no symptom, so no pressure.

It also sits outside both the build order and the workflow — which is why neither
the sequencing nor the user journey surfaced it.
