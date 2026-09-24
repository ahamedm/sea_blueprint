---
id: YB-004
legacy: "4"
title: "Model output is not structurally stable across runs"
status: open
priority: low
area: "`agents/base_agent.py`, `agents/knowledge_extraction/agent.py`"
created: 2026-09-20
updated: 2026-09-24
design: null
record: null
superseded_by: []
related: ["YB-023"]
blocks: []
blocked_by: []
---

# YB-004 — Model output is not structurally stable across runs

> **Open work.** This file is the source of truth for this item; `TODO.md` is generated from it.

**Legacy status:** Observed — no action yet
**Legacy priority:** Low (watch item)

---

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
- Structured output (ADR-0003) — fixes at the source, if it works
- A normalisation layer that canonicalises parsed output before comparison
- A more capable model for extraction
