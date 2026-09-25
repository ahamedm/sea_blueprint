# Real-run readings — what one document through the live pipeline says

> **Evidence note.** Not a design and not an entry: this is the measurement the
> handoff of 2026-09-24 asked for, kept so the numbers in
> [ADR-0012](../decisions/ADR-0012-req-arc-reconciliation-inversion.md),
> [ADR-0013](../decisions/ADR-0013-requirements-completeness-reporting.md) and
> [ADR-0016](../decisions/ADR-0016-quality-attribute-views.md) can be checked
> against a run rather than against a fixture.

---

### Why the fixtures could not answer the question

Three of the changes the 2026-09-24 session made were verified against
`data/output/*.json`, and those files predate the changes:

| Fixture | Reads today |
|---|---|
| `data/output/test_req_sample.json` | `completeness=UNKNOWN`, `passes=0`, `model_id=''` |
| `data/output/test_req_prd.json` | `completeness=UNKNOWN`, `passes=0`, `model_id=''` |
| `data/output/test_arch.json` | `completeness=COMPLETE`, `passes=4`, `model_id=''` |

They carry no `passes` metadata, so reconstructing a run from them correctly
reports ignorance. And they contain no `QualityAttribute` nodes, so any census
over them is empty by construction. A number measured against them describes the
code's treatment of an old payload, not a document.

So one real document was run through the real extractor and the real ingest route:
`test_data/prd/sample_requirements.md`, requirements profile, domain pack
`payment_processing@0.1.0`, model `unsloth/Qwen3.5-4B-GGUF:Q4_K_M` on the local
llama.cpp endpoint. The command is
[`scripts/run_real_ingest.py`](../../scripts/run_real_ingest.py); `--dry-run`
extracts and reports without touching the working set.

Two runs were made: one into a throwaway store (to see the shape of a
requirements-only graph) and one into `data/sea`, which already held
`payment_platform_arch.md` from 2026-09-24, so all three readings can be taken
with both sides of the graph present.

### Reading 1 — completeness: the fix holds on a real document

```
run_1833ab048ba4   sample_requirements.md   requirements   COMPLETE
  passes   1
    structured  outcome=ok  path=structured  triples=35
  model    unsloth/Qwen3.5-4B-GGUF:Q4_K_M
```

Not `UNKNOWN`, not `PARTIAL`: one structured attempt, it produced content, and the
run says so. The requirements profile now describes its own run on live output,
which is what ADR-0013 claimed and what the fixtures could not show. The merged
working set reads `COMPLETE` across all three runs and `is_auditable: true`.

### Reading 2 — the cross-graph join: real numbers, and two defects

On the merged working set (`realization_report`):

```
requirements 18   realized 8   unrealized 10
coverage  none 10  unresolved 0  partial 0  full 8
bound_edges 59→64   unbound_claims 46→45   proposed 24   unproposed 21
```

The fresh run moved `bound_edges` up by five: its requirement labels
(`PCI-DSS Compliance`, `AES-256 Encryption`, `Payment Authorization`,
`Cardholder Data Protection`) are worded close enough to architecture labels to
bind without reconciliation. The ten still-unrealized requirements are the
paraphrase gap `implements_requirement` cannot cross — which is the premise of
YB-029, now measured rather than asserted.

Running the fresh document surfaced two things a fixture replay hides.

**A. The requirements profile claims architecture-side realization.** The
requirements-only dry run, which contains **no architecture nodes at all**,
reported:

```
requirements 12   realized 11
requirement-side claims: implements_requirement ×19
  Payment Gateway Platform --implements_requirement--> Payment Request Acceptance
  Payment Gateway Platform --implements_requirement--> Payment Request Validation
  Payment Gateway Platform --implements_requirement--> Cardholder Data Encryption
```

The model attaches the one predicate that *means* "an architecture element answers
this requirement" to the system node of the requirements graph. `realization_state`
reads the claim, sees its target is a requirement, and counts the requirement
realized. So `implements_requirement` — the join the audit trusts — can report an
architecture answering requirements when no architecture exists. It is
run-dependent (the merged run emitted the plural `implements_requirements`, which
routes differently and therefore did *not* inflate the number), which is worse: the
reading is not stable across runs of the same document. Recorded as
[YB-030](../todos/entries/YB-030-requirements-profile-cross-graph-claims.md).

**B. The taught predicate vocabulary is not the routed one.** `CORE_ROUTED_PREDICATES`
spells the schema's own names for the model — `implements_requirements`,
`satisfies_quality_attributes` (plural) — while `CROSS_GRAPH_PREDICATES` routes
`satisfies_quality_attribute` (singular) and `implements_requirement` (singular).
Measured on the merged graph:

| Predicate | Count | Routed as cross-graph |
|---|---|---|
| `implements_requirement` | 36 | yes |
| `implements_requirements` | 1 | **no** — became a local edge |
| `satisfies_quality_attribute` | 24 | yes |
| `satisfies_quality_attributes` | 4 | **no** — became a local edge |

A predicate the prompt teaches and the router does not know is an edge that looks
present in the graph and is invisible to every consumer that routes on the cross-graph
set. Recorded as [YB-031](../todos/entries/YB-031-cross-graph-predicate-plural-routing.md).

### Reading 3 — quality-attribute coverage: the join converges

The census from [ADR-0016](../decisions/ADR-0016-quality-attribute-views.md), run
over the same working set:

```
attribute nodes 13 → canonical concerns 11   (2 labels merged)
characteristics 4
coverage   answered 3   architecture_gap 2   unasked 6   unaddressed 0
states     stated 5   delivered 9   technique 9   scenario 0
empty      has_quality_scenario
```

The merges are the finding. `Availability` (requirements side) and
`High Availability` (architecture side) are one concern; `Time Behaviour`,
`Low Latency` and `Throughput` are one concern. Grouped by label they are five
attributes, three of which would report as "stated and not delivered" or "delivered
and not stated" — a census that manufactured gaps out of spelling.

What the census says about this architecture, on this data:

- **Answered (3):** Time Behaviour, Confidentiality, Scalability — stated by a
  requirement and delivered by an element or technique.
- **Architecture gap (2):** `Capacity` and `Accountability` are stated and nothing
  delivers them.
- **Unasked (6):** `Reliability`, `Availability`, `Fault Tolerance`,
  `Recoverability`, `Integrity`, `Performance` are delivered and no requirement
  states them. This is the inverse direction `/gaps` cannot express.
- **Empty:** `has_quality_scenario` is populated by nothing anywhere — a statement
  about the extraction (no pass emits a `QualityScenario`), reported rather than
  omitted.

### One more defect, in the data rather than the code

The architecture profile fills the multivalued quality fields as one joined string
instead of one value per assertion:

```
Payment Gateway Platform --quality_category-->    'RELIABILITY, PERFORMANCE_EFFICIENCY, FLEXIBILITY'
Payment Gateway Platform --subcharacteristic-->    'AVAILABILITY, TIME_BEHAVIOUR, SCALABILITY'
```

The census does not depend on those edges — it groups attribute nodes by their own
labels — so it is unaffected. But any consumer that groups by `subcharacteristic`
would see one nonsense token. Recorded as
[YB-032](../todos/entries/YB-032-multivalued-quality-fields-joined.md).

### What this does not establish

- One document, one model, one run. Extract volume is not stable across runs
  ([YB-004](../todos/entries/YB-004-model-output-not-structurally-stable.md)), and
  the two requirements runs of the *same* file produced different requirement
  labels, which is why the merged graph now holds both spellings.
- The architecture side was ingested on 2026-09-24, before these readings. The
  cross-graph numbers are current; the architecture extraction output is not.
- `PROPOSED` versus accepted reconciliation is untouched: `proposed_claims 24` is
  the matcher's queue, not a reviewer's decision.
