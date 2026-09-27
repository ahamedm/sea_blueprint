# Target deployment architecture — weighting the AWS choices

> **Design document.** Evaluation of the proposed deployment shape against the
> current code and the open decisions: [YB-010](../todos/entries/YB-010-rdf-knowledge-layer.md),
> [YB-026](../todos/entries/YB-026-asynchronous-progress.md),
> [ADR-0026](../decisions/ADR-0026-run-journal-and-progress-transports.md),
> [YB-037](../todos/entries/YB-037-background-workflow-management.md),
> [YB-042](../todos/entries/YB-042-workspace-structure.md),
> [YB-043](../todos/entries/YB-043-system-of-record.md),
> [YB-044](../todos/entries/YB-044-platform-instances.md).

---

## 1. The proposal

- Agents hosted in Bedrock (AgentCore) Runtime.
- The ontology file read from AWS EFS (NFS) or S3.
- MCP for any tool access.
- Agents stream to the Flask web app as the architect logs in, ingests, reviews,
  reconciles.
- RDF itself in a durable store.
- An inference layer such as Apache Jena may be leveraged, but deferred.

## 2. What this changes against today's code

Today the Strands agents are constructed **in process** (`agents/base_agent.py:520`)
behind a `model_provider` branch that already knows `OpenAIModel` and
`AnthropicModel` (`:440,462,473,490`); `/ingest` runs the extractor inline and
reports through flash messages; state is JSON under one store root; the ontology is
read from `ontology/` at agent construction.

The proposal moves three things: **execution** out of process, **configuration**
out of the repo into a shared store, and **events** out of the request.

## 3. Choice by choice

### 3.1 Agents on AgentCore Runtime — right direction, one caveat

The model layer is already pluggable: a `BedrockModel` branch is a contained
change, and `invoke_structured` / `invoke` are the single seam the tests replace —
which is why the whole suite runs with no model server. The agent code therefore
moves essentially unchanged, and the runtime gives what 50 architects need:
per-session isolation, managed scaling, streaming, and a gateway to MCP tools.

**Caveat — this is the most AWS-coupled choice in the stack.** Portability was the
driver for the storage question, so AgentCore has to sit behind the same kind of
seam the model provider already has: an agent is a Strands agent with a model
provider and an event sink; AgentCore is one host for it. Its session and memory
APIs must not leak into `core/`.

**Keep the deterministic core in the container, not behind MCP.**
`core/knowledge/*` is pure Python with a no-model test suite. Turning `serialise`,
`ingest`, `reconcile` or `quality` into MCP calls would put a network boundary in
front of logic that must stay deterministic and unit-tested.

### 3.2 Ontology on EFS / S3 — pin it, do not share it live

The ontology is roughly 200 KB of YAML read once per agent construction. EFS adds
an NFS dependency and a VPC mount for something that could sit in the image. S3 is
the better **registry** — versioned, durable, auditable keys — but it should be
*published* to S3 and *baked into the image* (or fetched at start and hashed), not
read per request.

**The dangerous variant is a live, shared, mutable ontology.** If run A and run B
read different bytes of `architecture_base.yaml`, the graph changed for a reason
nothing records. Bake it, hash it, and record the hash per run — that gap is
[YB-045](../todos/entries/YB-045-ontology-version-provenance.md). EFS is justified
only if hot-editing the ontology without a redeploy is a real requirement, and even
then it needs hashing and version pinning to stay honest.

### 3.3 MCP for tools — at the edges, never through the core

MCP is right for everything the agent reaches *outside* itself: document stores,
the ontology registry, the graph store, catalogues and registries (the technology
radar), enterprise systems. It is wrong for pure functions already covered by
tests. The rule: **if it is deterministic and lives in `core/`, it is a library
import; if it lives over the network or in another system, it is MCP.**

Once agents are remote and serve many architects, each MCP server is new attack
surface and needs its own authentication and least privilege — the gateway helps,
but it does not remove that work.

### 3.4 Streaming to the web app — change the topology

"Agents stream to the Flask app when the architect is logged in" ties event
delivery to a browser session and makes Flask stateful. That requires sticky
sessions, loses events when the app restarts, and contradicts
[YB-037](../todos/entries/YB-037-background-workflow-management.md) (runs nobody is
waiting for: events, schedules) and YB-033 (event ingress).

The shape that works: agents append to a **run journal** — durable, per run, owned
by the record — and the Web App is one *subscriber* that tails it and fans out to
browsers over SSE, catching up with `Last-Event-ID`. Any Flask replica can serve any
session, an absent browser loses nothing, and an unattended run is the same
mechanism with zero subscribers. That is precisely
[ADR-0026](../decisions/ADR-0026-run-journal-and-progress-transports.md)'s "one progress mechanism
for the browser and for unattended runs"; adopt it rather than adding a second path.

**Refinement (2026-09-26) — Valkey Streams as the journal.** Rather than a
file-per-run journal, the agents publish minimal status events to an
ElastiCache/Valkey **Stream**, one stream per run; Flask tails it and fans out to
browsers. Same shape, better substrate: ordered replay (`XRANGE` from the
subscriber's last known id) gives a late subscriber the backlog and a reconnecting
one the events it missed, and several Flask replicas can tail one run — which is
what removes the sticky-session requirement.

Two constraints come with it:

- **Streams, not Pub/Sub.** Pub/Sub has no replay, so a late subscriber, a restart
  and a dropped terminal event each fail
  [ADR-0026](../decisions/ADR-0026-run-journal-and-progress-transports.md)'s acceptance criteria.
  A run that *looks* finished is exactly the false assurance that item exists to
  prevent.
- **Persist the verdict before publishing it.** Asynchronous durability on
  ElastiCache risks up to 10 s of uncommitted writes, so the terminal
  `ExtractionRun` must land in the system of record first. The stream is a
  notification that state changed — never the state itself.

**WebSocket or SSE?** SSE is one-way, which is all progress needs: it reconnects
with `Last-Event-ID` and adds no new server capability. A WebSocket is justified
when the browser drives control mid-run (cancel, pause, re-prioritise) or when event
rates are high — but then resume is yours to implement, and Flask's WSGI default
cannot serve it, so an ASGI or gevent layer joins the deployment. Cancellation alone
does not require a socket: a POST that publishes a control message to the run
achieves the same thing.

### 3.5 RDF in a durable store — as a materialised artifact, not the record

Materialise RDF **per revision** and write it immutably (a content-addressed S3
object). That gives cheap, versioned audit artifacts and a natural input for a
SPARQL engine later, while keeping the *editing* record in the system of record
([YB-043](system-of-record.md)). RDF is a projection, not a database you edit.

If "durable store" is intended to mean **Neptune**, that is a far larger commitment
than "deferred" implies: another AWS-only service, its own model and cost, and it
would become a second source of truth for the graph. Do not adopt it implicitly —
revisit with [YB-010](../todos/entries/YB-010-rdf-knowledge-layer.md) when
cross-product SPARQL actually justifies it.

**Jena deferred: agreed, unchanged.** `rdflib` already emits Turtle and JSON-LD,
and `SPARQLWrapper` bridges to Fuseki when wanted.

## 4. The tensions that decide the design

1. **Portability versus managed depth.** AgentCore + EFS + Neptune + ElastiCache is
   an AWS-shaped stack. Portability must be either a stated requirement or a
   deliberate sacrifice; if stated, it lives in the *seams* (model provider, store
   interface, journal format, MCP contracts, RDF serialisation), not in avoiding
   AWS services.
2. **Determinism and provenance across a shared ontology.** Every run must record
   what it read: model id (done), domain pack (done), document hash (done) —
   **ontology version/hash (missing)**. Without it, a fixed audit query over a
   varying graph can no longer localise blame to the model, which is the whole
   diagnostic value [YB-010](../todos/entries/YB-010-rdf-knowledge-layer.md) claims.
3. **Four stores, one coherence story.** Agent session/memory, run journal, graph
   record, review log. Name the owner of each and what happens when they disagree.
   The graph record is the source of truth; the rest must be derivable or
   explicitly ephemeral.

## 5. Recommended shape

```
Architect's browser
   |  HTTPS (+ SSE for progress)
   v
Flask web app  ─────────────►  System of record (Postgres; SQLite/WAL first)
   |  invoke(run id, async)        │  revisions, review log, workspace index
   v                               │
AgentCore Runtime (Strands)        ▼
   |  tools over MCP        S3: immutable revision artifacts (RDF included)
   |                        S3: ontology registry, versioned ──► baked + hashed
   v                                                             into the image
Bedrock models (or OpenAI-compatible, behind the existing provider seam)
```

- **Agents**: AgentCore Runtime; Strands unchanged; add a `BedrockModel` provider
  branch honouring the same call bounds and usage accounting
  (ADR-0021/0022/0023).
- **Ontology**: publish to S3, bake into the image, hash and record per run.
- **Tools**: MCP for external systems only; the deterministic core stays a library.
- **Progress**: a run journal plus SSE tailing (ADR-0026), not a point-to-point
  stream to one Flask process.
- **Graph**: Postgres/SQLite as the record; RDF materialised per revision to S3;
  Jena later.
- **Valkey**: optional accelerator (streams, locks, read-model cache), never the
  record.

## 6. What this touches in the backlog

- [YB-026](../todos/entries/YB-026-asynchronous-progress.md) /
  [ADR-0026](../decisions/ADR-0026-run-journal-and-progress-transports.md) — the progress
  mechanism this must reuse rather than duplicate.
- [YB-037](../todos/entries/YB-037-background-workflow-management.md) — the run
  journal and unattended runs.
- [YB-043](system-of-record.md) — the record; Postgres is the portable pick.
- [YB-042](workspace-structure.md) / [YB-044](platform-instances.md) — what the
  workspace and its shared layer hold.
- [YB-010](../todos/entries/YB-010-rdf-knowledge-layer.md) — RDF materialisation
  and the Jena deferral.
- [YB-011](../todos/entries/YB-011-domain-ontology-layer.md) — domain packs are
  part of the pinned vocabulary.
- **YB-045 (new)** — ontology version provenance, the gap this deployment makes
  acute.

## 7. Open questions

1. Is portability a **requirement** or a **preference**? It decides how hard the
   seams have to be.
2. Is **hot-editing the ontology** required? It decides EFS versus bake-into-image.
3. **Tenancy of agent sessions** — one AgentCore session per architect, per
   product, or per run?
4. Does "durable store" mean **S3 artifacts** or a **graph database**? This
   recommendation stands on S3 until SPARQL at scale justifies more.
