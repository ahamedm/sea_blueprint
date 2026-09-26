---
id: ADR-0022
title: "One agent, many independent requests — the model call no longer carries its transcript"
status: accepted
date: 2026-09-26
area: "agents/base_agent.py (_reset_conversation, invoke, invoke_structured)"
related: ["YB-020", "YB-007", "ADR-0021", "ADR-0023"]
---

# ADR-0022 — One agent, many independent requests

> **Record.** Found by measuring token usage on a hosted endpoint (ADR-0023), which
> is the only reason it was found at all. No TODO entry tracked it.

One Strands `Agent` object is built per SEA agent and reused for every pass and
every chunk. The SDK appends each exchange to `agent.messages`, and that list is
re-sent on the next call. Nothing reset it.

---

### What that cost

Measured directly: `agent.messages` grows 3 → 6 → 9 across three calls. On a real
twelve-call architecture ingest — four passes over three chunks — the run reported
**2,042,992 input tokens**. With per-call accounting fixed (ADR-0023) and history
dropped between calls, the same document cost **80,141 input tokens: $0.069 instead
of $1.03.** A 25× reduction, and the prompts themselves never changed.

The growth is quadratic in the number of calls. On a local server that was
invisible because it was free and slow; on a paid endpoint it is most of the bill.

### Why it is a correctness problem first

Every call this class makes is an **independent request**: one extraction pass over
one chunk, or one design pass. Carrying the previous call's question and answer
into the next one shows the model its own earlier output as context. The
connections pass should not be reading the structure pass's transcript, and the
techniques pass should not be primed by whatever the patterns pass said.

That the outputs happened to be reasonable is not evidence the isolation was
unnecessary — it is evidence that the model was robust to contamination, which is a
property of one model and not of the pipeline. A cheaper or more suggestible model
would follow the precedent it was shown.

### The decision

`_reset_conversation()` clears `agent.messages` at the start of `invoke` and
`invoke_structured`. Every call starts from the system prompt and its own prompt,
which is what "independent" has to mean if the passes are to be comparable.

**Retries inside a call are untouched.** The schema-retry loop appends turns during
the call, after the reset — which is exactly where those turns are needed, and why
the reset is at the start of the call rather than in a caller.

### What was rejected

- **Rebuilding the Strands agent per call.** Correct, and it would also discard the
  client and its connection pool. The reset is the same guarantee at a fraction of
  the cost.
- **A conversation manager / trimming strategy.** Solves the token growth while
  keeping the contamination, for more code.
- **Leaving it.** The cost is the visible half; the review-surface problem is that a
  pass's output would depend on which passes ran before it, which makes a
  re-run of one pass not comparable to the run it came from.

### Evidence

```
tests/test_usage_accounting.py — the conversation is cleared before a call, and
                                 the pass's OWN exchange survives it
```

Live: three identical calls report 2,994 / 3,097 / 3,200 input tokens — flat, where
before each call carried every previous one.
