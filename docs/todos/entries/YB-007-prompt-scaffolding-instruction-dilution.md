---
id: YB-007
legacy: "7"
title: "Prompt scaffolding now exceeds the document 2.5:1 — instruction dilution"
status: open
priority: high
area: "`agents/architecture_extraction/agent.py`, extraction prompt design generally"
created: 2026-09-20
updated: 2026-09-24
design: null
record: null
superseded_by: []
related: ["ADR-0010", "YB-012"]
blocks: []
blocked_by: []
---

# YB-007 — Prompt scaffolding now exceeds the document 2.5:1 — instruction dilution

> **Open work.** This file is the source of truth for this item; `TODO.md` is generated from it.

**Legacy status:** Observed — needs a strategy
**Legacy priority:** High (it makes every other prompt fix unreliable)
**Legacy area:** `agents/architecture_extraction/agent.py`, extraction prompt design generally

---

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

1. **ADR-0002** — tightening the object contract silently killed traceability
   predicates (`traces_to_goal/capability/process`, `binds_to_system`).
2. **YB-007** — adding structural containment silently resurrected
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
