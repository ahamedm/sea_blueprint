# Review batches — scope the review gate to a run, without fragmenting the graph

> **Design document** for `YB-018`. Status is tracked in that entry.
> Preserved verbatim from `TODO.md` v1 (YB-018).

---

### The question that exposed it

> "How is the 'run' selected for Review and further workflow? Or is every run's output
> fused together for Review? Ideally it should be independent and not fused, right?"

**Fused, deliberately** — but the second half of the intuition is right, and the gap it
points at is real. The graph must stay fused; the **review gate** should not be.

### What happens today

```
/ingest ─▶ graph_from_extraction ─▶ merge_graphs(before.graph, incoming)
        ─▶ save_working ─▶ redirect to /review
```

`core/knowledge/ingest.py:413` `merge_graphs` folds every incoming assertion through
`KnowledgeGraph.add_assertion`. `/review` then projects `state().graph` **in full**.
`ReviewFilters` (`app/projections.py`) carries `status`, `scope`, `kind`, `predicate`,
`q`, `only`, confidence bands and sort — **and no run dimension**. `run_id` is projected
onto every assertion but rendered in exactly one place, behind a click:
`app/templates/partials/assertion_detail.html:16`.

So there is no way to ask the queue *"what did run B produce?"*

### Why fused is correct and must stay correct

Replacing the graph per run would destroy every human decision on the next extraction —
the "sleeper" problem (`docs/architecture-review.md` §3.3). `docs/ui-review-workflow.md`
§5 states the rule: **re-extraction is merge + diff, never replace.** And the reason is
domain-driven, not expedient: if document A and document B both state that
`Payment precedes Authorization`, that is **one fact observed twice**, not two facts.
Convergence is the point of the knowledge model.

Verified behaviour of the fold rule, worth knowing before touching it:

| Situation | Result |
|---|---|
| Re-observation, lower confidence | older, higher confidence kept; only a **longer** `source_text` is absorbed |
| Re-observation, **equal** confidence | first observation's provenance kept; second treated as support |
| Re-observation, strictly higher confidence | confidence updated, but **provenance is not adopted** (only a human assertion replaces it) |
| Human assertion vs later agent run | human wins, by design |

**Consequence:** `Provenance` is a *pointer*, not a *log*. "Which runs observed this
fact?" is **not answerable** from a single assertion, and `model_id` can describe a run
that did not set the confidence. That is consistent with the design, but it is a hidden
limitation, and it is the reason a naive `run_id` filter would silently under-report.

### The three concrete costs of a single global queue

1. **No batch boundary.** After a prompt tweak, new facts are interleaved with everything
   already reviewed. The reviewer cannot see "what this run added".
2. **The audit gate is global.** `review_progress` (`core/knowledge/review.py:220`) walks
   every assertion in the graph, so **one weak run blocks the audit for the whole
   Initiative** — and `RevisionStore.freeze` refuses on exactly that count.
3. **Re-extraction is indistinguishable from a new document.** This sits badly with the
   v1 flow in `docs/user-journey.md` §6 ("corrections are made *in the source document*
   and re-extraction picks them up"), whose exit condition is the first time knowledge
   arrives from two sources.

### The key finding: the boundaries already exist

Ingest **already commits a revision per run** — `app/__init__.py:313`, labelled
`Ingest · <filename>`, via `RevisionStore.commit`. `store._stamp` (`store.py:354`) sets
`version_id` / `parent_version_id` on the snapshot, and `diff_against_revision`
(`store.py:325`) already computes the `GraphDelta` between consecutive revisions.
`/changes/diff` renders it.

**The path from the parent revision to the current one *is* exactly what one run added,
changed and removed.** No new artifact is needed — the batch boundary is already being
computed and thrown away.

### Sketch: batch the review, not the graph

| # | Change | Where |
|---|---|---|
| 1 | `ReviewFilters` gains `baseline`; `_matches_filters` filters assertions to the delta against the parent revision | `app/projections.py` |
| 2 | `/review` defaults to **"new since last revision"** after an ingest, with an explicit toggle to the whole-graph queue | `app/__init__.py` |
| 3 | Show the batch (run id, document, completeness) as a banner on the queue, not only in one assertion's detail | `app/templates/review.html` |
| 4 | Record the **revision a decision was made against** in the `Decision` record — `review.py` already threads `run_id` at lines 275 and 345, so this is adjacent | `core/knowledge/review.py` |
| 5 | Scope `review_progress` for the gate so a batch is judged on its own completeness | `core/knowledge/review.py` |

The payoff is a **defensible** review: "was this verified against the narrow batch or the
whole accumulated graph?" becomes answerable, which is the property an auditor actually
wants and which the current single queue cannot express.

### Explicitly NOT this

**Do not give each run its own graph.** That recreates the overlay/conflict-resolution
problem `docs/user-journey.md` §6 deliberately defers (`YB-009` §9b), and it breaks
reconciliation, which depends on ARC-G and REQ-G each being *one* graph. The
destructive-change and correction-merge answers already exist (`YB-009` §9a, §9b); this item
is about **review scoping only**.

### Relationship to other items

- **Depends on nothing.** ADR-0002–4 of the sketch use machinery that already ships.
- **Unblocks a sharper audit** (YB-009, gap 5): a gap report over a scoped batch is a
  defensible statement about *that* run.
- **Complements YB-005** (Initiative-scoped reconciliation): reconciliation is
  Initiative-scoped; review becomes run-scoped. Both are narrower than "the graph".
- **Complements YB-011:** a domain pack is recorded per assertion, so once a batch is
  reviewable, "did this run's vocabulary change what it found?" becomes measurable.

### The question to settle first

Is a **run** the right batch unit, or is it the **document**? They coincide today (one
ingest = one document = one run), and diverge the moment a document is chunked across
multiple runs or a run spans several documents. Worth deciding before building, because
it determines whether the batch is identified by `run_id` or by
`(document_ref, revision)`.
