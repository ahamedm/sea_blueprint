---
id: YB-059
legacy: null
title: "`measurable` is required on every NFR, so the three Platform* requirements inherit a shape they do not have"
status: open
priority: medium
area: "`ontology/requirements_base.yaml` (`NonFunctionalRequirement.measurable`, and the three `Platform*Requirement` subclasses)"
created: 2026-10-03
updated: 2026-10-03
design: null
record: null
superseded_by: []
related: [YB-053, YB-055, YB-007]
blocks: []
blocked_by: []
---

# YB-059 — `measurable` is required on every NFR

> **Open. Filed from the external ontology review, 2026-10-03.** A decision about what
> these three classes ARE, not a patch.

## The measurement

`NonFunctionalRequirement.measurable` is `range: boolean, required: true`. All three of
its Platform subclasses inherit it:

| Class | `is_a` | Own required slots | Inherited required |
|---|---|---|---|
| `PlatformExtensibilityRequirement` | `NonFunctionalRequirement` | `target_platform` | `measurable` |
| `PlatformMultiTenancyRequirement` | `NonFunctionalRequirement` | `target_platform` | `measurable` |
| `PlatformCompatibilityRequirement` | `NonFunctionalRequirement` | `target_platform` | `measurable` |

So an extractor must answer "does this have quantified targets?" for a compatibility or
multi-tenancy requirement, where the honest answer is often "that question does not
apply" — and a `boolean` forces `false`, which reads as a finding rather than as
"not applicable".

## Why this is a decision and not a fix

The slot is required for a reason: an NFR that cannot be measured is the thing the
quality model exists to prevent — it is why `QualityScenario` carries an ATAM response
measure, and why `core/knowledge/digest.py` treats an unmeasured NFR as a gap. Relaxing
it for three subclasses either:

1. **Overrides it locally.** LinkML's mechanism is `slot_usage` under the subclass, or
   re-declaring the slot. `core/ontology.py` builds `effective_attributes` by merging a
   parent's attributes, so the app's own reader may not honour an override — the schema
   and the tool would then disagree about the same class, which is the failure YB-053
   is about. Needs checking before choosing this.
2. **Splits the hierarchy.** The three classes stop being NFRs and become requirement
   kinds in their own right, with `target_platform` as their required slot. Cleaner
   semantically; it changes what `REQUIREMENT_KINDS`-driven coverage counts treat them
   as, and the realization report with it.
3. **Changes the slot's meaning** — `measurable` becomes an enum
   (`MEASURED` / `UNMEASURABLE` / `NOT_APPLICABLE`) so "does not apply" is sayable.
   Additive, but it changes every existing `measurable: true` in the graph.

## What closes it

A chosen option, the ontology change, and a test pinning that a
`PlatformCompatibilityRequirement` can be well-formed without a quantified target —
plus, if option 1 is chosen, evidence that `core/ontology.py`'s reader agrees with
LinkML about the override.
