---
id: ADR-0021
title: "The model-call contract against a hosted endpoint — thinking mode, which key, retries"
status: accepted
date: 2026-09-26
area: "agents/base_agent.py (_create_model), config/agent_config.py, .env.example"
related: ["YB-020", "ADR-0003", "ADR-0022", "ADR-0023"]
---

# ADR-0021 — The model-call contract against a hosted endpoint

> **Record.** The change of endpoint from a local llama.cpp server to DeepSeek's
> API. No TODO entry tracked it; this is the only record.

The platform was built against a local llama.cpp server, which never authenticates
and never rate-limits. A hosted endpoint does both, and every difference here was
found by running against one rather than by reading about it.

---

### Thinking mode has to be off, or every structured call fails

**DeepSeek enables thinking mode by default, and in thinking mode
`tool_choice: "required"` returns HTTP 400.** Strands sends exactly that on every
structured-output call — `_structured_output_context.py:87` sets `{"any": {}}`,
which `models/openai.py:355` maps to `"required"`. Verified against the live API:

```
tool_choice=required, thinking default  -> HTTP 400
  "Thinking mode does not support this tool_choice"
tool_choice=required, thinking disabled -> HTTP 200, finish_reason=tool_calls
```

The consequence is not a clean failure. Every pass 400s, `invoke_structured`
catches it and returns `None`, and `run_passes` falls back to text parsing — which
recovers triples, elements and connections and **nothing else**. Entities lose
`requirement_id`, `quality_category` and `initiative_refs`; the design profile's
patterns and scenarios have no text parser at all and come back empty. The run
still reports success.

**The parameter must ride inside `extra_body`.** The first attempt set
`{"thinking": ...}` directly and died with `AsyncCompletions.create() got an
unexpected keyword argument` — the OpenAI SDK validates keyword arguments against
its own typed signature. `extra_body` is the SDK's supported route for
provider-specific fields and merges into the request body.

The setting is configuration (`MODEL_EXTRA_PARAMS`), not code, because it is a
property of one provider. It is documented in `.env.example` with the failure it
prevents, since a silent degradation to text parsing is not something a reader
would guess from an empty env var.

### The key depends on where the endpoint is

`LOCAL_API_KEY` is a local-server placeholder. Sending it to a paid endpoint fails
auth; sending a live paid key to a local server is a secret on the wire for
nothing. `DEEPSEEK_API_KEY` is now preferred when the endpoint is not localhost,
and the log says which it built — *"Using hosted endpoint"* versus *"Using local
inference server"*. The old message claimed "local inference server" for
`https://api.deepseek.com`, which is how somebody later debugs the wrong thing.

A hosted endpoint with no hosted key falls back to the configured key rather than
refusing to construct the agent: the endpoint's own error names the real problem,
and a missing optional secret is not something this layer can diagnose.

### Retries and the output ceiling

The OpenAI SDK retries 429 and 5xx twice by default and Strands does not override
it; a run of a dozen paid calls wants more, since one rate limit otherwise costs a
whole pass. `REQUEST_MAX_RETRIES` (default 3) now reaches `client_args` on every
provider whose client we build. `0` disables retries and is honoured — an `or`
would coerce it back to a default.

`max_tokens` goes 8192 → 16384. It was already the ceiling while the local server
defaulted to unbounded, but a hosted model's non-thinking default is 8K as well,
and an architecture structure pass over a real document can exceed it. A truncated
structured call is a failed one.

### What was rejected

- **`response_format: json_object`** instead of tool calls. It would sidestep
  `tool_choice` entirely, but the pipeline is built on Pydantic-validated tool
  arguments, and this would replace schema validation with prose parsing — a
  strictly worse trade than one configuration line.
- **`strict` mode** (grammar-guaranteed tool arguments). Needs
  `base_url=.../beta` plus every object property `required` and
  `additionalProperties: false`; our records have optional-with-default fields, so
  it would 400 rather than help.
- **Detecting the provider and setting `thinking` in code.** It would work and
  would be one more place a provider's quirks live. Configuration keeps the quirk
  next to the endpoint that has it.

### Evidence

```
tests/test_hosted_endpoint.py   — the retry budget reaches every client, 0 is
                                  honoured, and the hosted/local key choice
tests/test_generation_bounds.py — the ceiling and the sampling parameters
```

Live: a full four-leg run against `deepseek-v4-pro` completed with every pass on
the structured path (**0 text fallbacks** across the design's six passes), and the
architecture ingest reported `COMPLETE`.
