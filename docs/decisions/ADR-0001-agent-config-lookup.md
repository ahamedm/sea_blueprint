---
id: ADR-0001
legacy: "0"
title: "Agent config lookup ignored the requested agent — REQ-G ran the ARC-G prompt"
status: accepted
date: 2026-09-23
area: "config/agent_config.py, tests/test_agent_config.py"
related: []
---

# ADR-0001 — Agent config lookup ignored the requested agent — REQ-G ran the ARC-G prompt

> **Record.** Closed work, preserved verbatim from `TODO.md` v1 (ADR-0001).
> Legacy source: [`docs/todos/legacy-todo-v1.md`](../todos/legacy-todo-v1.md).

**Legacy status:** ✅ FIXED — this invalidated earlier measurements; see the knock-on correction
**Legacy priority:** Was Critical (silent, and wrong in a way that still produced plausible output)
**Legacy area:** `config/agent_config.py`, `tests/test_agent_config.py` (new)

---

### The bug

```python
def get_default_agent_config(agent_name):          # the parameter
    ...
    for agent_name in ("knowledge_extraction", "architecture_extraction"):   # SHADOWS it
        configs[agent_name]["domain_pack"] = ...
    return configs.get(agent_name, {})             # always architecture_extraction
```

The loop variable shadowed the function parameter, so after the loop `agent_name` held
whichever agent the loop finished on. **Every `get_default_agent_config("knowledge_extraction")`
call returned the Architecture Extraction Agent's config.**

Consequence: requirements extraction ran under the **ARC-G system prompt** — told to
identify C4 elements, containers and datastores, and never told to carry `requirement_id`.
It still produced plausible triples, which is why nothing caught it. The log line naming the
wrong agent was the only visible symptom.

Introduced by the domain-pack work (YB-011), where that loop was added to apply
`SEA_DOMAIN_PACK` to the two extraction agents.

### Why it presented as a model failure

"The model does not emit identifiers" (YB-019a) was **this bug**. Measured immediately after
the fix, same document, same endpoint:

| | before fix | after fix |
|---|---|---|
| `requirement_id` emitted | **0** | **18 of 18** — every source identifier, none missing |
| path | text fallback | `structured_output` |
| elapsed, `payment_platform_brief.md` | 472–754 s | **78 s** |
| entities | 51–64 (derived) | 51–63 (real) |

The two false positives (`CHD`, `PGP`) are genuine over-capture, but narrow.

### Fixed by

Renaming the loop variable, with the reason recorded in a comment at the site.
`tests/test_agent_config.py` asserts each agent receives **its own** config, that lookup
order does not change the answer, and that the two extraction agents have *different*
prompts with `C4` only in the architecture one. A test asserting merely "a config comes
back" would have passed throughout the bug's life.

### Knock-on correction — measure again before quoting

These figures were all taken with the architecture prompt running against requirements
documents, so they describe a misconfigured system, not the pipeline:

- YB-007 / YB-020 timings: 754 s, 624 s, 472 s, and the "structured path exceeds budget"
  conclusion
- ADR-0010 predicate adoption: 6% → 67%, and the 5× slowdown

The structured path **completed in 78 s** once the config was right, so the YB-020 premise
("decide, don't retry into the timeout") may itself have been a symptom. Re-measure before
acting on any of them.
