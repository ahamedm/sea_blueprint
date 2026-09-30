# Omnigraph as a knowledge-layer candidate — an evaluation against YB-010

> **Evaluation, not a plan.** Companion to
> [`terminusdb-evaluation.md`](terminusdb-evaluation.md), which covers the first
> candidate in detail. Raised as *"another growing project, which comes close to such
> time-travel, ontology requirements"*
> ([ModernRelay/omnigraph](https://github.com/ModernRelay/omnigraph)).
>
> **Evidence discipline.** Everything about Omnigraph here is from its own README,
> schema reference, query reference and GitHub API, fetched 2026-09-30, and is
> *vendor-stated or measured from the repository* — not independently run. **[M]** marks
> what was measured. Claims about this repo were run against the code.

---

## 1. Verdict in one paragraph

**The premise splits, and the two halves land in opposite places.** Omnigraph is the
strongest of the three candidates on **time-travel and collaboration** — branch-per-agent,
review-and-merge, commit-pinned reads, optimistic concurrency — which is the YB-046
problem, and it independently states this platform's own architecture. It is the
**weakest of the three on ontology**: it has no RDF, no SPARQL, no OWL/SHACL and, more
fundamentally, **no class hierarchy** — so the LinkML ontology this graph is built on
cannot be expressed in its schema language at all. For YB-010 it is not a candidate. For
YB-046 it is the most relevant reference model found so far.

## 2. What it is **[M]**

| | |
|---|---|
| Description | *"Lakehouse graph database for context assembly & multi-agent coordination"* — "the operational state and coordination layer for fleets of agents" |
| Language / licence | **Rust** (edition 2024, ~8 workspace crates), **MIT** |
| Shape | `omnigraph` CLI + `omnigraph-server` (Axum). Cluster declared as code (`cluster.yaml`), Terraform-style `plan` / `apply`. Production storage is an S3-compatible object store over [Lance](https://github.com/lance-format/lance); an **embedded local file mode** exists (`omnigraph init --schema schema.pg ./graph.omni`) |
| Model | typed **property graph**: `node` / `edge` / `interface` declarations, scalar properties, `enum(...)`, lists, `Vector(N)`, `Blob` |
| Query | its own language, **`.gq`** — `query name($p: String) { match { … } return { … } }` |
| Collaboration | `branch create` / `branch merge` across the whole graph; "hundreds of agents … on parallel isolated branches, and every change is reviewed and merged safely" |
| Access control | **Cedar** policy enforced server-side on every mutation; actor resolved server-side; tokens hashed |
| Retrieval | graph traversal + vector ANN + full-text + reciprocal rank fusion in one runtime |
| Clients | TypeScript SDK, **MCP server**, HTTP/OpenAPI. **Python SDK: "coming soon"** |

**Maturity, measured from the GitHub API on 2026-09-30:** created **2026-04-10** (~5½
months old), last push **2026-09-30** (same day), **1,234 stars**, 249 forks, 89 open
issues, 31 watchers, topics include `knowledge-graph`, `context-graph`, `versioning`,
`lance`, `mcp`. Governance: open an issue first, and an `accepted` label is the green
light for a PR.

## 3. The ontology claim does not hold

Nothing in the README, the [schema reference](https://github.com/ModernRelay/omnigraph/blob/main/docs/user/schema/index.md)
or the [query reference](https://github.com/ModernRelay/omnigraph/blob/main/docs/user/queries/index.md)
mentions RDF, SPARQL, OWL or SHACL. The schema is a typed property graph. Absence from
documentation is evidence, not proof — but it is the whole surface a user would be told
about, and the model it describes cannot hold what this repo's ontology declares.

`linkml`'s expressive features this repo actually depends on, against what `.pg` offers:

| Feature | In `architecture_base.yaml` / the pack | `.pg` |
|---|---|---|
| `is_a` class chains | everywhere; **walked at runtime** by `ontology_slot_range` and `ontology_subclasses` in ADR-0032's containment guards, and by `digest.py`'s ancestor walks | **absent** — no inheritance |
| `abstract` types | `ArchitectureElement`, `PaymentDomainConcept`, `PaymentInstrument`, `PaymentOperation` | **absent** |
| `mixins` | `ExternallyReferenced`, `Provenanced` | `interface` exists, but it is *property reuse*, not typed polymorphism |
| `range` onto an abstract class | `Connection.source: ArchitectureElement` (8 concrete subtypes), `Payout.destination: PaymentInstrument` (3) | **absent** — "an edge's endpoint node types must already be declared", i.e. fixed concrete endpoints |

That last row is the practical blocker rather than a purity concern: a polymorphic slot
would have to become one edge type per permitted endpoint, or collapse to a generic node
type. And a generic encoding — one `Node { kind, label }` plus reified assertions, which
*is* expressible in `.pg` — would store the instance graph while making the store's own
schema validation vacuous, because the typing would live only in Python. The ontology
vocabulary itself still has to be translated LinkML → `.pg`, and cannot be.

That is the same objection raised against TerminusDB (a second schema language to keep
aligned — the drift `check_schema_consistency` exists to catch) but **stronger**: there
the translation is possible, here the target language lacks the source's features.

## 4. Where it is genuinely the best of the three

Set the ontology aside and this is the most interesting project of the two engines,
because its stated premise *is* this platform's architecture, stated by someone else.

| Capability | Why it matters here |
|---|---|
| **Branch per agent/task, merged on review** | `living-system-architecture.md` calls Initiatives "feature branches of the architecture"; `branching-and-promotion.md` (YB-046) designs exactly this and it is unbuilt. Omnigraph ships it, at a scale of "hundreds of agents" |
| **Reads at `--branch` / `--snapshot`** | Immutable-commit reads pinned with the returned rows — the repo's `load_revision`, but native and general |
| **`graph_commit_id` + `--if-commit`** | Optimistic concurrency at commit granularity: read, then write conditionally on the snapshot not having moved. This is the repo's `expected_version` CAS token, generalised (and the reason a lost update is impossible by construction) |
| **Static `reads` / `writes` per query** | Every compiled query reports the node/edge types it may inspect or change. That is the *same instinct* as ADR-0030's output-consumption accounting — know a unit's blast radius before trusting it |
| **Correlated `not { }` / `count { }`** | "Which requirements have **no** implementing element" is a native pattern, where the repo computes it in `realization_report` |
| **Restrictive schema migration with stable codes** | Adds nullable properties and enum widening; **refuses** "add a required property to a populated type" (`OG-MF-103`) and type changes, with `schema plan` preview. That is the refusal-at-the-boundary posture of ADR-0030/0032 applied to schema |
| **`lint` with `Q`/`L`/`T` codes; `L201`** | Warns when a nullable property is never set by any update query — a coverage check over the query set, adjacent to the gap report's job |
| **Cedar on every mutation path** | One enforcement point for HTTP, CLI and SDK — the "one choke point every fact crosses" argument the repo makes for `add_assertion` |
| **Declared as code, plan/apply** | Matches the repo's config-externalization convention; approval gates for destructive changes |

Two of these are close enough to the repo's *current* pain to be worth naming
specifically: `--if-commit` would make the lost-update class impossible rather than
guarded, and the static `reads`/`writes` descriptor is a better version of what
`INGESTED_OUTPUT_KEYS` approximates by hand.

## 5. Head to head, against what YB-010 actually asks for

| YB-010 requirement | rdflib + pySHACL (planned) | TerminusDB | Omnigraph |
|---|---|---|---|
| Standard RDF surface (Turtle/N-Quads artifacts) | ✅ `to_turtle` already ships | ⚠️ **Enterprise-only** | ❌ none |
| SPARQL for the REQ-G ⇄ ARC-G join (YB-005) | ✅ | ❌ WOQL | ❌ `.gq` |
| SHACL closed-world audit | ✅ generated from LinkML | ⚠️ own schema, CWA | ❌ no constraint language for *ontology* |
| OWL entailment kept open | ✅ (deliberately not used) | ⚠️ own model | ❌ |
| Jena path preserved | ✅ via `SPARQLWrapper` | ❌ | ❌ |
| No service boundary | ✅ | ❌ server + object store | ❌ server, though embedded mode exists |
| LinkML ontology expressible | ✅ it *is* LinkML | ⚠️ translatable | ❌ **not expressible** |
| Named graphs / revision diffing (YB-004) | ⚠️ build it | ✅ native | ✅ native |
| Branch / merge / guarded promotion (YB-046) | ❌ build it | ✅ native | ✅ native, and agent-oriented |
| Optimistic concurrency | ⚠️ `expected_version` | ✅ | ✅ `--if-commit` |
| Python | ✅ | ✅ client | ⚠️ **SDK "coming soon"** |

The table makes the shape plain: the planned rdflib path is the only column that
satisfies the *reasoning* requirements, and both engines are the only columns that
satisfy the *collaboration* requirements. They are not competitors for one slot.

## 6. What adopting it would cost

- **A second schema language for the ontology**, which cannot express it — §3.
- **A second stateful service.** Softer than TerminusDB's, because embedded file mode
  exists for development; but production is a server plus an S3-compatible object store,
  against a repo whose graph store is a file or SQLite inside the Flask process.
- **A Python-shaped gap.** This platform is Python; Omnigraph's typed SDKs are
  TypeScript and (soon) Python, with MCP and HTTP/OpenAPI available today.
- **Youth.** 5½ months old, 89 open issues, one organisation. MIT means forkable, which
  is the exit — but a platform dependency this young should be entered on a named
  trigger rather than by default.
- **The dual-history problem**, unchanged from the TerminusDB analysis: the repo's
  assertions carry `scope`, review status, provenance and `superseded_by` lineage, and its
  audit trail is the `ReviewLog` of *human decisions*. Omnigraph's commits and Cedar
  actor attribution are a **parallel** record of the same facts.

## 7. Recommendation

1. **Do not adopt Omnigraph for YB-010.** It fails the item's central requirement on
   every axis that item is about, and it cannot express the ontology the whole platform
   is grounded in.
2. **Cite it in `branching-and-promotion.md` alongside TerminusDB** — as the closer of
   the two, because its premise (*agents enrich a graph on isolated branches; humans
   review and merge*) is this platform's own architecture, independently arrived at, with
   `--if-commit` and static `reads`/`writes` as concrete design ideas worth stealing
   regardless of whether the engine is ever adopted.
3. **Revisit on a trigger, not on a review:**
   - multi-architect / multi-agent branching becomes a product requirement (YB-046);
   - the lost-update guard needs to become structural rather than checked;
   - an RDF/ontology story is ever required *of the store* rather than of the projection —
     in which case neither engine is the answer and the planned rdflib path stands.
4. **The cheap experiment, if evidence is wanted:** the embedded path needs no server —
   `omnigraph init --schema ./graph.omni`, load the reified assertion encoding of one
   saved revision, and ask the realization question (`not { … }`) in `.gq` next to the
   same question as SPARQL over `to_turtle`. That compares the two *query experiences* on
   real data. **[M] Not now** — YB-010's own gate is extraction reliability, and this is
   not on the critical path.

## 8. The one-sentence takeaway

TerminusDB and Omnigraph are both answers to *"how do I version and collaborate on a
graph?"* — which this repo asked as YB-004 and YB-046. YB-010 asks a different question,
*"how do I reason over one deterministically?"*, and the answer to that is still rdflib,
SHACL and SPARQL.

## 9. Sources

- Omnigraph README and schema/query references — <https://github.com/ModernRelay/omnigraph>
  (`docs/user/schema/index.md`, `docs/user/queries/index.md`)
- Repository metadata — <https://api.github.com/repos/ModernRelay/omnigraph> (created, pushed, stars, forks, issues, licence)

Repo side: [`rdf-knowledge-layer.md`](rdf-knowledge-layer.md) (YB-010),
[`graph-store-schema.md`](graph-store-schema.md) §5–6,
[`branching-and-promotion.md`](branching-and-promotion.md) (YB-046),
[`terminusdb-evaluation.md`](terminusdb-evaluation.md), `core/knowledge/rdf.py`.
