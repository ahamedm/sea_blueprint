"""
Per-run token and cost accounting.

WHY THIS EXISTS. Moving from a local llama.cpp server to a hosted endpoint changes
the failure mode from "slow" to "expensive". A provider reports usage per REQUEST
and a profile makes many — six passes for a design, four per chunk for an
architecture document — so the number anyone wants is the RUN's. Before this, every
handler took `structured_output` and dropped the metrics on the floor, which meant a
paid run could not say what it cost, and a pass that burned tokens and was then
discarded was invisible.

The numbers are the provider's own; the cost is derived from configured prices
rather than a table in source, because a hard-coded price is wrong within weeks.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from agents.base_agent import AgentConfig, UsageTotals, _usage_get
from core.knowledge import graph_from_extraction


def usage(inp: int, out: int, cache_read: int = 0, cache_write: int = 0) -> SimpleNamespace:
    """Shaped like `strands.types.event_loop.Usage`."""
    return SimpleNamespace(
        inputTokens=inp, outputTokens=out, totalTokens=inp + out,
        cacheReadInputTokens=cache_read, cacheWriteInputTokens=cache_write,
    )


# ============================================================================
# The accumulator
# ============================================================================


def test_tokens_accumulate_across_calls():
    """A run is many calls; per-call numbers are the provider's, not the run's."""
    totals = UsageTotals()
    totals.add(usage(100, 20, cache_read=80))
    totals.add(usage(50, 10))

    assert totals.calls == 2
    assert totals.input_tokens == 150
    assert totals.output_tokens == 30
    assert totals.total_tokens == 180
    assert totals.cache_read_tokens == 80


def test_a_provider_that_reports_nothing_is_not_counted_as_a_call():
    """Absent usage and zero usage are different facts."""
    totals = UsageTotals()
    totals.add(None)

    assert totals.calls == 0
    assert totals.to_dict()["total_tokens"] == 0


def test_usage_is_read_from_a_mapping_as_well_as_an_object():
    """`Usage` is a TypedDict in this SDK version and an object in others."""
    assert _usage_get({"inputTokens": 7}, "inputTokens") == 7
    assert _usage_get(usage(7, 1), "inputTokens") == 7
    assert _usage_get(None, "inputTokens") == 0


def test_tokens_are_reported_without_a_cost_when_no_price_is_configured():
    """Reporting a number and leaving the arithmetic to the reader beats inventing
    a price, which would be wrong within weeks and silently so."""
    totals = UsageTotals()
    totals.add(usage(1_000_000, 100_000))

    out = totals.to_dict()
    assert out["input_tokens"] == 1_000_000
    assert out["output_tokens"] == 100_000
    assert "estimated_cost" not in out


def test_a_configured_price_yields_an_estimated_cost():
    totals = UsageTotals()
    totals.add(usage(1_000_000, 200_000))

    out = totals.to_dict(input_price_per_mtok=0.5, output_price_per_mtok=2.0)

    assert out["estimated_cost"] == pytest.approx(0.5 + 0.4)
    assert out["price_input_per_mtok"] == 0.5


def test_cached_input_is_charged_at_the_cache_rate():
    """These profiles repeat the ontology context in every pass, which is exactly
    what a prompt cache is for. Charging cached tokens at the full input rate would
    overstate the bill by roughly thirty times on DeepSeek."""
    totals = UsageTotals()
    totals.add(usage(1_000_000, 0, cache_read=900_000))

    out = totals.to_dict(
        input_price_per_mtok=0.66, output_price_per_mtok=1.98,
        cache_read_price_per_mtok=0.022,
    )

    # 100k miss at 0.66 + 900k hit at 0.022
    assert out["estimated_cost"] == pytest.approx(0.066 + 0.0198)
    assert out["price_cache_read_per_mtok"] == 0.022


def test_charging_cache_reads_at_the_input_rate_is_stated_not_hidden():
    """Falling back is fine; falling back silently is how someone discovers a
    discrepancy from their invoice instead of from the run."""
    totals = UsageTotals()
    totals.add(usage(1_000_000, 0, cache_read=900_000))

    out = totals.to_dict(input_price_per_mtok=0.66, output_price_per_mtok=1.98)

    assert "cost_basis" in out
    assert out["estimated_cost"] == pytest.approx(0.66)


# ============================================================================
# The agent records what it spends
# ============================================================================


def _bare_agent(**config) -> SimpleNamespace:
    """An agent with no model, the way the unit tests build one."""
    from agents.knowledge_extraction import KnowledgeExtractionAgent

    from rich.console import Console

    agent = KnowledgeExtractionAgent.__new__(KnowledgeExtractionAgent)
    agent.config = AgentConfig(
        name="probe", description="probe", model_id="test-model", **config
    )
    # `invoke_structured` logs on the turn-cap and missing-output paths, so a bare
    # agent needs the console `__init__` would have given it.
    agent.console = Console()
    return agent


def test_an_agent_built_without_init_still_accounts():
    """Four test helpers build agents with `__new__`; the accumulator is lazy so
    none of them has to know it exists."""
    agent = _bare_agent()
    agent._usage.add(usage(10, 5))

    assert agent.usage_totals()["total_tokens"] == 15


class _Metrics:
    """Shaped like `EventLoopMetrics`: a LIFETIME total plus per-invocation usage.

    The distinction is the whole point. `accumulated_usage` grows across every call
    the agent ever makes and is never reset (`reset_usage_metrics` only appends an
    invocation), so a caller that reads it per call is summing a running total.
    """

    def __init__(self, per_call, lifetime):
        self._per_call = per_call
        self.accumulated_usage = lifetime

    def latest_agent_invocation(self):
        return SimpleNamespace(usage=self._per_call)


class _Structured(BaseModel):
    value: str = ""


@pytest.mark.parametrize("price_present", [False, True])
def test_the_structured_path_records_usage(price_present):
    """The integration that matters: `invoke_structured` used to return the parsed
    object and drop the metrics, so nothing downstream could see the bill."""
    agent = _bare_agent(
        model_provider="openai_compatible", base_url="http://127.0.0.1:9/v1",
        api_key="stub", ontology_path="ontology/requirements_base.yaml",
        ontology_dir="ontology",
        **({"price_input_per_mtok": 1.0, "price_output_per_mtok": 1.0}
           if price_present else {}),
    )
    result = SimpleNamespace(
        stop_reason="end_turn",
        structured_output=_Structured(value="ok"),
        metrics=SimpleNamespace(accumulated_usage=usage(1_000, 500)),
    )
    agent.agent = lambda *a, **k: result

    out = agent.invoke_structured("prompt", _Structured)

    assert out is result.structured_output
    totals = agent.usage_totals()
    assert totals["input_tokens"] == 1_000
    assert totals["output_tokens"] == 500
    assert ("estimated_cost" in totals) is price_present


def test_usage_is_taken_per_call_not_from_the_lifetime_total():
    """THE REGRESSION. Reading `accumulated_usage` per call sums a running total.

    Observed on a real twelve-call architecture run: 2,042,992 input tokens
    reported against a true figure near 314,000 — an overstatement of more than six
    times, on the number someone uses to decide whether a run is affordable.
    """
    agent = _bare_agent(
        model_provider="openai_compatible", base_url="http://127.0.0.1:9/v1",
        api_key="stub", ontology_path="ontology/requirements_base.yaml",
        ontology_dir="ontology",
    )
    # Call 1: 100 tokens so far. Call 2: 100 more, lifetime now 200.
    calls = [
        _Metrics(usage(100, 10), usage(100, 10)),
        _Metrics(usage(100, 10), usage(200, 20)),
        _Metrics(usage(100, 10), usage(300, 30)),
    ]
    agent.agent = lambda *a, **k: SimpleNamespace(
        stop_reason="end_turn", metrics=calls.pop(0)
    )

    for _ in range(3):
        agent.invoke("prompt")

    totals = agent.usage_totals()
    assert totals["input_tokens"] == 300, (
        f"expected three calls of 100, got {totals['input_tokens']} — "
        f"the lifetime total was summed instead of the per-call usage"
    )
    assert totals["calls"] == 3


def test_a_call_that_hit_the_turn_cap_is_still_accounted_for():
    """Tokens spent on a call whose result was discarded are exactly the ones
    worth knowing about."""
    agent = _bare_agent(
        model_provider="openai_compatible", base_url="http://127.0.0.1:9/v1",
        api_key="stub", ontology_path="ontology/requirements_base.yaml",
        ontology_dir="ontology",
    )
    result = SimpleNamespace(
        stop_reason="limit_turns",
        structured_output=None,
        metrics=SimpleNamespace(accumulated_usage=usage(900, 100)),
    )
    agent.agent = lambda *a, **k: result

    assert agent.invoke_structured("prompt", _Structured) is None
    assert agent.usage_totals()["total_tokens"] == 1_000


def test_the_plain_text_path_records_usage_too():
    """The fallback is a full generation and costs the same as any other call."""
    agent = _bare_agent(
        model_provider="openai_compatible", base_url="http://127.0.0.1:9/v1",
        api_key="stub", ontology_path="ontology/requirements_base.yaml",
        ontology_dir="ontology",
    )
    result = SimpleNamespace(
        stop_reason="end_turn", metrics=SimpleNamespace(accumulated_usage=usage(300, 40))
    )
    agent.agent = lambda *a, **k: result

    agent.invoke("prompt")

    assert agent.usage_totals()["total_tokens"] == 340


# ============================================================================
# It reaches the run record
# ============================================================================


def test_usage_in_metadata_lands_on_the_run_and_survives_a_round_trip():
    """Durable, because a run's cost is a property of the run and not of the
    process that happened to produce it."""
    from core.knowledge.serialise import run_from_dict, run_to_dict

    graph, run = graph_from_extraction(
        {"entities": [{"name": "X", "ontology_class": "FunctionalRequirement"}]},
        {"document_type": "requirements", "model_id": "m",
         "usage": {"calls": 6, "input_tokens": 1234, "output_tokens": 56,
                   "estimated_cost": 0.0123}},
        document_ref="r.md",
    )

    assert run.usage["input_tokens"] == 1234
    assert run.usage["estimated_cost"] == 0.0123
    assert run_from_dict(run_to_dict(run)).usage == run.usage


def test_a_run_with_no_usage_recorded_says_so_rather_than_reporting_zero():
    """A run predating this, or one whose provider reported nothing, is not free."""
    _graph, run = graph_from_extraction(
        {"entities": [{"name": "X", "ontology_class": "FunctionalRequirement"}]},
        {"document_type": "requirements", "model_id": "m"},
        document_ref="r.md",
    )
    assert run.usage == {}


# ============================================================================
# History must not carry between independent calls
# ============================================================================


def test_the_conversation_is_reset_between_calls():
    """THE OTHER HALF OF THE COST BUG.

    One Strands `Agent` serves every pass and every chunk, and the SDK appends each
    exchange to `agent.messages` — which is re-sent on the next call. Measured on a
    real twelve-call architecture run, that turned a few hundred thousand prompt
    tokens into roughly two million, growing quadratically with the pass count.

    It is a correctness problem first: the connections pass would otherwise be
    reading the structure pass's transcript as context.
    """
    agent = _bare_agent(
        model_provider="openai_compatible", base_url="http://127.0.0.1:9/v1",
        api_key="stub", ontology_path="ontology/requirements_base.yaml",
        ontology_dir="ontology",
    )

    class _Agent:
        def __init__(self):
            self.messages = [{"role": "user", "content": "left over from last pass"}]

        def __call__(self, *a, **k):
            # The SDK appends during the call; the reset happens before it.
            assert self.messages == [], "history was not cleared before the call"
            self.messages.append({"role": "assistant", "content": "this pass"})
            return SimpleNamespace(
                stop_reason="end_turn", structured_output=None,
                metrics=_Metrics(usage(10, 1), usage(10, 1)),
            )

    agent.agent = _Agent()
    agent.invoke_structured("prompt", _Structured)

    assert len(agent.agent.messages) == 1, (
        "the pass's own exchange should survive the call — only PRIOR history is dropped"
    )
