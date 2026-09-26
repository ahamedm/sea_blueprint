---
id: ADR-0024
title: "The requirements profile chunks like the architecture profile — same chunker, same merge"
status: accepted
date: 2026-09-26
area: "agents/knowledge_extraction/agent.py (run, _extract_from_prompt, _pass_records), agents/extraction/chunking.py (reused)"
related: ["YB-007", "YB-009", "YB-026"]
---

# ADR-0024 — The requirements profile chunks

> **Record.** Built as an enabler for running against real documents on a hosted
> model. No TODO entry tracked it; YB-009 recorded the symptom.

---

### The asymmetry

The architecture profile has always chunked at 7,000 characters and merged across
chunks. The requirements profile sent the **whole document in one call** and asked
for every collection back in a single response.

That was survivable while the corpus was samples. It is the wrong shape for a real
PRD, for three reasons that compound:

1. **The output budget is the document's.** One response carries every element,
   connection, triple and reference. A long document truncates, and a truncated
   structured call is a failed one.
2. **One dropped collection is unrecoverable.** There is no second chunk whose
   result could supply what the first omitted.
3. **`YB-009` already measured the consequence on the requirements side:** 169
   triples collapsing to 48 unique facts, each repeated up to four times, because
   the architecture agent deduplicates via its chunk/pass merge and this one had no
   merge at all. Content-addressed assertion ids hid it at ingest; the extraction
   was still producing them.

### The decision

The same chunker and the same merge helpers the architecture profile uses:
`chunk_document`, `merge_triples`, `merge_records`. Two profiles disagreeing about
what a large document means is how the same corpus produces two different graphs.

**A document that fits one chunk takes the OLD path byte-identically** — the same
prompt, no merge, no conversion through dicts. `chunk_document` returns exactly one
chunk whose text equals the input when the document is under budget, so no existing
behaviour moves and only a document that needs chunking is chunked. This is
deliberate: the change is otherwise invisible to every existing test, which means a
mistake in it would also be invisible.

Merge identity keys are `(name, ontology_class)` for entities and
`(source, predicate, target)` for relationships. The class is part of the entity
key on purpose — two chunks naming one string as different things is a conflict for
ingest to report as a finding, not for this layer to silently resolve.

Per-chunk pass records now carry `chunk_label`, so completeness describes the whole
document rather than whichever chunk answered last. An extra collection split by a
chunk boundary (technology stacks, architecture styles) is reunited rather than
half-lost.

### What was rejected

- **A separate requirements chunker.** Two chunkers drift, and the architecture one
  is structure-aware already.
- **Chunking by fixed character windows.** Loses heading breadcrumbs, which is what
  makes a chunk interpretable on its own.
- **Leaving the single call and raising `max_tokens`.** Moves the ceiling without
  addressing recoverability: one malformed response still loses the document.

### Evidence

```
tests/test_requirements_chunking.py — a small document takes one call with the
                                      document verbatim; a large one is extracted
                                      per chunk with a label per pass; merge
                                      deduplicates across chunks without collapsing
                                      distinct entities; a text fallback on one
                                      chunk stays visible in the run
```

Live: the architecture ingest on a 14,893-character document ran 12 calls
(4 passes × 3 chunks) and reported `COMPLETE`.
