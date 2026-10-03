---
id: YB-063
legacy: null
title: "A multi-tenancy requirement targets the commercial Platform, and there is no instance to check it against"
status: open
priority: medium
area: "`ontology/requirements_base.yaml` (`PlatformMultiTenancyRequirement.target_platform`, `isolation_level`, `data_segregation_model`), `ontology/architecture_base.yaml` (`DeploymentNode` once YB-044 lands)"
created: 2026-10-03
updated: 2026-10-03
design: docs/design/platform-instances.md
record: null
superseded_by: []
related: [YB-044, YB-062, YB-055]
blocks: []
blocked_by: []
---

# YB-063 — The multi-tenancy requirement has no instance to target

> **Open. Split out of YB-044 on 2026-10-03** because neither half of that item
> decides it: YB-044 adds the deployment instance, YB-062 decides what a platform
> *type* is, and this asks what a multi-tenancy requirement POINTS AT. It is the
> third piece, and leaving it inside YB-044 is how it gets silently answered by
> whichever option lands first.

## The measurement

`PlatformMultiTenancyRequirement` carries:

| Slot | Range | Read by |
|---|---|---|
| `target_platform` | `Platform` (the COMMERCIAL sense) | nothing |
| `isolation_level` | `string` | nothing |
| `data_segregation_model` | `string` | nothing |
| `tenant_capacity`, `cross_tenant_isolation` | `string` | nothing |

`Platform`'s own naming note is explicit: *"this is the COMMERCIAL sense of platform… It
is NOT the same as an 'Enterprise Technology Platform' (OpenShift, Splunk, Grafana) —
that concept lives in `architecture_base.SoftwareSystem.system_class`."* So a
multi-tenancy requirement is stated against the commercial platform, and the thing that
actually isolates tenants — the cluster or runtime an instance deploys to — is not in its
range at all.

YB-044's acceptance criterion 4 asks for exactly this: *"A `PlatformMultiTenancyRequirement`
can be checked against the instance it targets."* Today it cannot, and there is no node to
check it against.

**On the slots being unread** — a grep for `isolation_level` does return hits, in
`core/knowledge/store_sql.py` and `core/jobs.py`, where it is SQLite's DB-API parameter
and has nothing to do with tenancy. No code reads the GRAPH slots. Worth stating because
the naive grep reads as a contradiction and is not one.

## Why it is not covered by YB-044 or YB-062

- YB-044 adds the instance (`platform_type`, `sharing_scope`, `serves`) and stops there.
- YB-062 decides where a platform TYPE lives in the vocabulary.
- Neither says what a *requirement about tenancy* attaches to, and the answer differs by
  option: under YB-062 option (d) a `TechnologyPlatform` entity could carry the tenancy
  requirement directly, which re-ranges `target_platform` without needing an instance at
  all.

## Options

1. **Add `target_instance -> DeploymentNode` beside `target_platform`.** Additive: the
   commercial requirement keeps its target, and gains the deployment it is realised by.
   Mirrors the ref-then-resolve convention only loosely — this is a second target rather
   than a resolution.
2. **Re-range `target_platform` to the instance.** Simplest graph, but it moves the
   commercial platform's own multi-tenancy claim (`Platform.multi_tenancy_model` already
   exists) onto a deployment node, which is a different assertion.
3. **A binding class** (`PlatformTenancyBinding`) joining requirement → commercial
   platform → instance, in the higher layer. Most explicit, most expensive in prompt
   budget (YB-007).

## What closes it

A chosen option, the schema change, and a check that reads the chosen slots — since
nothing reads `isolation_level` or `data_segregation_model` today, "which platform is
tenant-isolated, and how" has no answer in the graph even for the commercial case.
