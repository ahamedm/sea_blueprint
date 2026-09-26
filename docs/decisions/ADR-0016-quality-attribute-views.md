---
id: ADR-0016
title: "Quality-attribute views — the census groups by canonical concern, and one taxonomy serves both layers"
status: accepted
date: 2026-09-25
area: "core/quality.py (new), core/knowledge/quality.py (new), agents/extraction/quality.py, app/projections.py, app/viewpoints/merged.py, app/templates/quality.html, tests/test_quality_census.py"
related: ["YB-029", "YB-030", "YB-031", "YB-032", "YB-009", "YB-024"]
---

# ADR-0016 — Quality-attribute views

> **Record.** Closes [YB-029](../todos/entries/YB-029-quality-attribute-views.md).
> The measurement this depends on is
> [`docs/design/real-run-readings.md`](../design/real-run-readings.md).

**Legacy status:** ✅ IMPLEMENTED — measured on the real working set
**Legacy priority:** High — the architect's primary lens, parked only because the
data it would render did not exist yet

---

### What was parked, and what unparked it

YB-029 proposed three views over `QualityAttribute` nodes and was parked on a
specific claim: the saved fixtures contained **zero** `QualityAttribute` nodes, so a
census over them would report five attributes with no delivery — an artefact of the
fixture, not a finding. It needed a run with the current profiles first.

The 2026-09-25 real run supplied one. The merged working set now holds 13
`QualityAttribute` nodes across both documents, and the census is the reason the
item existed:

```
attribute nodes 13 → canonical concerns 11   (2 labels merged)
characteristics 4
coverage   answered 3   architecture_gap 2   unasked 6   unaddressed 0
states     stated 5   delivered 9   technique 9   scenario 0
empty      has_quality_scenario
```

The premise held: **`QualityAttribute` converges where `implements_requirement` does
not.** Ten of eighteen requirements are unrealized because the architecture
paraphrases them; at the attribute node the two documents meet without either having
to name the other.

### What was built

1. **One taxonomy, in `core/quality.py`.** The keyword taxonomy, the classifier and
   the humanised names moved out of `agents/extraction/quality.py`, which now imports
   and re-exports them. The extractor classifies a requirement's *wording*; the census
   has to place an *attribute node's label* on the same taxonomy. Two copies would
   drift, and the drift would present as a coverage gap rather than as a bug.
   `canonical_quality_concern` is the shared resolver.
2. **The census, in `core/knowledge/quality.py`.** A query layer beside
   `realization.py`: it never mutates, and it reads the graph's inverse edges
   (`NFR --realizes_attribute--> QualityAttribute`, `Element --satisfies_attribute-->
   QualityAttribute`) rather than the forward slots the ontology declares
   (`stated_in_requirements`, `realized_by`, `covered`) — nothing populates those, and
   writing them would be a second copy of an answer the edges already give.
3. **The views.** `/quality` and `/api/quality` render the census;
   `/map?concern=<characteristic|sub-characteristic|label>` focuses the map on the
   attribute neighbourhood; `/quality` links into it.

### The decisions worth keeping

**Grouping key is the canonical concern, never the label.** The requirements profile
writes the taxonomy's own name (`Time Behaviour`) because a deterministic classifier
chose it; the architecture profile writes whatever the model called it
(`High Availability`, `Low Latency`). Grouped by label that is five attributes and
three invented gaps. `canonical_quality_concern` resolves in strict order — exact
sub-characteristic, exact characteristic, a renamed short form (`PERFORMANCE` →
`PERFORMANCE_EFFICIENCY`), the humanised name, then the keyword classifier for
synonyms — and reports an unrecognised label as its own concern rather than folding
it into a neighbour. Every original label is kept as an alias, so the merge is
visible on the page instead of silent.

**Four states, not one verdict.** `stated_in_requirements`,
`delivered_by_architecture`, `realized_by_technique`, `has_quality_scenario` are
reported independently because they fail independently and the fix differs. A
summary `coverage` is *derived* (`answered` / `architecture_gap` / `unasked` /
`unaddressed`) and never stored, because the ontology's `covered` slot invites a
second copy that drifts.

**A state nothing populates is reported as empty.** `has_quality_scenario` is zero
everywhere — no pass emits a `QualityScenario` — and the page names it under "nothing
populates this". A census that dropped the column it could not fill is how "no
scenarios anywhere" would read as "scenarios are fine".

**Both inverses, including the one the gap report cannot express.** `delivered but
not stated` is the architecture providing a quality nobody asked for: not a defect,
possibly an unstated requirement, and invisible from the requirement side. `/gaps`
is requirement-shaped and structurally cannot show it.

**The focus fails visibly.** `/map?concern=TYPO` selects nothing and says so, rather
than falling back to the whole graph — a filter that fails open makes a typo look
like a graph with no quality attributes. What the focus hides is counted and listed
on the page, the same posture as every lens.

**The census does not depend on the model's redundant triples.** The quality numbers
come from the structured `satisfies_attributes` collection and the deterministic
classifier, both of which produce bound node edges. The model's
`satisfies_quality_attributes` literals — four of which do not even route
([YB-031](../todos/entries/YB-031-cross-graph-predicate-plural-routing.md)) —
contribute nothing, which is why the census is stable while the join is not.

### Measured

On `data/sea` after the 2026-09-25 real run:

```
Performance Efficiency   Performance (unasked) · Capacity (gap) · Time Behaviour (answered)
Reliability              Reliability · Availability · Fault Tolerance · Recoverability  (all unasked)
Security                 Accountability (gap) · Confidentiality (answered) · Integrity (unasked)
Flexibility              Scalability (answered)
```

`Availability` carries two aliases and `Time Behaviour` two, from opposite sides of
the graph — the convergence the item predicted, on real output.

### Not done, stated rather than implied

- **`QualityConcernClass` is not used for grouping.** `TIME_BEHAVIOUR` and
  `SCALABILITY` are both PERFORMANCE_EFFICIENCY and are met by different designs;
  whether the census should offer that axis as well is still open. The concern class
  is not asserted anywhere in the graph today, so there is nothing to group by yet.
- **Nothing writes scenarios.** The state is ready and empty; populating it needs an
  extraction pass that does not exist.

  > **Resolved 2026-09-25 by
  > [ADR-0017](ADR-0017-design-assistant-proposes-arc-g.md).** The Design Assistant
  > proposes a measurable `QualityScenario` per stated quality attribute, so
  > `has_quality_scenario` is populated by a real design run and is no longer in
  > `unpopulated_states`. The edge is `scenario --realizes_attribute--> QualityAttribute`
  > — the same predicate an NFR uses, since both point at the same node — and the
  > census separates them by source kind, which a test pins.
- **The requirements profile still emits the architecture-side quality predicate**
  from its system node ([YB-030](../todos/entries/YB-030-requirements-profile-cross-graph-claims.md)).
  The census tolerates it; it is not correct.
- **The map focus filters nodes, not edges.** A link between two focused nodes whose
  predicate is unrelated to the concern is still drawn, because hiding it would make
  the neighbourhood look smaller than it is.
