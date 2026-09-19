# TODO — SEA Platform

Working list of known improvements. Items are captured with enough context to
implement later without re-deriving the findings.

---

## 1. Tighten the extraction prompt: objects must be entities, not clauses

**Status:** ✅ RESOLVED — node quality 35% → 6%
**Priority:** Closed
**Area:** `agents/knowledge_extraction/agent.py`

### Outcome

| | Baseline | After |
|---|---|---|
| Clause-like / contract violations | **35%** (14/40) | **6%** (2/34) |
| Triples | 40 | 34 |
| Entities | 14 | 23 |
| Distinct subjects | 5 | 10 |
| `ontology_class` populated | 40/40 | 34/34 |
| Distinct confidence values | 3 | 3 (0.8/0.9/1.0) |

Node quality now reads as actual graph nodes:

```
Payment Gateway Platform --has_functional_requirement--> Payment Request Mapping
Cardholder Data --encrypted_using--> TLS 1.2+
Transaction Routing --is_determined_by--> Country of Transaction
Role-Based Access Control Enforcement  (as a node, not a clause)
```

The two remaining flags are **false positives** of the heuristic — both are
legitimate compound names sitting 1–2 chars over the 40-char threshold
(`Payment Gateway Service Provider Selection`). Conservative over-flagging is the
right default; they cost a human one glance.

### Root cause — the prompt was teaching the bug

The few-shot example in the prompt contained the exact failure:

```json
"object": "Complete within 500ms"    ← a clause, presented as correct
```

The model was faithfully imitating the example. **Check examples before tuning
instructions** — a wrong example outweighs any amount of correct prose.

### What was changed

1. **Schema field descriptions tightened.** With structured output these *are*
   the prompt — they ship to the model as the tool schema. `object` now carries
   the rule, an explicit WRONG/RIGHT pair, and the one-triple-per-item rule.
2. **Prompt rewritten** with an OBJECT CONTRACT section, a worked WRONG→RIGHT
   example from the real document, and the document-structure rule (section
   headings are scaffolding, not concepts).
3. **Confidence guidance** now explicitly asks for variation, since a flat 1.0
   makes the human-review threshold useless.
4. **`_flag_contract_violations()`** — deterministic post-check, independent of
   model behaviour. Flags clause-shaped nodes, comma-lists, and clause markers.
   **Flags, never drops** — silently discarding extracted content is worse than
   surfacing it.

### Regression caught and fixed mid-work

The first pass fixed node quality (35% → 9%) but **silently dropped the
ontology's traceability predicates** — `traces_to_goal`, `traces_to_capability`,
`traces_to_process`, `binds_to_system` all vanished. Those edges are how the
Semantic Auditor finds gaps, so losing them is worse than clause-shaped nodes.

Cause: the contract emphasis crowded out inference of *implicit* traceability.
Fixed by adding explicit guidance that inferred traceability edges should still
be emitted, scored 0.5–0.8. Result: 5 traceability edges restored with honest
confidence scores (0.8–0.9), and violations improved further to 6%.

**Lesson: when tightening one dimension, re-measure the others.** A prompt fix
that improves the metric you're watching can quietly destroy a metric you aren't.

### Verification

Measured on `data/input/sample_requirements.md` via
`metadata["contract_violation_count"]`. Re-run and compare if the prompt changes.
- Multi-value statements should produce N triples, not 1 comma-joined triple

---

## 2. Test and use Strands structured output with the new model

**Status:** ✅ RESOLVED — working with Qwen3.5-4B on llama.cpp
**Priority:** Closed
**Area:** `agents/base_agent.py` (`invoke_structured`), `agents/knowledge_extraction/agent.py`

### Resolution

Structured output now works. Succeeded on the **first turn** (`Tool #1: ExtractionResult`)
with no retry loop:

```
path used:     structured_output
triples:       40    entities: 14    relationships: 9
confidence:    95.50%    elapsed: 49.1s
```

Two preconditions, both required:

1. `.env` must target the **llama.cpp** instance — not Unsloth Studio (port 8888)
2. The served model must be tool-capable

`USE_STRUCTURED_OUTPUT=true` is set in `.env`.

### Quality vs the text-parsing path

| | Structured | Text parsing |
|---|---|---|
| `ontology_class` populated | **40/40 (100%)** | partial |
| Distinct confidence values | **3** | 1 (all 1.0) |
| Entities / relationships | explicit | derived |
| Clause-like objects (>40 chars) | 30% | 22% |
| Elapsed | 49.1s | 26.3s |

Structured output wins on the things that matter for a graph — every triple is
ontology-mapped, and confidence scores actually discriminate instead of being a
flat 1.0. It costs ~2× the latency.

Object phrasing is still loose (30% clause-like), so **item 1 remains the next
piece of work** — that threshold is now measurable against a stable baseline.

### Root cause (historical — two separate blockers)

Worth keeping, because the failure was silent and cost several rounds to isolate.

**Blocker A — `.env` pointed at the wrong server.**

| Port | Server | |
|---|---|---|
| `8888` | `unsloth-studio` | what `.env` targeted; serves 5 models |
| `8080` | `llama.cpp` | the `--jinja` instance |

The `--jinja` restart was real and working; `.env` was talking to the wrong process.

**Blocker B — the model wasn't tool-capable.**

llama.cpp reported `chat_template_caps.supports_tools: false` for SmolLM3-3B.
With that flag false, llama.cpp **accepts the `tools` field and silently discards
it** — no error, `tool_calls: None`, even under `tool_choice="required"`.
`--jinja` alone is not sufficient; the model's template must qualify.

Switching to Qwen3.5-4B flipped it to `supports_tools: true` and everything worked.

### Pre-flight gate — run this FIRST, always

```bash
curl -s http://192.168.3.176:8080/props | jq .chat_template_caps.supports_tools
# must be true
```

If `false`, nothing downstream can work. This single check would have short-circuited
three rounds of dead-end probing (schema loosening, prompt tuning, flag changes —
none of which can affect this flag).

### What was implemented anyway (worth keeping)

1. **`SEABaseAgent.invoke_structured()`** — wraps structured invocation with a
   hard `limits={"turns": N}` cap. This is the guard that converts the old
   infinite retry loop into a deterministic, bounded failure. Also distinguishes
   `StructuredOutputException` from other errors so the log message is actionable.
2. **Permissive schemas** — `confidence` accepts `0.95`, `"0.95"`, `"95%"`, `95`,
   `None`. Every required field is a potential validation failure → retry. Note:
   percentage detection only fires above `2.0`; values in `(1.0, 2.0]` clamp to
   `1.0` rather than being divided by 100 (dividing `1.5` → `0.015` would have
   silently marked a high-confidence triple as near-zero).
3. **`use_structured_output` + `max_structured_turns` config** — env-controllable
   via `USE_STRUCTURED_OUTPUT` / `MAX_STRUCTURED_TURNS`.
4. **`extraction_path` in metadata** — records which path produced the result, so
   runs are comparable instead of ambiguous.

### Measured cost

| `MAX_STRUCTURED_TURNS` | Elapsed | Behaviour |
|---|---|---|
| 6 | ~137s | 6 wasted turns, then fallback |
| 3 | ~30s | 3 wasted turns, then fallback |
| disabled | ~26s | straight to text parsing |

### Decision

`.env` sets `USE_STRUCTURED_OUTPUT=false` for this llama.cpp setup — the attempt
provably cannot succeed, so it is pure latency. The capability is retained and
one line away from re-enabling for any provider that honours `tool_choice`
(OpenAI, Anthropic, vLLM with guided decoding).

### If revisiting

Only worth retrying if the inference server changes. Test with a one-line probe:
check whether `toolUse` blocks appear in `agent.messages` after a structured call.
If zero → the server still ignores `tool_choice`; don't pursue it further.

---

## 3. Relationship extraction returns 0 with the new model

**Status:** ✅ Resolved — relationships are now derived from triple predicates
**Priority:** Closed
**Area:** `agents/knowledge_extraction/agent.py`

### Problem

Relationship extraction dropped from **8 → 0** when switching models. This is
not a parse crash — the model emits relationships in a shape the parser doesn't
recognize:

```json
{
  "entity_type": "concept_relationship",
  "type": "is_part_of"
}
```

The parser currently handles: `relationships`, `identified_relationships`,
`extracted_relationships`, `relationships_identified`,
`summary.relationship_types_identified`, markdown tables, and backtick lists.
It does not recognize a list of `{"entity_type": "concept_relationship", "type": ...}`
objects.

### Note on approach

**Be careful here.** This is the same whack-a-mole pattern as the entity list —
the model invents a new key/shape each run. Adding one more handler fixes this
run and breaks on the next.

The more durable fix is one of:

- **Derive relationships from triples** — the predicate *is* the relationship.
  Same structural insight that fixed entities (subjects/objects are nodes).
  This makes relationships self-consistent with the triples by construction.
- **Or** solve it at the source via item 2 (structured output).

Prefer one of those over adding another key handler.

### Resolution

Took the derivation route: `_derive_relationships_from_triples()` now builds the
relationship list from triple predicates, deduplicated and order-preserving.

Chosen because item 2 proved structured output cannot work on this stack, so the
"fix it at source" option was unavailable. Derivation also has a better property
than any parser: the relationship list can no longer disagree with the triples,
because it *is* the triples' predicate set.

Verified: relationships now populate consistently (11 from a 13-triple run)
where they previously returned 0.

**No further key handlers were added.** The existing ones remain for the case
where a model does emit an explicit list, but derivation is the safety net.

---

## 4. Model output is not structurally stable across runs

**Status:** Observed — no action yet
**Priority:** Low (watch item)

The local model produces a **different JSON key name for the entity list on
every run** — observed so far: `entities_summary`, `entity_mapping`,
`identified_entities`, `extracted_entities`, `entities_identified`.
Relationships show the same variance.

Mitigated for entities by `_derive_entities_from_triples()` (structural
fallback), but the underlying instability remains.

**Why it matters:** if extraction output is ever diffed across runs (e.g. to
detect *changed requirements* between document revisions — a core SEA
workflow), structural variance will produce false diffs and drown the signal.

**Options:**
- Structured output (item 2) — fixes at the source, if it works
- A normalisation layer that canonicalises parsed output before comparison
- A more capable model for extraction

---

## Reference: current extraction result

Model: `unsloth/SmolLM3-3B-128K-GGUF:BF16` via local llama.cpp endpoint
Input: `data/input/sample_requirements.md`
Output: `data/output/ea_extraction.json`

```
Success:       True
Triples:       36
Entities:      44  (derived from triples — model emitted no explicit entity list)
Relationships:  0
Confidence:    99.17%
Triples with clause-like objects (>40 chars): 8/36
```

For comparison, previous model (`unsloth/gemma-4-E4B-it-GGUF:Q8_0`):
20 triples, 8 relationships. Fewer triples but tighter objects.

**Takeaway:** volume and precision traded off between models. Neither is
production-grade yet — the prompt contract (item 1) and output shaping
(item 2) matter more than model choice at this size.
