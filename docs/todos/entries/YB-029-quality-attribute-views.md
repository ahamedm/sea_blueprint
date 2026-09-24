---
id: YB-029
legacy: null
title: "Quality-attribute views for the map — the architect's primary focus has no view of its own"
status: parked
priority: high
area: "`app/viewpoints/merged.py` (new lens or viewpoint), `app/projections.py`, `core/knowledge/realization.py`, `app/templates/map.html`"
created: 2026-09-24
updated: 2026-09-24
design: null
record: null
superseded_by: []
related: ["YB-005", "YB-024", "YB-009", "YB-028"]
blocks: []
blocked_by: []
---

# YB-029 — Quality-attribute views for the map

> **Parked.** Raised while reviewing what the map could focus on, and deliberately
> not scheduled: the join it needs is already modelled, but the current profiles
> only just started producing it (see *What the graph gives us*). This file is the
> source of truth for the item.

**Raised:** "Architects' critical focus is Quality Attributes. What other views in
Map can help focus on Quality Attributes?"
**Priority:** High on merit — this is the architect's primary lens, not a nicety —
parked because the data it would render is not there yet, not because it is minor.

---

### Why quality attributes are the architect's focus, in this model

Everything else in the graph says what the system *is* or what it must *do*.
Quality attributes are what the architecture is **for**, and they are the only part
of the model that both sides genuinely share:

```
REQ-G                                    ARC-G
NonFunctionalRequirement                 Container / Component
   │ realizes_attribute                      │ satisfies_attribute
   ▼                                         ▼
            QualityAttribute  ◄─────────────┘
                     ▲
                     │ satisfies_attribute
               DesignTechnique ──realizes_quality_attribute──► NFR
```

`QualityAttribute` nodes are a **join point that does not depend on wording
matching between the two documents**. A requirement and a container that both
mention "Availability" meet at that node; neither had to be reconciled against the
other. For an NFR-heavy architecture that is a better-converging join than
`implements_requirement`, which needs a citation or a lexical match.

It is also the join the audit does not currently use. `realization_report` answers
"is this requirement answered?" — requirement-shaped. The architect asks
attribute-shaped questions, and nothing answers them.

### What the graph gives us today

Verified by running the current profiles over a minimal document:

| Assertion | Written by | Means |
|---|---|---|
| `NFR --realizes_attribute--> QualityAttribute` | requirements profile | the requirement is *about* this attribute |
| `Element --satisfies_attribute--> QualityAttribute` | architecture profile | this element *delivers* it |
| `Technique --satisfies_attribute--> QualityAttribute` | architecture profile | this technique delivers it |
| `Technique --realizes_quality_attribute--> <NFR>` | architecture profile | cross-graph, literal, reconciliation's job |
| `NFR --quality_category--> RELIABILITY`, `--subcharacteristic--> AVAILABILITY` | deterministic classifier | **canonical** ISO/IEC 25010 classification |

There is also an unimplemented coverage model already declared on the
`QualityAttribute` class: `covered`, `coverage_note`, `stated_in_requirements`,
`realized_by`, `realized_by_techniques`, `scenarios`, `parent_attribute`. Someone
designed the census and nothing populates it.

And the taxonomy needed to group by is already in the loader:
`QualityAttributeCategory` (10 values) and `QualitySubcharacteristic` (40 values),
with `SUBCHARACTERISTIC_PARENT` mapping each subcharacteristic to its category.

### The views worth building

**1. Coverage census by ISO characteristic** — the direct answer to "where are our
quality gaps?" Group attributes under their characteristic (Reliability, Security,
Performance Efficiency, …) and show, per attribute, four independent states:

| State | Comes from |
|---|---|
| stated in requirements | any NFR `realizes_attribute` it |
| delivered by architecture | any element `satisfies_attribute` it |
| has a realizing technique | any `DesignTechnique` satisfies it |
| has a quality scenario | `QualityScenario` linked to it |

Four states, not one, because they fail independently and the fix differs:
*stated but not delivered* is an architecture gap; *delivered but not stated* is the
interesting direction — the architecture provides a quality nobody asked for; *no
technique* means it is asserted without a mechanism; *no scenario* means it is not
testable.

**2. A quality-attribute lens on the map.** Bipartite around the attribute nodes:
requirements on one side, elements and techniques on the other, the attribute as the
spine. Selection is by *attribute*, not by layer — "show me Security" pulls in the
NFRs, containers and techniques that touch it, which is exactly the architect's
question.

**3. Attribute-level filtering.** A lens per ISO characteristic (10 values) or
subcharacteristic (40). This is a better default for an architect than
business/requirements/architecture, which are *document* layers, not concerns.

### What blocks it, and why this is parked

1. **The saved fixtures contain zero `QualityAttribute` nodes.** They predate the
   `satisfies_attributes` / `realizes_attribute` / `design_techniques` work, so the
   five quality references in `data/output/test_arch.json` are unbound literals
   ("High Availability", "Low Latency", "Performance", "Scalability", "PCI-DSS
   Compliance") pointing at nothing. A census run today would report five attributes
   with no delivery — an artefact of the fixture, not a finding. It needs a run with
   the current profiles first, exactly as YB-023 did.
2. **Grouping must be by `subcharacteristic`, not by the attribute's label.** A
   requirement saying "Availability" and an element saying "High Availability"
   become two `QualityAttribute` nodes, and the join silently fails — the same
   wording-collision problem as YB-005, in a place with no reconciliation pass. The
   classifier's `subcharacteristic` is deterministic and canonical, so it is the
   right grouping key; the label is for display only. Deciding that up front is what
   stops the census reporting duplicates as separate gaps.
3. **`ConcernClass` exists and is unmodelled here.** `QualityConcernClass` was added
   to separate concerns met by different designs (`TIME_BEHAVIOUR` vs `SCALABILITY`
   are both PERFORMANCE_EFFICIENCY). Whether the census groups by that too is a
   design question this item should answer rather than inherit.

### Acceptance criteria

- Every `QualityAttribute` node is listed with its characteristic and its four
  coverage states, and a state nothing populates is reported as such rather than
  silently omitted
- An attribute the architecture delivers but no requirement states is listed (the
  inverse of the gap report's direction)
- Grouping is by canonical subcharacteristic, and two labels for one attribute do
  not count as two attributes
- The map can be filtered to one ISO characteristic, and reports what the filter
  hides the way every other lens does

### Related

- [ADR-0014](../decisions/ADR-0014-map-replaces-c4-view.md) — the map and its lens
  mechanism, which this extends.
- [ADR-0015](../decisions/ADR-0015-retiring-extracted-facts.md) — parked alongside;
  both need a fresh run before they can be judged, for the same reason.
- `docs/design/` has no quality-model design note. The `QualityConcernClass` and
  `QualityAttribute` slots scattered through `requirements_base.yaml` are the
  closest thing, and they are comments rather than a design.
- [YB-009](YB-009-architecture-gaps.md) — the audit engine this census is a second
  face of.
