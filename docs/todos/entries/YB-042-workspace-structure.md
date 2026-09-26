---
id: YB-042
legacy: null
title: "Workspace structure — many products/systems per workspace, each with a baseline and its own initiatives"
status: open
priority: critical
area: "`core/knowledge/store.py`, `app/__init__.py`, `core/knowledge/model.py`, `core/knowledge/ingest.py`, `app/templates/`"
created: 2026-09-26
updated: 2026-09-26
design: docs/design/workspace-structure.md
record: null
superseded_by: []
related: ["YB-018", "YB-034", "YB-009", "YB-011", "YB-037", "YB-043", "YB-044", "YB-046", "ADR-0005", "ADR-0012"]
blocks: []
blocked_by: []
---

# YB-042 — Workspace structure

> **Open work.** This file is the source of truth for this item; `TODO.md` is generated from it.

**Full analysis:** [`docs/design/workspace-structure.md`](../../design/workspace-structure.md)

### The gap in one paragraph

An architect works on more than one product or system. The platform cannot express
that: one app process resolves one store root
(`store_root or SEA_DATA_DIR or "data/sea"`, `app/__init__.py:97,152,166`), so
there is one graph, one baseline, and one ambient initiative. Node identity is the
**label** (`_resolve`, `core/knowledge/ingest.py:296`), so two products that each
have a "Database" collapse into one node. `promote_to_baseline`
(`core/knowledge/review.py:616`) writes `SYSTEM_BASELINE` globally with no product
qualifier, and `review_progress(graph)` (`:251`) audits the whole store, so "is
this product's baseline auditable?" is not a question the system can answer. The
routes are flat (`app/__init__.py:211-1103`) with no product in the path or the
chrome.

### Why critical

Every user-journey stage past 2 is blocked or partial (`docs/user-journey.md`).
With one global graph, the first product with content owns the baseline and every
later product is reviewed and audited against the wrong context — and the Living
System merge, which is only correct for a single system, promotes one product's
proposal into every product's baseline. The failure is silent and looks like
success.

### What already exists

The model is written down (`docs/living-system-architecture.md`: Initiative →
feature branch, System Architecture → Baseline, Domain Ontology → shared frame);
the vocabulary is modelled (`Product`, `SubProduct`, `System`, `Application`,
`Platform` in `ontology/enterprise_structure.yaml:166,207,238,285,341`;
`Initiative` at `ontology/requirements_base.yaml:550`); and the mechanism is
half-built (assertion `scope` + `initiative_id`, `promote_to_baseline`,
`Revision.kind == "baseline"` + `RevisionStore.freeze`). **What is missing is the
container** that says which product a baseline, an initiative or a node belongs to.

### Direction

Recommended first slice: a **workspace as a directory tree** —
`data/workspaces/<ws>/products/<id>/`, where a product *is* today's store layout
unchanged, plus a `workspace.yaml` manifest. `RevisionStore` already takes a root,
so this reuses the proven store, keeps node identity safe by isolation, and makes
the audit gate per product for free. Product-scoped identity inside one graph
(option B) and a shared enterprise layer (option C) are recorded in the design doc
with their tradeoffs; the manifest should be shaped so C can be added without a
migration.

The UX this unlocks: `Workspace → Product → (Baseline | Initiatives) → Initiative`,
with a product switcher, a Baseline card (revision, frozen by/at, review progress,
auditable), an Initiatives list, and commit/freeze/promote scoped to the selected
product.

### Scale note — 50+ architects, 200+ products

This MVP shape is right for one architect and a handful of products, but the
enterprise target is two orders of magnitude larger, and it separates two
questions the design doc now keeps apart (§10):

- **The layout scales.** 200 products are 200 independent write domains — exactly
  the sharding wanted — and it lets the store be replaced *per product* rather
  than in one migration.
- **The file store behind it does not.** No locking, a read-modify-write index,
  and a whole-graph reload per request. That is its own decision,
  [YB-043](YB-043-system-of-record.md), and the defect is already live: the server
  runs threaded by default, so two reviewers on one product can clobber each other
  today.

### Baseline placement — corrected 2026-09-26

A product's baseline ARC-G must **not** live in that product's/architect's workspace.
It belongs to the **central layer**: cross-product reasoning is the product, the
enterprise owns the baseline while a product team only proposes, and re-baselining is
a governed enterprise act. The data model already half-agrees — `scope` separates
`INITIATIVE_PROPOSAL` from `SYSTEM_BASELINE` and `promote_to_baseline`
(`core/knowledge/review.py:616`) moves one to the other — but there is no
`system_id` anywhere in `core/` or `app/`, so no container boundary exists.

The workspace/store concept **is** reusable for this; the recommendation is that the
**system** becomes the container and an **initiative is a branch** inside it, rather
than adding a second tier of workspace. Full reasoning, the two models, and the
identity consequence (a central store reintroduces the label-collision problem, so it
needs `(system_id, node_id)` plus a shared scope for cross-system facts) are in
[`docs/design/workspace-structure.md`](../../design/workspace-structure.md) §11.

Model B removes the cross-store hop but **not** the guarded promotion — two
architects on one initiative still need a branch, a three-way merge and an approval.
That is [YB-046](YB-046-branching-and-promotion.md).
