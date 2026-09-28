# Extraction and reconciliation reliability — a brainstorm of levers

> **Brainstorm, not a plan.** Nothing here is committed, scheduled or estimated; it is a
> recorded list so the ideas are not lost and so whoever picks one up can see the evidence
> that motivated it. There is deliberately **no TODO entry yet** — this document is the
> holding pen, and entries get minted when something is actually chosen.
>
> Gathered 2026-09-27, while executing the MVP test case (REQ-G + ARC-G against
> `test_data/`, scope `acme_pillar_01` in `data/sea_home_01`, model `deepseek-v4-pro`).
> The measurements in §1 come from that session and from the earlier local-model runs; the
> items in §2 and §3 are hypotheses unless marked otherwise.
>
> Related: [ADR-0029](../decisions/ADR-0029-c4-specification-view.md) (the C4 view),
> [YB-051](../todos/entries/YB-051-connections-dropped-at-ingest.md),
> [YB-052](../todos/entries/YB-052-reflexive-and-duplicate-extraction.md),
> [YB-053](../todos/entries/YB-053-category-elements-and-duplicate-system.md).
>
> **Status 2026-09-28.** The first tranche was taken and recorded in
> [ADR-0030](../decisions/ADR-0030-boundary-refusals-and-output-accounting.md):
> §3.1 output-consumption accounting, §3.3 a paid-for run kept on a post-extraction
> error, §3.4 refusal at the write boundary (closing YB-052 defect 1), §3.13 the
> harness with gates, budgets and a committed baseline, and YB-053's fixture stating
> its own request path. The sections below are marked where they landed; the rest of
> the list is still uncommitted, and the measurement that made the tranche falsifiable
> now exists.

---

## 1. Why now — what actually failed

Nine defects surfaced in one session, and their *distribution* is the argument for the
ordering in §4. Only two were model-quality problems. Five were the system losing or
mangling work the model had already done correctly.

| # | Failure | Evidence | Class |
|---|---|---|---|
| 1 | A flawless run discarded after extraction | `'dict' object has no attribute 'subject'` in `repair.py`, after **12/12 passes ok, 182 triples, 27 elements, 19 connections in 146 s**. `merge_triples` returns dicts; `repair.py` read `.subject` | system |
| 2 | Connections extracted and thrown away | `graph_from_extraction` never read the `connections` key — every run, for the life of the pass | system |
| 3 | Agent findings computed and discarded | `agents/architecture_extraction/agent.py` sets `output["findings"]`; ingest reads it nowhere | system |
| 4 | Two runs collided on one id | `new_run_id` hashed a **second-resolution** clock: 2000 calls → **1** unique id. `graph.runs` is keyed by it, so the second run's record replaced the first | system |
| 5 | Configured endpoint could not run at all | The hosted branch of `get_default_agent_config` read `local_model_id` for any base URL → *"The supported API model names are deepseek-flash, deepseek-v4-pro, but you passed unsloth/Qwen3.5-4B-GGUF:Q4_K_M"* | system |
| 6 | Structured extraction rejected outright | DeepSeek returns 400 *"Thinking mode does not support this tool_choice"* while thinking is on, and every pass sends `tool_choice: "required"` | model/config boundary |
| 7 | Whole pass produces nothing | Local 4B: `connections` failed or empty on every attempt (~255 s each). Structured call returned a valid empty object in 1.9 s on one chunk | model |
| 8 | Same document, different graph | 27 vs 30 elements, 19 vs 13 connections; the **payment-flow edges present in run 1 and absent in run 2** | model (variance) |
| 9 | Plausible-but-wrong facts, human-verified | 3 then 4 × `X part_of X`, all `VERIFIED` via bulk review; `Database` and `External Services` emitted as elements | model + review gate |

Two further measurements bear on the ordering:

- **The validators are not exempt.** The five C4 well-formedness checks found three real
  defects *and* three false positives of their own (`run-completeness` firing on a
  complete run, `unplaced` firing for elements that are legitimately top-level, a missing
  connection counted once per representation). A validator is a measurement, and an
  unmeasured measurement is how a report overstates the damage.
- **What is already good.** Chunking is structure-aware with 500-char overlap
  (`agents/extraction/chunking.py`), reconciliation has an explicit threshold, review has a
  real verdict model, artifacts are content-addressed, and there is a `[DRAFT]` invariant
  harness at `scripts/run_extraction_tests.py`. Several levers below are "finish the thing
  that exists" rather than "build something new".

---

## 2. The levers already on the table, calibrated

The starting list, with what the evidence says about each.

| Lever | Assessment |
|---|---|
| **1. Prompt fine-tuning** | Real but bounded. Rule 5–6 added to the connections prompt removed the category-word endpoints. It **cannot** supply a fact the document never states (the request path), and inviting inference *increases* variance — the payment-flow edges appeared in one run and not the next. Cheapest lever, weakest guarantee. |
| **2. Skills for agents to generate architecture** | Downstream of extraction, and generation is where hallucination is unbounded. Highest value as a **critic over a merged graph**, not as another generator. |
| **3. Skills to detect individuals, for ontology compliance** | This is two things: entity resolution (deciding a mention is a new individual, an alias, or noise) and compliance. The duplicate `Payment Gateway Platform` — `Platform` from REQ-G, `SoftwareSystem` from ARC-G — is the entity-resolution half. |
| **4. Ontology validation** | Validate it **by use**: coverage in both directions against a real corpus (classes with zero instances; nodes that classify as nothing), plus lint for ranges no extraction can fill. Reading it proves nothing. |
| **5. Deterministic validators / MCPs over graph sections** | Strongest of the six, and the only one with a fully worked example in-repo (`app/viewpoints/c4.py` + `scripts/c4_scorecard.py`). Must be *measured* as well as run — see the false positives in §1. Extend from "report" to "refuse". |
| **6. ML models to aid extraction and design** | Too vague to act on. The specific, defensible wins are narrower: span-anchored extraction (§3-B), embedding retrieval for entity resolution, a reranker for connection candidates, and a small classifier trained on human verdicts (§3-D). |

---

## 3. Additions

Marked **[M]** where the motivation is a measurement from §1, **[H]** where it is a
hypothesis to test.

### A. Make silent loss impossible

1. **[M] Output-consumption accounting.** Every `PassSpec` declares `output_keys`; assert
   that ingest consumes each one, and report "pass emitted N, stored M" per run. Would have
   caught #2 and #3 on the first run. *Cost: small, bounded, one place.*
   **Landed** (ADR-0030 §2): `INGESTED_OUTPUT_KEYS` + a seam test for the static half, and
   `run.output_counts` / `unconsumed_keys` / `stored_facts` for the runtime half. It caught
   [YB-054](../todos/entries/YB-054-unrouted-requirements-output-keys.md) on its first real
   output.
2. **[M] Seam tests, one per producer→consumer pair.** #1 was a representation mismatch
   across a seam (`merge_triples` → `repair.py`) that no unit test could see because each
   side was correct in isolation. A test that feeds a producer's **real** output into the
   real consumer is the whole fix. *Cost: small per seam.*
3. **[M] Never lose a paid-for run to a post-extraction error.** If the merge throws, store
   what merged and mark the run PARTIAL with the error, rather than failing the job with
   nothing. Extraction is the expensive part and it had already succeeded. *Cost: small.*
   **Landed** (ADR-0030 §3): merge, repair and validation each under a guard; a failure
   keeps the facts, adds a `post_extraction_error` finding, and appends a failed
   `(post-extraction)` pass record so the run's own verdict is PARTIAL.
4. **[M] Invariants that can refuse, not only flag.** `X part_of X` cannot be true of
   anything, yet three became `VERIFIED` facts. Refusal at the boundary, with the reason
   recorded, is the general form of the reflexive guard. *Cost: small; see YB-052.*
   **Landed** (ADR-0030 §1): refused in `KnowledgeGraph.add_assertion`, reported on the run,
   and refused by `verify` singly and in bulk with the reason shown to the reviewer.
   YB-052 defect 1 is closed; defect 2 moved to YB-053.

### B. Shape the output rather than asking for it

5. **[M] Constrained decoding — GBNF grammars / guided decoding — not just JSON schema.**
   Both #6 and #7 are decoder-level: the local model spent ~255 s producing nothing, and
   DeepSeek refused the call outright. Schema-shaped *hope* becomes "it cannot emit anything
   else". llama.cpp and vLLM both support this. *Cost: per-provider plumbing.*
6. **[M] Span-anchored field values.** Require every element/connection name to be a
   substring of the source chunk (optionally with offsets), validated deterministically.
   Kills the invented `External Services` / `Database` / `Critical Components` class by
   construction instead of by prompt, and fills the `source_text` that is often empty —
   which is also what review needs to judge a fact. *Cost: medium; schema + validator.*
7. **[M] Derive the fields you already have.** `is_synchronous` is unset on **every** one of
   the 11 connections although `style` determines it (`SYNCHRONOUS_*` / `ASYNCHRONOUS_*`).
   Deterministic, no model call, and the ontology needs it for temporal coupling. Same
   family: `style` was empty on connections where the protocol implied it. *Cost: trivial.*
8. **[M] Endpoint pre-flight capability gate.** Before a run: does the endpoint accept
   `tool_choice: "required"`? does thinking-off work? what is the real `max_tokens` and
   context? Both #5 and #6 burnt a run and both *looked like* "the model produced nothing".
   *Cost: small; belongs with the run record so the run page can state it.*

### C. Reliability by redundancy — attacks variance directly

9. **[M] Self-consistency, k-of-n.** #8 is the strongest argument: two runs of one document
   disagreed on the part an architect cares about most. Keep facts that agree across k
   samples (or two temperatures), route disagreement to review. Single-shot extraction has
   no error bar; consensus manufactures one. *Cost: k× tokens — apply to `structure` and
   `connections` only.*
10. **[H] A verifier pass over the merged graph, not the text** — "find the elements and
    connections that are not in this document". Different prompt, different failure mode,
    and it is where lever 2/3 pays off as an adversary rather than a second generator.
11. **[H] Route review by disagreement.** The queue is ordered by document order; order it
    by where independent methods disagree — run-to-run diff, critic vs extractor,
    confidence band. The platform already has revisions and `/changes/diff` to reuse, so
    the sampling machinery exists and is unused for this.

### D. Leverage what is already owned

12. **[M] Close the feedback loop on human verdicts.** The graph holds
    `VERIFIED`/`CORRECTED`/`DISPUTED` with `correction_note`, and **nothing reads them back
    into extraction** — they are used only to promote facts to baseline
    (`REVIEWED_STATUSES`). Four uses, roughly in order of value: a per-domain-pack few-shot
    exemplar set built from *corrections*; a labelled evaluation set; a small
    `element_type` classifier (far more reliable than prompting a 4B model, and it is
    exactly the category-word defect); a corrections recall injected per document type.
    *This is the only genuinely proprietary asset here — competitors can copy prompts, not
    a reviewed corpus.*
13. **[M] Finish `scripts/run_extraction_tests.py` and give it tolerances.** It exists, is
    marked `[DRAFT]`, and its own docstring states the problem: *"every prompt change this
    session silently broke a previously-working invariant… makes it a command instead of a
    discipline."* Pair it with `scripts/c4_scorecard.py`: invariants as hard gates, quality
    counts as toleranced budgets. **Without this, every other lever here is unfalsifiable.**
    **Landed** (ADR-0030 §4): `[DRAFT]` removed; every `inv_*` classified as gate or budget
    in one self-checked table; budgets toleranced against a committed
    `scripts/extraction_test_baseline.json`; the scorecard folded in as gates (the four
    defect rules) and budgets (counts, plus the two share rules that legitimately fail on
    this test case). Exit code separates a broken run from a moved number.
14. **[M] Calibrate reconciliation instead of choosing it.** `DEFAULT_MATCH_THRESHOLD =
    0.75` is hand-set and documented as "deliberately conservative". With verdicts plus a
    labelled sample, pick the point that maximises precision at an acceptable recall and
    publish the curve.

### E. Attributability and iteration speed

15. **[M] Stamp prompt and ontology versions on provenance.** `Provenance` carries
    `model_id`, `pass_name`, `chunk_label`, `domain_pack` — **no prompt hash and no ontology
    version**. So "did quality drop because of the prompt or because of the model?" is
    currently unanswerable from the graph. [YB-045](../todos/entries/YB-045-ontology-version-provenance.md)
    covers the ontology half; the prompt half is its twin.
16. **[H] Cache by (document hash, pass, prompt hash, model).** There is already a
    content-addressed artifact store and deterministic chunking. Re-running one pass after
    a prompt tweak should not re-pay for the other three — which is what makes levers 9, 10
    and 13 affordable enough to run routinely.

### F. Outside the model

17. **[M] A completeness contract per document type.** "An architecture document must state
    its context, its containers, and the request path between them." Checked at ingest,
    failing into a **targeted question** ("which container calls the routing engine?")
    rather than a generic PARTIAL. Generalises the finding that only one container's
    relationships were stated in the test document, and that the container whose
    relationships *were* listed became the hub everything appeared to hang off.
18. **[H] Validate our own emitted notation with an external parser.** We emit Structurizr
    DSL and Mermaid, and both have real parsers. Feeding our own output to one is a cheap
    independent oracle on our model; today the failure path is a human pasting it and
    getting a parse error with no cause attached.

### G. DSPy-class optimizers — evaluated, and the verdict

Asked directly: can DSPy auto-tune agent output, or is it prompt fine-tuning by another
name? **It is a different category — and it is blocked on exactly the prerequisite in
§3.13, which makes it a payoff of the harness rather than a substitute for it.**

Read against DSPy 3.4.0's own documentation, it does three things hand-tuning cannot:

1. **Searches against a metric instead of against intuition.** Every optimizer is
   `compile(program, trainset=..., valset=..., metric=...)`, so a change comes with a
   before/after number. That is the falsifiability §3.13 exists to provide, which is why
   §5 currently says not to tune prompts without it.
2. **Uses a stronger model to write instructions for a weaker one — reflectively.** GEPA
   takes a separate `reflection_lm`, lets the metric return *natural-language* feedback
   ("don't reference the input season verbatim"), and rewrites instructions from examples
   that failed. This is the mechanism that could actually help the local 4B: a frontier
   model reading our validators' complaint strings and rewriting the rule, rather than a
   human guessing. The harness already emits exactly that shape of text — every `gap` and
   failed `check` carries a `detail` sentence explaining the consequence.
3. **Tunes weights as well as text.** `BootstrapFinetune` and `BetterTogether` are in the
   shipped optimizer list, so "DSPy = prompt tuning" is wrong on its face. Weight tuning
   is a different cost class (thousands of examples, a training loop) and a different
   project from anything else in this document.

**What it would and would not fix here.** It optimises the *program's* instructions and
demonstrations. It cannot touch five of the nine defects in §1 — a perfect run discarded
by a dict/object mismatch, connections never read by ingest, findings dropped, two runs
colliding on an id, an endpoint that could not run. Optimising a discarded run makes the
discarded run better. It also cannot fix the decoder-level failures (#6, #7): compiling
against an endpoint that 400s on `tool_choice: "required"` optimises noise.

**The repo-specific objection is YB-007.** GEPA's own worked example shows the optimised
prompt growing from one sentence into a long multi-section list of requirements — and
[YB-007](../todos/entries/YB-007-prompt-scaffolding-instruction-dilution.md) already
measures this project's scaffolding at ~2.5:1 against the document, with instruction
dilution as the named failure. Bootstrapped demonstrations and reflective instruction
growth push the same direction. If the local model is the target, an optimizer whose
metric does not penalise prompt length can make things worse; retrieval-style demo
selection (`KNNFewShot`) rather than appended demos is the shape that would avoid it.

**The blocker is data, not the framework.** Every optimizer needs `trainset`/`valset`, and
§1 measured 27-vs-30-element and 19-vs-13-connection
variance on *identical* input. Optimising against a metric whose noise floor exceeds the
effect being chased fits the noise — and the named test corpus is two documents plus four
small fixtures. So a compile needs a dev set of tens of items, not four. Two sources
exist: section-sliced chunks of the tracked `test_data/` documents (chunking is already
deterministic, so a slice is a stable example), and the human verdicts already sitting in
the graph as positive and negative labels (§3.12).

**The pragmatic integration: use it as a build-time tool, not a runtime.** DSPy has its
own LM abstraction, and this project's runtime is Strands plus `PassSpec`/`run_passes`.
There is no need to replace either. Compile offline, export the tuned instructions and
demos as an artifact, and paste them into `PassSpec.instructions` — then hash that
artifact into provenance (§3.15), which is the same discipline the rest of this document
argues for. The runtime stays as it is, and the optimisation becomes reviewable.

**Smallest useful experiment, if it is ever tried.** One pass (`connections`, where the
failure is measurable and the metric is almost entirely programmatic), a dev set built
from section-sliced chunks, `payment_platform_arch.md` held out *entirely* as the test
set, `auto="light"` (about six candidates), and `c4_scorecard.py` before/after on the
held-out document. If the arrow count and the dangling-endpoint count do not move, the
lever is spent. Note that this experiment presupposes §3.13 for the "no improvement is
distinguishable from noise" reason, and presupposes the test document stating its request
path ([YB-053](../todos/entries/YB-053-category-elements-and-duplicate-system.md)) —
otherwise the payment-flow edges are absent from the dev set too and nothing can learn
them.

For the record, DSPy's own reported case studies are the kind of result that motivates
this: Shopify at ~75× cheaper and ~2× more reliable on a small Qwen model after GEPA, and
a nano-model optimised from 78.1% to 90.1% against a frontier baseline of 82.4%. Those are
the project's published figures, not independently reproduced here, and both were on tasks
with a clean metric and a real labelled set — which is the point.

---

## 4. If five were chosen, in this order

1. **Output-consumption accounting** (§3.1) — proven silent loss, small cost, no model
   calls. **Done** (ADR-0030 §2).
2. **The invariant harness with tolerances** (§3.13) — makes every other lever measurable;
   without it, prompt tuning cannot be told apart from variance. **Done** (ADR-0030 §4).
3. **The verdict feedback loop** (§3.12) — the only asset here that cannot be copied, and
   it is currently inert.
4. **Span anchoring plus constrained decoding** (§3.6, §3.5) — moves correctness from
   prompt discipline to construction.
5. **Self-consistency on `structure` and `connections`** (§3.9) — the only lever that
   confronts run-to-run variance head-on rather than on average.

The first two are cheap and would have prevented or exposed five of the nine failures in
§1 on the day they happened. **Both are now in place** (ADR-0030); item 4's
"span anchoring plus constrained decoding" is the next one whose cost is not trivial, and
item 3 remains the one with the longest half-life.

## 5. Deliberately not yet

- **More prompt tuning on the strength of the new harness alone.** The blocker is no
  longer "no harness" — §3.13 exists — but the noise floor has not moved: the baseline is
  one saved run per case, the corpus is two documents, and §1 measured 27-vs-30 elements
  on identical input. A prompt change can now be *measured*, which is real progress, but
  one before/after pair is still a sample of size one. Accumulate runs and review the
  baseline diff before treating an arrow as a result.
- **DSPy-class prompt compilation, for the same reason and one more** (§3.G). It needs a
  metric and a dev set of tens of examples; the corpus is two documents and four fixtures.
  Compiling now would fit the noise floor rather than the effect — and if the target is the
  local model, an optimised prompt that grows makes YB-007's instruction dilution worse.
  The framework is not the obstacle.
- **An ML extractor before §3.6.** Span-anchored fields plus a constrained decoder gets
  most of the reliability for a fraction of the cost, and `element_type` labels are not
  clean enough to train on until the category-word class of error is stopped at the
  boundary.
- **Replacing the graph's relationship model to fix the hub.** The hub was a document gap
  plus data-access edges drawn as calls; the second half is already fixed in the view, and
  the first is a document/eval problem, not a schema problem.

## 6. Open questions

1. **Where does refusal live?** Ingest is the one choke point every fact crosses, but a
   refusal there is invisible to the agent that could have avoided it. Refuse in both, or
   refuse at ingest and report upstream?
2. **Does verdict-driven few-shot risk entrenching the reviewer?** A corrections-derived
   exemplar set teaches the model one architect's judgement. Worth it where the judgement
   is mechanical (naming, classification), not where it is architectural.
3. **Is k-of-n consensus affordable at document scale?** k=3 on `structure` and
   `connections` triples the calls for those passes. Needs a cost measurement against
   `usage` in the run record before it is proposed seriously.
4. **Should the extraction itself be allowed to ask for the missing flow?** §3.17 implies a
   question back to the author. That is a product decision (interactive ingest) rather than
   an extraction one.
5. **What is the right unit of a "golden" fixture?** A whole architecture document, or a
   section with hand-labelled elements and connections? The second is cheaper to maintain
   and pins precision; the first is the only thing that exercises chunking and cross-chunk
   repair.

## 7. Where the evidence lives

```
scripts/c4_scorecard.py --scope acme_pillar_01      # the C4 readiness measurements in §1
.venv/bin/python scripts/run_extraction_tests.py --validate-only   # gates + budgets, offline
uv run scripts/run_extraction_tests.py              # the same, re-running extraction (costs money)
data/sea_home_01/pillar01/jobs.sqlite               # job states, errors, timings
data/sea_home_01/pillar01.sqlite                    # the graph and its run records
```

`run_extraction_tests.py --validate-only` is the free, deterministic half: it re-checks
saved output JSON and never calls a model. `--update-baseline` implies it.

Per-pass outcomes, elapsed time and `triples_produced` are on the `ExtractionRun` record,
so the numbers in §1 can be re-derived from the store rather than taken on trust.
