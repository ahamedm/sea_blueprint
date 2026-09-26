# Workspace structure — many products/systems per workspace

> **Design document** for `YB-042`. Status is tracked in that entry.
> The model this implements already exists on paper: [`docs/living-system-architecture.md`](../living-system-architecture.md).

---

## 1. What the product asks for

An architect works on more than one product or system, and moves between them
across time: pick a product, look at its current **Baseline**, see the
**Initiatives** changing it, review and merge one, and re-baseline. Today the
platform cannot express any of that. One app instance is one store is one graph,
with one baseline and one ambient initiative, and the UI has no concept of which
product you are looking at.

The ask is a **Workspace** that holds many Products/Systems, and a UX that
navigates `Workspace → Product → (Baseline | Initiatives) → Initiative`, with
re-baselining as a first-class act rather than a button that mutates everything.

## 2. What already exists — do not re-invent this

**The model is written down.** `docs/living-system-architecture.md` names the
three layers and their lifecycle:

| Layer | Persistence | Role |
|---|---|---|
| Initiative | transient, project-based | "feature branch" of the architecture; proposed change |
| System Architecture | persistent, evolving | **the Baseline** — the deployed reality |
| Domain Ontology | persistent, learning | the shared reference frame |

**The vocabulary is modelled.** `Product`, `SubProduct`, `System`, `Application`,
`Platform` are `EnterpriseConstruct`s in
`ontology/enterprise_structure.yaml:166,207,238,285,341`. `Initiative` is at
`ontology/requirements_base.yaml:550`.

**The mechanism is half-built.** Assertions carry `scope`
(`INITIATIVE_PROPOSAL` / `SYSTEM_BASELINE` / `DOMAIN_TRUTH`) and `initiative_id`
(`core/knowledge/model.py:108-110,267`); `promote_to_baseline`
(`core/knowledge/review.py:616`) merges verified initiative facts into the
baseline; `Revision.kind == "baseline"` plus `RevisionStore.freeze`
(`core/knowledge/store.py:85,282`) freezes a revision.

**What is missing is the container.** There is no object anywhere that says which
*product* a baseline, an initiative, or a node belongs to.

## 3. The measurement

1. **One store per process.** `create_app` resolves one root —
   `store_root or SEA_DATA_DIR or "data/sea"` (`app/__init__.py:97,152`) — and
   constructs one `RevisionStore` at startup (`:166`). Every route reads
   `state()` = `store.load_working()` (`:184`). Today's "workspaces" are separate
   directories (`data/sea`, `data/sea-deepseek`, `data/sea_bak`) with no way to
   see or switch between them in the UI.
2. **Node identity is the label.** `_resolve` keys on
   `label.strip().lower()` (`core/knowledge/ingest.py:296`). Two products that
   each have a "Payment Service" — or, far more likely, a "Database" — collapse
   into one node. `declared_by` records which *side* declared a node
   (requirements vs architecture), not which product.
3. **One baseline.** `promote_to_baseline` sets `scope = SYSTEM_BASELINE` on every
   verified initiative assertion, with no product qualifier
   (`core/knowledge/review.py:616`). Several revisions can be frozen and
   `store.baselines()` returns them all, but nothing binds a baseline to a
   product, and parent chaining (`Revision.parent_id`, set from `self.latest()` in
   `store.commit`) is a single global line.
4. **One ambient initiative.** `app.config["INITIATIVE_ID"]` comes from
   `SEA_INITIATIVE` (`app/__init__.py:160`). The graph can hold many initiative
   ids; the *application* offers one, chosen at boot.
5. **The audit gate is global.** `review_progress(graph)` iterates every active
   assertion in the graph (`core/knowledge/review.py:251`), so "is *this product's*
   baseline auditable?" cannot be asked — only "is the whole store reviewed?".
6. **The routes are flat.** `/ingest`, `/review`, `/map`, `/gaps`, `/quality`,
   `/reconcile`, `/changes`, `/ontology`, `/export/*` (`app/__init__.py:211-1103`)
   with no product in the path or the chrome.

## 4. Why this is critical, not cosmetic

Every stage of the user journey past stage 2 is blocked or partial
(`docs/user-journey.md`): review (3, 6), baseline (4), reconcile (7), resolve gaps
(9), publish (10). A single global graph means the first product with content owns
the baseline, and every later product's extraction is reviewed and audited against
the wrong context.

The Living System merge is only *correct* when there is exactly one system. With
several, `promote_to_baseline` promotes one product's proposal into every
product's baseline — the failure mode is silent and looks like success.

## 5. Options

**A — Workspace as a directory tree (recommended first slice).**

```
data/workspaces/<workspace>/
  workspace.yaml                 # name, products, ontology pins, domain packs
  products/<product-id>/         # exactly today's store layout, unchanged
    working.json  index.json  revisions/  drafts/
```

`RevisionStore` already takes a root, so a product **is** a store and a workspace
is a manifest plus a directory. Cross-product views aggregate over stores.

- Pros: reuses the proven, tested store; node identity is safe by isolation;
  smallest change; the audit gate becomes naturally per product; domain packs
  become per product.
- Cons: no shared node identity across products (an enterprise platform such as
  OpenShift is duplicated per product); cross-product queries must load several
  stores.

**B — Product scoping inside one graph.** Add `product_id` to nodes and
assertions, and make identity `(product_id, label)`.

- Pros: cross-product queries are joins; a shared enterprise platform is one node.
- Cons: touches `_resolve`, `KnowledgeGraph`, `serialise.py` (schema-version bump)
  and every projection. This is the largest risk surface in the codebase, and
  exactly the class of silent breakage `docs/architecture-review.md` warns about.

**C — Hybrid.** Product-scoped stores plus a workspace-level *shared* graph for
enterprise platforms and the domain ontology, referenced by id. Most faithful to
the three-layer model; most work.

**Recommendation: A now.** Design the manifest so C can be added without a
migration — a product directory is already the unit C would keep.

## 6. The UX this makes possible

- `/workspaces` → workspace picker; `/w/<ws>/products` → product picker; once
  inside, a single switcher in the header.
- **Product home**: a *Baseline* card (current baseline revision, frozen date and
  actor, review progress, and whether **this** product is auditable), an
  *Initiatives* list (open proposals vs promoted, with phase), and a
  *Re-baseline* action.
- `changes` becomes per product: commit, freeze and promote, with the diff shown
  against *this product's* baseline.
- A breadcrumb that always shows `Workspace / Product / Initiative` and the
  baseline state, so "which world am I reviewing?" is never ambiguous.
- **Initiative detail**: its assertions, its review progress, and what promotion
  would add to the baseline (the existing `PromotionResult` counts, scoped).

## 7. Acceptance

- Two products coexist in one workspace, and a container named the same in both
  remains two nodes.
- Each product has its own baseline; freezing or promoting in one does not change
  another's.
- The review gate reports per product, and the UI shows each product's own
  progress.
- An Initiative belongs to exactly one product, and promotion moves only its
  facts.
- Switching product changes map, gaps, quality and changes views without a
  restart or a `.env` edit.
- A workspace lists its products with baseline state and outstanding review at a
  glance.

## 8. Decisions to make

1. **Is a Product the ontology's `Product`/`System`, or a workspace-level
   construct containing many ontology Systems?** (Recommend the latter: a Product
   is the unit of work and ownership; Systems are what the graph describes.)
2. **Where does the domain ontology live** — workspace-level (shared) or
   per-product? [YB-011](../todos/entries/YB-011-domain-ontology-layer.md) already
   puts packs under `ontology/domains/`.
3. **Are cross-product queries in scope for the first slice?** (Recommend: no.)
4. **Does a shared enterprise platform exist once per workspace or once per
   product?** Resolved in [`platform-instances.md`](platform-instances.md): it is
   a **type/instance** distinction, not a placement choice. The *type* (OpenShift)
   is workspace-global; the *instance* (a cluster) is first-class, carries the
   sharing topology (enterprise / business unit / dedicated) and serves many
   products or one. That is Option C with a rule, and it is what keeps the shared
   layer bounded. Tracked as
   [YB-044](../todos/entries/YB-044-platform-instances.md).

## 9. First slice

1. `Workspace` manifest + `data/workspaces/<ws>/products/<id>/` layout, with a
   migration/alias for `SEA_DATA_DIR` so existing stores keep working.
2. `create_app` resolves a workspace and a current product instead of one root;
   product selection is a session/route value with a URL prefix.
3. Per-product audit gate: `review_progress` already takes a graph — call it on
   the product's graph, not the process's.
4. Product switcher + Baseline card + Initiatives list in the UI.
5. `promote_to_baseline` scoped to the selected product's graph.

## 10. Scaling to the enterprise — 50 architects, 200 products

The MVP numbers are one architect, one product, ~600 KB of graph. The enterprise
target is two orders of magnitude larger, and it changes which parts of this
design are load-bearing.

### The concurrency shape today (measured, not assumed)

- **No locking.** `RevisionStore._write_atomic`
  (`core/knowledge/store.py:149`) writes a temp file and `os.replace`s it — atomic
  per file, but there is no lock, no version check, and no transaction across the
  two or three files one action writes.
- **Read-modify-write index.** `_append_index` (`store.py:338`) reads the whole
  index, appends, and rewrites it. Two concurrent commits lose one.
- **Whole-graph reload per request, whole-graph rewrite per mutation.** `state()`
  is `store.load_working()` (`app/__init__.py:184`), so the entire JSON is parsed
  on every request, and `save(snapshot)` rewrites it on every mutation. At
  ~590 KB (`data/sea-deepseek/working.json`) that is invisible; at 10× it is a
  per-click cost.
- **Concurrency already exists.** `app.run` sets `threaded=True` by default
  (Flask 3.1.3, `run.py:18`), so the current server already serves requests
  concurrently against an unlocked store. Two reviewers on one product can
  clobber each other **today** — this is not a future risk.
- **Identity is a form field.** `reviewer()` reads `request.form["actor"]`, then a
  query argument, then config (`app/__init__.py:189-192`). With 50 architects,
  "who verified this, and when" is not trustworthy, and there is no per-user
  conflict detection.

### What scale demands

| Concern | Requirement | Does this design give it? |
|---|---|---|
| Write isolation across products | one writer per product at a time | **Yes, by construction** — product = store |
| Coherence within a product | a transaction, or optimistic concurrency | **No** — needs the store decision |
| Consistent cross-product reads | one snapshot, or a read model | **No** — N stores read at N moments |
| Identity | an authenticated principal on every decision | **No** — free-text actor |
| Durability | crash-safe, no lost update | **Partial** — atomic file, no lost-update protection |

### The storage decision becomes two decisions

This is what the workspace design makes unavoidable, and why the deferred storage
choice stops being deferrable once the workspace is real. See
[`system-of-record.md`](system-of-record.md):

1. **System of record** — the store concurrent humans edit. At 50 writers that is
   a transactional multi-user database, and the natural answer is relational
   (PostgreSQL keyed by product, or schema-per-product), with SQLite/WAL as a
   credible per-product intermediate.
2. **Query and audit representation** — the form the audit reads. This stays RDF
   via `rdflib` ([YB-010](../todos/entries/YB-010-rdf-knowledge-layer.md)); a
   triple *store* such as Jena/TDB2 earns its place only when cross-product graph
   queries outgrow memory.

Keeping them separate is what avoids choosing a store for its query language and
then discovering it cannot do concurrent editing.

### How the options fare at scale

- **A (directory tree)** — the *layout* scales well: 200 products are 200
  independent write domains, which is exactly the sharding wanted, and it lets the
  system-of-record migration happen per product instead of as a big bang. What
  does not scale is the file store behind each product; A is fully compatible with
  replacing that store.
- **B (product scoping in one graph)** — puts every product in one mutable graph,
  so one lock or row version covers all 200. Worse under concurrency, not better,
  unless the store is already a real database — at which point B is a schema
  choice rather than a file choice.
- **C (hybrid)** — the shared layer becomes the contention point and needs the
  strongest store guarantees.

### Non-goals at this stage

Real-time collaborative editing, distributed workers, offline sync. At-least-once
with idempotent apply remains the honest target.

## 11. Where the baseline lives — the central layer

The model so far treats a product as the store, which puts each product's baseline
inside that product's workspace. **That is wrong**, and it is worth correcting
before anything is built on it.

### Why a baseline is not product state

1. **Cross-product reasoning is the product.** Coverage, shared platforms, "which
   systems depend on this capability" — all of it compares baselines. Trapped in 200
   stores, every such question loads 200 graphs.
2. **Authority.** A product team *proposes*; the enterprise *owns* the baseline. If
   every product workspace holds its own baseline there are 200 independent truths
   and no controlled artefact.
3. **Re-baselining is an enterprise act** — promoting a verified initiative into a
   system's baseline, under a governance gate.
4. **It is the same kind of state as the domain ontology**, which is already central
   and shared in [`living-system-architecture.md`](../living-system-architecture.md)'s
   three layers.

The data model already half-agrees: `scope` distinguishes `INITIATIVE_PROPOSAL` from
`SYSTEM_BASELINE`, and `promote_to_baseline` (`core/knowledge/review.py:616`) moves
one to the other. What is missing is the **container boundary** and a governed
promotion across it — today it flips `a.scope` in place, in the same graph, with no
system scoping at all (there is no `system_id` anywhere in `core/` or `app/`).

### Can the workspace concept be reused? Yes — but not as a second tier

**Model A — two tiers of workspace.** An `enterprise-ws` holding every system's
baseline, plus product workspaces holding proposals that reference
`(system_id, baseline_revision_id)`. Promotion becomes a cross-store, version-guarded
merge, and every product view becomes a composite read over two stores.

**Model B — the system is the container; initiatives are branches. (Recommended.)**
One enterprise workspace. A **system** owns the store; its **baseline** and its
**initiative branches** live in that store, so promotion is a local, version-guarded
merge — which is what `promote_to_baseline` already does, scoped to a system instead
of the whole store. The architect works on a *branch*; the baseline is central
because the **system store is central**, not because the architect needs a second
workspace.

Model B meets the requirement with less machinery: no composite read across two
stores, no cross-store transaction, and it matches both the Living System document
(an initiative is a feature branch of the baseline) and how every version-control
system already works. The reuse is therefore of the **store, schema and interface** —
one implementation serves both layers — while the *unit* becomes the system rather
than the product.

### The consequence that must not be missed

A central store holding **many systems** reintroduces the identity problem that
product isolation solved. Node ids are `kind:label` slugs (`make_node_id`,
`core/knowledge/model.py:62`), so two systems each with a `Database` container
collide on `datastore:database`. The central layer therefore needs both:

- a **system-scoped partition** — `(system_id, node_id)` rather than `node_id`;
- and a **shared scope** for facts belonging to no single system, the first of which
  is a platform instance serving many products
  ([YB-044](platform-instances.md)'s `serves`).

That shared scope *is* the "shared layer" of §5 — this is what it holds.

### What this changes upstream

- **Option C stops being optional.** The baseline layer *is* the shared layer; the
  choice is A versus C only for the *proposal* stores.
- **Decision 1 gets sharper.** The ontology's `System` is the baseline's owner, so a
  Product that owns no System has no baseline — which is the correct reading.
- **Decision 4 is answered** by the shared scope above.

### The merge this still needs

Model B removes the cross-store hop but **not** the guarded promotion: two architects
on one initiative each produce an ARC-G, and one is promoted under a version guard
and an approval. That is a branch dimension in the store plus a three-way merge and a
promotion request — worked through in
[`branching-and-promotion.md`](branching-and-promotion.md)
([YB-046](../todos/entries/YB-046-branching-and-promotion.md)).

## 12. Related

- [YB-018](../todos/entries/YB-018-review-batches.md) — the gate scoped to a run;
  this scopes it to a product.
- [YB-034](../todos/entries/YB-034-initiative-delivery-phase.md) — Initiative
  status; this gives initiatives a home.
- [YB-011](../todos/entries/YB-011-domain-ontology-layer.md) — packs per product
  or per workspace.
- [YB-009](../todos/entries/YB-009-architecture-gaps.md) — gap reporting per
  product.
- [YB-037](../todos/entries/YB-037-background-workflow-management.md) — a job
  belongs to a product.
- [YB-043 — System of record](system-of-record.md) — the store the workspace
  writes through.
- [YB-044 — Platform instances](platform-instances.md) — what the shared layer
  holds.
- [`docs/living-system-architecture.md`](../living-system-architecture.md) — the
  three-layer model this implements.
- [`docs/user-journey.md`](../user-journey.md) — the stages this unblocks.
