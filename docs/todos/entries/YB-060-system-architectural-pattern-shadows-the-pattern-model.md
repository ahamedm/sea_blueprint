---
id: YB-060
legacy: null
title: "`System.architectural_pattern` is a free string shadowing the ArchitecturePattern model"
status: open
priority: low
area: "`ontology/enterprise_structure.yaml` (`System.architectural_pattern`), `ontology/architecture_base.yaml` (`ArchitecturePattern`, `ArchitectureStyle`)"
created: 2026-10-03
updated: 2026-10-03
design: null
record: null
superseded_by: []
related: [YB-053, YB-055, ADR-0018]
blocks: []
blocked_by: []
---

# YB-060 — `System.architectural_pattern` shadows the pattern model

> **Open. Filed from the external ontology review, 2026-10-03.** A naming/identity
> decision, not a patch.

## The measurement

`enterprise_structure.yaml` gives `System` a free-text `architectural_pattern`
(`range: string`). `architecture_base.yaml` models the same idea properly:
`ArchitecturePattern` with `category`, `mechanism`, `trade_offs`, `rationale`,
`realizes_quality_attributes`, `satisfies_attributes`, `mandated_by` and `applies_to`
— and `ArchitectureStyle` beside it for the coarse shape. So one enterprise says
"microservices" as an unvalidated string on the System, and the architecture layer can
say it as a node with a resolvable identity.

This is the same two-vocabulary-one-axis shape ISS-12 records for platforms, and
`architecture_base.yaml`'s own `Platform` naming note is the precedent for how the
repo handles it: name the sense deliberately, under a different name, "so the two do
not blur". Here they blur.

## Why it is a decision

Three defensible outcomes, and they are not equivalent:

1. **Deprecate it** and let `follows_style` / `applies_pattern` carry the claim. Best
   if nothing reads the string — needs a check across `core/`, `app/` and the passes.
2. **Keep it as a denormalised extraction hint** (the document's own words) and add a
   resolved `architecture_styles` / `patterns` slot beside it, mirroring the
   ref-then-resolve convention `requirement_refs` / `implements_requirements` uses.
3. **Keep it and document the overlap**, on the grounds that a System's coarse shape
   is a different claim from a named pattern being applied to a container.

## What closes it

A chosen option, the schema change, and — if option 1 — a measured statement that
removing the slot breaks nothing that reads it.
