---
id: ADR-0013
title: "Requirements runs report their own completeness — REQ-G can be audited"
status: accepted
date: 2026-09-24
area: "agents/knowledge_extraction/agent.py, core/knowledge/ingest.py, tests/test_completeness_reporting.py"
related: ["YB-023", "YB-004", "YB-026"]
---

# ADR-0013 — Requirements runs report their own completeness

> **Record.** Closes [YB-023](../todos/entries/YB-023-requirements-completeness-reporting.md).
> Legacy item: 23.

**Legacy status:** ✅ IMPLEMENTED — measured through the real agent and the real ingest
**Legacy priority:** High — it blocked the audit for every requirements-only graph, permanently
**Legacy area:** `agents/knowledge_extraction/agent.py` (metadata), `core/knowledge/ingest.py` (`_passes_from_metadata`)

---

### The defect

`ExtractionRun.compute_completeness()` returns `UNKNOWN` when a run has no pass
records, and that branch is correct — absence of pass detail is ignorance, not
success. But it was the **permanent** answer for every requirements run.

Observed on `run_aa36f85b79f4` (`sample_requirements.md`), while the architecture
run in the same working set reported COMPLETE with 12 passes:

```
run_aa36f85b79f4:  completeness=UNKNOWN  passes=0  model_id=''
```

The requirements agent's metadata carried only:

```
contract_violation_count, document_type, domain, extraction_path,
low_confidence_count, structured_output_error, total_entities,
total_relationships, total_triples
```

`_passes_from_metadata` reconstructs records from `model_calls` / `failed_calls` /
`empty_calls`, **none of which were set**. So `ok = max(0, 0-0-0) = 0`, the list
came back empty, and `compute_completeness` took the ignorance branch. `model_id`
was empty for the same class of reason: ingest reads `metadata["model_id"] or
metadata["model"]`, and the agent set neither. The architecture profile has always
supplied all four counters, which is the entire asymmetry.

It reproduces exactly from the saved output: the same payload plus `model_calls=1`
reports COMPLETE; with `failed_calls=1`, FAILED. So everything downstream of the
metadata was sound — only the metadata was missing.

### What was built

1. **Per-attempt records from the profile.** `KnowledgeExtractionAgent._pass_records`
   turns the two attempts it actually makes — one structured call, then at most one
   text fallback — into real `PassRecord`s, emitted as `metadata["passes"]`,
   alongside `model_id`, `model_calls`, `failed_calls`, `empty_calls` and
   `text_fallback_calls` derived **from** those records so the two cannot disagree.

2. **Ingest reads records first.** `_passes_from_metadata` now prefers the `passes`
   key (accepting either a `PassRecord` or the dict the agent serialises it into)
   and keeps the counter reconstruction as the fallback for output that predates
   the change. A malformed or unreadable outcome is **skipped, never defaulted to
   `ok`** — that is the one reading that could open the audit gate on evidence
   nothing can parse.

3. **A second site of the same bug, fixed.** The architecture profile has always
   emitted `text_fallback_calls`, and ingest never read it: a run whose calls all
   fell back to text reconstructed as every call `ok` → COMPLETE → the gate opened
   on a genuinely incomplete run. That is the false-assurance direction, and it was
   live. The counter is now read as `empty`.

### The design decision worth keeping

**Counters are derived from records, not maintained in parallel.** Two shapes of
the same information, with only one of them checked, is what let this stay
invisible: the requirements profile reported nothing for months and every
downstream consumer read the absence as ignorance. Emitting the records makes the
counter reconstruction a fallback for old output rather than the only path, and
`test_counters_and_records_describe_the_same_run` crosses the two so a divergence
fails loudly.

### What a text fallback means: `empty`, and it stays PARTIAL

The fallback attempts to be `ok` (it did produce content) while the run is
`PARTIAL`, because the text parser has no reliable parser for several collections
(technology stacks, styles, techniques, conventions, references). Reporting
`COMPLETE` because *something* came back would be the false assurance this field
exists to prevent — so a requirements run that fell back says PARTIAL, and PARTIAL
still refuses the audit. What changed is that the run now **states** what happened
instead of reporting an unexplainable UNKNOWN.

`invoke_structured` returning `None` is deliberately not treated as one thing: it
returns `None` for a model that cannot satisfy the schema, for a cancelled call,
and for an unsupported `tool_choice`. All three leave the same absence behind so
the outcome is `empty` for each, but the reason is kept on the record's `error`,
and `path` distinguishes "came back empty" (`structured`) from "never got there"
(`none`), because they are different problems to fix.

### Measured

Through the real agent and the real Flask ingest route, with the model stubbed:

```
extraction_path = 'text_parsing'
model_id        = 'qwen2.5-14b'      (was '')
model_calls     = 2                  (was absent)
passes          = [('structured','empty','none'), ('text_fallback','ok','text')]

ingest flash    = "... (requirements) → PARTIAL: +2 facts, 0 changed"
run.completeness= PARTIAL            (was UNKNOWN)
/api/gaps       = completeness PARTIAL, auditability "Absence of a fact is NOT evidence of its absence."
```

### Backward compatibility, stated rather than assumed

The saved `data/output/` fixtures predate this and **keep reading `UNKNOWN`** until
they are re-run. That is correct behaviour and is pinned by a test: the fix must
not turn every silent run into an assurance, only make new runs describe themselves.

### Still open (not this item)

- **Architecture passes still reconstruct as one `ok` per call**, with
  `pass_name="(unspecified)"`. The counters and outcomes are now right; per-pass
  names are not carried through `_passes_from_metadata`. Recorded rather than
  silently improved.
- **`_run_summaries` still shows `passes` and `failed` as bare counts** on the
  ingest page. The rest of the record (path, reason) is available now and could be
  surfaced; YB-026 (asynchronous progress) is the natural home.
- **Requirements per-pass granularity is one, by construction.** A multi-pass
  requirements profile would need this generalising; the record shape already
  supports it.
