---
id: YB-053
legacy: null
title: "Category elements, one system named twice, and a hub that is really a document gap"
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

> **Open.** Found by running the MVP test case end to end against DeepSeek
> (`deepseek-v4-pro`) on a clean scope, measured with `scripts/c4_scorecard.py`. They are
> what is left of the C4 model after the earlier defects were fixed — the diagram is now
> real, and these are what still make it read wrong.
>
> Defects 1 and 2 are in the pipeline and the graph. Defects 3 and 4 are in the source
> **document**, and 3 is the reason the diagram's shape is misleading: the one container
> whose relationships the prose lists becomes the hub everything hangs off.

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

### Defect 3 — the document states ONE container's relationships, so that container is the hub

Asked why `Settlement Job Orchestrator` looks like the entry point every other container
hangs off. The graph is **faithful**: all 11 arrows trace to a sentence, and the reason is
the source document, not the extractor.

`test_data/arch/payment_platform_arch.md` is 169 lines. §2.7 (`Settlement Job
Orchestrator`) is ~40 of them and ends with an explicit **"Relationships:"** bullet list —
the only place in the document where container→container edges are stated as such:

| Document line | Edge extracted |
|---|---|
| L88 "Reads and writes … in **PostgreSQL**" | → PostgreSQL (JDBC) |
| L89 "Calls the **PGSP Gateway** … to initiate settlement" | → PGSP Gateway |
| L90 "Obtains routing context from the **Payment Routing Decision Engine**" | → Routing Decision Engine |
| L91 "Receives PAN/CVV only through the **PAN-Card Encryption Service**" | → PAN-Card Encryption Service |
| L92 "Reports tenancy and storefront attribution via the **Storefront Management Service**" | → Storefront Management Service |
| L93 "the **Payment Orchestrator** may enqueue an ad-hoc capture" | Payment Orchestrator → Settlement Job Orchestrator |

The expected request path — Payment Orchestrator → Routing Decision Engine → PGSP Gateway
— is **never stated as a call anywhere**. §2.1 says the orchestrator "Handles payment
initiation"; §2.2 says the engine "Implements the Rule-Based Routing logic"; §2.3 says the
gateway "Provides a standardized API". Those are *responsibilities*, and a human reader
reconstructs the flow from them; the text does not assert it. A grep for call verbs finds
exactly one stated container→container call in the whole document (L89).

**Lever 1, and the root cause: the fixture is ours.** `test_data/arch/` is a hand-written
input, and it should state the request path the way §2.7 states the settlement
relationships — a "Relationships" list under §2.1/2.2/2.3. Then the test case exercises
flow extraction instead of demonstrating a document gap. Nothing else here fixes that.

**Lever 2: inference where the text is silent is unstable, so the flow arrives by luck.**
Same document, same model (`deepseek-v4-pro`), two runs: the first produced
`Payment Orchestrator → Routing Decision Engine`, `→ PGSP Gateway`,
`→ PAN-Card Encryption Service`, `→ Storefront Management Service` and
`Payment UI Service → Payment Orchestrator`. The second produced **none** of those five.
The edges that vary are exactly the inferred ones; the stated ones (§2.7) are stable
across both. That is [YB-004](../entries/YB-004-model-output-not-structurally-stable.md)
landing precisely where the document is silent — and it means "is this graph right?" has
a different answer per run for the part that matters most to an architect.

### Defect 4 — a PERMITTED capability became an asserted connection

`Settlement Job Orchestrator → Valkey` comes from §3: *"Valkey caching of job status **is
permitted** for read-heavy operational dashboards only."* A permission is not a fact: the
document says the platform may do this, and the graph asserts that it does, with
`style: SYNCHRONOUS_REQUEST_RESPONSE`. Its own job-store sentence says the opposite
about durability ("Valkey is **not** used as the job store").

**Shape.** `is_synchronous` is currently unset on every connection even though `style`
determines it (YB-051 decision 2). Worth deciding together with a modality: the ontology
has room for a MAY/optional distinction, and an asserted edge drawn from a permissive
sentence is the kind of thing a reviewer should be shown rather than have to notice.

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

- `test_data/arch/payment_platform_arch.md` states the request path (a "Relationships"
  list under §2.1/2.2/2.3, mirroring §2.7), and a re-run's container diagram shows
  Payment Orchestrator → Routing Decision Engine → PGSP Gateway **in every run**, not in
  one run out of two.
- No element in a re-extracted `payment_platform_arch.md` is named after a bare category:
  the `c4_scorecard.py` count of `Database`/`External Services`-style names is zero.
- No connection is asserted from a permissive sentence ("is permitted", "may").
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
