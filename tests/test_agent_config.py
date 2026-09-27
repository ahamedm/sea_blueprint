"""
Agent configuration resolution.

There was no test over `get_default_agent_config`, and that gap hid a bug that
made every requirements extraction run under the architecture agent's system
prompt: a loop variable named `agent_name` shadowed the function parameter, so
the `return` handed back whichever agent the loop finished on.

Nothing failed. Extraction still produced triples, and the graph looked plausible
— it just described containers and deployment for a requirements document. A test
that merely asserted "a config comes back" would not have caught it either, which
is why these assert the config belongs to the agent that was asked for.
"""

from __future__ import annotations

import pytest

from config import get_default_agent_config

# agent -> (expected name, expected ontology layer it reads)
AGENTS = {
    "knowledge_extraction": ("Knowledge Extraction Agent", "requirements_base"),
    "architecture_extraction": ("Architecture Extraction Agent", "architecture_base"),
    "ontology_engineer": ("Ontology Engineer Agent", "requirements_base"),
    "domain_context": ("Domain Context Agent", "requirements_base"),
    # The architecture layer: this profile proposes ARC-G, and architecture_base
    # imports the other three, so the requirements vocabulary is reachable through it.
    "design_assistant": ("Design Assistant Agent", "architecture_base"),
    "semantic_auditor": ("Semantic Auditor Agent", "requirements_base"),
}


@pytest.mark.parametrize("agent,expected", sorted(AGENTS.items()))
def test_each_agent_gets_its_own_config(agent, expected):
    """The bug, as a test.

    Asserting only that *something* is returned would have passed while every
    agent silently received the architecture config. The name is the observable
    that proves the right block came back.
    """
    name, ontology = expected
    config = get_default_agent_config(agent)
    assert config, f"no config for {agent}"
    assert config["name"] == name, (
        f"{agent} received {config['name']!r} — a lookup that ignores the requested "
        f"agent is how REQ-G extraction ended up running the ARC-G prompt"
    )
    assert ontology in config["ontology_path"]


def test_the_requested_agent_does_not_depend_on_lookup_order():
    """Asking twice, in either order, must give the same answer per agent.

    A shadowed loop variable is order-dependent in exactly this way: the last
    iteration wins. Asserting both orders makes the failure mode explicit rather
    than incidental.
    """
    first = get_default_agent_config("knowledge_extraction")
    get_default_agent_config("architecture_extraction")
    second = get_default_agent_config("knowledge_extraction")
    assert first["name"] == second["name"] == "Knowledge Extraction Agent"


def test_the_two_extraction_agents_are_differently_prompted():
    """They must not merely have different names.

    The knowledge agent extracts requirements; the architecture agent extracts C4
    elements. If their prompts agree, one of them is wrong regardless of naming.
    """
    req = get_default_agent_config("knowledge_extraction")
    arc = get_default_agent_config("architecture_extraction")
    assert req["system_prompt"] != arc["system_prompt"]
    assert "C4" in arc["system_prompt"]
    assert "C4" not in req["system_prompt"]


def test_unknown_agent_returns_no_config():
    assert get_default_agent_config("not_an_agent") == {}


def test_structured_output_settings_reach_every_agent():
    """These are injected once, after the config blocks, so a missing key means the
    injection loop silently skipped an agent."""
    for agent in AGENTS:
        config = get_default_agent_config(agent)
        for key in (
            "use_structured_output",
            "max_structured_turns",
            "structured_timeout_seconds",
            "request_timeout_seconds",
            "ontology_dir",
            # Hosted-endpoint settings, injected the same way. `extra_params` is
            # how a provider-specific field (DeepSeek's thinking mode, for one)
            # reaches the request at all, and the price keys are what let a run
            # report a cost — an agent missing either degrades silently.
            "request_max_retries",
            "extra_params",
            "price_input_per_mtok",
            "price_output_per_mtok",
            "price_cache_read_per_mtok",
        ):
            assert key in config, f"{agent} is missing {key}"


# ============================================================================
# Which credential an endpoint gets
# ============================================================================
#
# A 401 against an endpoint whose key is correct is almost always the wrong key
# being sent. The platform chose it with
#   hosted = "localhost" not in base_url and "127.0.0.1" not in base_url
# so every other address was "hosted" — including a private LAN address such as
# http://192.168.3.176:8080/v1, which is a local inference server. A local server
# was therefore sent the paid DEEPSEEK_API_KEY and LOCAL_API_KEY was never used.
# These tests pin the classification and, more importantly, WHICH KEY comes out.


from config.agent_config import (  # noqa: E402
    EnvironmentConfig,
    choose_api_key,
    endpoint_is_local,
)


@pytest.mark.parametrize("url", [
    "http://localhost:8080/v1",
    "http://127.0.0.1:8080/v1",
    "http://192.168.3.176:8080/v1",     # the case that produced the 401
    "http://10.0.0.5:1234/v1",
    "http://172.16.4.2:8080/v1",
    "http://llm.internal:8000/v1",
    "http://box.local:8080/v1",
])
def test_a_private_or_loopback_address_is_local(url):
    assert endpoint_is_local(url) is True


@pytest.mark.parametrize("url", [
    "https://api.deepseek.com",
    "https://api.openai.com/v1",
    "https://my-endpoint.openai.azure.com/",
    "http://8.8.8.8:80/v1",
])
def test_a_public_address_is_hosted(url):
    assert endpoint_is_local(url) is False


def _env(**overrides) -> EnvironmentConfig:
    values = {
        "local_api_key": "local-dummy",
        "deepseek_api_key": "sk-deepseek",
        "openai_api_key": "",
        "local_model_id": "some-model",
    }
    values.update(overrides)
    return EnvironmentConfig(**values)


def test_a_local_endpoint_never_carries_a_paid_key():
    """The defect in one assertion: sending the paid key to a local server is both a
    401 waiting to happen and a secret on the wire for nothing."""
    key, source = choose_api_key("http://192.168.3.176:8080/v1", _env())

    assert key == "local-dummy"
    assert source == "LOCAL_API_KEY"


def test_a_hosted_deepseek_endpoint_uses_the_deepseek_key():
    key, source = choose_api_key("https://api.deepseek.com", _env())

    assert key == "sk-deepseek"
    assert source == "DEEPSEEK_API_KEY"


def test_another_hosted_endpoint_uses_the_openai_key():
    """`OPENAI_API_KEY` was loaded and never read, so a hosted non-DeepSeek endpoint
    silently fell back to the LOCAL dummy — the same 401 seen from the other side."""
    key, source = choose_api_key(
        "https://api.openai.com/v1", _env(openai_api_key="sk-openai")
    )

    assert key == "sk-openai"
    assert source == "OPENAI_API_KEY"


def test_a_hosted_endpoint_with_no_provider_key_falls_back_and_says_so():
    key, source = choose_api_key("https://api.openai.com/v1", _env())

    assert key == "local-dummy"
    assert source == "LOCAL_API_KEY"


def test_the_source_is_reported_so_a_401_can_be_diagnosed():
    """Which key was sent is the first question a 401 raises; the log has to answer it,
    because the previous silent inference is what hid this for so long."""
    for url in ("https://api.deepseek.com", "https://api.openai.com/v1",
                "http://192.168.3.176:8080/v1"):
        key, source = choose_api_key(url, _env(openai_api_key="sk-openai"))
        assert source in {"LOCAL_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"}
        assert key   # never an empty credential chosen silently


def test_a_provider_endpoint_is_never_given_another_providers_key():
    """`tests/test_hosted_endpoint.py` already decided this policy — degrade to the
    configured fallback rather than substitute a credential — and it is worth pinning
    here too, because substituting is the same mistake as sending the paid key to a
    local server. Both 401; only the logged source says why."""
    key, source = choose_api_key(
        "https://api.deepseek.com", _env(deepseek_api_key="", openai_api_key="sk-openai")
    )

    assert key == "local-dummy"
    assert source == "LOCAL_API_KEY"


# ============================================================================
# A provider-specific flag sent to the wrong kind of endpoint
# ============================================================================
#
# Reasoning models spend their output budget on chain-of-thought before answering,
# and each server spells "don't" differently. A flag from the wrong provider is
# silently ignored, so every call burns budget on reasoning and can hit the 180s
# wall-clock cancel — which surfaces as "cancelled. Falling back to text parsing"
# and, on the architecture profile, as no triples at all. Measured against the
# local server: DeepSeek's `thinking` flag is ignored (101 chars of reasoning,
# empty content, finish_reason=length), while `chat_template_kwargs:
# {enable_thinking: false}` and `reasoning_effort: "none"` both stop it dead.


from config.agent_config import extra_params_mismatch  # noqa: E402


def test_deepseek_flag_on_a_local_endpoint_is_named_as_wrong():
    warning = extra_params_mismatch(
        "http://192.168.3.176:8080/v1",
        {"extra_body": {"thinking": {"type": "disabled"}}},
    )

    assert "local" in warning and "thinking" in warning
    assert "enable_thinking" in warning          # names the fix, not just the fault


def test_local_flag_on_a_local_endpoint_is_fine():
    assert extra_params_mismatch(
        "http://192.168.3.176:8080/v1",
        {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
    ) == ""


def test_deepseek_flag_on_deepseek_is_fine():
    assert extra_params_mismatch(
        "https://api.deepseek.com", {"extra_body": {"thinking": {"type": "disabled"}}}
    ) == ""


def test_local_flag_on_a_hosted_endpoint_is_named_as_wrong():
    warning = extra_params_mismatch(
        "https://api.deepseek.com",
        {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}},
    )

    assert "hosted" in warning
    assert "thinking" in warning                 # names the fix for that side too


def test_no_extra_params_is_not_a_warning():
    assert extra_params_mismatch("http://192.168.3.176:8080/v1", {}) == ""


def test_the_warning_is_derived_not_remembered():
    """Both directions are computed from the endpoint class, so adding a server
    vocabulary later cannot leave a stale hardcoded pairing behind."""
    local = "http://10.0.0.9:8000/v1"
    hosted = "https://api.deepseek.com"
    assert extra_params_mismatch(local, {"extra_body": {"thinking": {}}})
    assert extra_params_mismatch(hosted, {"extra_body": {"reasoning_effort": "none"}})
