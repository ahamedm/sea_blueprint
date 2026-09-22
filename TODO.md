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

## 5. ARC-G ⇄ REQ-G linkage — Initiative-scoped reconciliation

**Status:** In progress — a bulk resolution path now exists (item 14); the
resolution pass itself is still outstanding
**Priority:** **Critical** — this is the platform's core purpose, currently unmet
**Area:** `agents/knowledge_extraction/`, `agents/architecture_extraction/`, ontology, `core/knowledge/ingest.py`

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

### New approach: Initiative as the primary scoping identifier

Relying on document names or input source names is fragile. `Initiative` (from
`requirements_base.yaml`) is the stable, semantic anchor that spans both graphs:

- **REQ-G:** Requirements are linked to their authorising `Initiative` via `authorised_by_initiative`.
- **ARC-G:** Architecture elements are linked to the `Initiative` they deliver via `delivers_initiative`.
- **The Join:** We can now ask: *"Which architecture elements are delivering the same Initiative that authorised these requirements?"*

This makes cross-reference robust even when requirement IDs are lost or paraphrased.
If both a requirement and an architecture element point to `INIT-2024-001 (Payment Modernisation)`,
they belong in the same reconciliation scope.

**Implemented:** `core/knowledge/ingest.py` now captures `Initiative` nodes and
creates `delivers_initiative` / `authorised_by_initiative` assertions during ingestion.

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

## 9. Architecture gaps — canonical model, correction merge, incompleteness

**Status:** Analysed, not started
**Priority:** High — these are load-bearing, not polish
**Full analysis:** [`docs/architecture-review.md`](docs/architecture-review.md)

A sanity check of the proposed system architecture (agents / workflow / two UIs /
MCP servers / API) surfaced two gaps that force decisions everything else depends
on. Recorded in full in the linked document; the actionable core:

**9a. Canonical knowledge model + owned serialisation layer. — ✅ DONE**
`core/knowledge/` implements the model, the ingest transform and RDF emission.
28/28 checks pass (`scripts/test_knowledge_layer.py`). View projection layer
proven via CLI table view (`scripts/review_assertions.py`) — exposes all assertions
with provenance for human review. Still to do: wiring the agents to emit the
canonical model rather than JSON dicts, and C4 / gap-report view models.

Verified on real output: ARC-G ingests to 31 nodes / 156 assertions / 0 dangling /
13 unresolved cross-graph references; 2,030 RDF triples. Two findings from the
exercise are below.

*Finding — the requirements extraction emits 3.5x duplication.* 169 triples
collapse to **48 unique facts**, each repeated up to 4x. The architecture agent
deduplicates via its chunk/pass merge; the requirements agent is still single-call
and has no merge, so duplicates survive. Content-addressed assertion ids collapse
them at ingest, but the extraction itself should stop producing them.

*Finding — completeness must be UNKNOWN, not FAILED, when pass data is absent.*
The requirements output carries no per-pass metadata, and treating that as FAILED
is a false alarm — while treating it as COMPLETE would be a false assurance, which
is worse. `RUN_UNKNOWN` is now distinct from both, and reaches the RDF so a
consumer can honour it. The requirements agent should emit per-pass records.

**9a-i. Canonical knowledge model + owned serialisation layer.**
Five representations are already in play (Markdown → Pydantic → LinkML → RDF →
UI views) and no component owns the transformations between them. The implied gap
is:

```
extraction output → ??? → Jena → SPARQL → UI
```

That `???` is the highest-risk component in the system: every extraction change
breaks it, it is where `confidence`/`source_text` must land on reified
assertions, and it is the only thing that can guarantee a well-formed graph. A
named part with tests, not an implementation detail.

**9b. Human corrections vs re-extraction — ✅ PROVEN**
The canonical model structurally distinguishes agent-asserted from human-confirmed
knowledge via `Provenance.source_type` and `Assertion.status`. The merge logic in
`add_assertion()` ensures human corrections survive agent re-observations. Proven
via `scripts/review_assertions.py` — interactive CLI allows marking assertions as
VERIFIED or CORRECTED, with corrections persisting in the graph. Next: wire this
into a proper UI and implement the diff-and-review workflow for re-extractions.

**9c. Incompleteness must be representable. — ✅ IMPLEMENTED**
`ExtractionRun.completeness` tracks COMPLETE / PARTIAL / FAILED / UNKNOWN per run.
`PassRecord` captures per-chunk outcomes. This prevents partial extractions from
being mistaken for complete ones, which would cause the auditor to report missing
content as architectural gaps. Verified: completeness state reaches the RDF so
consumers can honour it.

**9d. Revisions.** The PRD requires evolving requirements/architecture. Nothing
diffs or versions. Named graphs would carry it; no component owns it.

**Also worth settling:** orchestrator vs workflow (recommendation: **workflow**
with explicit human gates — a dynamic router adds non-determinism to a product
selling determinism), agents stateless with the graph as memory, one validation
service consumed by both UI and API, and a stage→validate→commit write path.

### Sequencing — REORDERED by the user journey

`docs/user-journey.md` reorders this. The journey is blocked at the **human
review gate**, not at extraction: knowledge can be produced but has no route to a
person and no route onward. The pipeline's only exit is a JSON file that only the
test harness reads (verified).

Blocking, in dependency order:

1. **View projection layer** — nothing to look at; every downstream step needs it
2. **Correction write path** — the review gate has nowhere to record a decision
3. **Revision / baseline** — without it "verified" is a flag on a mutable graph
4. **Reconciliation** — the core job; 13 unresolved refs already surface and go nowhere
5. **Audit engine + gap report** — the product

Demoted: the requirements pass-split was going to be next. It improves step 2,
which already works. Quality, not blocking.

### Original sequencing (superseded)

1. Canonical model + serialisation layer — nothing else is testable without it
2. Validation as a service, rules **generated from the ontology**
3. Correction write path + merge semantics — hardest, so before UI depends on it
4. Validation/correction UI
5. Workflow (scriptable first; four passes do not need an engine)
6. MCP servers — good encapsulation, not load-bearing early
7. Interaction UI — last, least risky, most likely to change

**Build UIs last.** They are the most tempting to start with and the most likely
to lock in data-model decisions that should be deliberate.

---

## 10. Adopt RDF for the knowledge layer (rdflib first, Jena later)

**Status:** Analysed, not started
**Priority:** Medium — staged behind extraction reliability
**Full analysis:** [`docs/architecture-review.md`](docs/architecture-review.md) Part 2

**Why:** makes the *audit* deterministic while leaving extraction as-is, and its
biggest value is diagnostic — a fixed query over a varying graph does not hide
extraction non-determinism, it **exposes** it. Also makes ARC-G ⇄ REQ-G a SPARQL
join (item 5) and gives named graphs for revision diffing (item 4).

**Tool:** `rdflib 7.6.0` and `SPARQLWrapper 2.0.0` are already installed.
`pyshacl` would be needed. rdflib + pySHACL delivers most of the benefit with no
service boundary and no second toolchain; `SPARQLWrapper` already bridges to a
Jena Fuseki endpoint later, so choosing rdflib now does not foreclose Jena.

**Verified:** LinkML generates SHACL from our ontology (242,561 chars from
`architecture_base.yaml`), including `sh:closed true`. **Caveat:** generation
fails on any layer with `imports:` (`KeyError` on the imported schema name);
workaround is `SchemaView(...).merge_imports()` before generating.

**The trap:** SHACL is closed-world (absence is a violation — right for gap
auditing); OWL is open-world (absence entails nothing — a gap query finds nothing,
ever, silently). **SHACL for the audit, OWL for entailment.**

### Do not start this until

Extraction reliability is proven by the harness. A precise reasoning layer over a
lossy graph produces **rigorously-derived wrong answers** — worse than fuzzy ones,
because precision implies trust. **The `req_prd` `ontology_class` coverage
invariant is currently failing and is the gate.**

---

## 11. Domain ontology layer — the ontology of the SUBJECT MATTER, not the artifact

**Status:** Not started — **flagged CRITICAL for review**
**Priority:** High
**Area:** new `ontology/domains/`, new Domain Context Agent, extraction grounding

### What is missing

We built the ontology **of requirements**, not the ontology of the **domain being
required**. `requirements_base` describes the engineering artifact — Requirement,
Goal, Capability, Stakeholder, Process. A Payment Processing domain ontology
describes the subject matter — Payment, Card, PAN, Authorization, Capture,
Settlement, Chargeback, Merchant, Acquirer, Token.

One is the vocabulary of *what we build and how we reason about it*. The other is
the vocabulary of *what the business is actually about*. The meta-level exists;
the content does not.

### Evidence it is structurally absent, not merely unfinished

```
PRD agent #2     Domain Context Agent — "propose ontological structures for a
                 new domain", "suggest initial concepts and relationships"
PRD NFR 5.4      adding a new domain "requires only the creation of a new Domain
                 Context Agent specialization and a corresponding set of LinkML
                 modules"
Built            sea_common, enterprise_structure, requirements_base,
                 architecture_base
Not built        domains/<name>.yaml, and agent #2 itself
```

**The tell is in the flow:** `domain` is read from `input_data`, logged, and
written to metadata by **both** extraction agents — and never reaches a prompt or
a schema. It does nothing. That is the signature of a layer that was assumed
rather than built: the interface exists, the substance does not.

**And `DomainConcept` is the catch-all it was meant to replace.** From the real
requirements output:

```
DomainConcept  contains:  Tenancy Identifier, Payment Request Status,
                          Primary Payment Gateway, Secondary Payment Gateway,
                          Spring Boot, AlpineJS
```

Two **frameworks** classified as domain concepts. A generic placeholder absorbs
whatever does not fit, and in doing so hides mis-classification. Separately, **20
of 47 entities (43%) carry no ontology class at all.** With a domain vocabulary
to map onto, both become findings rather than silence.

### Why this is critical — the coverage question

A requirements document implicitly claims to cover a domain. Whether it does is
**currently unmeasurable**, because there is no reference frame. The auditor can
find gaps *internal* to the requirements (orphans, broken traceability) because
those are relations within one graph. It cannot ask "does this requirement set
cover the subject matter?" because the subject matter is nowhere represented.

With a domain ontology, four questions become measurable — and **the mismatches
run both ways**:

| | Finding |
|---|---|
| Domain concept present in the requirements | covered |
| Domain concept absent | deliberate exclusion, or an oversight |
| Requirement references a concept not in the domain ontology | ontology incomplete, **or** the requirement is confused |
| Requirement references a control as if it were a domain entity | mis-classification (the `Spring Boot` case) |

The third and fourth are the ones usually missed: requirements also **invent**
things the domain does not have, and conflate controls with entities. Both
directions are findings, and neither is expressible today.

### It makes reconciliation triangular, not pairwise

```
REQ-G  ⇄  ARC-G    does the design answer the requirement?          (item 5)
REQ-G  ⇄  DOMAIN   does the requirement set cover the subject matter?    (new)
ARC-G  ⇄  DOMAIN   which domain concepts has the design ignored?         (new)
```

The third leg is the valuable one, and it is unreachable by any other means.
*"You have built authorisation and capture, but nothing anywhere handles
chargebacks"* is a **domain-coverage** finding, not a traceability finding — no
amount of REQ⇄ARC reconciliation surfaces it, because nothing in the requirements
ever mentioned chargebacks. There is no requirement to be unimplemented.

### Where it sits

Topmost, most-specific layer. It must import `requirements_base` to subclass
`DomainConcept`:

```
sea_common → enterprise_structure → requirements_base → architecture_base
                                                             ↑
                                       domains/payment_processing.yaml
```

Extraction then points at the domain ontology instead of the base and inherits
everything below by import — which the existing import-resolution in
`_collect_ontology_names()` already handles. **No restructuring required.**

### What it takes

1. **`ontology/domains/payment_processing.yaml`** — domain concepts subclassing
   `DomainConcept`, their relationships and constraints, and the payment
   lifecycle state machine (PENDING → AUTHORIZED → CAPTURED → SETTLED).
2. **The Domain Context Agent** (PRD #2) — the component that *proposes* a domain
   ontology from business context. Without it, every new domain is hand-authored.
3. **Wire `domain` through** — inject the domain vocabulary into the extraction
   prompt so concepts map to real classes instead of collapsing to a placeholder.
4. **A coverage audit** — the third reconciliation leg plus the both-ways
   mismatch report.

### Ordering note

Its value is realised **through the coverage audit**, which needs the audit engine
that does not yet exist (item 9, gap 5). So building the domain ontology before
the audit engine produces a vocabulary nothing consumes.

**Recommended:** record now (done), build after the review gate and audit engine
— unless domain grounding is wanted earlier purely for extraction precision, in
which case step 3 alone is independently useful and much smaller than the whole.

### Why it lingered

**Its absence causes imprecision, not error.** Nothing fails. Extraction works;
the graph is simply coarser than it should be, and coverage questions silently
cannot be asked. The same silent-absence pattern as the corrupted enum
descriptions and the inert subsets: no symptom, so no pressure.

It also sits outside both the build order and the workflow — which is why neither
the sequencing nor the user journey surfaced it.

---

## 12. C4 notation parser — deterministic extraction from structured architecture sources

**Status:** Not started — **flagged CRITICAL**
**Priority:** Critical
**Area:** new `agents/extraction/c4_parser.py`, integration with architecture extraction agent

### What is missing

Architects don't write prose markdown for architecture. They write **C4 notation** in PlantUML, Structurizr DSL, or Mermaid. These are **structured, parseable, and already contain the C4 elements with their metadata.**

We're currently using LLM extraction on prose for something that's already structured. That's the mismatch.

**Example — PlantUML:**
```plantuml
@startuml
!include https://raw.githubusercontent.com/plantuml-stdlib/C4-PlantUML/master/C4_Container.puml

Person(user, "Customer", "A customer of the bank")
System_Boundary(banking, "Banking") {
    Container(web, "Web Bank", "JavaScript and Angular", "Allows customers to check accounts")
    Container(db, "Database", "Oracle", "Stores customer information")
}
Rel(user, web, "Uses", "HTTPS")
Rel(web, db, "Reads from", "JDBC")
@enduml
```

**Example — Structurizr DSL:**
```structurizr
workspace {
    model {
        user = person "Customer"
        bankingSystem = softwareSystem "Banking" {
            webapp = container "Web Bank" "Allows customers to check accounts" "JavaScript and Angular"
            database = container "Database" "Stores customer information" "Oracle"
        }
        user -> webapp "Uses" "HTTPS"
        webapp -> database "Reads from" "JDBC"
    }
}
```

Both are **deterministic, parseable, and already typed.** They're not prose — they're domain-specific languages with precise semantics.

### Why this matters

**Current flow:**
```
prose MD → LLM extraction → canonical graph
```

**Proposed flow:**
```
C4 notation → deterministic parser → canonical graph
prose MD → LLM extraction → canonical graph (fallback only)
```

**Key benefits:**

1. **Deterministic** — no LLM non-determinism for architecture. Same input → same output, every time.
2. **Complete** — all elements and connections captured. No silent drops like we've seen with responsibilities and monitoring platforms.
3. **Fast** — parsing is milliseconds, not minutes. No 80-second calls.
4. **Accurate** — technology, description, relationships all preserved from the source.
5. **Testable** — parser output is deterministic, easy to verify with unit tests.

This directly addresses the item 7 problem (prompt dilution causing silent drops) by eliminating the LLM step for architecture entirely.

### Implementation options

**Option A: Add a C4 parser module**

Create `agents/extraction/c4_parser.py` that handles multiple C4 dialects:

```python
class C4Parser:
    def parse_plantuml(self, text: str) -> KnowledgeGraph:
        # Parse PlantUML syntax
        # Map Person → Node(kind="Person")
        # Map Container → Node(kind="Container")
        # Map Rel → Assertion(predicate="connects_to")
        # Preserve technology, description, etc.
        
    def parse_structurizr(self, text: str) -> KnowledgeGraph:
        # Parse Structurizr DSL or JSON
        
    def parse_mermaid(self, text: str) -> KnowledgeGraph:
        # Parse Mermaid C4 syntax
```

**Pros:**
- Clean separation of concerns
- Can support multiple C4 dialects
- Deterministic and testable

**Cons:**
- Need to implement parsers for each dialect
- PlantUML parsing is non-trivial (need ANTLR grammar or plantuml CLI)

**Option B: Use existing tools**

**PlantUML:**
- Use `plantuml` CLI to export to JSON/XML, then parse
- Or use an existing Python PlantUML parser

**Structurizr:**
- Use Structurizr CLI to export to JSON
- Or parse the DSL directly (simpler syntax)

**Mermaid:**
- Parse directly (simpler syntax)
- Or use mermaid-cli to export

**Pros:**
- Leverage existing tooling
- Faster to implement

**Cons:**
- External dependencies
- May not preserve all metadata

**Option C: Hybrid approach**

- Use existing tools where available (Structurizr JSON export)
- Implement custom parsers for others (PlantUML, Mermaid)
- Fall back to LLM extraction if parsing fails

**Pros:**
- Best of both worlds
- Robust (fallback to LLM)

**Cons:**
- More complex

### Canonical mapping

The parser needs to map C4 elements to the canonical model:

| C4 Element | Canonical Model |
|---|---|
| `Person` | `Node(kind="Person", label=...)` |
| `SoftwareSystem` | `Node(kind="SoftwareSystem", label=...)` |
| `Container` | `Node(kind="Container", label=..., attributes={"technology": ..., "description": ...})` |
| `Component` | `Node(kind="Component", label=...)` |
| `Rel` | `Assertion(subject=..., predicate="connects_to", object=..., attributes={"description": ..., "technology": ...})` |

**Metadata preservation:**
- Technology → `Node.attributes["technology"]` or `Assertion.attributes["technology"]`
- Description → `Node.attributes["description"]` or `Assertion.attributes["description"]`
- Tags → `Node.tags`
- Boundaries → containment relationships

### Integration with existing pipeline

The C4 parser should produce a `KnowledgeGraph` directly, bypassing the extraction agent:

```python
# Current flow
agent = create_architecture_extraction_agent()
result = agent.run({"document": prose_md, ...})

# New flow
parser = C4Parser()
graph = parser.parse_plantuml(plantuml_text)
# or
graph = parser.parse_structurizr(structurizr_text)
```

Or integrate into the agent:

```python
class ArchitectureExtractionAgent:
    def run(self, input_data):
        document = input_data["document"]
        
        # Detect format
        if self._is_plantuml(document):
            parser = C4Parser()
            return parser.parse_plantuml(document)
        elif self._is_structurizr(document):
            parser = C4Parser()
            return parser.parse_structurizr(document)
        else:
            # Fall back to LLM extraction
            return super().run(input_data)
```

### Testing strategy

1. **Parser tests** — deterministic, fast
   - Input: PlantUML/Structurizr/Mermaid text
   - Output: KnowledgeGraph
   - Assert: correct nodes, assertions, metadata

2. **Integration tests** — C4 → canonical → RDF
   - Input: C4 file
   - Output: RDF graph
   - Assert: correct triples, assertion resources

3. **Sample files** — need real C4 examples
   - PlantUML C4 diagram
   - Structurizr DSL workspace
   - Mermaid C4 diagram

### Trade-offs

**What we gain:**
- Deterministic architecture extraction
- No silent drops
- Fast (milliseconds vs minutes)
- Complete metadata preservation

**What we lose:**
- Flexibility (can't extract from arbitrary prose)
- Need to support multiple C4 dialects

**What stays the same:**
- Requirements extraction still uses LLM (requirements are typically prose)
- The canonical model, validators, RDF emission — all unchanged

### Recommendation

**Implement the C4 parser as the primary path for architecture extraction.**

1. Start with **Structurizr JSON** (easiest to parse, already structured)
2. Add **PlantUML** support (most common C4 notation)
3. Add **Mermaid** support (growing in popularity)
4. Keep LLM extraction as fallback for prose docs

This addresses the fundamental mismatch: we're using LLM extraction on something that's already structured. The C4 parser makes architecture extraction deterministic, complete, and fast.

### Implementation priority

This should be **critical** — not because it's blocking the current workflow, but because it's the right way to do architecture extraction. The current LLM-based approach works, but it's solving the wrong problem.

**Effort:** Medium. Structurizr JSON parsing is straightforward. PlantUML parsing is more complex but doable. Mermaid is simpler.

**Timeline:** Can be done in parallel with the review gate work, since it's independent.

---

## 13. MVP UI — extraction projection, review gate, change management

**Status:** ✅ IMPLEMENTED (first slice of the journey) — see
[`docs/ui-review-workflow.md`](docs/ui-review-workflow.md)
**Priority:** Closed for this slice; follow-ups below
**Area:** `app/`, `core/knowledge/` (`serialise.py`, `review.py`, `store.py`), `tests/`

### What this closes

`docs/user-journey.md` §3: *"extraction output has nowhere to go once produced.
The pipeline's only exit is a JSON file."* It now has somewhere to go — a
projection to judge, a write path to record judgement, and revisions to judge
against. Three of the journey's five blocking gaps (1–3) are addressed:

| Journey gap | Status |
|---|---|
| 1. View projection layer | ✅ `/`, `/review`, `/graph`, `/gaps`, `/changes`, `/changes/diff` |
| 2. Correction write path | ✅ verify / correct / dispute / reopen + audit trail |
| 3. Revision / baseline | ✅ working set vs immutable revision vs frozen baseline |

Still open, in dependency order: **4. Reconciliation** (item 5) and **5. Audit
engine + gap report** (item 9) — `/gaps` does the structural half only.

### Bugs fixed

1. **Ingest produced an empty graph and reported success.** `app/__init__.py`
   passed `result.model_dump()` (the `AgentResult` envelope) to
   `graph_from_extraction`, which reads `triples`/`elements` from the top level
   and therefore found nothing. Now unwraps `result.output`. A characterisation
   test pins the failure mode.
2. **Document type was read and ignored.** The form collected `type` and always
   ran the requirements extractor. Now selects the REQ-G or ARC-G agent.
3. **`/graph` and `/gaps` were nav links with no routes** — hard 404s. Both now
   exist, plus `/changes` and `/changes/diff`.
4. **Graph state was a module global.** Every decision was lost on restart and two
   workers could not agree on what the graph was. Now persisted per request.
5. **`/graph` would also have 500'd** — the nav called `url_for('graph')` while the
   endpoint was `graph_view`. The route is now `/c4` (endpoint `c4`) with `/graph`
   kept as a redirect: "graph" in this project means the knowledge graph, so the
   URL was itself part of the projection/viewpoint confusion recorded below.

### New knowledge-layer modules

- `serialise.py` — field-complete JSON round trip. The architecture review called
  this transform the highest-risk component and required a named part with tests.
  It did not exist; nothing could persist a graph.
- `review.py` — decisions, audit trail, `ReviewProgress`, baseline promotion.
  Correction is **supersession, not mutation**, so lineage survives.
- `store.py` — working set / revision / baseline, with `BaselineNotReady` as the
  freeze gate. Ordering is by insertion, not `created_at` (second precision would
  order same-second commits arbitrarily).
- `ingest.merge_graphs` — re-extraction **merges** rather than replaces, so human
  corrections survive a re-run (architecture-review §3.3, the "sleeper").

### Verification

`tests/` was empty; it now holds 246 tests covering the knowledge layer, the
projections and every route, running against an injectable fake extractor so the
suite needs no model server. All routes verified 200 against a real booted
server.

### Follow-ups (deliberately not in this slice)

1. **Reconciliation at scale** — see item 14. `/reconcile` binds to existing nodes
   only: it does not create a missing target, does not invert the direction, and
   matches lexically.
2. **Semantic audit** — all checks are structural. "Does this design answer this
   requirement?" is unimplemented.
3. **Authentication / multi-user review** — one reviewer identity from config.
   Open question 1 in the journey (is Reviewer/Auditor a distinct persona?) is
   still unanswered, and it decides whether review is inline or a queue.
4. **Concurrent writers** — reads/writes hit disk per request. Fine for a
   single-process MVP; a multi-worker deployment needs locking or a real store.
5. **Correction merge conflicts** — supersession is sufficient while one document
   owns a graph. Item 9b returns the first time knowledge arrives from two sources.
6. **The graph view is a depiction, not a diagram editor** — no layout persistence,
   no manual arrangement, no write-back from the canvas.

---

## 14. Bulk reference resolution — reconciliation, first slice

**Status:** ✅ IMPLEMENTED — see
[`docs/ui-review-workflow.md`](docs/ui-review-workflow.md) §4
**Priority:** Closed for this slice; follow-ups below
**Area:** `core/knowledge/reconcile.py`, `core/knowledge/ingest.py`, `app/`

### What this closes

Item 5 called ARC-G ⇄ REQ-G linkage *"the platform's core purpose, currently
unmet"*. It is still unmet in full, but unresolved references now have a place to
go: a projection that proposes targets, a decision that records the binding, and a
bulk path that is honest about what it declines.

| | |
|---|---|
| Unresolved references | 13 |
| Resolvable at the default 0.75 threshold | 1 |
| Below threshold | 3 |
| No candidate of the expected kind | 9 |
| Predicate looks wrong | 2 |

### Design decisions worth keeping

1. **Kind scoping is mandatory.** Measured on the real ARC-G output, unscoped
   best-match picks `Concept:'Card Payment Processing'` (0.94) over the correct
   `BusinessCapability:'Unified Payment Processing'` (0.92) for
   `supports_capability → 'Payment Processing'`. Lexical similarity alone prefers
   the wrong kind of thing, so every predicate declares the kinds it may point at
   and bulk resolve never crosses them. An override is available and recorded.
2. **Resolution is not `review.correct()`.** `correct()` refuses to turn a
   cross-graph reference into a node, because doing that silently erases the
   resolved/unresolved distinction. Resolution is the deliberate opposite act.
3. **Bulk reports what it declined.** `below_threshold`, `no_candidate` and
   `unknown_ids` are returned and surfaced, not swallowed. A wrong traceability
   link is worse than a missing one.
4. **`mislabel_suspected`** — a strong match in the wrong kind means the predicate
   is probably wrong. On the real data, two `traces_to_goal` references score 0.90
   and 0.86 against `FunctionalRequirement`s with no `BusinessGoal` candidate at
   all: the extractor attached a goal predicate to a function name. This is an
   extraction finding surfaced by reconciliation, and it is worth acting on
   upstream (item 7 / item 12).

### Bug found and fixed while building this

**Requirement IDs never reached the graph.** The requirements profile emits
`requirement_id`; `ingest._collect_declared_nodes` read only the architecture
profile's `external_references`. So the document's own stable key was discarded at
ingest — meaning root cause 1 of item 5 was still live *below* the extractor, and
reconciliation's strongest signal was structurally unreachable no matter how good
the extraction got. `ingest._external_refs` now reads both shapes.

**Caveat, stated plainly:** the saved fixture in `data/output/` carries **zero**
`requirement_id` values (0 of 54 entities) and an empty `references` collection,
because it predates that work. The fix is therefore forward-looking for this data;
`test_a_preserved_id_joins_two_documents_end_to_end` proves the path works end to
end when a key does survive.

### Follow-ups

1. **Invert the direction.** All unresolved references currently run ARC → REQ.
   REQ-G emits no cross-graph predicates, so "requirements with no architectural
   answer" cannot be computed at all. It is the inversion of the resolved links,
   and it needs links that resolve first.
2. **Create the missing target.** Deliberately not offered: resolution asserts the
   referent was already extracted. A separate, explicitly different action should
   handle "the architecture references a requirement the document never stated" —
   which is itself a finding, not a binding.
3. **Semantic matching.** Lexical candidates cannot bridge a paraphrase with no
   shared vocabulary. Embedding or model-assisted proposals belong here, with the
   same propose/decide split and the same audit trail.
4. **Surface the mislabel finding upstream.** `traces_to_goal` carrying function
   names is an extraction defect (item 7's pass-split, or item 12's deterministic
   C4 parser).
5. **Coverage reporting.** `RequirementRealization` carries `coverage` and
   `evidence` in the ontology; resolution currently sets `ontology_class` but
   populates neither.

---

## 15. Split graph projection from architecture viewpoints

**Status:** ✅ IMPLEMENTED — see
[`docs/ui-review-workflow.md`](docs/ui-review-workflow.md) §8
**Priority:** Closed
**Area:** `app/projections.py`, `app/viewpoints/`, `app/templates/c4.html`

### The conflation

"Project the graph" and "project the architecture as a C4 view" were one module,
one entry in the docs' module map, and one nav label ("Graph"). They are not the
same thing:

| | Graph projection | Architecture viewpoint |
|---|---|---|
| Question | make the graph readable and judgeable | describe the architecture in a recognised notation |
| Knows about | assertions, confidence, provenance, filters | C4 levels, element kinds, element detail |
| Changes when | the knowledge model changes | the notation, or the views offered, changes |
| Domain-specific | no | yes |

### What changed

1. `app/views.py` → **`app/projections.py`**, renamed for what it is (Flask
   *routes* are the views). Its C4 content was removed.
2. **`app/viewpoints/`** added, holding `c4.py`. The C4 decisions — level→kind
   tables, which facts are element detail, what the renderer receives — now live
   beside the notation they describe.
3. The fused work was split by concern:
   - notation-agnostic → new primitives in `projections.py` (`node_records`,
     `edge_records`, `literal_facts`), which any viewpoint composes;
   - C4-specific → `viewpoints/c4.py`, which now *selects* instead of
     re-deriving. `project_c4_context` → `c4_view`.
4. `/graph` → **`/c4`**, endpoint `c4`, nav label **"C4 view"**. `/graph` is kept
   as a 301 redirect. `/api/graph/c4` → `/api/c4`.
5. `graph.html` → `c4.html`. Its page copy was describing *generic assertion
   flattening* — the projection layer's job — on a page about C4. Rewritten to
   explain what a C4 level is and why it deliberately omits elements.
6. Tests split to mirror the layers: `test_views.py` → **`test_projections.py`**
   plus **`test_viewpoint_c4.py`**, which covers the new primitives.

### Guards against re-fusing

- `test_projection_layer_does_not_own_architecture_notation` — no C4 level tables
  and no import of a viewpoint from the projection layer.
- `test_the_viewpoint_composes_projection_primitives` — the viewpoint must call
  `node_records`/`edge_records`/`literal_facts` and must not walk `graph.active()`
  itself, because a second implementation of assertion flattening is a second
  thing to keep correct.

### Consequence worth keeping

A viewpoint is a **deliberate reduction**: at C4 context level a `Container` is
real, is in the graph, and is not drawn. The view therefore reports
`excluded_kinds`, so "not at this level" is never mistaken for "not in the graph".
The same reasoning is why the direction is one-way — viewpoints compose
projections, never the reverse.

---

## 16. Ontology reference view — a browsable view of the four schemas

**Status:** ✅ IMPLEMENTED — see
[`docs/ui-review-workflow.md`](docs/ui-review-workflow.md) §8, §8a
**Priority:** Closed
**Area:** `core/ontology.py`, `app/ontology_reference.py`,
`app/templates/ontology.html`, `tests/test_ontology*.py`

### Why

The four foundational ontologies are the vocabulary every extracted fact is
expressed in, and the only way to read them was the YAML plus a README whose tables
have drifted — it still calls `architecture_base.yaml` "(future)" and never mentions
`sea_common.yaml`. A list of class names is not comprehension. What actually explains
these schemas is structural: the import chain, `is_a` versus mixins, inheritance, and
which slots point at other classes.

### The third kind of view

This is **not** graph projection and **not** an architecture viewpoint; it reads the
LinkML *schemas*, not the extracted graph. Two of those three were fused in one module
until item 15 split them, so this one was placed and guarded deliberately:

| Module | Reads |
|---|---|
| `app/projections.py` | the instance graph (ABox) |
| `app/viewpoints/c4.py` | the instance graph + a notation |
| `core/ontology.py` + `app/ontology_reference.py` | the LinkML schemas (TBox) |

`core/ontology.py` imports nothing from `core.knowledge` or `app`, so the Ontology
Engineer and Domain Context agents can use it without the web layer. Guarded by
`test_the_loader_reads_schemas_and_not_the_graph`.

The schema and the instance graph meet in exactly one place — the `instances` column
on `/ontology` — and that join lives in the view layer so the loader never learns
about graphs.

### What it shows

- **The one-way import chain**, which is why the layers exist at all.
- **`is_a` versus `mixins`**, kept visually distinct. Each layer's abstract root mixes
  in `ExternallyReferenced` and `Provenanced`, so every class beneath inherits identity
  and provenance — but a mixin is not a taxonomy edge and is not drawn as one.
- **Inheritance, resolved.** `NonFunctionalRequirement` carries 33 slots of which 7 are
  its own; the slot table separates own from inherited and names the declaring class,
  because "`id` from `Requirement`" is a different fact from a local slot.
- **Relationships versus hierarchy.** Slots whose `range` is a class are the real
  concept-to-concept links (150 of them).
- **Binding classes** (`SubProductScope`, `SystemCapabilityBinding`) that exist only to
  break a circular import, stated as such rather than silently omitted.
- **Integrity findings** — unresolved supertypes and slot ranges whose type is neither
  a class, an enum, nor a primitive. Currently zero.

The focus drawing uses **semantic rows** (supertypes above, subtypes below, relationship
targets and incoming references further out) rather than a force simulation: position
means something, whereas a hairball of 58 classes teaches nothing.

### Verified against LinkML

The loader's own resolution is checked against LinkML's authoritative parser for
**every one of the 58 classes** — ancestors and effective slot sets
(`test_resolution_matches_linkml`). Asserting against hand-written expectations would
only prove the loader is consistently wrong. It agrees exactly.

### Measured state

| | |
|---|---|
| Classes | 58 (4 common, 7 enterprise, 28 requirements, 19 architecture) |
| Enums / subsets | 37 / 13 |
| Slots declared (whose range is a class) | 457 (150) |
| Unresolved supertypes / ranges / duplicates | 0 / 0 / 0 |

### Findings

1. **A class's direct supertype row shows only `is_a` plus its own mixins.** Mixins are
   not repeated down the tree — they hang off each layer root. That is good design, and
   it is exactly why `ancestors()` must include mixins: otherwise
   `NonFunctionalRequirement` appears to have 7 slots instead of 33.
2. **`ontology/README.md` has drifted** and is now flagged as such at the top. Fine to
   keep as history; the live view cannot drift.
3. **Coverage is the useful cross-reference.** Classes with 0 instances explain why an
   empty view elsewhere is empty — a requirements-only graph has no C4 elements, so
   `/c4` is legitimately blank. The page makes that visible instead of leaving a reader
   to guess.

### Follow-ups

1. **The Ontology Engineer and Domain Context agents are still stubs.** This loader is
   the first real capability they need; wiring them is item 11's territory.
2. **No editing.** The page is read-only by design — schema changes belong in the YAML
   and through the (unbuilt) Ontology Engineer agent, not in a form that silently
   diverges from the file.
3. **Domain ontologies** (`ontology/domains/*.yaml`, item 11) are not placed as a layer.
   When they arrive, `LAYER_ORDER` needs branches rather than a single chain.

---

## 17. Move the shared domain layer out of agents/ into core/

**Status:** ✅ IMPLEMENTED
**Priority:** Closed
**Area:** `core/`, `pyproject.toml`, `AGENTS.md`, `tests/test_layering.py`

### Why

`agents/knowledge/` and `agents/ontology.py` were not agents. Neither was an agent
runtime concern, and **at the time of the move no agent imported either** — the
consumers were `app/`, `scripts/` and `tests/`. The extraction agents import only
`..base_agent`, `..knowledge_extraction` and `..extraction`.

So the justification is not present sharing but a naming error: putting the domain
model under `agents/` made it look like part of the agent runtime and invited the
belief that reading a graph requires the agent stack. The move is also
forward-looking — the Semantic Auditor, Ontology Engineer and Domain Context agents
are stubs that will read exactly these modules.

### Layout: three-way, by who reads what

| Package | Role |
|---|---|
| `core/` | the domain — knowledge model, ingest, review, reconciliation, schema reader |
| `agents/` | LLM agents that act on the graph; stateless functions over it |
| `app/` | the human interface — projections, architecture viewpoints, routes |

**Moved:** `agents/knowledge/` (8 modules, ~3,060 LOC) → `core/knowledge/`;
`agents/ontology.py` → `core/ontology.py`. Renames via `git mv`, so history follows.

**Stayed, by the same criterion** (shared by both → moves; one consumer → stays):
`agents/base_agent.py`, `agents/cli.py`, `agents/extraction/` (agent-side pass
infrastructure), the two real extraction agents, the four stub agents. On the app
side, `app/projections.py`, `app/viewpoints/` and `app/ontology_reference.py` have
only the app as a consumer, so they stay.

### The rule, and the guards that hold it

**Agents and app may import core; core imports neither.** That is what keeps the
knowledge model usable without an LLM and the web layer replaceable without
touching the model.

`tests/test_layering.py` asserts it as source rather than trusting convention:
no file under `core/` imports `agents` or `app`; `agents.knowledge`/`agents.ontology`
do not come back; `core*` is registered in `pyproject.toml`; and — the concrete
payoff — **importing `core.knowledge` and `core.ontology` in a subprocess must not
load `strands`, `flask` or `linkml_runtime`.**

Consequence worth noting: the test suite no longer imports the agent runtime at all.
Importing `agents.knowledge` used to execute `agents/__init__.py`, which imports
`base_agent` and its pydantic model — the suite's pydantic deprecation warnings
disappeared with the move, which is the decoupling showing up as a side effect.

### Deliberately not done

Pre-existing lint debt in `core/knowledge/model.py` (W293/I001/E501), `rdf.py`
(I001/F401) and `ingest.py` (5 × E501) was **not** swept up. Auto-fixing it inside
a rename commit would make the move unreviewable and would silently reformat files
the change has no business touching. It remains recorded here rather than hidden.

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

