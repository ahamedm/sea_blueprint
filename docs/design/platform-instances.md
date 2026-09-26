# Platform instances and deployment topology

> **Design document** for `YB-044`. Status is tracked in that entry.
> This is the rule that resolves [`workspace-structure.md`](workspace-structure.md) §8 decision 4.

---

## 1. The problem

One platform product — OpenShift — has many **instances** (clusters), and each
instance has a **sharing topology**:

- shared for the **enterprise** (one cluster, every product),
- shared for a **business unit**,
- **dedicated** to a platform or product.

Same platform, many individual instances. The graph has nowhere to put "which
instance", and no vocabulary for the sharing scope. The result is that the
extractor emits the platform as a structural node and the model cannot say what it
serves.

## 2. What already exists

| Construct | Where | What it gives |
|---|---|---|
| `DeploymentNode` | `ontology/architecture_base.yaml:1127` | "where containers run"; `infrastructure_type`, `environment`, `region`, `network_zone`, `hosted_on` (nesting), `runs_containers` (multivalued), `parent_system` (**singular**) |
| `InfrastructureType` | `:181` | `CONTAINER_PLATFORM` = "Kubernetes/ECS/OpenShift cluster", `VM`, `BARE_METAL`, `SERVERLESS`, `MANAGED_SERVICE`, `ON_PREMISE_DC`, `EDGE_CDN` |
| `DeploymentEnvironment` | `:172` | DEV / TEST / STAGING / PRODUCTION / DISASTER_RECOVERY |
| `SoftwareSystemClass` | `:406` | `ENTERPRISE_TECHNOLOGY_PLATFORM` — "shared internal infrastructure owned by a cross-cutting team … OpenShift, Grafana, Splunk" |
| `shared_across_enterprise` | `:813` | a **boolean** on SoftwareSystem — the binary ancestor of a sharing scope |
| `SoftwareDeploymentModel` | `:468` | self-hosted / vendor-managed-dedicated / SaaS / hybrid, and an explicit admission: *"A fuller Deployment construct is likely warranted later, once deployment topology and operational responsibility need modelling properly"* (`:475-477`) |
| `PlatformMultiTenancyRequirement` | `ontology/requirements_base.yaml:1670` | on the requirements side: `target_platform`, `isolation_level` ("dedicated compute", "dedicated storage", "full stack isolation"), `data_segregation_model`, `tenant_capacity`, `cross_tenant_isolation` |
| `EnterpriseTopologyType` | `ontology/enterprise_structure.yaml:44` | STANDALONE / PLATFORM_CENTRIC / HYBRID |
| `Platform` | `enterprise_structure.yaml:341` | the **commercial** sense, explicitly *not* OpenShift |

So the pieces are adjacent but never joined: a deployment node has no link to the
platform *type* it instantiates, and no sharing scope.

## 3. The confusion is already in the output

Latest architecture run (deepseek-v4-pro):

```
DeploymentNode   OpenShift              parent=-  system_class=-
DeploymentNode   OpenShift Platform     parent=-  system_class=-
DeploymentNode   DataCentre             parent=-
DeploymentNode   Data Centre            parent=-
SoftwareSystem   Prometheus   system_class=ENTERPRISE_TECHNOLOGY_PLATFORM
SoftwareSystem   Grafana      system_class=ENTERPRISE_TECHNOLOGY_PLATFORM
SoftwareSystem   ELK Stack    system_class=ENTERPRISE_TECHNOLOGY_PLATFORM
SoftwareSystem   Splunk       system_class=ENTERPRISE_TECHNOLOGY_PLATFORM
```

Two OpenShift nodes for one cluster and two data-centre nodes, and OpenShift
classified differently from its own peers. The structure pass teaches exactly this
by omission — `agents/architecture_extraction/passes.py:303`: *"Infrastructure
(OpenShift, data centre, cluster) is a DeploymentNode"* — while `system_class`
makes Grafana and Splunk SoftwareSystems. The two rules disagree about where a
platform lives.

## 4. The model

Three levels, and the workspace decision follows from them:

1. **Platform type** — OpenShift. One node, **workspace-global**: shared
   vocabulary, like the domain ontology. A `TechnologyStack` and not an element.
2. **Platform instance** — a cluster. A `DeploymentNode` with
   `infrastructure_type = CONTAINER_PLATFORM`, plus the two slots it is missing:
   - `platform_type` — the type it is an instance of;
   - `sharing_scope` — the topology below.
3. **Topology** — a new enum on the **instance**, not the type:
   `ENTERPRISE` / `BUSINESS_UNIT` / `DEDICATED`. The same product type is
   routinely deployed three ways at once (an enterprise cluster for shared
   services, a dedicated cluster for a regulated product), which a property of the
   type cannot express. `shared_across_enterprise` (`:813`) becomes **derived**
   from the instance scope rather than asserted in parallel.

One structural gap blocks this and is worth naming separately:
**`DeploymentNode.parent_system` is singular.** A cluster shared by 200 products
cannot be the `parent_system` of 200 things. It needs a multivalued `serves`
(or `hosts`), which is the edge the workspace model actually reads.

## 5. Why this answers the workspace question

YB-042 §8 asked whether a shared enterprise platform exists once per workspace or
once per product. The answer is **neither, precisely**:

- the **type** is workspace-global — one node, shared reference frame;
- the **instance** is a first-class node carrying a sharing scope, and may serve
  many products or exactly one.

That is Option C with a rule instead of a vague "shared layer": *shared
vocabulary, scoped instances.* It also means the shared/workspace-level graph
holds types and instances, not product content — which bounds how contended that
layer is.

## 6. The requirements join this enables

`PlatformMultiTenancyRequirement` (`requirements_base.yaml:1670`) already states an
`isolation_level` and a `data_segregation_model` for a target platform. With
instances modelled, the audit can check it: a requirement demanding "full stack
isolation" aimed at an instance whose `sharing_scope` is ENTERPRISE is a finding.
Today the two halves cannot meet, because the instance does not exist as a node.

## 7. Extraction impact

- Structure pass rule 3 must say infrastructure is a `DeploymentNode` that is an
  **instance of a named platform type**, and that the type itself is never an
  element (the technology pass already gives the type a home).
- The duplicate-name problem (`OpenShift` / `OpenShift Platform`) is a merge-key
  issue: `named_key` folds exact names only, so a name variant becomes a second
  node.
- Classification must be consistent: a platform is either an
  `ENTERPRISE_TECHNOLOGY_PLATFORM` system or an instance of one, never whichever
  the run happened to prefer.

## 8. Acceptance

- One OpenShift **type** in the workspace; N **instance** nodes, each with a
  `sharing_scope`.
- A cluster shared by many products is **one** node with many `serves` edges, not
  one node per product.
- `shared_across_enterprise` is derived from the instance scope, not asserted in
  parallel.
- A `PlatformMultiTenancyRequirement` can be checked against the instance it
  targets.
- A re-run does not produce `OpenShift` and `OpenShift Platform` as two nodes.

## 9. Related

- [YB-042 — Workspace structure](workspace-structure.md) — §8 decision 4.
- [YB-043 — System of record](system-of-record.md) — what the shared layer costs.
- [YB-011](../todos/entries/YB-011-domain-ontology-layer.md) — the shared
  vocabulary this joins.
- [YB-010](../todos/entries/YB-010-rdf-knowledge-layer.md) — "is this requirement
  satisfied by a dedicated instance?" is a query.
