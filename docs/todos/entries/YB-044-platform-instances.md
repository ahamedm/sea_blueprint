---
id: YB-044
legacy: null
title: "Platform instances and deployment topology — one platform type, many instances, each with a sharing scope"
status: open
priority: high
area: "`ontology/architecture_base.yaml` (DeploymentNode, sharing scope), `agents/architecture_extraction/passes.py`, `core/knowledge/ingest.py`"
created: 2026-09-26
updated: 2026-09-26
design: docs/design/platform-instances.md
record: null
superseded_by: []
related: ["YB-042", "YB-043", "YB-011", "YB-010", "YB-009"]
blocks: []
blocked_by: []
---

# YB-044 — Platform instances and deployment topology

> **Open work.** This file is the source of truth for this item; `TODO.md` is generated from it.

**Full analysis:** [`docs/design/platform-instances.md`](../../design/platform-instances.md)

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
