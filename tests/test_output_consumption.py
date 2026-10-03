"""
Output-consumption accounting: an emitted key must have a consumer (YB-051).

THE DEFECT THIS PINS. The architecture profile ran a `connections` pass over every
chunk, emitted the records under their own key, and `graph_from_extraction` read
eleven keys without that one — so every run paid for the model call and put nothing
in the graph. The C4 diagram had boxes and no arrows, for the life of the pass.

The per-pass `triples_produced` counter could not see it, because that pass emitted
triples as well: its count was non-zero while its connections were discarded. A
counter that cannot see the loss is not a guard, so there are two here:

  1. **Static** — every key a `PassSpec` declares is a key some consumer reads. A
     new pass output that nothing routes fails this test instead of losing facts.
  2. **Runtime** — the run records what it emitted per key, whether a consumer read
     it, and how many facts were stored, so "the pipeline made this and nothing
     looked at it" is visible on the run rather than inferred from an empty view.
"""

from __future__ import annotations

from agents.architecture_extraction.passes import ARCHITECTURE_PASSES
from agents.design_assistant.passes import design_passes
from agents.knowledge_extraction.agent import KnowledgeExtractionAgent
from agents.architecture_extraction.agent import ArchitectureExtractionAgent
from core.knowledge import (
    ROUTED_OUTPUT_KEYS,
    UNROUTED_OUTPUT_KEYS,
    graph_from_extraction,
)
from core.knowledge.serialise import run_from_dict, run_to_dict


def _declared_keys(specs) -> set:
    keys: set = set()
    for spec in specs:
        keys |= set(spec.output_keys.values())
    return keys


# ============================================================================
# 1. Static: every declared pass output has a consumer
# ============================================================================


def test_every_architecture_pass_output_key_is_routed():
    declared = _declared_keys(ARCHITECTURE_PASSES)

    unaccounted = declared - ROUTED_OUTPUT_KEYS - set(UNROUTED_OUTPUT_KEYS)
    assert not unaccounted, (
        f"these pass outputs have no consumer: {sorted(unaccounted)} — "
        f"add them to ingest.INGESTED_OUTPUT_KEYS (and read them) or to "
        f"UNROUTED_OUTPUT_KEYS with the reason they are safe to drop"
    )


def test_every_design_pass_output_key_is_routed():
    declared = _declared_keys(design_passes("", ()))

    unaccounted = declared - ROUTED_OUTPUT_KEYS - set(UNROUTED_OUTPUT_KEYS)
    assert not unaccounted, f"these design pass outputs have no consumer: {sorted(unaccounted)}"


def test_every_profile_node_and_edge_key_is_routed():
    """The requirements profile names its keys through a hook, not a PassSpec.

    `_output_keys()` returning `relationships` is exactly the key YB-051 checked and
    cleared: it is a predicate vocabulary with no endpoints, and the triples carry
    the same claim, so it is an EXPLAINED unrouted key rather than an unaccounted one.
    """
    for agent_cls in (KnowledgeExtractionAgent, ArchitectureExtractionAgent):
        keys = set(agent_cls._output_keys(None).values())
        unaccounted = keys - ROUTED_OUTPUT_KEYS - set(UNROUTED_OUTPUT_KEYS)
        assert not unaccounted, f"{agent_cls.__name__} emits unrouted keys: {sorted(unaccounted)}"


# ============================================================================
# 2. Runtime: emitted vs stored, per key, on the run
# ============================================================================


def test_the_run_reports_emitted_counts_and_what_was_stored():
    output = {
        "elements": [
            {"name": "Payments", "element_type": "SoftwareSystem"},
            {"name": "Transaction Store", "element_type": "DataStore",
             "parent": "Payments"},
        ],
        "connections": [{"source": "Payments", "target": "Transaction Store"}],
        "triples": [{"subject": "Payments", "predicate": "uses_technology",
                     "object": "PostgreSQL"}],
    }

    graph, run = graph_from_extraction(output, {"model_id": "stub"},
                                       document_ref="arch.md", document_text="...")

    assert run.output_counts["triples"] == {"emitted": 1, "consumed": 1}
    assert run.output_counts["elements"] == {"emitted": 2, "consumed": 1}
    assert run.output_counts["connections"] == {"emitted": 1, "consumed": 1}
    assert run.unconsumed_keys == []
    assert run.stored_facts == len(graph.assertions)
    # The report is a pair, not a claim: N emitted, M stored.
    assert sum(c["emitted"] for c in run.output_counts.values()) == 4
    assert run.stored_facts > 0


def test_an_emitted_key_nothing_reads_is_reported_on_the_run():
    """The exact shape of the connections defect, with a key no consumer knows."""
    output = {
        "elements": [{"name": "Payments", "element_type": "SoftwareSystem"}],
        "payment_routes": [{"from": "Payments", "to": "Nowhere"}],
    }

    _graph, run = graph_from_extraction(output, {"model_id": "stub"},
                                        document_ref="arch.md", document_text="...")

    assert run.unconsumed_keys == ["payment_routes"]
    assert run.output_counts["payment_routes"] == {"emitted": 1, "consumed": 0}


def test_an_explained_unrouted_key_is_recorded_but_not_an_alarm():
    """`relationships` is emitted by the requirements profile and read by nothing.

    That is safe — it is a predicate vocabulary and the triples carry the claim — so
    it is still recorded as unconsumed, but the pipeline does not warn about it. The
    distinction is data (`UNROUTED_OUTPUT_KEYS`), not a special case in the warning.
    """
    output = {
        "entities": [{"name": "Payments", "entity_type": "System"}],
        "relationships": [{"relationship_type": "part_of", "description": ""}],
    }

    _graph, run = graph_from_extraction(output, {"model_id": "stub"},
                                        document_ref="req.md", document_text="...")

    assert "relationships" in UNROUTED_OUTPUT_KEYS
    assert run.unconsumed_keys == ["relationships"]
    assert run.output_counts["relationships"]["consumed"] == 0


def test_the_accounting_survives_a_save_and_a_load():
    output = {
        "elements": [{"name": "Payments", "element_type": "SoftwareSystem"}],
        "payment_routes": [{"from": "Payments", "to": "Nowhere"}],
        "triples": [{"subject": "Payments", "predicate": "uses_technology",
                     "object": "PostgreSQL"}],
    }
    _graph, run = graph_from_extraction(output, {"model_id": "stub"},
                                        document_ref="arch.md", document_text="...")

    restored = run_from_dict(run_to_dict(run))

    assert restored.output_counts == run.output_counts
    assert restored.unconsumed_keys == run.unconsumed_keys
    assert restored.stored_facts == run.stored_facts


def test_the_architecture_agent_collects_every_pass_output_key():
    """The other half of "declared but not consumed", and the one nothing checked.

    `test_output_consumption` proves INGEST reads each pass's key. It says nothing about
    whether the AGENT collects it — and that is where this failed: `decisions` ran, the
    model returned records, the agent's merge never asked for them, and the graph got zero
    decisions while every test passed. A pass whose output the agent does not collect is
    a prompt sent for nothing.
    """
    from pathlib import Path

    import agents.architecture_extraction.agent as arch_agent

    source = Path(arch_agent.__file__).read_text()
    missing = []
    for spec in ARCHITECTURE_PASSES:
        for key in sorted(set(spec.output_keys.values()) - {"triples"}):
            if f'collect(outcomes, "{spec.name}", "{key}")' not in source:
                missing.append(f"{spec.name}.{key}")

    assert not missing, (
        f"these pass outputs are never collected by the agent, so they cannot reach "
        f"the graph: {missing}"
    )
