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
        ):
            assert key in config, f"{agent} is missing {key}"
