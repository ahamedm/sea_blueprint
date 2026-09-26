---
id: ADR-0020
title: "The techniques pass answers a closed list of the quality attributes REQ-G states"
status: accepted
date: 2026-09-26
area: "agents/design_assistant/passes.py (design_technique_pass), agents/design_assistant/agent.py (_stated_quality_attributes), agents/design_assistant/validators.py (check_techniques_are_linked)"
related: ["YB-038", "YB-035", "YB-007", "YB-020", "ADR-0011", "ADR-0016", "ADR-0017"]
design: docs/design/design-assistant.md
---

# ADR-0020 — The techniques pass answers a closed list

> **Record.** Closes [YB-038](../todos/entries/YB-038-techniques-pass-echoes-requirements.md).
> Found by the first real Design Assistant run; measured in ADR-0017 §Measured.

---

### The defect, measured

The pass was asked for the MECHANISM delivering each stated quality attribute —
`Redundancy / Replicas`, `Stateless Services`, `Connection Pooling`. On the live
model it returned the requirements' own names, eight of eight, with every quality
field empty:

```
Role-Based Access Control Enforcement   cat=''  realizes=[]  quality=''
Horizontal Scaling                      cat=''  realizes=[]  quality=''
TLS 1.2+ Transport Security             cat=''  realizes=[]  quality=''
Payment Gateway Fallback Strategy       cat=''  realizes=[]  quality=''
...
```

The run's other five passes produced usable output, so this was specific to this
pass rather than to the profile.

### Why it is more than a naming slip

`DesignTechnique` exists for one reason: to supply the edge between a required
quality attribute and the design that claims to deliver it (ADR-0011, ADR-0016). A
technique named after the requirement, with no quality link, adds a node and no
information — and `core.knowledge.quality` counts it, so `realized_by_technique`
would report the attribute as having a mechanism on the strength of a **renamed
requirement**. That is a false assurance about coverage, which is the one thing this
layer must not produce. The harness agreed: `inv_design_techniques_linked` was left
failing deliberately as the diagnostic.

### Decision

**Turn an open question into a closed list.** `design_technique_pass(attributes)` is
built per run, like the patterns pass, with the quality attributes REQ-G *states*
interpolated as the job: one mechanism per attribute, with the attribute's exact
name in `realizes_quality_attributes`. "Aimed at nothing" stops being a shape the
pass can return by accident, because the task is the list, and an attribute with no
technique is a visible missing item rather than silence.

The attributes are read through `quality_report`, not off the NFR nodes, because the
census is what already decides that `Availability` and `High Availability` are one
concern — reading labels directly would list one attribute twice under two
spellings and the pass would answer it twice. "Stated in requirements" specifically:
an attribute the architecture delivers and no requirement asks for is not this
pass's gap to close.

**The instruction names the failure.** It lists the mechanisms that are right, the
eight restatements that are wrong, and the rule in one line: *a technique name may
never be a requirement's name.* Naming observed failures as counter-examples is the
cheap attempt the item asked for, with YB-007's caution in mind — prose is not
monotonic, so the prompt is not the only defence.

**A second validator makes a recurrence visible.**
`check_techniques_are_linked` flags a technique carrying no
`realizes_quality_attributes`, `quality_category` or `subcharacteristic`. The
existing `check_techniques_are_mechanisms` catches the echo; this catches the empty
fields, which is the other half of the same live failure and the half that would
otherwise read as coverage on the page. The two together are the operational
definition of "fixed": the harness invariant asserts exactly this condition.

### What was rejected

- **Deterministic post-processing** (resolve a requirement-named technique onto the
  attribute it names, then onto catalogue mechanisms). It works on any model — the
  ADR-0011 posture — but it *decides the technique for the model*, and the technique
  layer is the one place in this profile that is a genuine design choice rather than
  a reading of the input.
- **A stronger model for this pass.** An escape hatch, not a fix, and one that
  should be measured rather than assumed.
- **Making `realizes_quality_attributes` required in the schema.** It would reject
  the output rather than shape it, and this is the pass observed burning twenty
  minutes on a retry loop — a schema the model keeps failing is how that happens.
  The closed list shrinks the ask instead, which is the direction that helps.

### Measured on a live run

Run after the change, against the same local model and the same digest
(`scripts/run_extraction_tests.py --only design`):

```
6/6 invariants passed   226s
  [PASS] inv_design_techniques_linked  5 technique(s), all linked to a quality concern
  [PASS] inv_design_collections        10 elements, 11 connections, 5 design_techniques,
                                       4 architecture_patterns, 5 quality_scenarios, 33 references
  [PASS] inv_design_patterns_resolved  4/4 resolved from the catalogue
```

The five techniques the model returned:

| name | realizes | category | sub-characteristic |
|---|---|---|---|
| `Caching` | Time Behaviour | PERFORMANCE_EFFICIENCY | TIME_BEHAVIOUR |
| `Role-Based Access Control` | Accountability | SECURITY | ACCOUNTABILITY |
| `PGP Encryption` | Confidentiality | SECURITY | CONFIDENTIALITY |
| `TLS 1.2+ Encryption` | Confidentiality | SECURITY | CONFIDENTIALITY |
| `AES-256 Encryption at Rest` | Confidentiality | SECURITY | CONFIDENTIALITY |

Every one is a MECHANISM rather than a restatement, every one carries the closed-list
attribute it was asked to answer, and `check_techniques_are_mechanisms` fired on
none of them. The run before this change returned eight of eight requirement
echoes with every quality field empty.

Two notes on the stall. The run took **226s for six passes** — about 38s a pass,
bounded and inside the request budget — and no pass retried into a loop. That is
consistent with the bounded-generation work of 2026-09-26 rather than with the
19-minute run this item originally inferred from. One run is not a measurement of
variance, so the inference is retired rather than replaced by a new claim.

### What remains open

- **One document, one model, one run.** The five techniques above are a real
  improvement on eight echoes, not a distribution. Per YB-020's standing note, the
  number that settles this is a repeat measurement, not this one.

### Evidence

```
tests/test_design_agent.py   — the closed list reaches the prompt, the attributes
                               come from the census, an unlinked technique is
                               flagged and reaches the run's findings
```

The prompt-budget test (`tests/test_prompt_budget.py`) still passes with the
interpolated list, so the pass did not grow past its own budget.
