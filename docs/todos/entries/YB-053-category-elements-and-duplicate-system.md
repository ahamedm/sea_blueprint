---
id: YB-053
legacy: null
title: "Category-word elements and one system named twice — the two content defects left in the C4 model"
status: open
priority: high
area: "`agents/architecture_extraction/passes.py` (structure rules), `agents/extraction/validators.py` (a name validator), `core/knowledge/ingest.py` (`_resolve` identity)"
created: 2026-09-27
updated: 2026-09-27
design: docs/design/c4-specification-view.md
record: null
superseded_by: []
related: ["ADR-0029", "YB-051", "YB-052", "YB-020", "YB-004"]
blocks: []
blocked_by: []
---

# YB-053 — Category-word elements and one system named twice

> **Open.** Both found by running the MVP test case end to end against DeepSeek
> (`deepseek-v4-pro`) on a clean scope, measured with `scripts/c4_scorecard.py`. They are
> what is left of the C4 model after the defects below were fixed — the diagram is now
> real, and these two things still make it look wrong.

### Where the model stands

`data/sea_home_01` scope `acme_pillar_01`, `sample_requirements.md` (REQ-G) +
`payment_platform_arch.md` (ARC-G), `payment_processing` domain pack:

| | Local 4B (before) | DeepSeek + fixes |
|---|---|---|
| C4 arrows | **0** | **11** |
| container diagram | 0 boxes, 0 arrows | 14 boxes, 11 arrows |
| level stated by the extraction | 4 of 75 | **25 of 26** |
| reflexive / nesting / duplicate defects | 4 / 29 / 21 | **0 / 0 / 0** |
| passes failed | 2 of 12 | **0 of 12** |
| wall time | ~28 min | **146 s** |
| `sanity_check`-style readiness | `READY: NO` on four rules | `READY: NO` on two |

The remaining two rules that fail are `levels_stated` (25/26 — one element carries no
`c4_level`) and `runs_complete` (the architecture run is PARTIAL because one connections
call returned `empty`). Neither is a C4 defect; both are reported honestly and are
discussed below.

### Defect 1 — elements named after a category, drawn as boxes

The emitted diagram contains:

```
external_services = softwareSystem "External Services" "External services communicating
                    with microservices over TLS 1.2+." "TLS 1.2+" { tags "External" }
database          = container      "Database" "Database whose access is restricted
                    based on RBAC."
```

Both are placeholders, not things. `Database` and `External Services` are groups whose
actual members ARE in the graph — `PostgreSQL` and `Valkey` are containers, and `Elavon`,
`Mastercard`, `CCnet` and `PGSP` are ExternalSystems. So the diagram shows a box for the
category *and* boxes for its members, which reads as six things where there are four.

**The structure prompt already forbids this** (rule 8: "Never emit a bare category word —
'Database', 'Cache', 'Services', 'Microservices' — as an element when the document names
the specific thing"). The escape clause is the defect: *"when the document names the
specific thing"* invites the model to emit the category whenever the document itself is
generic, which it usually is. A category word is a placeholder either way.

**Shape.** Two halves, and the second is the one that catches what the prompt will still
miss:

1. **Tighten rule 8** to forbid the category word outright when it is a plural or
   mass noun denoting a group (`External Services`, `Services`, `Components`,
   `Microservices`, `Databases`), rather than only when a specific name is available.
2. **A `check_category_names` validator**, following `style_as_element` and
   `check_element_types` — a deterministic check that flags an element whose name is a
   bare category. Prompt rules are advice; a validator is a finding. The existing
   `style_as_element` is the precedent: the prompt already forbade styles as elements and
   the model emitted `Microservices` anyway, so a validator was added and it now catches
   it every run (1 style merged on the last run).

### Defect 2 — one system, two nodes

The graph holds the same system twice:

```
platform:payment_gateway_platform         kind=Platform        label="Payment Gateway Platform"
softwaresystem:payment_gateway_platform   kind=SoftwareSystem  label="Payment Gateway Platform"
```

One comes from REQ-G (the requirements profile names the system a `Platform`) and one
from ARC-G (the architecture profile names it a `SoftwareSystem`). `_resolve` matches by
label *within* a kind, so the two profiles each mint their own node for one real thing.

Consequences, all visible in the emitted DSL:

- **Two boxes** for one system, disambiguated only by the identifier suffix this item's
  predecessor added (`payment_gateway_platform` vs
  `payment_gateway_platform_softwaresystem`). A reader sees a duplicate.
- **The system choice is a coin toss.** `c4_model` picks the element with the most
  descendants, so ARC-G's node becomes *the* system and REQ-G's becomes a second
  top-level `softwareSystem` with no containers. Nothing says which is authoritative.
- **Containment can point at the wrong one.** A container whose `part_of` names the
  requirements-profile node is drawn outside the boundary that holds its siblings, and
  the `nesting` check cannot see it — the parent level is `context`, which is what a
  container expects. This is the failure mode that makes it worth fixing rather than
  tolerating: a check that passes while the diagram is wrong.

**Shape.** Identity, not presentation. Either `_resolve` matches an existing node by
label across *compatible* kinds for a known set of element-like kinds, or the ingest
records the second node as an alias of the first. The first is smaller and matches how
`_resolve` already works; the risk is an over-eager merge (a `Container` and a
`Component` that share a name in different documents are not one thing), which is why the
compatible set has to be explicit and small. Whatever is chosen, the review gate should
be able to see the merge, because merging two nodes is not reversible from the UI.

### Also measured, and deliberately NOT changed

- **`empty` counts against run completeness.** One connections call answered `empty` on a
  chunk with no runtime calls in it, so the run is `PARTIAL` and `/c4` reports
  `run-completeness`. This is ADR-0013's deliberate rule — `empty` and `failed` both mean
  "content may be missing", because the pipeline cannot distinguish "nothing was there"
  from "the model missed it". Changing it would trade a false alarm for a silent miss,
  which is the wrong direction. Recorded here so the next reader does not "fix" it.
- **One element with no stated level** (`inferred-level: 1`). The C4 view infers it from
  the node kind and says so. Not a defect.
- **Four `unlevelled` DeploymentNodes.** Deployment is not L1–L4 and is reported rather
  than placed (ADR-0029, decision 5).

### Acceptance

- No element in a re-extracted `payment_platform_arch.md` is named after a bare category:
  the `c4_scorecard.py` count of `Database`/`External Services`-style names is zero.
- `Payment Gateway Platform` is one node, and the C4 model draws one system box.
- A container whose `part_of` names a system is drawn inside that system's boundary.
- The scorecard's `READY` verdict is reachable on this test case, or every remaining
  failed rule is one this entry argues is not a defect.

### Related

- [ADR-0029](../../decisions/ADR-0029-c4-specification-view.md) — the view these defects
  are visible in, and the checks that report them.
- [YB-052](../entries/YB-052-reflexive-and-duplicate-extraction.md) — the defects the same
  view found before this run; both are now fixed and both counts are zero.
- [YB-051](../entries/YB-051-connections-dropped-at-ingest.md) — the connections pass. Its
  output is what the prompt rule here is measured against.
- [YB-004](../entries/YB-004-model-output-not-structurally-stable.md) — the same document
  extracted twice produced 27 then 30 elements and 19 then 13 connections, which is the
  variance this item's prompt change has to be judged against rather than a single run.
