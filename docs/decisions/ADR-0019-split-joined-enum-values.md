---
id: ADR-0019
title: "A scalar enum field written as a list is split at ingest, not stored as one token"
status: accepted
date: 2026-09-26
area: "core/knowledge/ingest.py (_enum_values), agents/architecture_extraction/passes.py (unchanged, deliberately)"
related: ["YB-032", "YB-029", "YB-030", "ADR-0011", "ADR-0016"]
design: docs/design/real-run-readings.md
---

# ADR-0019 — A scalar enum field written as a list

> **Record.** Closes [YB-032](../todos/entries/YB-032-multivalued-quality-fields-joined.md).
> Found in `data/sea` and recorded in
> [`docs/design/real-run-readings.md`](../design/real-run-readings.md).

---

### The defect

Two assertions in the merged working set carried three concerns where the
vocabulary allows one:

```
Payment Gateway Platform --quality_category-->   'RELIABILITY, PERFORMANCE_EFFICIENCY, FLEXIBILITY'
Payment Gateway Platform --subcharacteristic-->  'AVAILABILITY, TIME_BEHAVIOUR, SCALABILITY'
```

The architecture profile's element record declares both fields as scalar strings;
the model filled one with a list. Nothing split it, so the graph held a token that
is not an enum member and matches nothing.

### Why it matters less than it looks, and why it was still fixed

The quality census (ADR-0016) does not read these edges — it groups attribute nodes
by their own labels — so no number on the census page was wrong. But the ontology
presents `subcharacteristic` as *the* precise axis, and any consumer that groups by
it would see one nonsense token and report the attribute as unclassified. A field
declared one thing and written another is the object-contract defect one layer down.

### Decision

**Split on ingest, in one place.** `_enum_values` turns a separated string into the
values it contains and returns a single value unchanged, and all three sites that
write `quality_category` / `subcharacteristic` (architecture element, design
technique, NFR) now loop over it.

Splitting rather than rejecting, because it is lossless: every value the model did
emit survives, which is the posture this repo takes everywhere else with vocabulary
it does not recognise. A value with no separator comes back as the single element it
always was, so the sixteen technique nodes already carrying one clean token are
untouched.

**The schema stays scalar, deliberately.** Making the fields `List[str]` would be
the tidier fix, and it was rejected because the deterministic quality classifier
(ADR-0011) *writes* these fields as single values from `core/quality.py`. A list
type would ripple through that path for no behavioural gain — and the classifier is
the part of the system that is reliable, which is a poor place to spend a change.

**Membership is not decided here.** Whether a part is a real ISO 25010 member is
`check_enum_membership`'s question. This function only refuses to let a list hide
behind a scalar; an unrecognised single token stays in the graph and is reported,
exactly as before.

### What was rejected

- **Rejecting the record.** Louder, but it discards two facts the model did produce
  correctly and turns a display problem into a lost extraction.
- **A blanket split on every scalar field.** Wrong for a free-text value that
  legitimately contains a comma, which is why this is applied to the two enum fields
  and nowhere else.
- **Repairing the reader instead.** Each consumer splitting on read would be the
  same decision made N times, and the N+1th would forget.

### Evidence

```
tests/test_semantic_accuracy.py   — the measured joined string, the clean single
                                    value left alone, an empty field writing nothing,
                                    and the split's whitespace/empty handling
```

The graph now holds one assertion per concern; no comma-joined token reaches it.
