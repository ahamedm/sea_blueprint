---
id: YB-032
legacy: null
title: "Multivalued quality fields arrive as one comma-joined string on a single assertion"
status: open
priority: low
area: "`agents/architecture_extraction/` (profile schema and prompt), `core/knowledge/ingest.py`"
created: 2026-09-25
updated: 2026-09-25
design: docs/design/real-run-readings.md
record: null
superseded_by: []
related: ["YB-029", "YB-030"]
blocks: []
blocked_by: []
---

# YB-032 — Comma-joined values in a multivalued field

> **Found by the real run of 2026-09-25**, in the merged working set.

### The defect

Two assertions in `data/sea` carry three concerns where the vocabulary allows one:

```
Payment Gateway Platform --quality_category-->   'RELIABILITY, PERFORMANCE_EFFICIENCY, FLEXIBILITY'
Payment Gateway Platform --subcharacteristic-->  'AVAILABILITY, TIME_BEHAVIOUR, SCALABILITY'
```

The architecture profile's element record has these as scalar strings and the model
filled one with a list. Nothing splits it, so the graph holds a token that is not an
enum member and matches nothing.

### Why it is low priority, and why it is still worth an entry

The quality census ([YB-029](YB-029-quality-attribute-views.md),
[ADR-0016](../../decisions/ADR-0016-quality-attribute-views.md)) does not read these
edges — it groups attribute nodes by their own labels — so the numbers on the census
page are unaffected. But any consumer that groups by `subcharacteristic`, which the
ontology presents as *the* precise axis, would see a nonsense token and report the
attribute as unclassified. A field that is declared one thing and written another is
the same class of defect as the object contract, one layer down.

### What the fix has to decide

1. **Split, or reject.** Splitting `'A, B, C'` into three assertions is lossless and
   cheap; rejecting it is safer and louder. The repo's posture elsewhere is to keep an
   unrecognised value rather than drop it, which favours splitting.
2. **Where.** The profile schema could declare the field multivalued so the model is
   shaped correctly, ingest could split defensively, or both — with the schema being
   the fix and the split being the repair for output already emitted.
3. **Whether it is always a list.** The same field is a clean single token on sixteen
   other technique nodes, so a blanket split is safe here and would be wrong for a
   value that legitimately contains a comma.

### Acceptance

- A comma-joined enum value either becomes one assertion per value or is reported as
  unparseable; it never reaches the graph as a token that matches nothing.
- A test pins the joined-string case.
