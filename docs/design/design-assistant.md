# The Design Assistant — proposing a core architecture from REQ-G

> **Design document** for `YB-035`. Status is tracked in that entry; the record is
> [`ADR-0017`](../decisions/ADR-0017-design-assistant-proposes-arc-g.md).
> The event-driven trigger this slots into is
> [`event-driven-integration.md`](event-driven-integration.md).

---

### The problem, stated once

Architecture extraction reads a **document**. The Design Assistant has none. Its
input is REQ-G — a graph — plus, where one exists, the frozen baseline ARC-G. So
"flesh out the Design Assistant" is really two questions:

1. **What is the document when there is no document?**
2. **What does it mean to produce a fact that a model proposed rather than one a
   document stated?**

Everything below is an answer to one of those.

### It is a profile, not a pipeline

The PRD's step 5 has the agent propose components, connections and technologies —
the same collections architecture extraction already emits. So the Design Assistant
reuses the whole existing apparatus:

| Reused | Why |
|---|---|
| `PassSpec`, `run_passes`, `build_pass_prompt` | A profile *is* "N small passes", and the dilution lesson (YB-007) is already encoded there |
| `ElementRecord`, `ConnectionRecord`, `DesignTechniqueRecord`, `ReferenceRecord` | The proposed things are the same KINDS of thing an architecture document describes |
| `merge_triples`, `merge_records`, `completeness` | Cross-pass dedup with the same "more complete record wins" rule |
| `check_object_contract`, `check_containment`, `check_element_types`, … | A clause is still a clause when a model proposes it |
| `graph_from_extraction`, `merge_graphs` | The proposal folds into the graph by the same rules |

The genuinely new pieces are the two the architecture profile has no record for —
`ArchitecturePattern` and `QualityScenario` — and the digest.

### The digest: REQ-G plus the baseline, as one chunk

`core/knowledge/digest.py` renders both graphs to deterministic text. It is passed
to `run_passes` as a **single `Chunk`**, and that is a design decision rather than a
shortcut:

- A design is a **global act**. Chunked, one call could choose a monolith while the
  next chooses microservices, and both would be locally defensible.
- The digest is bounded instead. `requirements_digest` and `architecture_digest`
  have budgets; over budget they degrade in a **documented order** —
  responsibilities, then descriptions, then non-NFR detail, then a hard truncation
  — and every drop is recorded in `caveats`, printed into the prompt, and shown on
  the page. A design that silently saw half the requirements is not a smaller
  design, it is a wrong one.

What it contains, in order: the Initiative and its goals and capabilities; every
requirement with its identifiers and, for NFRs, its ISO classification; the quality
attributes **with their coverage states** (so the design can see what is stated but
undelivered); the existing architecture grouped by kind; and, replacing what would
otherwise be a raw dump of every recorded link, the two lists that are actually the
worklist:

```
## Requirements the existing architecture already answers (8)
- Payment Request Validation <- Payment Orchestrator
## Requirements with NO architectural answer (10)
- Role-Based Access Control (ConstraintRequirement)
```

Deterministic, with no timestamps or run ids, so the same graph renders the same
text and a diff between two digests is a diff between two knowledge states.

### Six passes

| Pass | Schema | What it proposes |
|---|---|---|
| `structure` | `StructurePassResult` (reused) | SoftwareSystems, Containers, Components, DataStores, ExternalSystems, containment |
| `connections` | `ConnectionPassResult` (reused) | runtime call paths with protocol and style |
| `techniques` | `TechniquePassResult` (new wrapper, reused record) | the mechanisms that deliver each stated quality attribute |
| `patterns` | `PatternPassResult` (new) | named solutions chosen from the catalogue |
| `scenarios` | `ScenarioPassResult` (new) | one measurable ATAM scenario per quality attribute |
| `traceability` | `TraceabilityPassResult` (reused) | which requirement each element answers |

Each instruction block says **propose**, not extract, and says what that changes:
reuse an existing element by name rather than inventing a second one; name the
mechanism rather than the quality attribute; write a number into the response
measure, because a scenario without one is not a scenario.

### Deterministic pattern selection

The catalogue (`ontology/catalogues/architecture_patterns.yaml`, read by
`core/patterns.py`) holds ~24 named solutions with their category, mechanism,
trade-offs and quality links. Two decisions about it matter:

**The prompt gets names and categories only.** Mechanisms and trade-offs are
resolved from the name by `canonical_pattern`. Putting the substance in the prompt
would repeat several thousand characters across passes for information the model is
not being asked to produce, and YB-007 already measured what prompt bloat costs.

**Resolution is strict, in this order:** the entry's own spelling, a declared
alias, the same with noise words removed (`Bulkhead pattern`), then *unresolved*.
There is deliberately no fuzzy matching: substring containment cannot tell
"Monolith" from "Modular Monolith", which are two published patterns with opposite
trade-offs. A pattern the catalogue does not carry is kept under its own name and
reported as a finding, because an organisation's own pattern is a legitimate thing
to propose and renaming it to the nearest neighbour destroys the one piece of
information a reviewer needs.

### Where a proposal lands

`/changes/discard` **empties the entire working set**. That single fact decides the
landing zone: a draft merged straight into the working set could not be undone on
its own. So a run writes a draft to `data/sea/drafts/<id>.json`, the page renders
it, and only an explicit **Apply** merges it into the working set and commits one
revision.

This is not YB-018's review-batch model, and does not pretend to be. A batch is a
unit of the review gate with its own progress, exclusions and promotion rules; this
is a staging file with an Apply button. Building the smaller thing first is
deliberate: applied facts land in the **existing** gate as ordinary unreviewed
assertions, so no new gate semantics were needed, and the batch item stays free to
design them properly. Re-running does not disturb a previous draft — a new run
writes a new file — which is the property YB-035 asked for, obtained for free.

### Provenance: a proposal is not an extraction

`graph_from_extraction` gained a `source_type` parameter, and the design run passes
`SOURCE_DESIGN_ASSISTANT`. Every fact it produces is therefore distinguishable from
both extracted facts and human decisions, while remaining `UNVERIFIED` and
non-human so the review gate applies.

`document_type` stays **`"architecture"`**. The proposed nodes *are*
architecture-side, and `reconcile._node_sides` reads exactly this to decide which
side of the join a node sits on; a third value would have made every proposed
container a side that reconciliation has to learn, for no gain. What makes it a
proposal is the provenance source and the `document_ref`
(`design:<initiative>@<baseline|working>`), not a new side.

### Scenarios light up a census state that was always empty

ADR-0016's quality census reports four coverage states, and `has_quality_scenario`
read **0 on every graph** because nothing emitted a `QualityScenario`. A design run
does. The edge is `scenario --realizes_attribute--> QualityAttribute` — the same
predicate an NFR uses, because both point at the same node; the census separates
them by the **source kind**, and a test pins that a scenario is not counted as a
requirement stating the attribute.

### Prompt budget, measured rather than asserted

YB-007 showed that prompt fixes are not monotonic: adding one section silently broke
a rule that had been working. The Design Assistant is the first profile written
since, so it is the first that can be held to a number, and
`tests/test_prompt_budget.py` measures it: scaffolding (ontology context +
instructions) must not exceed the digest, i.e. ratio ≤ 1.0, against YB-007's
measured 2.5:1. Two properties keep it there — the catalogue never enters the prompt
in bulk, and the digest degrades instead of growing.

### Failure modes, and what happens

| Failure | Behaviour |
|---|---|
| No frozen baseline | Designs against the working set and says so, in the caveats and on the page |
| REQ-G `PARTIAL`/`UNKNOWN` | The completeness note travels in the prompt header; run completeness is never upgraded to `COMPLETE` |
| One pass fails or comes back empty | Recorded per pass; the other passes still merge |
| Structured output unavailable | The text fallback cannot parse technique/pattern/scenario records, so the run reports PARTIAL — the honest outcome per ADR-0013 |
| Digest over budget | Deterministic degradation; every drop is a caveat |
| Pattern not in the catalogue | Kept as proposed, flagged unresolved |
| Scenario with no number | Flagged as a finding — an unmeasurable scenario reads as coverage |
| Technique named after the requirement it answers | Flagged as `mechanism`. Measured on the first real run: all eight techniques came back as renamed requirements, which would let the census report an attribute as "has a realizing technique" on the strength of a restated requirement |
| Element answering nothing | Flagged `ungrounded`; infrastructure legitimately may be, so it is a finding rather than a failure |
| Proposed name already used by another kind | Flagged `collision` — `ingest._resolve` reuses any node with a matching label, so this would otherwise merge silently into the wrong node |
| Model unreachable | `AgentResult(success=False)`; the page flashes and the graph is untouched |

### Deliberately not done

- **Technology stacks and engineering conventions.** The architecture profile
  extracts them; the Design Assistant leaves them out because they were not asked
  for and every collection costs prompt budget. Adding a pass is mechanical.
- **Iterative design.** PRD capability D3 ("accept human modifications and maintain
  graph integrity") is served today by the review gate: an architect corrects the
  applied facts. Feeding those corrections back to the agent is a separate item.
- **The event trigger.** YB-033/034/037. This is the UI path; the agent is already a
  plain `run(input_data)` and does not care who calls it.
