# TODO — SEA Platform

Working list of known improvements. Items are captured with enough context to
implement later without re-deriving the findings.

---

## 1. Tighten the extraction prompt: objects must be entities, not clauses

**Status:** Not started
**Priority:** High
**Area:** `agents/knowledge_extraction/agent.py` → `_build_extraction_prompt()`

### Problem

With `unsloth/SmolLM3-3B-128K-GGUF:BF16`, 8 of 36 extracted triples (22%) have
**clause-like objects instead of entity names**. A triple's object should be a
graph node — a short noun phrase — but the model emits whole sentences:

```
Transaction Routing --has_rule_based_engine--> "Configurable, rule-based engine to determine optimal PGSP for transaction routing"
Transaction Routing --has_routing_criteria-->  "Country of Transaction, Transaction Currency, Payment Method preference (Card Holder preference)"
Security Requirements --has_rbac-->            "RBAC enforced across all administrative and backend interfaces"
Performance Requirements --has_performance_constraint--> "95% of transactions within 500ms, 1000 TPS"
```

These triples extract fine but **cannot link or be queried** — which defeats the
purpose of building a knowledge graph. They also inflate the entity count with
non-entities (see derived-entity fallback below).

### Second problem in the same area

The model treats **document section headings as entities**:

- `"Security Requirements"` and `"Performance Requirements"` came through as entities
- The document uses these as markdown headings (`### Security Requirements`), not as domain concepts

### Proposed fix

Constrain the prompt:

1. **State the object contract explicitly** — objects must be entity names
   (short noun phrases, ≤ ~40 chars), never sentences or clauses.
2. **Rule for multi-value objects** — if a statement lists several things
   ("Country, Currency, Payment Method"), emit **one triple per item**, not one
   triple with a comma-joined list. This is the correct graph shape anyway and
   lets the auditor query each criterion independently.
3. **Tell it to ignore document structure** — section headings, table headers,
   and heading text are not entities unless they name a real domain concept.
4. **Add a few negative examples** — the prompt currently only shows good
   examples. One or two "do not do this" pairs will help a 3B model.

Optional: add a **post-extraction validator** that flags triples whose object
exceeds a length threshold or contains comma-lists, surfacing them as
low-confidence for human review. Cheap safety net independent of model quality.

### Verification

Re-run extraction on `data/input/sample_requirements.md` and check:

- Count of triples with `len(object) > 40` should drop toward 0
- No heading text (`Security Requirements`, `Performance Requirements`) should
  appear as a subject or object
- Multi-value statements should produce N triples, not 1 comma-joined triple

---

## 2. Test and use Strands structured output with the new model

**Status:** ✅ Implemented — **structured output does NOT work with local models. Fallback is the operating mode.**
**Priority:** Closed
**Area:** `agents/base_agent.py` (`invoke_structured`), `agents/knowledge_extraction/agent.py`

### Outcome

Implemented structured-output-first with a guarded fallback. Tested against both
`gemma-4-E4B` and `SmolLM3-3B`. **Both fail.** The fallback works correctly, so
extraction is unaffected — but the capability itself is unusable on this stack.

### Root cause (confirmed, not a guess)

```
strands.types.exceptions.StructuredOutputException:
  The model failed to invoke the structured output tool even after it was forced.
```

The model **writes the JSON as plain text** in its response instead of calling
the structured-output tool. Diagnostic trace confirmed **zero `toolUse` blocks** —
the tool is never invoked.

The critical detail: **the JSON the model writes is correct.** It produces
well-formed `{"subject": ..., "predicate": ..., "object": ..., "confidence": ...}`
objects. So the model understands the *shape* — it simply ignores *forced tool
choice* (`tool_choice`), which the llama.cpp server does not honour.

**This is a server/model capability gap, not a schema or prompt problem.**
No amount of schema loosening or prompt tuning will fix it.

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
