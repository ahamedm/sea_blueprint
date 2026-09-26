"""
Generation bounds — the parameters that stop a model looping.

THE BUG. `config/agent_config.py` set `temperature` and `max_tokens` per agent and
neither ever reached the model: `AgentConfig` had no field for either, so Pydantic
dropped them silently, and `_create_model` passed neither. The model therefore ran
on the inference server's own defaults, which on llama.cpp are temperature 1.0,
`repeat_penalty` 1.0 (off), DRY off, and `n_predict -1` (unbounded). A 2B model
under those settings will restate its plan until the context fills, and the turn
cap cannot stop it — a generation looping inside one turn never advances a turn.
That is the Designer "going into loop".

These tests assert the parameters survive from the config dict, through
`AgentConfig`, onto the model, and that a generation which loops anyway is NAMED
rather than recorded as an empty pass.
"""

from __future__ import annotations

import threading

import pytest

from agents.base_agent import AgentConfig, looks_like_a_repetition_loop
from agents.design_assistant import DesignAssistantAgent
from config import get_default_agent_config

# Verbatim from the run that reported "the Designer goes into loop". One cycle of
# the model restating its plan — a real capture, so the detector is tested against
# the failure it exists for rather than against an invented string.
LOOP_CYCLE = """And I need to surface any other relationships I need for traceability.

Let me write it out now.

OK, I'm going to write the final answer now. I'll be concise but complete.

Let me also think about whether I need to emit any QualityScenarios. The rules don't explicitly ask for them. Let me skip them.

OK, here's my final answer:

I need to propose:
1. A SoftwareSystem (payment_processing_system)
2. Container elements (new)
3. DataStore elements (new)
4. Component elements (new)
5. Parent-child relationships
6. Responsibilities
7. satisfies_attributes

And I need to surface any other relationships I need for traceability.

Let me write it out now.

OK, I'm going to write the final answer now. I'll be concise but complete.
"""

# The live model repeats this cycle until something stops it, so a real capture
# holds many passes. Reconstructing that is what makes the period visible.
LOOPING_ANSWER = LOOP_CYCLE * 4


def agent_for(config: AgentConfig) -> DesignAssistantAgent:
    agent = DesignAssistantAgent(config)
    return agent


def compatible_config(**overrides) -> AgentConfig:
    """An AgentConfig that needs no environment and contacts nothing.

    Constructing the Strands model opens a client pointed at a dead port; it makes
    no request until a prompt is sent.
    """
    fields = {
        "name": "Design Assistant Agent",
        "description": "test",
        "model_provider": "openai_compatible",
        "model_id": "stub-model",
        "base_url": "http://127.0.0.1:9/v1",
        "api_key": "stub",
        "ontology_path": "ontology/architecture_base.yaml",
        "ontology_dir": "ontology",
    }
    fields.update(overrides)
    return AgentConfig(**fields)


def sent_params(agent: DesignAssistantAgent) -> dict:
    """What the request body will carry, read off the constructed model."""
    return dict(agent.agent.model.config.get("params") or {})


# ============================================================================
# The silent drop
# ============================================================================


def test_the_per_agent_config_declares_generation_bounds():
    """The source values exist, and are not the server's defaults."""
    config = get_default_agent_config("design_assistant")
    assert config["temperature"] == 0.3
    # 16384, not 8192: a hosted model's non-thinking default is 8K too, and an
    # architecture structure pass over a real document can exceed it. A truncated
    # structured call is a failed one.
    assert config["max_tokens"] == 16384


def test_those_bounds_survive_into_agent_config():
    """THE REGRESSION. `AgentConfig` had no such fields, so Pydantic dropped them
    and nothing failed — the bound was believed to be in force and was not."""
    config = AgentConfig(**get_default_agent_config("design_assistant"))
    assert config.temperature == 0.3
    assert config.max_tokens == 16384


def test_they_are_sent_to_the_model():
    agent = agent_for(compatible_config(temperature=0.3, max_tokens=2048))
    params = sent_params(agent)
    assert params["temperature"] == 0.3
    assert params["max_tokens"] == 2048


def test_absent_bounds_are_not_invented():
    """A provider default is legitimate when nobody asked for anything — the bug
    was losing a bound that WAS asked for."""
    agent = agent_for(compatible_config())
    assert "temperature" not in sent_params(agent)
    assert "max_tokens" not in sent_params(agent)


# ============================================================================
# The escape hatch — samplers the per-agent config does not name
# ============================================================================


def test_a_parameter_the_config_does_not_name_is_passed_through():
    """`repeat_penalty` and DRY are the actual cure for repetition, and they are
    server-specific, so they must be settable without editing code."""
    agent = agent_for(compatible_config(
        temperature=0.3, max_tokens=2048,
        extra_params={"repeat_penalty": 1.1, "dry_multiplier": 0.8},
    ))
    params = sent_params(agent)
    assert params["repeat_penalty"] == 1.1
    assert params["dry_multiplier"] == 0.8
    # ...and the named bounds are still there.
    assert params["temperature"] == 0.3


def test_extra_params_win_over_the_per_agent_default():
    """Merged last on purpose: an operator who set a value explicitly is the
    authority, not the per-agent default it collides with."""
    agent = agent_for(compatible_config(max_tokens=8192, extra_params={"max_tokens": 512}))
    assert sent_params(agent)["max_tokens"] == 512


def test_env_extra_params_are_parsed(monkeypatch):
    from config.agent_config import load_environment

    monkeypatch.setenv("MODEL_EXTRA_PARAMS", '{"repeat_penalty": 1.1}')
    assert load_environment().model_extra_params == {"repeat_penalty": 1.1}


def test_env_extra_params_default_to_empty(monkeypatch):
    from config.agent_config import load_environment

    monkeypatch.setenv("MODEL_EXTRA_PARAMS", "")
    assert load_environment().model_extra_params == {}


def test_malformed_env_extra_params_fail_loudly(monkeypatch):
    """Silently dropping them would reproduce the exact bug being fixed: a bound
    the operator believes is in force and the model never receives."""
    from config.agent_config import load_environment

    monkeypatch.setenv("MODEL_EXTRA_PARAMS", "{not json")
    with pytest.raises(ValueError, match="JSON object"):
        load_environment()


def test_a_json_env_value_that_is_not_an_object_fails_loudly(monkeypatch):
    from config.agent_config import load_environment

    monkeypatch.setenv("MODEL_EXTRA_PARAMS", "[1, 2, 3]")
    with pytest.raises(ValueError, match="JSON OBJECT"):
        load_environment()


def test_the_env_escape_hatch_reaches_every_agent(monkeypatch):
    """A repetition loop is not specific to the Designer; the Designer found it."""
    monkeypatch.setenv("MODEL_EXTRA_PARAMS", '{"repeat_penalty": 1.1}')
    for name in ("design_assistant", "architecture_extraction", "knowledge_extraction"):
        assert get_default_agent_config(name)["extra_params"] == {"repeat_penalty": 1.1}


# ============================================================================
# The plain-text path is bounded too
# ============================================================================


class _RecordingAgent:
    """Stands in for the Strands agent, to see the call's keyword arguments."""

    def __init__(self, result="a plain answer"):
        self.result = result
        self.calls: list = []

    def __call__(self, prompt, **kwargs):
        self.calls.append((prompt, kwargs))
        return self.result


def test_a_plain_call_carries_the_turn_cap_and_the_cancel_signal():
    """The text fallback runs on exactly the calls the structured path already
    failed, and it was the ONE unguarded route to the model."""
    agent = agent_for(compatible_config(
        max_structured_turns=4, structured_timeout_seconds=30
    ))
    recorder = _RecordingAgent()
    agent.agent = recorder

    agent.invoke("prompt")

    _prompt, kwargs = recorder.calls[0]
    assert kwargs["limits"] == {"turns": 4}
    assert isinstance(kwargs["cancel_signal"], threading.Event)


def test_the_token_cap_travels_with_the_turn_cap_when_set():
    agent = agent_for(compatible_config(
        max_structured_turns=2, max_structured_tokens=10_000
    ))
    recorder = _RecordingAgent()
    agent.agent = recorder

    agent.invoke("prompt")

    assert recorder.calls[0][1]["limits"] == {"turns": 2, "total_tokens": 10_000}


def test_the_structured_path_still_works_through_invoke():
    agent = agent_for(compatible_config())
    recorder = _RecordingAgent()
    agent.agent = recorder

    agent.invoke("prompt", structured_output_model=dict)

    _prompt, kwargs = recorder.calls[0]
    assert kwargs["structured_output_model"] is dict
    assert "limits" in kwargs and "cancel_signal" in kwargs


# ============================================================================
# Per-pass temperature — the generative pass may run warmer than the rest
# ============================================================================


def test_a_pass_can_run_warmer_without_losing_its_bound():
    """Warming one pass must not un-bound it.

    `update_config(params=...)` REPLACES the mapping, so the max_tokens ceiling
    and any MODEL_EXTRA_PARAMS sampler have to be carried across. Dropping them
    would leave the warmed pass as the one unbounded call in the run — the exact
    bug this module exists for, reintroduced by a temperature tweak.
    """
    agent = agent_for(compatible_config(
        temperature=0.3, max_tokens=8192,
        extra_params={"repeat_penalty": 1.1},
    ))

    with agent.sampling(0.6):
        warmed = sent_params(agent)
        assert warmed["temperature"] == 0.6
        assert warmed["max_tokens"] == 8192
        assert warmed["repeat_penalty"] == 1.1

    restored = sent_params(agent)
    assert restored["temperature"] == 0.3
    assert restored["max_tokens"] == 8192
    assert restored["repeat_penalty"] == 1.1


def test_the_override_is_restored_even_when_the_pass_raises():
    """A failed pass must not leave the model warm for the ones after it."""
    agent = agent_for(compatible_config(temperature=0.3))

    with pytest.raises(RuntimeError, match="pass exploded"):
        with agent.sampling(0.9):
            assert sent_params(agent)["temperature"] == 0.9
            raise RuntimeError("pass exploded")

    assert sent_params(agent)["temperature"] == 0.3


def test_no_override_leaves_the_agent_temperature_alone():
    agent = agent_for(compatible_config(temperature=0.3))
    with agent.sampling(None):
        assert sent_params(agent)["temperature"] == 0.3


def test_a_provider_with_no_model_of_ours_is_not_a_crash():
    """Bedrock routes through Strands' default client, so there is nothing of ours
    to configure — the override is skipped rather than pretended."""
    from types import SimpleNamespace

    agent = agent_for(compatible_config(temperature=0.3))
    agent.agent = SimpleNamespace(model=None)

    with agent.sampling(0.9):
        pass


# ============================================================================
# The live loop
# ============================================================================


def test_the_live_loop_is_detected():
    assert looks_like_a_repetition_loop(LOOPING_ANSWER) is True


def test_a_single_cycle_is_not_called_a_loop():
    """Ambiguity resolves to silence. One pass over a plan is something a model is
    allowed to do; calling it a loop would make the signal noise."""
    assert looks_like_a_repetition_loop(LOOP_CYCLE) is False


def test_an_ordinary_answer_is_not_flagged():
    answer = (
        "# Proposed architecture\n\n"
        "The system is a single deployable container.\n\n"
        "## Containers\n\n"
        "- Payment Orchestrator: routes authorizations to the scheme.\n"
        "- Transaction Store: persists authorization outcomes.\n\n"
        "## Responsibilities\n\n"
        "- Payment Orchestrator: routing, validation, retry.\n"
        "- Transaction Store: durability, query.\n"
    )
    assert looks_like_a_repetition_loop(answer) is False


def test_repetitive_but_not_cyclic_output_is_not_flagged():
    """A list of similar rows is repetitive without being a cycle."""
    rows = "\n".join(f"- Requirement {i} is satisfied by the orchestrator." for i in range(20))
    assert looks_like_a_repetition_loop(rows) is False


def test_empty_output_is_not_a_loop():
    assert looks_like_a_repetition_loop("") is False
    assert looks_like_a_repetition_loop("   \n\n  \n") is False
