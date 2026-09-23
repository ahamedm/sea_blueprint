# Domain Ontology Integration — where it sits, how it is told apart, how it is chosen

**Status:** Design — steps 1–3 **implemented** · 23 September 2026 · answers TODO item 11
**Question:** Where does the Domain Ontology integrate? How is it differentiated in
the view from the Business Requirements and Architecture ontologies? And can the
Architect/BA simply *pick* the relevant domain ontology for the Initiative to start
with?

**Answer in one line:** the domain ontology is a **fourth kind of schema** — a
*domain pack* — that is **selected per Initiative at runtime**, not a fifth link in
the fixed base-ontology chain and not a new ontology of the artifact. In the view it
is told apart by being **selectable, versioned per Initiative, and consumed by
coverage rather than traceability**.

---

## 0. What is built (vs designed)

| Step | State | Where |
|---|---|---|
| 1. The pack | ✅ | `ontology/domains/payment_processing.yaml` — 20 classes, 7 enums, 3 subsets |
| 2. Overlay loader + per-Initiative selection | ✅ | `core/ontology.py`; `Provenance.domain_pack`; `/ingest` picker; `SEA_DOMAIN_PACK` |
| 3. Extraction grounding | ✅ | `_format_ontology_context`; base prompt de-payment-ised |
| 4. `/ontology/domain` concept-map view | ⬜ | — |
| 5. Coverage audit, both legs | ⬜ | blocked on the audit engine (TODO 9) |

Two things learned while building, both recorded in the code:

- **The import walk was silently lossy.** `_collect_ontology_names` resolved
  `imports:` relative to the importing file and skipped a miss with a bare
  `continue`. A pack under `ontology/domains/` importing `requirements_base`
  inherited *nothing*, with no error anywhere. Resolution now delegates to the
  shared loader and a miss is fatal. This is the same silent-absence pattern that
  let the `domain` field sit inert, and it is why §2.4 was not a hypothetical.
- **The ARC-G link cannot be a class range.** `realised_by` points at
  `ArchitectureElement`, which lives *above* the requirements layer the pack
  imports. Ranging it there would invert the one-way import rule, so it stays a
  string reference — an unresolved cross-graph link reconciled at the graph level,
  exactly like the predicates in `core.knowledge.model.CROSS_GRAPH_PREDICATES`.

---

## 1. The distinction that has to be made first

TODO item 11 already states it, and it is the whole design:

| | Vocabulary of… | Classes |
|---|---|---|
| `requirements_base` | **what we build and how we reason about it** | `BusinessRequirement`, `BusinessGoal`, `BusinessCapability`, `Stakeholder`, `BusinessProcess` |
| `architecture_base` | **how we describe the built thing** | `Container`, `Component`, `Responsibility`, `RequirementRealization` |
| `domains/<name>.yaml` | **what the business is actually about** | `Payment`, `PAN`, `Merchant`, `Authorization`, `Capture`, `Chargeback` |

The first two are **method artifacts** — they change when our *practice* changes, and
they are the same for an HR Initiative and a payments Initiative. The third is
**subject matter** — it changes when the *business* changes, is different for every
Initiative, and is precisely the thing that is generic-tool/domain-agnostic today and
must not become payment-shaped tomorrow.

That asymmetry is not a detail. It is the reason the domain ontology cannot be
integrated the way `architecture_base` was.

---

## 2. Where it integrates — three placements, and only one is right

### 2.1 Physically: `ontology/domains/<domain>.yaml`, importing `requirements_base`

A domain pack is a peer of `requirements_base` and `architecture_base`, not a layer
above them:

```
sea_common ──▶ enterprise_structure ──▶ requirements_base ──▶ architecture_base
                                              ▲
                                              └── domains/payment_processing.yaml
```

The pack imports `requirements_base` **only to subclass `DomainConcept`** and to
reuse the enums. It does not extend `architecture_base`, and `architecture_base` must
never import a pack — otherwise the base ontology acquires a payment dependency and
the "generic, not tied to one domain" requirement is dead.

**The import resolution already works.** `agents/base_agent._collect_ontology_names()`
walks `imports:` recursively from the agent's single `ontology_path`. Point an agent at
`ontology/domains/payment_processing.yaml` and it inherits the entire base chain for
free. **No restructuring required** (item 11 is right about this) — with one caveat in
§2.4.

### 2.2 Conceptually: the pack is **TBox**, and this is the subtle part

The instinct is to read "we have Business Req ontology, Architecture ontology, so the
Domain ontology is the *third graph*." **That is the wrong move**, and it is the move
that would break the project.

There are only ever **two graphs** — REQ-G and ARC-G. The domain ontology is not a
third instance graph; it is a **schema that both graphs are stated in**. The domain
entities that extraction finds (`Payment`, `PAN`, `Merchant`) remain **nodes** in
REQ-G/ARC-G exactly as today, of kind `DomainConcept` — except now they are instances
of a *specific* class (`Card`) instead of the catch-all.

```
TBox / schema:     sea_common → enterprise → requirements → architecture
                                                    └→ domains/payment_processing   ← the pack
ABox / instances:  REQ-G                                    ARC-G
                     └─ nodes of kind Card, PAN, Merchant, Chargeback ─┘
```

Why this matters concretely: `KnowledgeGraph.add_node(kind, label)` takes an arbitrary
kind string and `ingest.py` never validates it against a schema (line 103/118 — the
kind is taken from the extraction output verbatim). So making the pack a *schema*
costs **nothing** in the storage or graph layers and immediately makes `kind` mean a
real class instead of a free string. That is the whole win of item 11 in one sentence:
*43% of entities currently carry no ontology class, and `Spring Boot` is filed as a
domain concept, because `DomainConcept` is a catch-all and there is nothing to be
wrong against.*

### 2.3 At runtime: **selected per Initiative**, not loaded globally

This is the piece that makes the solution generic and is the direct answer to "can the
Architect or BA pick the relevant Domain Ontology?"

**Yes — and the pick belongs to the Initiative, not to a global env var.** The four base
layers are loaded once at app startup (`app/__init__.py` → `load_ontology(...)`,
cached). The pack must **not** join that call, for three reasons:

1. `load_ontology` is `lru_cache`d **per directory**, and `_load_cached` treats every
   entry of `LAYER_ORDER` as mandatory — a missing layer raises. Adding a pack to that
   tuple makes the loader demand one specific domain forever.
2. `tests/test_ontology.py:20,39,45` assert *exactly four* layers and an exact
   `classes_per_layer` dict. A fifth hard-coded layer is a design change to the base
   ontology; a runtime parameter is not.
3. Per-Initiative selection is impossible if the pack is baked into a global cached
   load — you would have to restart the app to switch from payments to HR.

So the pack is an **overlay resolved per Initiative**:

```
Initiative (INIT-MVP-001, "Payment Processing")
   ├─ domain_pack: payment_processing@0.1.0     ← the pick, recorded on the Initiative
   └─ drives: extraction prompt vocabulary, /ontology domain tab, coverage audit leg
```

Selection point in the UI is **`/ingest`** (an Initiative-level choice, made once when
the Initiative starts) with the active pack echoed read-only on `/ontology` and as a
third leg in the audit surface. The choice is **recorded in the graph's provenance**
next to `scope` / `initiative_id`, so that a later reader can tell *which vocabulary
was in force* when a fact was extracted — otherwise a re-extraction under a new pack
looks like a content change rather than a vocabulary change.

### 2.4 The one mechanical caveat — **confirmed, then fixed**

`base_agent._collect_ontology_names()` resolved imports as `candidate = base_dir / f"{imp}.yaml"`
— **relative to the importing file's directory**. A pack at `ontology/domains/payment_processing.yaml`
importing plain `requirements_base` looked for `ontology/domains/requirements_base.yaml`
and silently skipped it (`if not candidate.exists(): continue` — *silent*, which is the
dangerous part).

This was not theoretical: it **reproduced exactly** while building step 2. The pack
loaded, reported `imports: []`, and inherited nothing, with no error anywhere.

**Fix applied:** import resolution moved out of the agent and into `core.ontology`,
which normalizes `requirements_base` and `../requirements_base` to the same layer and
**raises** on an import that is neither a base layer nor a LinkML module. The same
strictness now covers unresolvable slot ranges and a pack that specialises nothing.
Held by `tests/test_domain_pack.py`.

---

## 3. How it is told apart **in the view**

Four axes. The first two are structural, the last two are the ones a reader actually
sees.

### 3.1 By selection, not by position in the chain

`/ontology` today draws the base chain as four fixed, always-present layers. The pack is
not part of that chain and must not be drawn as `Layer 5`, because it is **conditional**
and **swappable**. It gets a separate, clearly-labelled region:

```
┌─ Foundational ontologies (fixed, enterprise-wide) ────────────────┐
│  Common → Enterprise → Business Requirements → Architecture      │
│  61 classes · 42 enums · same for every Initiative               │
└──────────────────────────────────────────────────────────────────┘
┌─ Active domain pack (per Initiative) ────────────  [ Change ▾ ] ──┐
│  payments 0.1.0 · 24 classes · extends DomainConcept             │
│  Imported by: nothing — it is a leaf.  Selected by: INIT-MVP-001  │
└──────────────────────────────────────────────────────────────────┘
```

The **"Change" control on the pack** is the whole feature the user is asking for, and
it belongs here as well as on `/ingest`.

### 3.2 By what it is *not* selectable by

The base layers are **not choosable** — enterprise, requirements and architecture apply
to every Initiative. Offering them as picks would be a lie. Only the domain pack is a
pick. This asymmetry is itself the visual differentiation, and it is honest: the base
ontology is *the tool*, the domain pack is *the subject*.

### 3.3 By the graph it draws

The pack has its own neighbourhood shape, and it is a **third distinct shape**, not a
rehash of the other two:

| View | Nodes | Edges | Question |
|---|---|---|---|
| REQ-G (`/review`, `/gaps`) | requirements, goals, capabilities | traces, derives, conflicts | "is the requirement set coherent?" |
| ARC-G (`/c4`) | containers, components, datastores | containment, realization | "does the design answer the requirements?" |
| **Domain pack** (`/ontology/domain`) | **concepts** | `ConceptRelationship` — named, cardinal, directional | "what does this business consist of, and what is a constituent of what?" |

It is a **concept map**, not a taxonomy and not a deployment diagram. `PaymentCard —
issued_by → Issuer`, `Authorization — precedes → Capture`. That is recognisably a
different diagram to anyone who has seen a C4 container view, so the differentiation is
legible without a legend.

### 3.4 By how it is *consumed* — coverage, not traceability

This is the deepest distinction and the reason the feature earns its place. The base
ontologies are consumed by **traceability** (`does A answer B?`). The domain pack is
consumed by **coverage** (`is the subject matter fully spoken for?`), and coverage runs
**both ways**:

| | Finding |
|---|---|
| concept defined, instantiated in REQ-G | covered |
| concept defined, **zero instances** | deliberate exclusion — or an oversight |
| node in the graph with **no domain class** | ontology incomplete — or the node is confused (`Spring Boot`) |
| requirement cites a **control** as a domain entity | mis-classification |

A **zero-instance column** is therefore the most informative thing on the domain page —
the same insight the `/ontology` instances column already proves, applied to a
vocabulary where zero actually *means* something. And it creates the **third
reconciliation leg**, which is the genuinely new capability:

```
REQ-G ⇄ ARC-G     does the design answer the requirement?      (exists, item 5)
REQ-G ⇄ DOMAIN    does the requirement set cover the subject?  (new)
ARC-G ⇄ DOMAIN    what has the design ignored?                 (new)
```

> *"You have built authorisation and capture, but nothing anywhere handles chargebacks"*
> is a **domain-coverage** finding. No amount of REQ⇄ARC reconciliation surfaces it,
> because nothing in the requirements ever mentioned chargebacks — there is no
> requirement to be unimplemented.

### 3.5 The UI surfaces

| Surface | What the Architect/BA does |
|---|---|
| `/ingest` | **picks the domain pack** for the Initiative (or "generic / none" to start) |
| `/ontology` | sees the base chain (fixed) *and* the active pack (swappable), clearly separated |
| `/ontology/domain` | the pack's concept map + the two-way coverage table with zero-instance concepts |
| `/gaps` | the two new coverage legs alongside the existing traceability legs |

---

## 4. Why not the alternatives

| Option | Why it fails |
|---|---|
| **Add all domain classes to `requirements_base`** | Recreates exactly the problem item 11 names. `DomainConcept` is a catch-all *because* there was no domain vocabulary; widening the base layer makes the vocabulary domain-shaped while pretending to be generic. The user's requirement is explicit: not tied to one domain. |
| **Hard-code the pack as `LAYER_ORDER` entry five** | Breaks `stats["layers"] == 4` and `classes_per_layer` (tests `test_ontology.py:39,45`), and `_load_cached` would demand a specific domain forever. Also kills per-Initiative selection — the global `lru_cache` is keyed by directory only. |
| **Load every pack always** | Prompt dilution is already the known failure mode (`TODO.md` item 7: scaffolding 2.5:1 over the document). Adding HR + payments + LMS vocabularies to a payments extraction makes mis-classification *worse*, not better, and `Spring Boot`-as-a-concept is the current symptom of too much permissive vocabulary. |
| **Model the domain as instances, not as a pack** | Loses the schema. You cannot validate `kind` against a concept that is itself just another node, and you cannot ask "which concepts have zero instances" because there is no closed list of concepts to have zero of. |
| **A separate third graph store** | Unnecessary — §2.2. Nodes already carry kind; instances still live in REQ-G/ARC-G. A third store invents a merge problem that does not exist. |

---

## 5. Sequencing — what to build now, honestly

TODO item 11's ordering note is correct and should be kept: the pack's *full* value is
realised through the coverage audit, which needs the audit engine (item 9, gap 5) that
does not exist. Building the whole thing now yields a vocabulary nothing consumes.

But **step 3 alone is independently useful and much smaller**, and it is the step that
fixes a defect that exists today (43% of entities unclassified; frameworks filed as
domain concepts):

| Step | State | Build | Value | Depends on |
|---|---|---|---|---|
| **1. Pack** | ✅ done | `ontology/domains/payment_processing.yaml` — 20 classes, 7 enums, lifecycle state machine (`INITIATED → AUTHORIZED → CAPTURED → SETTLED → RECONCILED`), `worked_example` annotation | closes the vocabulary | — |
| **2. Loader** | ✅ done | pack resolved **per Initiative**, separate from `load_ontology`; missing imports/ranges fatal; pack id in `Provenance.domain_pack`; picker on `/ingest` | makes it selectable | 1 |
| **3. Grounding** | ✅ done | pack classes injected as a separate prompt block; **payment example moved out of the base prompt** into the pack | **immediate extraction precision** | 1, 2 |
| **4. View** | ⬜ | `/ontology/domain` concept map + coverage table | the differentiation becomes visible | 2 |
| **5. Coverage** | ⬜ | the two-way coverage audit and the two new reconciliation legs | the new capability | 4 + item 9 |

**Recommendation:** 4 next (small, and it is what makes the distinction visible), 5 when
the audit engine lands. Do **not** ship the pack as a fifth fixed base layer at any point.

**One deviation from this plan, deliberately:** the pack selector was built into
`/ingest` during step 2 rather than waiting for step 4. Selectability is step 2's whole
claim — a loader with no way to choose a pack is untestable end-to-end — and `/ingest` is
where the choice is actually made (per Initiative), whereas `/ontology` is a reference.
Step 4 still owes the concept-map view and the coverage column.

---

## 6. Generic-by-construction checklist

The test of "not tied to one domain" is mechanical, not a sentiment. Each item is
either **held by a test** or still open:

- [x] No base layer *declares* a payment class — `test_the_base_schema_files_declare_no_domain_class`. (A base layer may still *mention* a domain noun in prose to explain a generic construct: `ResponsibilityType.DATA_OWNERSHIP` asks "who owns Cardholder Data?". Prose is not a dependency; a class declaration is.)
- [x] `architecture_base.yaml` imports no pack — `test_the_architecture_layer_imports_no_pack`.
- [x] The pack is never hard-coded: it is named in a YAML file and a form field, and read by path/parameter everywhere else.
- [x] The loader, the agent and ingest read the pack **by reference**, so `hr.yaml` or `learning_management.yaml` needs zero code changes.
- [x] An Initiative with **no** pack works end-to-end — `test_an_empty_selection_means_no_pack_and_does_not_raise`, `test_no_pack_means_no_domain_block_and_no_payment_vocabulary`, and the whole existing suite running packless.
- [x] The base extraction prompt is **subject-neutral**, and a pack supplies its own worked example — `test_a_pack_supplies_its_own_worked_example`.
- [ ] The `/ontology/domain` view exists (step 4) — currently the pack is visible at `/ingest` and in provenance, but there is no concept-map or coverage surface.

The packless-path item is the one that keeps the MVP honest: **payment processing is
the first pack, not the only one,** and "no domain pack" must remain a first-class
choice rather than a degraded path.

### The remaining payment-shaped content

One item is worth naming rather than hiding: the worked examples in
`test_data/prd/*.md` and the `expected` element list in
`scripts/run_extraction_tests.py` are ACME payment documents. That is *fixture* data,
not product code — the equivalent of a test named `test_payments_work`. The rule that
matters is the one above: no file under `ontology/*.yaml`, `core/`, `app/` or
`agents/*.py` may declare or hard-code a domain concept.
