---
id: ADR-0032
title: "Aspect guards — a required slot nobody produced, containment by kind, and a backstop for the connections rule"
status: accepted
date: 2026-09-28
area: "`agents/extraction/validators.py`, `agents/architecture_extraction/passes.py`, `agents/architecture_extraction/agent.py`, `agents/design_assistant/passes.py`, `agents/design_assistant/agent.py`, `core/knowledge/ingest.py`, `scripts/run_extraction_tests.py`, `tests/test_aspect_guards.py`"
related: ["YB-055", "YB-051", "YB-053", "YB-007", "ADR-0029", "ADR-0030"]
---

# ADR-0032 — Three aspects of an architecture, and what was holding each in shape

> **Record.** The audit behind this asked one question of each core aspect of an
> architecture — Container, Component, Design techniques, Integration to external
> systems, Domain data elements — namely: *what shapes this?* The ontology, the pass
> schema, the prompt, a deterministic validator, or refusal at the write boundary?
>
> Four aspects had an answer. Three of those answers had holes, and they are closed
> here. The fifth had no answer at all and is filed as
> [YB-055](../todos/entries/YB-055-data-element-design.md), because it is a
> modelling decision rather than a defect.

### The question, and why it was worth asking

Aspects of an architecture are shaped in layers, and the layers are not equivalent:

| Layer | What it constrains | Fails how |
|---|---|---|
| Ontology | what MAY be said | silently — a slot with no reader is invisible |
| Pass schema (`Literal`) | what the decoder CAN emit | loudly — the SDK rejects at the schema level |
| Prompt | what the model is ASKED for | quietly — by dilution ([YB-007](../todos/entries/YB-007-prompt-scaffolding-instruction-dilution.md)) |
| Validator | what is REPORTED after the fact | by false positives, which discredit the report |
| Refusal at write | what cannot enter the graph at all | never, if the predicate is declared |

The audit's value was mechanical: for each aspect, name the thing at each layer.
Three questions found the holes, and none of them needed a model call:

1. For every declared ontology slot, **name the code that reads it.**
2. For every validator, **name the rule the ontology states that it does not check.**
3. For every prompt rule, **name the check that backstops it.**

### 1. Container — a `required: true` slot the pipeline never produced

`Container.container_type` is `required: true` in the ontology and ranges over
`ContainerType` (11 values). The string `container_type` appeared **nowhere** in
`agents/` or `core/`: not in `ElementRecord`, not in ingest, not in
`check_enum_membership`'s checked fields, not in `check_schema_consistency`. A
container could therefore carry no kind at all, and nothing failed — because an
absent field and an unasked question are indistinguishable in the output.

Fixed at all four layers:

- **Schema.** `ElementRecord.container_type`, deliberately a plain `str` with a
  `mode="before"` coercer rather than a `Literal`. This follows the policy already
  stated for `c4_level`: a `Literal` ships a strict `enum`, so a near-miss
  (`"SERVICE"`) is rejected at the SCHEMA level, a `mode="before"` coercer never
  runs, and the pass retries into the turn cap having produced nothing. The allowed
  values are still named in the field description for guidance.
- **Prompt.** A rule in both structure passes (`STRUCTURE_PASS` rule 9,
  `DESIGN_STRUCTURE_PASS` rule 8).
- **Validator.** `("container_type", "ContainerType")` added to
  `check_enum_membership`, which reads the vocabulary from the ontology — and
  `check_nonempty_field(elements, "container_type", applies_to=("Container", "DataStore"))`
  for the `required` half, which is the ontology's actual claim.
- **Seam.** Added to the attribute tuple ingest stores, so the field is not merely
  collected and dropped ([YB-051](../todos/entries/YB-051-connections-dropped-at-ingest.md)'s
  failure mode).

### 2. Component — containment was proven to EXIST, never to be the right KIND

`Component.belongs_to_container` ranges over `Container`. `check_containment` proves
a parent exists, is declared, and is backed by a `part_of` edge — and accepts any
declared element as that parent. So a Component attached to a SoftwareSystem passed
every check. The graph then *reported* a C4 hierarchy while being flat: the component
sat at context level and every C4 reduction downstream had to guess which level it
belonged to.

`check_containment_kinds` adds the missing half, and the rule is **read from the
ontology rather than restated**:

- `ontology_slot_range(class, slot)` walks the `is_a` chain, because the slot is
  usually inherited — a `DataStore` declares no `parent_system`, it has `Container`'s.
  Reading only the named class would report the rule as absent and silently disable
  the check that depends on it.
- `ontology_subclasses(class)` gives the range *plus its subclasses*, because a range
  names the parent class and a graph carries the concrete one: a Component inside a
  **DataStore** is legitimate, and a set built from the range alone would call it a
  violation.
- `_CONTAINMENT_PARENT_SLOT` is the one local thing: the extraction record carries a
  single `parent` field where the ontology has four differently-named slots
  (`parent_system`, `belongs_to_container`, `belongs_to_component`). The *pairing* is
  local; the *range* is the ontology's.

**One cause, one finding.** `repair_containment` attaches an unplaced element to the
system under design and reports `containment_repaired`. The kind check therefore
takes `inferred_parents` and skips them: otherwise a single document gap would be
reported twice, once as "the repair had to guess" and once as "the guess is
level-wrong", and a gap would read as two defects. This is the same double-counting
the C4 scorecard was already caught doing — a missing connection counted once per
representation, three false positives on a complete run
([reliability brainstorm §1](../design/extraction-reliability-levers.md)).

### 3. Integration — a prompt rule with no backstop, and two enums nothing read

Three separate holes, all on the same aspect.

**(a) The endpoint rule had no check.** The connections prompt says it in the
strongest terms the codebase has: both endpoints must be a named element, and a
style, quality attribute, technique, category word or group of things "cannot be
drawn". Nothing verified it. An off-list endpoint became a placeholder node through
`_resolve` and surfaced only as a *dangling* edge in the C4 view — downstream of
ingest, and itself measured producing three false positives. `check_connection_endpoints`
adds the backstop, including the missing-end case the text-fallback path could
produce and that ingest silently skips.

`known_labels` is load-bearing rather than decorative: the Design Assistant is
explicitly told to **reuse** an existing element by its exact name instead of
re-proposing it, so a correctly reused endpoint appears in no proposed element
record. Without the existing graph, every correct reuse would be reported as
undeclared — a guard that punishes the behaviour the prompt asks for. It is also
why the guard returns nothing when no elements are declared at all: an empty set
would measure the run, not the graph.

**(b) The integration vocabularies could drift unseen.** `IntegrationStyle` and
`IntegrationProtocol` are `Literal`s hand-copied from ontology enums that **no Python
read**, and `check_schema_consistency`'s field map did not cover them. Editing either
enum would have left the pass schema asserting the old vocabulary with no failing test
and no run reporting anything — precisely the "green but wrong" divergence
`validators.py` warns about in its own docstring. `CONNECTION_FIELD_ENUMS` closes it,
and `inv_schema_ontology_consistency` — already a GATE — now checks both schemas, so
the drift fails a run rather than accumulating.

**(c) Two declared slots were unreachable.** `carries_sensitive_data` and
`failure_handling` are declared on `Connection` and were read by nothing: the pass
schema did not ask, so the graph could not answer *"which links carry regulated
data?"* — the PCI-scope question the payment domain exists to ask. Both are now on
`ConnectionRecord` and both reach the graph. `carries_sensitive_data` is emitted
**only when true**, because recording `false` for every quiet link would assert a
negative the document never made, and a reviewer could not tell that from a reviewed
answer.

### What was deliberately not changed

- **`carries_sensitive_data` is not gated.** It is a finding-level fact, and a link
  the document does not classify is a gap for review, not a refusal. Refusal at the
  write boundary stays reserved for facts that cannot be true of anything
  (ADR-0030 §1).
- **`check_containment_kinds` does not report an undeclared parent.** That is
  `check_containment`'s finding, and the double-report is the thing the
  `inferred_parents` exclusion exists to prevent.
- **The `tools` field is left alone.** It is dead config (no read site), and deleting
  it is a separate, cosmetic call; noting it here keeps this record about the aspects.
- **No new encoder, no new pass, no prompt growth beyond four rule lines.** An extra
  collection would dilute the scaffolding that is already the measured constraint
  ([YB-007](../todos/entries/YB-007-prompt-scaffolding-instruction-dilution.md)), and
  the cheaper levers were not exhausted first.

### The lesson worth keeping

`container_type` was invisible to every kind of review that looks at behaviour:
nothing errored, no test failed, no run reported PARTIAL, and the field's existence
in the ontology read as a capability. The check that finds this class of defect is
mechanical — *for every declared slot, name the code that reads it* — and it found a
required slot, two unreachable connection attributes, and two unconsumed enums in a
single pass. It is cheap enough to run over the whole ontology, not only over the
aspects that prompted it.

The second lesson is about guards that are never run against a *clean* input. A
validator asserted only on broken data is untested in the direction that matters most,
because the failure that discredits a report is the false positive: `test_aspect_guards.py`
pins the correct C4 tree, the Component inside a DataStore, the reused endpoint and
the repair-attached parent alongside the defects.

**What this does not fix:** domain data elements and their exchanges, which have no
ontology class, no pass rule and no guard at all — see
[YB-055](../todos/entries/YB-055-data-element-design.md).
