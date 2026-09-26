---
id: YB-038
legacy: null
title: "The Design Assistant's techniques pass echoes requirements and links no quality attribute"
status: done
priority: high
area: "`agents/design_assistant/passes.py` (the techniques instruction), `agents/design_assistant/validators.py`, `agents/base_agent.py`"
created: 2026-09-25
updated: 2026-09-26
design: docs/design/design-assistant.md
record: docs/decisions/ADR-0020-techniques-pass-closed-list.md
superseded_by: []
related: ["YB-035", "ADR-0011", "ADR-0017", "YB-027", "ADR-0016"]
blocks: []
blocked_by: []
---

# YB-038 — The techniques pass echoes requirements

> **Closed 2026-09-26.** The record is
> [`ADR-0020-techniques-pass-closed-list`](../../decisions/ADR-0020-techniques-pass-closed-list.md), which carries the decision and
> the evidence. The write-up below is preserved as it stood when the item
> was written. Found by the first real Design Assistant run (2026-09-25); the
> measurement is in
> [`ADR-0017`](../../decisions/ADR-0017-design-assistant-proposes-arc-g.md) §Measured.

### The defect, measured

The techniques pass is asked for the MECHANISM that delivers each stated quality
attribute — `Redundancy / Replicas`, `Stateless Services`, `Connection Pooling`.
On the live model it returned the requirements' own names, with every quality field
empty:

```
Role-Based Access Control Enforcement   cat=''  realizes=[]  quality=''
Horizontal Scaling                      cat=''  realizes=[]  quality=''
TLS 1.2+ Transport Security             cat=''  realizes=[]  quality=''
Payment Gateway Fallback Strategy       cat=''  realizes=[]  quality=''
Payment Request Validation              cat=''  realizes=[]  quality=''
Payment Settlement Management           cat=''  realizes=[]  quality=''
Transaction Processing Capacity         cat=''  realizes=[]  quality=''
Cardholder Data Encryption              cat=''  realizes=[]  quality=''
```

Eight of eight. The run's other five passes produced usable output — elements,
connections, two resolved patterns, two measurable scenarios, 25 traceability
references — so this is specific to the techniques pass rather than to the profile.

### Why it matters more than a naming slip

`DesignTechnique` exists for one reason: to supply the edge between a required
quality attribute and the design that claims to deliver it
(`ontology/README.md`, ADR-0011). A technique named after the requirement, with no
quality link, adds a node and no information. Worse, `core.knowledge.quality`
counts it: `realized_by_technique` would report the attribute as having a
mechanism on the strength of a renamed requirement. That is a **false assurance
about coverage**, which is the one thing this layer must not produce.

The run's own gate says so. `scripts/run_extraction_tests.py --only design
--validate-only` reports **5/6 invariants** and fails
`inv_design_techniques_linked`. Left failing deliberately: it is the diagnostic.

### It is not only wrong, it can also stall

A second run of the same pass over the same digest, this time through the
`/design/draft` route, did not finish in **19 minutes** where the first finished in
**19 seconds**. The structure pass in that run visibly burned three turns on a schema
rejection (`"parent": null`), which is the retry loop the turn budget exists to
bound; the techniques pass produced no completion line before it was killed.

The cause is inferred rather than proven — a slow local server is not excluded — but
the shape fits: a schema the model keeps failing is re-prompted until the cap, and
this is the pass whose content the model is demonstrably getting wrong. Worth
re-measuring alongside whichever option below is chosen, because a pass that
occasionally takes twenty minutes is unusable inside a request — the same argument
[YB-026](../entries/YB-026-asynchronous-progress.md) makes about extraction.

One thing the stall did prove: killing the request left the working set
**byte-identical** to the last revision and staged no draft, because the route writes
nothing until the agent returns.

### What is already in place

`check_techniques_are_mechanisms` (`agents/design_assistant/validators.py`) flags
any technique whose name matches a requirement label, and it catches **8 of 8** on
this output. So the failure is visible on the design page and in the findings list;
what is missing is a technique layer that works.

### Options, none of them chosen yet

1. **Prompt.** The instruction already says "name the MECHANISM, not the attribute"
   and gives five examples. On a 4B model that is evidently not enough. Few-shot
   examples drawn from the catalogue's mechanisms, or an explicit "a technique name
   may never be a requirement name" rule, are the cheap attempts — with the YB-007
   caution that adding prose is not monotonic and the pass's budget is measured by
   `tests/test_prompt_budget.py`.
2. **Deterministic post-processing.** The model is unreliable at mechanical
   fidelity (ADR-0011). A requirement-named technique could be resolved onto the
   quality attribute it *does* name and then onto catalogue mechanisms for that
   attribute. That is the ADR-0011 posture applied here, and it would work on any
   model — but it decides the technique for the model, which is a real loss.
3. **A different, smaller schema.** Asking for one technique per stated quality
   attribute (a closed list from the digest) rather than an open list of techniques
   makes "aimed at nothing" unrepresentable. Narrower output, harder to get wrong.
4. **A stronger model for this pass.** Not a fix, an escape hatch, and it should be
   measured rather than assumed.

Whichever is chosen, the acceptance below is what "fixed" means — and the second
criterion is the one that matters, because a renamed requirement with a
`realizes_quality_attributes` link bolted on would satisfy the first and still be
worthless.

### Acceptance

- Every proposed technique names a mechanism, and its name matches no requirement
  label in REQ-G.
- Every technique carries `realizes_quality_attributes` or `quality_category` (or
  `subcharacteristic`), so the census's `realized_by_technique` state means
  something.
- `scripts/run_extraction_tests.py --only design` passes all six invariants on a
  real run.
- A test pins the rename case, which `test_design_agent.py` already does for the
  validator.
