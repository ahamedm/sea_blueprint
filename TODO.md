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

Measured on `test_data/prd/sample_requirements.md` via
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

## 5. ARC-G ⇄ REQ-G linkage is not joinable — blocks cross-verification

**Status:** Not started
**Priority:** **Critical** — this is the platform's core purpose, currently unmet
**Area:** `agents/knowledge_extraction/`, `agents/architecture_extraction/`, ontology

### Problem

The two graphs are extracted independently and **cannot be joined**. The Semantic
Auditor's whole job — find requirements with no architectural answer, find
architecture answering nothing — is impossible in the current state.

Evidence from the current draft outputs:

```
                               arch nodes: 60
                               req  nodes: 41
                               shared identity: 4   (AES-256, TLS 1.2+, Payment Gateway Platform, Transaction Currency)
```

Those 4 shared values are incidental coincidences of wording, not links. Of the
**10 requirement references** the architecture graph emitted, **0 match any REQ
node by name**:

| Architecture says | In REQ graph? |
|---|---|
| `Request Acceptance and Validation` | ✗ |
| `Rule-Based Routing` | ✗ |
| `Tenancy Mapping and State Tracking` | ✗ |
| `Settlement Request Initialization` | ✗ |
| `PCI-DSS Compliance` | ✗ |
| `Low Latency`, `Scalability`, `Security`, `High Availability` | ✗ |

### Root causes (three, all fixable)

1. **Requirement IDs are discarded during extraction.** The requirements document
   carries 18 stable identifiers (`FR-PM-001`, `NFR-SC-001`, …). **Neither graph
   preserves a single one** — 0 found in either output. The extraction schema has
   no `requirement_id` field and the prompt never asks for one. Losing the
   document's own stable keys is the primary cause.

2. **The architecture document references requirements by paraphrase, not by ID.**
   Its §2.1 says the Payment Orchestrator handles *"Request Acceptance and
   Validation"* — which is `FR-PM-001` ("Payment Acceptance"). The reference is
   semantically correct and lexically unjoinable. Even perfect ID preservation
   would not fix this alone.

3. **No resolution step exists.** Nothing reconciles an architecture-side
   reference against requirement-side nodes. The ontology already has the right
   construct — `RequirementRealization` (requirement ↔ realised_by, with
   `coverage`, `evidence`, `confidence`) — but nothing populates it.

### The `requirement_type` observation

`requirement_type` is `null` on **all 73** architecture triples. That is the visible
symptom of the above: no architecture triple carries any requirement
classification because none is linked to a requirement.

Note this field means BUSINESS / FUNCTIONAL / NON_FUNCTIONAL / CONSTRAINT — a
*requirement* classification. On architecture triples it should arguably be
populated **only on the traceability edges** (where an arch element points at a
requirement), not on every arch triple. Worth deciding explicitly rather than
leaving it perpetually null.

### Design decision (Ahmed, review session)

**The link structure is established in the extraction phase. Reconciliation
happens in later stages.**

My original proposal leaned the other way — defer matching to the Auditor. That
was wrong. The extraction pass must actively build the linkage structure:

- capture requirement identifiers verbatim
- capture architecture→requirement references, including paraphrased ones
- emit traceability edges even when uncertain

Later stages then *resolve and refine* those links. Anything imperfect or missed
in extraction is correctable by a **human architect** — which is acceptable, and
far better than emitting nothing. An absent link is invisible and unrecoverable;
an imperfect link is a reviewable assertion.

This sets the posture for the whole platform: **capture eagerly, resolve later,
let humans adjudicate.**

### Implemented (this session)

**A. Identifier preservation — DONE.**
`ExtractedEntity` now carries `requirement_id` and `initiative_refs`;
`ArchitectureElementRecord` carries `requirement_refs` and `initiative_refs`.
Both prompts instruct verbatim preservation and explicitly forbid inventing IDs.

Verified against the PRD (18 identifiers in source):

```
requirement_ids captured: 16 of 18
  FR-PM-001..003, FR-SR-001..003, FR-TR-001, FR-TR-004,
  NFR-PS-001..003, NFR-SC-001..003, NFR-UM-001..002
```

Two misses (`FR-TR-002` Routing Criteria, `FR-TR-003` Fallback Mechanism) and one
false positive (`PGP` captured as an ID — it is the platform name). Both are the
class of error a human architect corrects in review; the mechanism is sound and
the misses are visible rather than silent.

**B. Initiative construct — ADDED.**
New `Initiative` class in `requirements_base.yaml` (+ `InitiativeType`,
`InitiativeStatus` enums). The business case / work item that formalises *why* the
work exists. It is the common root of the chain:

```
Initiative (business case)
   │ originates_requirements / delivers_capabilities / affects_systems
   ▼
Requirement (REQ-G, with stable id)
   │ requirement_refs  ← captured at extraction, resolved later
   ▼
Architecture element (ARC-G)
```

`BusinessRequirement.originates_from: Initiative` and
`ArchitectureElement.initiative_ref` complete it, so business case → requirement →
architecture is walkable as one chain.

### Remaining

**C. Resolution pass (later stage).** Match captured `requirement_refs` against
REQ-G nodes, emit `RequirementRealization` with `coverage`/`evidence`/`confidence`,
and — critically — report **unresolved refs in both directions**. That dual
reporting is the actual gap analysis:
- architecture citing a requirement that does not exist
- requirement with no architecture citing it

Semantic matching, not string equality: the source paraphrases
(`"Request Acceptance and Validation"` for `FR-PM-001`).

**D. Better ID recall.** 16/18 with 1 false positive. Revisit once the resolution
pass can measure what was actually missed — tuning recall blind, without knowing
which misses matter, is guesswork.

### Acceptance criteria

- A requirement with no architecture can be listed
- An architecture element answering no requirement can be listed
- Every resolved `implements_requirement` edge points at a real REQ-G node id
- Unresolved references are surfaced, never silently dropped

---

## 6. Extract from C4 Structurizr (model / DSL), not just prose

**Status:** Not planned yet — capture for later
**Priority:** Medium (high leverage when reached)
**Area:** new importer alongside `agents/architecture_extraction/`

### Why this matters more than it looks

Structurizr is a **structured architecture model**, not prose. Extracting from it
is a **deterministic mapping**, not an LLM inference problem — which removes the
entire class of errors we keep fighting:

- no C4 level guessing (levels are explicit in the model)
- no container/component misclassification (item from the arch draft)
- no invented elements or genericised names
- no hallucinated connections
- stable element IDs, so ARC-G ⇄ REQ-G joining (item 5) becomes tractable —
  architecture-side references can carry real identifiers instead of paraphrases

Essentially: the prose path needs a 15-rule prompt and a contract validator to
approximate what Structurizr states outright. Prefer the structured source
wherever one exists.

### Inputs to support

- **Structurizr DSL** (`.dsl`) — text, needs a parser
- **Structurizr JSON export** — direct; the model is already a graph
  (people, softwareSystems, containers, components, relationships, views,
  and `properties` for arbitrary metadata like REQ ids)

JSON first — no parser to write, and it is the canonical interchange format.

### Mapping sketch

| Structurizr | ARC-G |
|---|---|
| `softwareSystem` | `SoftwareSystem` / `ExternalSystem` (per `location`) |
| `container` | `Container` (or `DataStore` by tag/technology) |
| `component` | `Component` |
| `person` | `Person` |
| `relationship` | `Connection` (+ `technology`, `description`) |
| `views` | `ArchitectureView` |
| `properties` | traceability slots — e.g. `properties["requirement"] = "FR-PM-001"` |

The `properties` field is the clean bridge for item 5: it gives architecture
elements a place to carry requirement identifiers natively.

### Note

Structurizr exports also carry `documentation` and `decisions` (ADR) sections,
which map directly onto `ArchitectureDecision` — another reason to prefer it.

---

## 7. Prompt scaffolding now exceeds the document 2.5:1 — instruction dilution

**Status:** Observed — needs a strategy
**Priority:** High (it makes every other prompt fix unreliable)
**Area:** `agents/architecture_extraction/agent.py`, extraction prompt design generally

### The problem

Measured on the architecture prompt:

```
prompt total:      15,903 chars  (~4,000 tokens)
  document:         4,542 chars
  instructions:    11,361 chars   ← 2.5x the document
```

**Observed consequence:** adding the structural-containment section caused a rule
that was *working* to silently regress. In the previous run, **0** technologies
were emitted as elements. After the containment addition, **7 came back**:
`Spring Boot / Java 21`, `Docker`, `TLS 1.2+`, `AlpineJS`, `RBAC`,
`Stateless Modular Microservices`, `Active-Active`.

Nothing about the technology rule changed. It was simply crowded out.

### Why this matters more than the individual bug

It means **prompt fixes are not monotonic**. Every section added can undo a prior
fix, silently, with no error. Observed **three times** now:

1. **Item 1** — tightening the object contract silently killed traceability
   predicates (`traces_to_goal/capability/process`, `binds_to_system`).
2. **Item 7** — adding structural containment silently resurrected
   technology-as-element (7 items: `Spring Boot / Java 21`, `Docker`, `TLS 1.2+`,
   `AlpineJS`, `RBAC`, `Stateless Modular Microservices`, `Active-Active`).
3. **system_class/origin** — adding two classification fields silently dropped
   **responsibilities entirely** (18 elements → 0) and lost the monitoring
   platforms (Prometheus, Grafana, ELK, Splunk). Elements went 19 → 12.

Instance 3 is the most alarming: the model emitted `"responsibilities": []` for
every element — it *recognised the field and left it empty*. And the harness
reported `inv_software_systems_classified` as **PASS** ("1 SoftwareSystem, all
classified") when the truth was that four had been dropped. **A green check
measuring a diminished graph is worse than a red one.**

Both earlier instances were caught only by diffing output against the previous
run rather than trusting the headline number. Instance 3 was caught because the
harness happened to assert on that field.

**Standing rule: after ANY prompt change, re-measure ALL prior fixes, not just the
one being worked on.** Ideally make this mechanical rather than discipline.

### Options

**A. Move invariants into schema field descriptions.** Schema descriptions attach
to the specific field being filled rather than sitting in a scroll, so they resist
dilution far better. This is already the reason the object contract works on the
triples — its rules live in `ExtractedTriple`'s field descriptions. The
technology-vs-element rule currently lives only in prompt prose; it should move to
`element_type` / `technology` descriptions.

**B. Deterministic validators for anything a prompt cannot be trusted to enforce.**
The pattern already proven this session — the containment check caught 2 real gaps
immediately on first run. Cheap, model-independent, and immune to dilution. Extend
to cover technology-as-element and pattern-as-element.

**C. Split extraction into focused passes** instead of one mega-prompt. Separate
calls for elements / connections / traceability, each with a short prompt. Costs
more round-trips but each instruction set stays small and coherent. Probably the
right long-term answer if the rule count keeps growing.

**D. Ruthless prose cutting.** Much of the current scaffolding is explanatory. Rules
in tables, no rationale, no worked examples beyond one. A worked example earns its
place only where the model has actually failed without it.

Likely combination: **A for rules, B for invariants, D for bulk, C if it keeps growing.**

### Test to add

A regression harness: run the sample doc, assert a battery of invariants
(0 technologies as elements, ≥1 `part_of` per container, 0 orphaned containers,
C4 levels valid). Run it after every prompt change. This converts the "re-measure
everything" discipline into a command.

### Escalation — the single mega-call has hit its limit

Adding the technology-stack and architecture-style constructs pushed the prompt and
schema past what one call can do:

```
prompt:  ~16k chars scaffolding, 2.5x the document
schema:  5 record types, ~15 fields, long descriptions
output:  triples + elements + connections + technology_stacks + architecture_styles
result:  a single structured generation exceeding 500s on a 4B local model
```

The model kept producing correct content — its text-fallback output routed
technologies to `TechnologyStack`, styles to `ArchitectureStyle`, and topology to
`ArchitectureDecision` exactly as instructed. It simply cannot emit that volume of
validated JSON in one shot at this speed.

**Option C (split into focused passes) is now required, not optional.** The schema
complexity and prompt scaffolding were both supposed to stay manageable; one call
carrying five output collections broke that assumption.

Suggested split:
1. **elements + containment** — the C4 structure
2. **connections** — runtime edges
3. **technology_stacks + architecture_styles** — the constructs
4. **traceability refs** — requirement/initiative references

Each prompt stays short, each schema small, and each call fast enough to be
bounded. Also fixes the dilution problem below, since each pass carries a fraction
of the current rules.

### Guarding — three layers, only one of which bounds a single call

| Guard | Bounds | Does not bound |
|---|---|---|
| `max_structured_turns` | retry loops | a single slow call (never advances a turn) |
| `structured_timeout_seconds` | **between-turn** stalls only | an in-flight generation |
| `request_timeout_seconds` | ✅ any single request, at the transport layer | — |

Learned the hard way: `cancel_signal` is checked **between turns**, so a single
long generation is uninterruptible from Python. Only the HTTP client's own timeout
aborts it. `request_timeout_seconds` (default 300s) is now passed to the model
client's `client_args` — verified aborting an oversized generation and falling
back cleanly.

### Known limitation of the fallback path

`technology_stacks` and `architecture_styles` come only from the structured schema,
so a text-parsing fallback yields **0 for both** — verified. The text path extracts
triples (and derives elements from them) but has no notion of these collections.
Another reason to split passes (option C): a focused pass per construct keeps each
one extractable independently of whether the mega-schema validates.

---

## 8. Team / Organisational Unit construct (PARKED)

**Status:** Parked — decided during review, not for now
**Priority:** Medium (deferred)
**Area:** `ontology/enterprise_structure.yaml`

### Why it matters

Ownership is the dimension that makes `SoftwareSystemClass` *mean* something.
Today ownership is `ArchitectureElement.owner: str` and
`Application.team_ownership: str` — free text, unqueryable, unvalidatable.

With a Team construct these become possible:

- "Which systems does the Payments team own?" — a real EA question today unanswered
- Cross-cutting vs domain ownership becomes **derived** from the owning team's
  remit, rather than asserted per system
- `ENTERPRISE_TECHNOLOGY_PLATFORM` stops being a label and becomes a consequence
  of being owned by a cross-cutting function
- Vendor/origin analysis can be paired with ownership: who is accountable for the
  single-vendor dependencies?

### Shape when picked up

A `Team` (or `OrganisationalUnit`) class in `enterprise_structure.yaml`, alongside
`Stakeholder`, with: id, name, remit (`DOMAIN` | `CROSS_CUTTING` | `PLATFORM` |
`SHARED_SERVICES`), parent unit (for nested orgs), and external references (HR/org
registry — the `ExternallyReferenced` mixin already exists).

Then `ArchitectureElement.managed_by`, `EnterpriseConstruct.owner`, and
`Application.team_ownership` become references rather than strings.

### Why parked

Adds a construct and a dimension of change to three layers for a benefit that is
real but not blocking. The `system_class` enum carries the cross-cutting signal
adequately for now; this makes it rigorous later.

### Note the pattern

This is the third "give it a proper class rather than filtering it" change —
after `TechnologyStack` and `Platform`. Excluding noise keeps failing; classifying
it keeps working.

---

## Reference: current extraction result

Model: `unsloth/Qwen3.5-4B-GGUF:Q4_K_M` via llama.cpp `:8080`
Input: `test_data/prd/sample_requirements.md` (REQ) / `test_data/arch/payment_platform_arch.md` (ARCH)

| | REQ-G | ARC-G (draft) |
|---|---|---|
| Triples | 68 | 61 |
| Nodes | 53 entities (16 with requirement ids) | 21 elements ⚠ |
| Edges | — | 26 connections, 6 `part_of` |
| Traceability to the other graph | — | captured as refs, not yet resolved (item 5) |

⚠ 21 elements includes 7 technologies/patterns that regressed into elements —
see item 7. The genuine architecture element count is 14, all correctly classified.

ARC-G containment now captured: 6 `part_of` triples (the 6 microservices → the
platform) plus `parent` on every container/datastore. The containment validator
flaggged 2 real gaps on first run (PostgreSQL and Valkey declared a parent with no
matching triple) — the deterministic-check pattern working as intended.

ARC-G element classification is correct across all 15 elements (verified:
Container / DataStore / ExternalSystem / DeploymentNode / SoftwareSystem).
Technologies are correctly predicates (`uses_technology: 14`) and deployment is
`deploys_on: 9`, not connections.

