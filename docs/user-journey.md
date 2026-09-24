# SEA Platform — User Journey

**Status:** Draft for review · 21 September 2026
**Purpose:** shape the workflow, make it firm, and surface the gaps the journey
exposes — which are not the gaps the build order has been addressing.

---

## 1. Who the users are

*Assumptions — correct these before they harden into the design.*

| Persona | Owns | Cares about |
|---|---|---|
| **Business Analyst / Product Owner** | The business case and the requirement set | Requirements captured faithfully; knowing the architecture answers them |
| **Solution / Enterprise Architect** | The architecture and its defensibility | Producing a design that can be proven to answer what was asked |
| **Reviewer / Auditor** | Independent verification | What is missing, what conflicts, what is unverified |
| **Engineer** | Component-level detail | What they are accountable for, and the constraints on it |
| **Governance / Compliance** | Regulatory obligations | That PCI-DSS-class obligations are traceable to design decisions |

Secondary: delivery leads consuming the gap report as work.

---

## 2. The core job

Not "extract knowledge from documents." That is a means. The job is:

> **Prove that an architecture answers its requirements — and show exactly where
> it does not.**

Everything built so far serves that sentence. Extraction, the ontology, the
canonical model and the validators are all *enablers*; the job is the proof.

That reframing matters for the journey: a journey that ends at "knowledge
extracted" delivers nothing. It has to end at **a defensible answer**.

---

## 3. The journey

Concrete scenario: the ACME payment gateway, whose requirement and architecture
documents already exist in `test_data/`.

| # | Stage | Who | What happens | Artefact | Status |
|---|---|---|---|---|---|
| 0 | **Establish the domain ontology** | Architect | Base ontologies extended for the domain | Domain ontology | ⚠️ Base only |
| 1 | **State the business case** | BA | Business case captured as an Initiative | Initiative | ⚠️ Modelled, not extracted |
| 2 | **Load requirements** | BA | Document ingested; REQ-G drafted | Draft REQ-G + provenance + completeness | ✅ Works |
| 3 | **Review & correct requirements** | Architect / BA | Each requirement confirmed, corrected or rejected | **Verified REQ-G** | ⛔ **Blocked** |
| 4 | **Baseline REQ-G** | Architect | The verified set is frozen | REQ-G v1 | ⛔ Blocked |
| 5 | **Load architecture** | Architect | Document ingested; ARC-G drafted | Draft ARC-G | ✅ Works |
| 6 | **Review & correct architecture** | Architect / Engineer | Elements, containment, responsibilities, technology confirmed | **Verified ARC-G** | ⛔ **Blocked** |
| 7 | **Reconcile** | System | ARC-G references resolved to REQ-G nodes | RequirementRealization links | ⛔ Blocked |
| 8 | **Audit** | System | Gaps, conflicts, redundancy, coverage | **Gap report** | ⚠️ Partial |
| 9 | **Resolve gaps** | Architect | Design changed, or requirement challenged | Updated graphs | ⛔ Blocked |
| 10 | **Publish** | System | Validation exposed for consumers | API response | ⛔ Blocked |

### Stages 2 and 5 work. Almost nothing else does.

This is the finding. The journey's critical path runs through **human review**
(stages 3 and 6) and **reconciliation** (7), and neither exists.

We have been building enablers in the order that felt natural — ontology,
extraction, canonical model, validators. All necessary. But the journey shows
the bottleneck is not extraction quality: it is that **extraction output has
nowhere to go once produced.** The pipeline's only exit is a JSON file.

Concretely: a user can run extraction today and get a graph. They cannot review
it, cannot correct it, cannot baseline it, cannot join the two graphs, and cannot
get a gap report out of it. Four of the five jobs in the core sentence are
unreachable.

---

## 4. The workflow this implies

Firm steps, in order, with the human gates explicit. Fixed rather than dynamic —
the PRD describes a known process, and a dynamic router would add
non-determinism to a product selling determinism.

```
 ┌─ 1. INGEST ──────────────────────────────────────────────┐
 │  document → chunk → passes → canonical graph             │
 │  Records: provenance, completeness, per-pass outcome     │
 └──────────────────────────────────────────────────────────┘
                          ↓
 ┌─ 2. REVIEW  (human gate — mandatory, not optional) ──────┐
 │  Present: assertions with confidence + completeness      │
 │  Human: confirm / correct / reject, per assertion        │
 │  Output: verified graph + audit trail of what changed    │
 └──────────────────────────────────────────────────────────┘
                          ↓
 ┌─ 3. BASELINE ────────────────────────────────────────────┐
 │  Freeze a revision. Later runs diff against it.          │
 └──────────────────────────────────────────────────────────┘
                          ↓
 ┌─ 4. RECONCILE ───────────────────────────────────────────┐
 │  Resolve cross-graph references → RequirementRealization │
 │  Report unresolved in BOTH directions                    │
 └──────────────────────────────────────────────────────────┘
                          ↓
 ┌─ 5. AUDIT  (requires COMPLETE inputs) ───────────────────┐
 │  Structural: gaps, orphans, containment, coverage        │
 │  Semantic: does this design answer this requirement?     │
 │  Output: gap report — every finding carries provenance   │
 └──────────────────────────────────────────────────────────┘
                          ↓
 ┌─ 6. RESOLVE  (human gate) ───────────────────────────────┐
 │  Change the design, or challenge the requirement         │
 │  Loop to 1 for the affected scope only                   │
 └──────────────────────────────────────────────────────────┘
```

Two properties to hold firm:

- **Step 2 is a gate, not a screen.** The journey cannot proceed on unverified
  knowledge, because step 5's findings are only meaningful over verified inputs.
  An audit of unverified extraction reports extraction artifacts as architecture
  gaps — confidently wrong, which is worse than no audit.
- **Step 5 refuses to run on an incomplete graph.** `completeness != COMPLETE`
  means absence of a fact is not evidence of its absence. The canonical model
  already carries this; the workflow must honour it rather than warn.

---

## 5. Gaps the journey exposes, in dependency order

Not the order we have been building. The journey reorders them.

| Gap | Blocks | Why it is now first |
|---|---|---|
| **1. View projection layer** | 3, 6, 8 | Nothing to look at. Every downstream step needs a view of the graph. |
| **2. Correction write path** | 3, 6, 9 | The gate has nowhere to record a decision. |
| **3. Revision / baseline** | 4, 9 | Without it, "verified" is a flag on a mutable graph, not a state. |
| **4. Reconciliation** | 7, 8 | The core job. 13 unresolved refs already surface and go nowhere. |
| **5. Audit engine + gap report** | 8, 10 | The product. Structural checks exist; no report, no semantic audit. |
| **6. Requirements pass-split** | 2 quality | 3.5× duplication; single-call, no merge. Quality, not blocking. |
| **7. Run orchestration** | whole journey | Currently manual script invocation. Can stay scripted initially. |

Note what moved: **the requirements pass-split was going to be next.** The
journey says it is not on the critical path — it improves step 2, which already
works. The blocking items are 1–5.

---

## 6. The first journey worth completing

The thinnest end-to-end path that delivers the core job for one graph type.
Deliberately narrow: **requirements only, no architecture, no reconciliation.**

```
load requirements → extract → canonical graph → completeness report
  → human reviews findings → edits the source document → re-run
```

What this proves: that extraction output can be *reached, judged, and acted on*
by a human. Today it cannot — there is no view and no way to record a judgement.

**Why this is the right first slice:** it exercises the review gate — the actual
bottleneck — without needing the correction-merge design (TODO 9b), because
corrections are made *in the source document* and re-extraction picks them up.
The document stays the source of truth, so there is no overlay and no conflict
resolution to design.

That is a legitimate v1 simplification **only while one document owns a graph.**
It breaks the moment knowledge accumulates across documents or runs, at which
point `YB-009` §9b comes back. Worth taking deliberately, with the exit condition
written down.

---

## 7. Open questions

1. **Are the personas right?** Especially: is the *Reviewer/Auditor* a distinct
   role, or is it the Architect wearing a second hat? It changes whether review
   is an inline step or a separate workflow with its own queue.
2. **Is the Initiative on the critical path, or peripheral?** It is modelled but
   has no extraction path. If a business case is the entry point for every
   engagement, stage 1 is unavoidable and moves up.
3. **Does v1 accept document-edits-as-corrections?** (Section 6.) Cheap and
   fast; the exit condition is the first time knowledge comes from more than
   one source.
4. **One domain at a time, or multi-domain from the start?** Affects whether
   stage 0 is a real stage or an assumption.
5. **Who consumes the gap report?** If delivery leads do, it needs to be
   actionable work items — a different artefact shape from an architect's review.

---

## 8. Summary

The enablers are sound and necessary — the ontology, extraction, canonical model
and validators are exactly what the core job requires.

But **the journey is blocked on the human review gate, not on extraction.**
Everything built so far produces knowledge that has no route to a person and no
route onward. The next work is the path *out* of extraction: a view to judge, a
place to record judgement, and a baseline to judge it against — then
reconciliation and the audit that the whole platform exists to deliver.
