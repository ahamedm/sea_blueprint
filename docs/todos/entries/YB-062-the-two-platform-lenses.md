---
id: YB-062
legacy: null
title: "The two platform lenses — and the joint decision with YB-044 on what a technology platform IS"
status: open
priority: high
area: "`ontology/enterprise_structure.yaml` (`Platform`), `ontology/architecture_base.yaml` (`SoftwareSystem.system_class`, `DeploymentNode`, `shared_across_enterprise`), `app/` (the capability-coverage audit)"
created: 2026-10-03
updated: 2026-10-03
design: docs/design/platform-instances.md
record: null
superseded_by: []
related: [YB-044, YB-053, YB-055, YB-008, YB-007]
blocks: []
blocked_by: []
---

# YB-062 — The two platform lenses

> **Open. Promoted from [ISS-12](../../ISSUES.md#iss-12--the-two-platform-lenses-are-named-in-two-namespaces-and-neither-is-complete)
> on 2026-10-03, deliberately at the same time as YB-044.**
> This is **one** decision, not two items that cite each other: YB-044 chose a platform
> *type* without the lens question being answered, and the lens question cannot be
> answered without saying what an instance points at. Deciding them apart is how a third
> vocabulary gets minted.

## The question

*"A Platform can be viewed through two lenses: Business (a Payment Platform supporting
Payment Products like Card, APM) and Technical (OpenShift, Airflow, n8n). Does the
ontology support both?"*

**Both are named, asymmetrically, and nothing joins them.** The ontology says so itself —
`Platform`'s naming note reads *"this is the COMMERCIAL sense of platform… It is NOT the
same as an 'Enterprise Technology Platform' (OpenShift, Splunk, Grafana) — that concept
lives in `architecture_base.SoftwareSystem.system_class`, deliberately under a different
name so the two do not blur."*

| Lens | Where | Shape |
|---|---|---|
| Business / commercial | `enterprise_structure.Platform` | A class: Product **and** System, `value_proposition`, `target_extenders`, `contracts -> PlatformContract`, `extended_by_products -> Product`, `hosted_systems -> System`, `multi_tenancy_model`, `backward_compatibility_policy`, plus free-text `platform_type` |
| Management scope (covers both) | `architecture_base.SoftwareSystem.system_class` | `SoftwareSystemClass`: ENTERPRISE_TECHNOLOGY_PLATFORM, BUSINESS_TECHNOLOGY_PLATFORM, BUSINESS_APPLICATION, SHARED_TECHNICAL_SERVICE, INTEGRATION_PLATFORM |
| Supporting flags | `SoftwareSystem` | `shared_across_enterprise`, `managed_by` (free text), `origin`, `deployment_model`, `vendor` |

## What works

The architecture lens is real and in use: live scope `payments_v2` holds **2 verified
`system_class` facts**, both `BUSINESS_TECHNOLOGY_PLATFORM` (Payment Platform, Payment
Gateway Platform), and the harness gates that every `SoftwareSystem` carries one
(`inv_software_systems_classified`). `repair.system_under_design` reads it to refuse an
enterprise technology platform as the anchor for an unplaced element — the one place the
enum's own auditor note is implemented.

## The four gaps

- **Gap 1 — a technology platform can only be a `SoftwareSystem`.** `system_class` is
  declared on `SoftwareSystem` alone (checked across the layer), and in the live scope
  OpenShift is a `DeploymentNode` plus a `TechnologyStack`, so **it carries no lens at
  all**. Airflow and n8n would land the same way. A technology platform is a thing with
  an owner, a lifecycle and consumers; today the ontology can only say where it *runs* or
  what something *uses*.
- **Gap 2 — two vocabularies for one axis, unreconciled.** `Platform.platform_type` is
  free text whose own examples include "infrastructure" and "integration platform",
  overlapping `SoftwareSystemClass.ENTERPRISE_TECHNOLOGY_PLATFORM` and
  `INTEGRATION_PLATFORM`. "Is this an infrastructure platform?" has two answers and no
  rule relating them.
- **Gap 3 — no join between the lenses.** `System.is_part_of_platform`,
  `Product.extends_platform` and `Application.consumes_platform_contracts` all sit on the
  enterprise side and all have **0 instances** in the live scope; nothing on the
  architecture side points at a `Platform`. So the commercial platform and the
  architecture element implementing it are two nodes joined only by label — the YB-053
  defect 2 mechanism, which is also why `Payment Gateway Platform` exists twice ([ISS-10](../../ISSUES.md#iss-10--one-system-three-names-the-notation-draws-the-same-architecture-twice)).
- **Gap 4 — the commercial half is declared but unreachable, exactly like
  `ConceptAttribute` was.** `Platform.contracts -> PlatformContract` is `inlined_as_list`,
  the nested shape no pass can emit (YB-055's defect), so a platform's contracts,
  multi-tenancy and extenders cannot reach the graph. Live: 0 `Product`, 0 `SubProduct`,
  0 `PlatformContract` nodes. Ownership (`managed_by`) is free text pending YB-008.

## On the payment-products half of the question

The pack models methods, not products: `PaymentMethod` = CARD, DIRECT_DEBIT,
BANK_TRANSFER, WALLET, INVOICE, OPEN_BANKING, with instruments as concepts (`Card`,
`BankAccount`). "APM" as a category is not named — the pack lists the individual methods
instead, consistent with its "a state is a value, not a concept" reasoning. And a platform
does not state what it supports: `payment_method` is declared on the pack's root
`PaymentDomainConcept`, so a domain concept carries it, not the `Platform`.
`Product`/`SubProduct` exist for "the products a platform is extended by" and are empty.

## Options, cheapest first

- **(a)** Make the capability-coverage audit honour the auditor note the enum already
  states (exclude ENTERPRISE_TECHNOLOGY_PLATFORM from business coverage) — no ontology
  change. Today nothing in `app/` reads `system_class` except a tooltip, so the false-gap
  risk the enum warns about is still live.
- **(b)** Retire the duplicate vocabulary: `platform_type` as an enum, or a pointer to
  `SoftwareSystemClass`.
- **(c)** Declare the architecture→enterprise binding
  (`SoftwareSystem.realizes_platform -> Platform`, a binding class in the higher layer per
  the import rule) — which would also give [ISS-10](../../ISSUES.md#iss-10--one-system-three-names-the-notation-draws-the-same-architecture-twice) a mechanical answer: two nodes realizing
  one platform ARE one thing.
- **(d)** A `TechnologyPlatform` entity in `enterprise_structure` (owner, criticality,
  consumers) realized by architecture elements, Pattern B — the shape `Container ->
  Application` already uses.

Every one of these must name its check, and (c)/(d) cost prompt budget (YB-007).

## The joint decision with YB-044

YB-044 proposes a platform **type** (workspace-global, one node) with N **instances**
(`DeploymentNode`s) each carrying a sharing scope
([design doc](../../design/platform-instances.md)). Three parts, and they are not equally
safe:

1. **The instance and its sharing scope — safe now.** Additive schema: the instance gains
   a link to the platform type it instantiates and a topology enum
   (`ENTERPRISE` / `BUSINESS_UNIT` / `DEDICATED`). Nothing existing changes meaning, and
   it is the concrete answer to **Gap 1's "no lens at all"** — a cluster with a named type
   and a sharing scope is a technology platform the graph can reason about, without yet
   deciding where that type lives in the vocabulary.
2. **The type — NOT safe now.** YB-044's design says the type is *"a `TechnologyStack` and
   not an element"* (`platform-instances.md:65-66`). That is exactly the decision this
   entry has not made, and choosing it by implementing it would:
   - mint a **third vocabulary** for one axis, beside `Platform.platform_type` and
     `SoftwareSystemClass` — the Gap 2 defect, made worse rather than fixed;
   - collide with **option (d)**, which puts a technology platform in
     `enterprise_structure` as a first-class entity with owner, criticality and consumers
     — a `TechnologyStack` has none of those, and cannot acquire them without becoming
     the entity (d) describes.
3. **`shared_across_enterprise` — the shared seam, and the reason to do this early.**
   The slot is currently claimed from both sides. `architecture_base.yaml` says it is
   *"usually implied by `system_class`"*; YB-044's acceptance says it must be *"derived
   from the instance scope, not asserted alongside it."* Those are two mechanisms for one
   fact, and YB-044 is right that they cannot both hold: whether a platform is
   enterprise-shared is a property of the **deployment instance**, not of the software
   class. Reconciling it is cheap — one slot, one derivation, one check — and it is the
   only place the two items must agree before either can be finished. Doing it first is
   what stops (1) and (2) from drifting into separate vocabularies.

## What closes it

A chosen option for the type (a–d), taken **together with** YB-044's type half, plus:

- YB-044's instance half landed without it (it does not depend on the choice);
- `shared_across_enterprise` derived from the instance scope, with the `system_class`
  implication removed rather than left as a second rule;
- `platform_type` either given a rule relating it to `SoftwareSystemClass` (option b) or
  retired;
- a check named for whichever option is chosen — (a) and (c) each name one, (d) needs the
  extraction cost stated against YB-007.
