---
id: YB-068
legacy: null
title: "Rules and regulations reach the graph as text, on a slot that declares a node range"
status: open
priority: medium
area: "`agents/knowledge_extraction/` (the `triples` pass and the predicate list), `agents/extraction/validators.py` (range checks cover object edges only), `ontology/requirements_base.yaml` (`Requirement.governed_by_rules`)"
created: 2026-10-03
updated: 2026-10-03
design: null
record: null
superseded_by: []
related: [YB-047, YB-066, YB-010]
blocks: []
blocked_by: []
---

# YB-068 — `governed_by_rules` is declared over a class and populated with strings

> **Open.** Filed while answering "which pass captures Governance Principles or Policies?" —
> the answer for principles and policies is *no pass, deliberately* ([YB-047](YB-047-enterprise-governance-layer.md));
> the answer for rules and regulations turned out to be a defect.

## The measurement

`Requirement.governed_by_rules` is declared with **`range: BusinessRule`** — a class, so an
edge to a node. Every live assertion is a literal instead:

| Scope | `governed_by_rules` | object edges | literal values | `ontology_class` tagged |
|---|---|---|---|---|
| `payments_v2` | 7 | **0** | 7 | BusinessRule 6, Regulation 1 |
| `payments_v3` | 5 | **0** | 5 | BusinessRule 2, Regulation 2, Standard 1 |

So `governed_by_rules` is never used as declared: the graph holds `"PCI-DSS"` and
`"Routing Rule Set"` as **text**, and `Regulation` / `Standard` / `BusinessRule` nodes that do
exist (1–2 each) are not what those links point at. "Which requirements does PCI-DSS
constrain?" therefore cannot be walked, even though the name of the regulation is in the
graph.

The second half is worse and is the reason this is filed rather than noted: "PCI-DSS" is
tagged **`ontology_class: Regulation`**, and `Regulation` is **outside that slot's declared
range**. Nothing flags it, because `check_reference_kinds` reads *records* and range checks in
this repo cover **object** edges — a value-based assertion has no target kind to check. The
same class of defect as an out-of-range `serves`, arriving through the one door no guard
watches.

## Why it matters now

[YB-066](YB-066-natural-language-enquiry.md) seeds a `governance.principles` question that
answers `substrate_absent` and points at YB-047. That is right for Principle/Policy/Control.
It is **not** the whole story for regulations: the graph does hold the regulation's name, as a
string, so a question about it is not "nothing to answer from" — it is "the fact is here and
unaddressable". Those are different answers and the state model would currently give the same
one.

## Options

1. **Extract the node, not the name.** Have the `triples` pass emit an object edge to a
   `BusinessRule` / `Regulation` / `Standard` node — matching the declared range, and making
   the regulation walkable. Costs nodes for what may be one-off citations.
2. **Re-range the slot to `string` and be explicit.** Honest about what is captured, and it
   removes a declaration nothing satisfies — but it gives up the join permanently.
3. **Both, split by what the target is.** `governed_by_rules → BusinessRule` as a node (a rule
   has structure worth querying) and a separate `cites_regulation` literal for a name the
   document only mentions. Costs a second slot and a decision about which is which.

## What closes it

A chosen option, the slot and the pass agreeing, and a guard that covers the gap either way: a
value-based assertion declaring an `ontology_class` outside its slot's declared range should be
a finding, the way an out-of-range object edge already is.
