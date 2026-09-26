---
id: YB-041
legacy: null
title: "The architecture pattern catalogue reaches the Design Assistant only — never ARC-G extraction, and no verifier"
status: open
priority: medium
area: "`core/patterns.py`, `agents/architecture_extraction/`, `config/agent_config.py`, `core/knowledge/ingest.py`"
created: 2026-09-26
updated: 2026-09-26
record: null
superseded_by: []
related: ["YB-040", "YB-007", "YB-010"]
blocks: []
blocked_by: []
---

# YB-041 — The pattern catalogue reaches one profile

> **Open work.** This file is the source of truth for this item; `TODO.md` is generated from it.

### The finding

`ontology/catalogues/architecture_patterns.yaml` is loaded and used by exactly one
profile.

| Consumer | Receives the catalogue? | Evidence |
|---|---|---|
| Design Assistant | **Yes** — its `patterns` pass prompt, plus in-agent resolution/validation | `agents/design_assistant/agent.py:31,89,141`; `validators.py:26` |
| Architecture extraction (ARC-G) | **No** | zero references in `agents/architecture_extraction/` |
| Any verifier / auditor | **No** | no reference in `core/knowledge/`, `app/`, `scripts/` |

- `core/patterns.py:55` (`DEFAULT_CATALOGUE_PATH`), `:202`
  (`load_pattern_catalogue`), `:333` (`pattern_prompt_context`).
- Config: `pattern_catalogue` is set **only** in the `design_assistant` block —
  `config/agent_config.py:367` (block runs 323–369); the
  `architecture_extraction` block starts at `:394` and has no such key. The field
  and `SEA_PATTERN_CATALOGUE` exist (`:86`, `:184`).
- Catalogues have no generic loader: `load_ontology` reads a fixed four-file chain
  and never scans `catalogues/`; `core/patterns.py` is the only reader.

### Two consequences

**1. ARC-G cannot be checked against the pattern vocabulary.** The architecture
system prompt says *"Capture architecture patterns and decisions where stated"*,
but no arch pass, schema or output key emits `architecture_patterns` —
`data/output/test_arch.json` has no such key. The config and the schema disagree,
and a mandated pattern ("must be microservices") has no catalogue vocabulary to be
verified against.

**2. The catalogue's own content never reaches the graph.** Ingest reads the
design output key (`core/knowledge/ingest.py:250`, `:499`) but never resolves a
name against the catalogue, so `mechanism` / `trade_offs` / `quality_attributes`
are not written — only the model's own prose is. The one cross-check that does
happen lives inside the Design Assistant
(`agents/design_assistant/validators.py:97-159`) and its result sits in
`output["pattern_resolutions"]`, which ingest ignores. A pattern the catalogue does
not know is therefore reported only inside that one run.

Measured: `data/output/test_design.json` carries `architecture_patterns` and
`pattern_resolutions` (4 resolved); the architecture artifact carries neither.

### The decision

1. **Give ARC-G a patterns pass, or extend the technology pass.** Precedent is the
   Design Assistant's `PatternPassResult` / `ArchitecturePatternRecord`
   (`agents/design_assistant/passes.py:191-225`). Collection merging already exists
   (`merge_records(..., named_key, completeness)`), and ingest already understands
   the `architecture_patterns` key — so the change is pass + schema + output
   wiring, plus a `pattern_catalogue` key in the arch config block.
2. **Cross-check at ingest.** Resolve an emitted pattern name against the
   catalogue and flag an unknown one, the way the Design Assistant already does —
   otherwise "the extractor invented a pattern" is invisible to every consumer.
3. **Budget carefully.** The Design Assistant deliberately puts the catalogue in
   the `patterns` pass only, not shared context, for the instruction-dilution
   reason in YB-007. The same restraint applies here.

This is the same class as [YB-040](YB-040-technology-ring.md): a vocabulary the
ontology (or catalogue) declares, that nothing carries into the graph.
