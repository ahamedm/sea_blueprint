---
id: YB-044
legacy: null
title: "Platform instances and deployment topology — one platform type, many instances, each with a sharing scope"
status: open
priority: high
area: "`ontology/architecture_base.yaml` (DeploymentNode, sharing scope), `agents/architecture_extraction/passes.py`, `core/knowledge/ingest.py`"
created: 2026-09-26
updated: 2026-10-03
design: docs/design/platform-instances.md
record: null
superseded_by: []
related: ["YB-042", "YB-043", "YB-011", "YB-010", "YB-009", "YB-062"]
blocks: []
blocked_by: []
---

# YB-044 — Platform instances and deployment topology

> **Open work.** This file is the source of truth for this item; `TODO.md` is generated from it.

**Full analysis:** [`docs/design/platform-instances.md`](../../design/platform-instances.md)

> ## Split, 2026-10-03 — this item is now TWO decisions, and only one of them is unblocked
>
> **[YB-062](YB-062-the-two-platform-lenses.md) is the other half of this decision.** It
> was promoted from ISS-12 on the same day, because the two were cross-referencing
> nothing while sharing one question: *what IS a technology platform, and where does its
> type live in the vocabulary?*
>
> - **Unblocked — the instance and its sharing scope.** Additive schema: the instance
>   gains a link to the platform type it instantiates, plus a topology enum
>   (`ENTERPRISE` / `BUSINESS_UNIT` / `DEDICATED`). Nothing existing changes meaning, and
>   it answers ISS-12 Gap 1's "a technology platform carries no lens at all".
> - **Blocked on YB-062 — the type.** The proposal below says the type is *"a
>   `TechnologyStack` and not an element"* (`platform-instances.md:65-66`). That is a
>   vocabulary decision YB-062 has not made, and taking it here would mint a **third**
>   name for one axis beside `Platform.platform_type` and `SoftwareSystemClass` (its Gap
>   2), and collide with its option (d) — a `TechnologyPlatform` entity carrying owner,
>   criticality and consumers, which a `TechnologyStack` has nowhere to put.
> - **The seam — `shared_across_enterprise`.** This item's acceptance already says it must
>   be *derived from the instance scope, not asserted alongside it*, and the slot's own
>   description currently says it is *"usually implied by `system_class`"*. Two mechanisms
>   for one fact is the single point the two items must agree on, and it is cheap to
>   settle — which is why these are worth doing early together rather than late apart.
>
> ## Status, 2026-10-03 — the unblocked half LANDED
>
> Landed in lockstep, because on this repo a slot declared on one end and not the other
> becomes the declared-but-unemitted defect it keeps rediscovering:
>
> | Layer | Change |
> |---|---|
> | Ontology | `DeploymentNode.platform_type` (cited string), `.sharing_scope` + the `SharingScope` enum, `.serves` (multivalued); `shared_across_enterprise` stopped claiming `system_class` as a second mechanism |
> | Extraction | `ElementRecord` gained the three fields — the first DeploymentNode slots that pass has ever emitted |
> | Ingest | the structure-fact whitelist (`ingest.py:480`) gained `platform_type` and `sharing_scope`; `serves` writes one EDGE per target |
> | Guards | `serves` added to `IRREFLEXIVE_PREDICATES` and to the C4 view's stated exclusions; `("sharing_scope", "SharingScope")` added to `check_enum_membership` |
> | Prompt | structure-pass rules 3 and 6: the type is never an element, a platform is not classified both ways in one run; rule 8 asks for one name per thing |
>
> **Still open, and deliberately not decided by implementing them:**
>
> - **`serves`' range.** Declared `SoftwareSystem` to mirror the singular `parent_system`
>   it augments. The design's prose says "serves 200 products", and `Product` IS reachable
>   from this layer (`architecture_base` imports `enterprise_structure`), so the two
>   readings are genuinely undistinguished. A `Product` variant would be a second slot,
>   not a re-range.
> - **Deduplication (OpenShift vs OpenShift Platform).** Only the SAFE half landed: rule 8
>   now asks for one name per thing, which is a prompt instruction. A normalised or fuzzy
>   `named_key` was NOT adopted — merging two labels on similarity is the confident
>   wrong-join this repo ranks as worse than a missing join, and `Payment Platform` vs
>   `Payment Gateway Platform` are exactly the pair it would have to get right.
> - **The multi-tenancy join**, split out as its own piece:
>   [YB-063](YB-063-the-multi-tenancy-requirement-has-no-instance-to-target.md). Neither
>   this item nor YB-062 decides what a tenancy requirement attaches to.
> - **The prompt-budget consequence.** The `SharingScope` enum name tipped the design
>   profile's scaffolding 10 bytes over its document budget — measured, and recorded as
>   [ISS-15](../../ISSUES.md#iss-15--the-design-profiles-prompt-scaffolding-sits-at-its-ceiling-so-any-vocabulary-growth-breaks-the-budget-guard)
>   rather than absorbed by adjusting that guard.

### The distinction the graph cannot make

OpenShift is one platform **type** with many **instances** (clusters), and each
instance has a sharing topology: shared for the enterprise, shared for a business
unit, or dedicated to a platform/product. The model must hold *one* OpenShift type
and *N* instances, with the topology on the instance — the same type is routinely
deployed three ways at once, which a property of the type cannot express.

Today a `DeploymentNode` (`ontology/architecture_base.yaml:1127`) has
`infrastructure_type`, `environment`, `region`, `network_zone` and `hosted_on`,
but **no link to the platform type it instantiates and no sharing scope**, and its
`parent_system` is **singular** — so a cluster shared by 200 products cannot be
the parent of 200 things. `shared_across_enterprise` (`:813`) is a boolean that
approximates the topology without naming it.

The ontology already admits the gap: `SoftwareDeploymentModel` (`:468`) says
*"A fuller Deployment construct is likely warranted later, once deployment
topology and operational responsibility need modelling properly"* (`:475-477`).

### It is already visible in the output

From the latest architecture run:

```
DeploymentNode   OpenShift              parent=-
DeploymentNode   OpenShift Platform     parent=-
SoftwareSystem   Prometheus   system_class=ENTERPRISE_TECHNOLOGY_PLATFORM
SoftwareSystem   Grafana      system_class=ENTERPRISE_TECHNOLOGY_PLATFORM
SoftwareSystem   Splunk       system_class=ENTERPRISE_TECHNOLOGY_PLATFORM
```

One cluster, two nodes, and OpenShift classified differently from its own peers —
because the structure pass teaches "Infrastructure (OpenShift, data centre,
cluster) is a DeploymentNode" (`agents/architecture_extraction/passes.py:303`)
while `system_class` makes the neighbouring platforms SoftwareSystems. The two
rules disagree about where a platform lives.

### Why it matters here

This is the rule that resolves YB-042's open question — *does a shared enterprise
platform exist once per workspace or once per product?* Neither, precisely: the
**type** is workspace-global (shared vocabulary, like the domain ontology) and the
**instance** is first-class with a sharing scope, serving many products or one.
That is what makes the workspace's shared layer bounded instead of a free-for-all.

It also completes a join that already exists on the requirements side:
`PlatformMultiTenancyRequirement` (`ontology/requirements_base.yaml:1670`) states
an `isolation_level` and `data_segregation_model` for a target platform, and there
is currently no instance node for it to target.

### Acceptance

- One OpenShift **type** in a workspace; N **instances**, each with a sharing
  scope (enterprise / business unit / dedicated).
- A cluster shared by many products is one node with many `serves` edges.
- `shared_across_enterprise` is derived from the instance scope, not asserted
  alongside it.
- A `PlatformMultiTenancyRequirement` can be checked against the instance it
  targets.
- A re-run does not emit `OpenShift` and `OpenShift Platform` as two nodes.
