---
id: YB-023
legacy: "23"
title: "Requirements extraction never reports completeness — so REQ-G can never be audited"
status: open
priority: high
area: "`agents/knowledge_extraction/agent.py`, `core/knowledge/ingest.py`"
created: 2026-09-23
updated: 2026-09-24
design: null
record: null
superseded_by: []
related: ["YB-004", "YB-026"]
blocks: []
blocked_by: []
---

# YB-023 — Requirements extraction never reports completeness — so REQ-G can never be audited

> **Open work.** This file is the source of truth for this item; `TODO.md` is generated from it.

**Legacy status:** Not started — **the gate is right, the input is missing**
**Legacy priority:** High (it blocks the audit for every requirements-only graph, permanently)
**Legacy area:** `agents/knowledge_extraction/agent.py`, `core/knowledge/ingest.py`

---

### The symptom, and why it is not what it looks like

`run_026719149d6d` (`sample_requirements.md`) shows `completeness = UNKNOWN` while
**all 35 assertions are VERIFIED** and `outstanding = 0`. The reasonable guess is that
something about verification or the ARC-G link is unfinished. Neither is involved.

Three things that all sound like "complete", kept apart:

| | Question | Source | This graph |
|---|---|---|---|
| Review progress | has a human decided on every assertion? | `ReviewProgress.is_auditable` | ✅ 35/35, outstanding 0 |
| **Run completeness** | **did extraction recover the whole document?** | **`ExtractionRun.completeness`** | ❌ **UNKNOWN** |
| Reconciliation | are REQ↔ARC references bound? | `unresolved_references()` | unrelated to completeness |

ARC-G contributes nothing to completeness. It only produces unresolved references.

### The mechanism

`ExtractionRun.compute_completeness()` returns `RUN_UNKNOWN` on its first branch:

```python
if not self.passes:
    return RUN_UNKNOWN
```

The run record has **`"passes": []`** — and so does every run from this agent. The
requirements profile is a single structured-output call and emits **no pass-level
metadata**. The architecture profile does (`ARCHITECTURE_PASSES` → `run_passes` → one
`PassRecord` per pass per chunk), which is why the arch page shows pass and failed-pass
counts and the requirements page shows none.

Ingest then falls back to `_passes_from_metadata`, which reconstructs records from
`failed_calls` / `empty_calls` / `model_calls`. The requirements agent populates **none**
of the three, so all are `0` and the list is empty. `model_id` is empty for the same
reason — that metadata is not reaching the graph either.

### Why this is a real defect and not just a display issue

Because no current code path can produce a pass record for this agent, **every
requirements-only graph is permanently `UNKNOWN` and therefore never auditable**. The
gate itself is correct — an incomplete extraction yields a graph where absence of a fact
is not evidence of its absence, and treating that as auditable would be a false
assurance. But it is being driven by *absent data* rather than by *bad data*, which is a
reporting gap wearing the costume of a quality verdict.

### What the fix looks like

The agent already knows enough to be honest. It records `extraction_path`
(`structured_output` vs `text_parsing`) and whether content came back, and that is
genuine evidence about whether the document was fully processed:

| What happened | Should report |
|---|---|
| structured output satisfied the schema and returned content | an `ok` pass |
| fell back to text parsing, or structured returned empty | `empty` → run is **PARTIAL** |
| the call failed | `failed` |

That makes the value **true** rather than merely **known**, and it unblocks auditing
requirements-only graphs. Also populate `model_calls` (and `model_id`) so the coarse
metadata fallback has something to work from instead of producing an empty list.

### Care needed

`PARTIAL` and `UNKNOWN` both refuse the audit, but they mean different things to a
reader: "we know some content is missing" versus "we cannot tell". A fix that reports
`COMPLETE` when the text fallback actually ran would be worse than the current silence,
because the silent version at least refuses to claim safety. The text fallback in
particular has no reliable parser for several collections (technology stacks, styles,
techniques, conventions, references) — so a run that used it genuinely is partial and
should say so.

### Related

- YB-004 (model output is not structurally stable) is the same underlying weakness seen
  from another angle: output volume varied from 34 to 250 triples across runs of one
  small document, so "did it find everything?" is not answerable from the output alone.
- The gap report already renders the distinction correctly
  (`"UNKNOWN": "Absence of a fact is NOT evidence of its absence."`). Only the input to
  it is missing.
