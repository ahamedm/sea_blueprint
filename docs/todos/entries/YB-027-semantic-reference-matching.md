---
id: YB-027
legacy: null
title: "Semantic reference matching — bridge the paraphrase the deterministic scorer cannot"
status: open
priority: high
area: "`core/knowledge/reconcile.py`, possibly `core/knowledge/realization.py`, `app/`"
created: 2026-09-24
updated: 2026-09-24
design: null
record: null
superseded_by: []
related: ["YB-005", "YB-011", "YB-007"]
blocks: []
blocked_by: []
---

# YB-027 — Semantic reference matching — bridge the paraphrase the deterministic scorer cannot

> **Open work.** This file is the source of truth for this item; `TODO.md` is generated from it.

**Legacy status:** Not started — split out of YB-005 when that closed
**Legacy priority:** High — it bounds how much of the audit reconciliation can ever reach
**Legacy area:** `core/knowledge/reconcile.py` (candidate generation), `app/` (proposal review)

---

### What is left over from YB-005

[ADR-0012](../decisions/ADR-0012-req-arc-reconciliation-inversion.md) closed the
deterministic half: identifiers, citations, Initiative scoping, and the
requirement-side audit. This is the residue, and it is a different problem rather
than a missing feature of that one.

Measured on the saved fixture (22 requirements, 13 cross-graph references):

| | |
|---|---|
| References with a proposal the matcher can offer | 4 |
| References the matcher cannot see at all | **9** |
| Requirements still unanswered after everything binds | 20 |

The 9 are not obscure. They are the entry's original examples:

| Architecture says | The requirement graph has |
|---|---|
| `High Availability`, `Scalability`, `Performance` | NFRs about availability, scaling, latency |
| `Request Acceptance and Validation` | `FR-PM-001` "Payment Acceptance" |
| `Settlement Request Initialization` | "Settlement Initialization" |
| `PAN and CVV Data Protection` | "Cardholder Data Encryption" |

`"Payment Acceptance"` vs `"Request Acceptance and Validation"` shares one token
("acceptance") and scores 0.45 — below any threshold a false-positive-averse
matcher can accept. The two are the same requirement, stated twice, and no amount
of tuning the lexical scorer will say so.

### Why it was not done inside YB-005

The current matcher is **deterministic and explainable on purpose**: every
proposal carries the reason it was proposed (`exact`, `external_ref`,
`citing_document_key`, `contains`, `tokens`), which is what lets a reviewer accept
a link without re-reading both documents. Semantic matching replaces that reason
with a number nobody can inspect, so it is a change of mechanism with a real cost
— and the cost has to be paid deliberately.

### What a solution has to preserve

1. **Propose, do not bind.** The auditor's rule stands: a wrong traceability link
   is worse than a missing one, because it makes the audit confidently wrong.
   Semantic proposals enter the same queue as lexical ones and go through the same
   `resolve_reference`.
2. **The reason has to survive.** If an embedding proposes `"Request Acceptance
   and Validation" -> Payment Acceptance`, the reviewer needs the evidence shown —
   the two texts side by side, the score, and what model produced it. A proposal
   nobody can evaluate is worse than no proposal.
3. **Kind scoping still applies.** `EXPECTED_TARGET_KINDS` gating is what stops a
   capability reference binding to a container on similarity alone, and semantic
   similarity is *more* prone to that, not less.
4. **Provenance.** Which model and which method produced the suggestion belongs in
   the audit trail, next to the lexically-derived reasons, so the two kinds of
   proposal are distinguishable after the fact.
5. **Determinism where it is still available.** Embeddings are deterministic for a
   fixed model version; a model-assisted *proposal* is not. Prefer the former, and
   if the latter is used, cache by (reference text, candidate label) so the same
   pair is not re-costed and does not move between runs.

### Open questions

- Where does the vector live? Recomputing at request time is affordable on
  hundreds of nodes and not on the enterprise graphs this is meant for; a stored
  index is a new piece of state with its own invalidation problem.
- Does the privacy posture allow sending requirement text to an embedding service?
  The requirements documents may contain the kind of thing an NFR is written about
  precisely because it must not leave. A local model is a different cost.
- Embedding or model-assisted? Embeddings are cheap, explainable (cosine distance)
  and blind to structure; a model can be told the ontology's kinds and asked why,
  at orders of magnitude more cost per pair.

### Related

- [YB-005](YB-005-arcg-reqg-linkage.md) — the closed item this was split from.
- `ROUTING_ALIASES` and the predicate-vocabulary work ([ADR-0010](../decisions/ADR-0010-predicate-vocabulary.md))
  are the same lesson one axis over: the mechanism existed, and nothing used it.
- The `mislabel_suspected` flag (ADR-0006 decision 4) already catches the case
  where the *predicate* is wrong rather than the target missing. Several of the 9
  unseeable references look like that instead, so YB-007's extraction work may
  shrink this list before any semantic matcher touches it.
