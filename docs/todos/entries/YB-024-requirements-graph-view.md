---
id: YB-024
legacy: "24"
title: "Graph view — cover the requirements graph, not only C4"
status: open
priority: high
area: "`app/viewpoints/`, `app/projections.py`, `app/templates/`"
created: 2026-09-23
updated: 2026-09-24
design: null
record: null
superseded_by: []
related: ["ADR-0007", "YB-025", "YB-026"]
blocks: []
blocked_by: []
---

# YB-024 — Graph view — cover the requirements graph, not only C4

> **Open work.** This file is the source of truth for this item; `TODO.md` is generated from it.

**Legacy status:** Not started — **the current view says so on the page**
**Legacy priority:** High (currently the requirements graph has no view at all)
**Legacy area:** `app/viewpoints/`, `app/projections.py`, `app/templates/`

---

### What is missing

`/c4` is the only graph view, and it draws **only architecture elements**. Its own
empty state admits the gap:

> *"This graph has no nodes at the C4 element kinds (…). Architecture extraction
> produces these; requirements extraction produces requirement and business-context
> nodes instead."*

So a requirements-only graph — which is what Step 2 of the user journey produces, and
what all 35 verified assertions in `run_026719149d6d` are — **has no view whatsoever**.
The reviewer sees a list of assertions in `/review` and `/gaps`, but never the graph
those assertions form. Requirements, goals, capabilities, processes, domain concepts and
their traceability edges are all present in the graph and none of them are drawn.

The layering already anticipates this and nothing has used it: `app/viewpoints/` exists
so a second notation has a home, and the module docstring says so. `app/viewpoints/c4.py`
is 100 lines; the viewpoint layer is one worked example.

### Why this is a real gap, not a nice-to-have

The core job is *"prove that an architecture answers its requirements — and show exactly
where it does not."* The proof is a walk over REQ→ARC edges. Half of that walk has no
visual representation, so the traceability the platform exists to establish can only be
inspected one assertion at a time.

A second, subtler cost: because only C4 is drawn, the project's mental model quietly
equates "the graph" with "the architecture graph". `docs/ui-review-workflow.md` §8 warns
about exactly this fusion of view types, and the absence of a REQ view is that warning
coming true in the opposite direction.

### Shape

A **requirements viewpoint**, parallel to `c4.py`, not an extension of it:

| | |
|---|---|
| Levels | requirement altitude — Business → Functional/NFR → Constraint, or traceability-hop shaped |
| Elements | `BusinessGoal`, `BusinessCapability`, `BusinessProcess`, `Stakeholder`, `BusinessRequirement`, `FunctionalRequirement`, `NonFunctionalRequirement`, `ConstraintRequirement`, `DomainConcept`, `QualityAttribute` |
| Edges | `traces_to_goals`, `traces_to_capabilities`, `traces_to_processes`, `refines`, `depends_on`, `conflicts_with`, `derives_from`, **and the unresolved cross-graph references** |
| Must report | `excluded_kinds`, the way C4 does — a level is a deliberate reduction and "not at this level" must not read as "not in the graph" |

Design constraints worth fixing up front:

- **A view is a deliberate reduction.** Whatever is chosen to hide must be reported, not
  silently dropped. That is the existing convention and it is what makes the omission
  legible.
- **Coverage, not decoration.** The most valuable REQ view is the one that shows an NFR
  with no realizing element and a capability with no requirement — the gaps — not just a
  tidy picture of what is present.
- **`DomainConcept` and `QualityAttribute` nodes are currently undrawable anywhere.**
  They are neither C4 elements nor requirements, so no view can show them today.

### Accepted / bounded by the current data

- Drawing must degrade honestly on an unverified or partially-extracted graph. The graph
  may be `UNKNOWN` completeness (YB-023), and a view that renders confidently over that
  is the same false assurance the audit gate refuses to give.
- Node counts are small today (28 nodes / 35 assertions on the fixture), so a force
  layout is adequate. If that changes, ADR-0004 below and pagination become relevant first.

### Related

- **ADR-0007** (split graph projection from architecture viewpoints) built the seam this
  item fills.
- **YB-025** (C4 specification view) is the same layering question for architecture
  notation. Both should share whatever view-shell is chosen.
