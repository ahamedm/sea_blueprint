# Branching, merge and guarded promotion

> **Design document** for [YB-046](../todos/entries/YB-046-branching-and-promotion.md).
> Continues [`workspace-structure.md`](workspace-structure.md) §11 and
> [`graph-store-schema.md`](graph-store-schema.md).

---

## 1. The requirement

Two architects work the same Initiative. Each produces an updated ARC-G. One is
promoted into the baseline — under a guard, possibly a process or an offline
approval. So **a guarded promotion exists regardless of Model A or B**; the question
is where the merge happens and what the guard is made of.

## 2. Model B removes the cross-store hop, not the merge

Worth stating plainly: Model B co-locates the baseline and its branches in one
system store, so promotion is intra-store — but the **version guard and the merge
remain**, and they are where the difficulty lives. Topology was the easy part. The
rest of this document is about the hard part.

## 3. The branch dimension

The store needs a branch axis, not just a system axis:

```
branch(
  system_id, branch_id,
  kind,        -- baseline | initiative | personal
  base_branch, base_revision,   -- the fork point
  owner, created_at, state
)
working_set(system_id, branch_id, version)     -- the CAS token is per branch
revision(system_id, branch_id, revision_id, parent_id, graph_ref, ...)
```

- The **baseline** is the distinguished branch.
- An **initiative** is a branch forked from the baseline.
- Two architects on one initiative have a real choice, and both are legitimate:
  - **share the branch** — co-editing one working set, protected by the version
    CAS; no merge, just conflict-on-write;
  - **fork personal branches** — independent ARC-G variants, which is the case you
    describe and the one that needs merge semantics.

## 4. Merge is a three-way diff, and content-addressing does most of it

`base` is the fork-point revision; `ours` and `theirs` are the two branch revisions.
`compute_graph_delta` (`core/knowledge/model.py`) already computes each side's change
set, and two properties of the identity scheme make most of the merge automatic:

- **Assertion ids are content-addressed** (`a_<sha1(subject,predicate,object,value)[:16]>`).
  The same fact added by both architects is the *same assertion id* — it converges
  with no conflict at all. Two people doing the same correct thing is not a
  disagreement.
- **Nodes are `kind:label` slugs**, so a node is never "modified": it is removed and
  added. Node merge is therefore set arithmetic.

What is left is genuinely semantic, and is exactly what a human should adjudicate:

| Conflict class | Example | Resolution |
|---|---|---|
| Identical addition | both add `PGP --uses_technology--> Kafka` | **automatic** — same id |
| Independent additions | different facts | **automatic** — union |
| Divergent value | one proposes latency ≤ 500 ms, the other ≤ 800 ms | **human** |
| Changed vs retired | one edits a fact, the other removes it | **human** |
| Contested confidence | same fact, very different confidence and source text | fold by the existing rule, flag if close |

### The sharp edge — identity *is* the label

Today `node_id = slug(kind):slug(label)` (`make_node_id`, `core/knowledge/model.py:62`)
and `assertion_id = a_<sha1(subject, predicate, object, value)>` (`:72`) where
subject and object **are node ids**. So the label is not an attribute of identity —
transitively, it *is* identity for every assertion touching the node. `make_node_id`'s
own docstring leans on this: *"the same kind and label in a different run produce the
same id, so re-extraction converges rather than duplicating."*

A worked example, on real behaviour:

```
Run 1   "Payment Orchestrator"                 → container:payment_orchestrator
        responsibility = "Routes payments"     → a_ab12…   human VERIFIES it

Run 2   the model says "Payment Orchestration Service"   (label drift, no human act)
        → container:payment_orchestration_service        a NEW node
        → the same responsibility becomes a NEW assertion id

Result: two nodes, two assertions, and the verified one hangs off a node
        nothing references any more.
```

An explicit rename triggers the same cascade: every assertion on the node gets a new
id, and with it `status`, `confidence`, `source_text`, the `Decision` rows naming
`assertion_id`, and any `superseded_by` chain.

#### Option A — surrogate node ids

`node_id` is **assigned once** (deterministically, from `(kind, label)` at creation)
and **never recomputed**. `(slug(kind), slug(label))` becomes a mutable unique *key*.
A rename is one `UPDATE node SET label = …`: the id — and therefore every assertion
id — is unchanged, so verified status, decisions and supersession chains survive.

What it does **not** do by itself: make label *drift* converge. A genuinely new label
still needs a resolution rule. But that is then a **policy** decision, explicit and
reviewable, instead of an accident of the id function.

#### Option B — a rename map

Keep derived ids, record `old_node_id → new_node_id`, and apply it wherever ids are
stored: `node`; `assertion.subject / object / id / superseded_by`;
`decision.assertion_id / replacement_id / promoted_ids`; `declared_by`;
`external_reference.node_id`; and the materialised RDF.

#### Why A is the recommendation

**Revisions are immutable.** Option B cannot migrate them, so its rename map becomes
**permanent read-time indirection over all history** — every historical revision must
be interpreted through the map forever, and any code path that forgets to consult it
silently reports the old identity. Option A keeps ids stable, so historical revisions
stay self-consistent with no indirection at all.

Secondary: B's fan-out is roughly eight places, and a repair procedure that must be
applied everywhere will eventually be missed in one — orphaning exactly the reviewed
state it exists to protect.

#### What A costs

One mechanism changes: `add_node` can no longer rely on id collision for cross-run
convergence. It needs a `(kind, label)` index over the existing graph and must look
up *before* creating. The seed stays deterministic, so a fresh build of the same
document produces the same ids — reproducibility is preserved, and only
*reassignment* is forbidden. Plus a `SCHEMA_VERSION` bump.

## 5. Promotion is a request, not a button

`promote_to_baseline` today is synchronous and flips `scope` in place
(`core/knowledge/review.py:616`). With several architects it becomes a **pull
request against the baseline**:

```
promotion_request(
  system_id, request_id,
  source_branch, source_revision,
  target_branch, expected_version,      -- the version guard
  delta_summary JSONB,                  -- added / changed / removed / conflicts
  requested_by, requested_at,
  state,                                -- PENDING | APPROVED | REJECTED | SUPERSEDED
  resolved_by, resolved_at, note
)
```

Two gates, and they are different things:

- **The automated gate already exists.** `BaselineNotReady` refuses to freeze while
  assertions are unverified or disputed — "a baseline that absorbs unchecked
  extraction is not a baseline".
- **The human gate is the approval**, and it can be **asynchronous/offline**: the
  request is created, reviewers read it without write access, and a different actor
  approves later. That is a workflow state, not a UI detail — which is exactly what
  [YB-018](../todos/entries/YB-018-review-batches.md) (a batch as the unit of the
  gate) and [YB-037](../todos/entries/YB-037-background-workflow-management.md)
  (`AWAITING_REVIEW`) already anticipate.

This also re-frames the earlier "offline approval" instinct: **the promotion request
is the artefact the board reviews**, and it carries its own conflicts, counts and
provenance.

## 6. What already exists to build on

| Need | Already there |
|---|---|
| Diff two graphs | `compute_graph_delta` |
| Merge primitive | `add_assertion` folding (human outranks agent; confidence maxes; fuller source text wins) |
| Audit trail | `Decision` / `ReviewLog` |
| Automated guard | `BaselineNotReady` |
| Fork chain | `Revision.parent_id` (needs to become per-branch) |
| Async approval state | YB-018, YB-037 |

## 7. Acceptance

- Two architects on one initiative, each on a branch: promotion surfaces the
  conflicts rather than silently overwriting.
- A fact both branches added converges without being reported as a conflict.
- A promotion fails cleanly when the target baseline has moved (version guard).
- An approval can be recorded later by a different actor, with the request, its
  conflicts and the counts preserved.
- The baseline never absorbs unverified or disputed assertions.
- **A relabel does not silently orphan the review state of every assertion on that
  node.**

## 8. Related

- [YB-042 — Workspace structure](workspace-structure.md) §11.
- [YB-043 — System of record](system-of-record.md) / [graph-store-schema](graph-store-schema.md).
- [YB-018 — Review batches](../todos/entries/YB-018-review-batches.md) — the gate
  becomes per request.
- [YB-037 — Background workflow](../todos/entries/YB-037-background-workflow-management.md)
  — `AWAITING_REVIEW` as a durable state.
