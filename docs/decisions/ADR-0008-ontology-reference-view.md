---
id: ADR-0008
legacy: "16"
title: "Ontology reference view — a browsable view of the four schemas"
status: accepted
date: 2026-09-22
area: "core/ontology.py, app/ontology_reference.py"
related: []
---

# ADR-0008 — Ontology reference view — a browsable view of the four schemas

> **Record.** Closed work, preserved verbatim from `TODO.md` v1 (ADR-0008).
> Legacy source: [`docs/todos/legacy-todo-v1.md`](../todos/legacy-todo-v1.md).

**Legacy status:** ✅ IMPLEMENTED — see [`docs/ui-review-workflow.md`](docs/ui-review-workflow.md) §8, §8a
**Legacy priority:** Closed
**Legacy area:** `core/ontology.py`, `app/ontology_reference.py`, `app/templates/ontology.html`, `tests/test_ontology*.py`

---

### Why

The four foundational ontologies are the vocabulary every extracted fact is
expressed in, and the only way to read them was the YAML plus a README whose tables
have drifted — it still calls `architecture_base.yaml` "(future)" and never mentions
`sea_common.yaml`. A list of class names is not comprehension. What actually explains
these schemas is structural: the import chain, `is_a` versus mixins, inheritance, and
which slots point at other classes.

### The third kind of view

This is **not** graph projection and **not** an architecture viewpoint; it reads the
LinkML *schemas*, not the extracted graph. Two of those three were fused in one module
until ADR-0007 split them, so this one was placed and guarded deliberately:

| Module | Reads |
|---|---|
| `app/projections.py` | the instance graph (ABox) |
| `app/viewpoints/c4.py` | the instance graph + a notation |
| `core/ontology.py` + `app/ontology_reference.py` | the LinkML schemas (TBox) |

`core/ontology.py` imports nothing from `core.knowledge` or `app`, so the Ontology
Engineer and Domain Context agents can use it without the web layer. Guarded by
`test_the_loader_reads_schemas_and_not_the_graph`.

The schema and the instance graph meet in exactly one place — the `instances` column
on `/ontology` — and that join lives in the view layer so the loader never learns
about graphs.

### What it shows

- **The one-way import chain**, which is why the layers exist at all.
- **`is_a` versus `mixins`**, kept visually distinct. Each layer's abstract root mixes
  in `ExternallyReferenced` and `Provenanced`, so every class beneath inherits identity
  and provenance — but a mixin is not a taxonomy edge and is not drawn as one.
- **Inheritance, resolved.** `NonFunctionalRequirement` carries 33 slots of which 7 are
  its own; the slot table separates own from inherited and names the declaring class,
  because "`id` from `Requirement`" is a different fact from a local slot.
- **Relationships versus hierarchy.** Slots whose `range` is a class are the real
  concept-to-concept links (150 of them).
- **Binding classes** (`SubProductScope`, `SystemCapabilityBinding`) that exist only to
  break a circular import, stated as such rather than silently omitted.
- **Integrity findings** — unresolved supertypes and slot ranges whose type is neither
  a class, an enum, nor a primitive. Currently zero.

The focus drawing uses **semantic rows** (supertypes above, subtypes below, relationship
targets and incoming references further out) rather than a force simulation: position
means something, whereas a hairball of 58 classes teaches nothing.

### Verified against LinkML

The loader's own resolution is checked against LinkML's authoritative parser for
**every one of the 58 classes** — ancestors and effective slot sets
(`test_resolution_matches_linkml`). Asserting against hand-written expectations would
only prove the loader is consistently wrong. It agrees exactly.

### Measured state

| | |
|---|---|
| Classes | 58 (4 common, 7 enterprise, 28 requirements, 19 architecture) |
| Enums / subsets | 37 / 13 |
| Slots declared (whose range is a class) | 457 (150) |
| Unresolved supertypes / ranges / duplicates | 0 / 0 / 0 |

### Findings

1. **A class's direct supertype row shows only `is_a` plus its own mixins.** Mixins are
   not repeated down the tree — they hang off each layer root. That is good design, and
   it is exactly why `ancestors()` must include mixins: otherwise
   `NonFunctionalRequirement` appears to have 7 slots instead of 33.
2. **`ontology/README.md` has drifted** and is now flagged as such at the top. Fine to
   keep as history; the live view cannot drift.
3. **Coverage is the useful cross-reference.** Classes with 0 instances explain why an
   empty view elsewhere is empty — a requirements-only graph has no C4 elements, so
   `/c4` is legitimately blank. The page makes that visible instead of leaving a reader
   to guess.

### Follow-ups

1. **The Ontology Engineer and Domain Context agents are still stubs.** This loader is
   the first real capability they need; wiring them is YB-011's territory.
2. **No editing.** The page is read-only by design — schema changes belong in the YAML
   and through the (unbuilt) Ontology Engineer agent, not in a form that silently
   diverges from the file.
3. **Domain ontologies** (`ontology/domains/*.yaml`, YB-011) are not placed as a layer.
   When they arrive, `LAYER_ORDER` needs branches rather than a single chain.
