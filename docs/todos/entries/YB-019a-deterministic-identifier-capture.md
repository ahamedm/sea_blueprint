---
id: YB-019a
legacy: "19"
title: "Deterministic identifier capture — stop asking the model to copy literals"
status: superseded
priority: high
area: "new extraction pass in `agents/extraction/`, `core/knowledge/ingest.py`, `agents/knowledge_extraction/agent.py`"
created: 2026-09-23
updated: 2026-09-24
design: null
record: null
superseded_by: ["ADR-0001", "YB-019b"]
related: ["YB-019b"]
blocks: []
blocked_by: []
---

# YB-019a — Deterministic identifier capture — stop asking the model to copy literals

> **Superseded.** Kept only for the evidence it records; it is not work to do.

**Legacy status:** Superseded by item 0's fix and the second item 19
**Legacy priority:** High
**Legacy area:** new extraction pass in `agents/extraction/`, `core/knowledge/ingest.py`, `agents/knowledge_extraction/agent.py`

**Superseded by:** ADR-0001, YB-019b — kept for the evidence it records, not as work to do.

---

> **SUPERSEDED — read YB-019b below.** The evidence in this entry is sound;
> the diagnosis is not. ADR-0001 (the config bug) was the cause of the missing
> identifiers, and this item's premise is retracted there. Kept for the record.


### The finding, measured live

Four extraction runs against a working model, two code paths, on documents that
demonstrably contain identifiers:

| document | path | entities | with `requirement_id` |
|---|---|---|---|
| `sample_requirements.md` | `structured_output` | 12 | **0** |
| `payment_platform_brief.md` | `text_parsing` | 64 | **0** |
| `payment_platform_brief.md` | `text_parsing` (repeat) | 64 | **0** |
| `test_req_prd.json` (saved, older server) | `structured_output` | 54 | **0** |

`payment_platform_brief.md` contains **12** identifiers (`FR-TR-001`…`FR-TR-004`,
`NFR-PS-001`…`003`, `NFR-SC-001`…`003`, `NFR-UM-001`…`002`) written plainly as
`**NFR-PS-001 (Latency):**`. The model returns `requirement_id: ""` for every entity
while satisfying the schema, and the payload contains no `FR-`/`NFR-` string at all.

**This is not instruction dilution, which was the earlier hypothesis.** On the
structured path `entities` IS enforced and `requirement_id` IS a field on it — the model
simply leaves it empty. Strengthening the prose will not fix a literal-copy task that a
4B model does not perform.

### The fix, and why it is deterministic

An identifier is a **short literal string present verbatim in the source**. Recovering it
is pattern matching, not language understanding, so it should not be delegated to a model
at all. This is the same posture as the existing validators — `check_containment`,
`check_object_contract`, `check_element_types` are deterministic precisely because the
model is unreliable at mechanical fidelity.

Sketch:

1. A pass that scans the source for identifier-shaped literals and the entity names they
   annotate (`**NFR-PS-001 (Latency):** …` → identifier + nearby heading/concept).
2. Attribute each identifier to the entity it names, by exact name match first and
   proximity second — never by model judgement.
3. Record it as a typed `ExternalReference` (**already built**): `reference_type=OTHER`,
   `scope=DOCUMENT`, `system=<document>` — a label local to its source, refused as a
   cross-document join, which is the correct default per YB-020's scope rule.
4. Emit a **review finding** for a requirement-typed entity with no identifier only when
   the source contained one nearby — otherwise every identifier-less document produces
   noise.

### Two related defects found in the same investigation

- **The free-form path never emits `entities` at all.** Its response had exactly one
  top-level key, `triples`. `requirement_id` exists only on `ExtractedEntity`, never on
  `ExtractedTriple`, so on that path every per-entity field — identifiers, quality
  classification, `initiative_refs` — has nowhere to land, and `_derive_entities_from_triples`
  synthesises entities that structurally cannot carry them. No prompt change fixes this.
- **The prompt contains no output skeleton.** It asks for *"list all unique entities and
  relationship types you used"* — a summary of names, not a structured list — and never
  shows the required `{triples, entities, relationships}` shape. Adding an explicit
  example is a prerequisite for the structured path carrying anything per-entity.

### Not done here

`.env.example` still documents the retired `192.168.3.176:8080` host and the full
checkpoint id `unsloth/Qwen3.5-4B-GGUF:Q4_K_M`. Both endpoints are live and serve the same
checkpoint under **different model ids** (`Qwen3.5-4B-GGUF` on `localhost:13305`), so the
host and id must be changed together or the client gets `model_not_found`.

### Measured: the vocabulary works, and it is not free

`relationship_predicates()` compiles **47** relationship predicates from the schema
(≈1,000 chars rendered), injected by `SEABaseAgent._predicate_vocabulary_context` with
the graph-routed ones spelled out plus their accepted singular aliases.

| | before | after |
|---|---|---|
| declared-or-aliased predicates used | 1 of 16 (6%) | **10 of 15 (67%)** |
| routed to reconciliation | 1 | **3** |
| invented | 15 | **5** |
| elapsed, `sample_requirements.md` | 148 s | **754 s** |

The model visibly reasons over the offered names now, and for the first time used the
schema's own spellings — `delivers_initiatives`, `satisfies_quality_attributes`,
`traces_to_capabilities`, and the documented alias `realizes_quality_attribute`. That is
`CROSS_GRAPH_PREDICATES` firing on names the model was previously never told.

**Two costs, both real:**

1. **Wall-clock, 5×.** Whether the cause is the extra ~1,000 characters or the longer
   reasoning trace they provoke, the vocabulary is not attributable from a single run —
   this needs repeats before it is trusted as a headline number.
2. **It worsens YB-007.** The brief's scaffolding-to-document ratio moves from 2.0:1 to
   **2.7:1**, past the 2.5:1 threshold YB-007 already flags as High priority. The block was
   compressed once (targets only on the 11 routed predicates; the other 36 listed as names)
   to get from 2.8:1 to 2.7:1, and cannot be compressed much further without dropping the
   target kinds — which are what make a predicate checkable.

**So this is a deliberate trade with an open question**, not a clean win: it buys the
routing edges the graph exists to produce, at the cost of prompt dilution that YB-007
identifies as making *every other* prompt fix unreliable. If YB-020's option 4 lands
(deterministic capture decoupled from the model), the prompt may be able to shrink
substantially and this block could then be reviewed on its own merits.

**Not changed here:** the hand-written predicate examples in the prompt's §4 relationship
section still say `traces_to_goal`/`traces_to_capability`/`traces_to_process` where the
schema declares the plurals. They are consistent with the documented aliases and are left
alone pending a decision on whether to align spelling across the repo wholesale.
