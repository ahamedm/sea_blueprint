"""
Hosted-endpoint readiness: transport retries, and which key goes where.

WHY THIS EXISTS. Moving from a local llama.cpp server to a paid endpoint changes
two things that fail quietly. A local server never rate-limits, so nothing in the
platform ever needed a retry; and a local server never authenticates, so the key
was a placeholder. Both are one config value away from being wrong, and being wrong
looks like a successful run with no passes.

The retry budget and the key choice are asserted here because neither is visible in
the output: a run that silently used the dummy local key against a paid endpoint
fails at the first call, and a run with no retries loses a pass to a single 429.
"""

from __future__ import annotations

import pytest

from agents.base_agent import AgentConfig
from agents.design_assistant import DesignAssistantAgent
from config import get_default_agent_config


def agent_for(config: AgentConfig) -> DesignAssistantAgent:
    """A real agent, so `_create_model` actually runs.

    Constructing the model opens a client; it makes no request until a prompt is
    sent, so no network is touched.
    """
    return DesignAssistantAgent(config)


# ============================================================================
# Transport retries reach the client
# ============================================================================


@pytest.mark.parametrize("provider,extra", [
    ("openai_compatible", {"base_url": "https://api.deepseek.com"}),
    ("openai", {}),
    ("ollama", {}),
])
def test_the_retry_budget_reaches_the_model_client(provider, extra):
    """A hosted endpoint rate-limits, and one 429 would otherwise cost a whole pass.

    The OpenAI SDK retries twice by default; this asserts OUR configured value gets
    through, on every provider whose client we build.
    """
    agent = agent_for(AgentConfig(
        name="probe", description="probe", model_provider=provider,
        model_id="deepseek-v4-pro", api_key="k",
        ontology_path="ontology/architecture_base.yaml", ontology_dir="ontology",
        request_max_retries=5, **extra,
    ))

    assert agent.agent.model.client_args.get("max_retries") == 5


def test_zero_retries_is_honoured_rather_than_treated_as_unset():
    """`0` disables retries. `or` would silently coerce it back to a default."""
    agent = agent_for(AgentConfig(
        name="probe", description="probe", model_provider="openai_compatible",
        base_url="https://api.deepseek.com", model_id="m", api_key="k",
        ontology_path="ontology/architecture_base.yaml", ontology_dir="ontology",
        request_max_retries=0,
    ))

    assert agent.agent.model.client_args.get("max_retries") == 0


def test_the_default_retry_budget_is_higher_than_the_sdk_default():
    """The SDK's 2 is thin for a run of a dozen paid calls."""
    config = get_default_agent_config("design_assistant")
    assert config["request_max_retries"] >= 3


def test_the_retry_budget_is_env_overridable(monkeypatch):
    monkeypatch.setenv("REQUEST_MAX_RETRIES", "7")
    assert get_default_agent_config("design_assistant")["request_max_retries"] == 7


def test_every_agent_gets_a_retry_budget():
    for name in ("design_assistant", "architecture_extraction",
                 "knowledge_extraction", "semantic_auditor"):
        assert "request_max_retries" in get_default_agent_config(name)


# ============================================================================
# Which key is used
# ============================================================================


def _with_endpoint(monkeypatch, base_url: str, local: str, deepseek: str):
    """Pin the environment the config resolves against.

    Both base-url spellings are set/cleared explicitly because `load_environment`
    accepts either and `load_dotenv` will not override an existing value.
    """
    monkeypatch.setenv("OPENAI_BASEURL", base_url)
    monkeypatch.setenv("OPENAI_BASE_URL", base_url)
    monkeypatch.setenv("LOCAL_API_KEY", local)
    monkeypatch.setenv("DEEPSEEK_API_KEY", deepseek)
    return get_default_agent_config("design_assistant")


def test_a_hosted_endpoint_uses_the_hosted_key(monkeypatch):
    """Sending a llama.cpp dummy to a paid endpoint fails auth on the first call."""
    config = _with_endpoint(
        monkeypatch, "https://api.deepseek.com", "local-dummy", "sk-hosted"
    )
    assert config["api_key"] == "sk-hosted"


@pytest.mark.parametrize("local_url", [
    "http://127.0.0.1:8080/v1",
    "http://localhost:11434/v1",
])
def test_a_local_endpoint_keeps_the_local_key(monkeypatch, local_url):
    """Sending a live paid key to a local server is a secret on the wire for nothing."""
    config = _with_endpoint(monkeypatch, local_url, "local-dummy", "sk-hosted")
    assert config["api_key"] == "local-dummy"


def test_a_hosted_endpoint_with_no_hosted_key_falls_back_rather_than_failing(monkeypatch):
    """Degrading to the configured key beats refusing to construct the agent: the
    error the endpoint returns names the real problem, and a missing optional
    secret is not something this layer can diagnose."""
    config = _with_endpoint(monkeypatch, "https://api.deepseek.com", "local-dummy", "")
    assert config["api_key"] == "local-dummy"
