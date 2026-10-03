"""
Decisions and trade-offs.

Three facts this feature makes true, each pinned by a test:

- the recorded ADRs parse into ArchitectureDecision records and ingest as nodes;
- a decisions-pass output ingests with its literals and the two edges
  (`affects_element`, `supersedes`) that make impact reasoning answerable;
- a structured `trade_offs` record becomes TradeOff nodes linked by `has_trade_off`
  with `gains`/`sacrifices` edges to QualityAttribute nodes, not a `trade_off`
  literal on the owner.
"""

from __future__ import annotations

from core.knowledge import KnowledgeGraph, graph_from_extraction
from core.knowledge.decisions import (
    DEFAULT_DECISIONS_DIR,
    ingest_decisions,
    load_adr_records,
)


def _build(output: dict) -> KnowledgeGraph:
    graph, _run = graph_from_extraction(
        output,
        {"model_id": "fake-model", "model_calls": 1},
        document_ref="design:INIT-1@working",
        document_text="digest",
    )
    return graph


# ============================================================================
# ADR ingest
# ============================================================================


def test_load_adr_records_parses_frontmatter_and_body(tmp_path):
    (tmp_path / "ADR-0001-example.md").write_text(
        "---\nid: ADR-0001\ntitle: Use PostgreSQL\ndate: 2026-01-01\nstatus: accepted\n"
        "---\nForces and context.\n"
    )
    records = load_adr_records(tmp_path)
    assert len(records) == 1
    record = records[0]
    assert record["id"] == "ADR-0001"
    assert record["title"] == "Use PostgreSQL"
    assert record["status"] == "accepted"
    assert record["decided_date"] == "2026-01-01"
    assert record["decision"] == "Use PostgreSQL"
    assert record["context"] == "Forces and context."


def test_the_real_adr_directory_parses():
    """The shipped ADRs are the source of truth; if none parse, decisions vanish."""
    records = load_adr_records(DEFAULT_DECISIONS_DIR)
    assert len(records) >= 10
    assert all(record.get("id") and record.get("title") for record in records)


def test_ingest_decisions_writes_nodes_and_literals():
    records = [{
        "id": "ADR-0001",
        "title": "Use PostgreSQL",
        "status": "accepted",
        "decided_date": "2026-01-01",
        "decision": "Use PostgreSQL",
        "context": "Relational integrity.",
    }]
    graph = ingest_decisions(KnowledgeGraph(), records)
    decision = next(n for n in graph.nodes.values() if n.kind == "ArchitectureDecision")
    assert decision.label == "Use PostgreSQL"
    facts = {
        a.predicate: a.value for a in graph.active()
        if a.subject == decision.id and a.object is None
    }
    assert facts["id"] == "ADR-0001"
    assert facts["status"] == "accepted"
    assert facts["decided_date"] == "2026-01-01"
    # A record, not a proposal.
    assert all(a.is_human for a in graph.active())


# ============================================================================
# Decisions pass ingest
# ============================================================================


def test_a_decision_ingests_with_links_and_literals():
    graph = _build({
        "architecture_decisions": [{
            "title": "Synchronous gateway calls",
            "context": "Authorizations must be fast.",
            "decision": "Call the scheme switch synchronously.",
            "consequences": ["Higher latency coupling"],
            "alternatives_considered": ["Async with outbox"],
            "status": "proposed",
            "affects_elements": ["Order Service"],
            "supersedes": ["ADR-0001"],
        }],
        "elements": [{"name": "Order Service", "element_type": "Container"}],
    })
    decision = next(n for n in graph.nodes.values() if n.kind == "ArchitectureDecision")
    facts = {
        a.predicate for a in graph.active()
        if a.subject == decision.id and a.object is None
    }
    assert {"context", "decision", "status", "consequence", "alternative"} <= facts

    element = next(n for n in graph.nodes.values() if n.label == "Order Service")
    assert any(
        a.predicate == "affects_element" and a.object == element.id
        for a in graph.active() if a.subject == decision.id
    )
    assert any(
        a.predicate == "supersedes" and a.object
        for a in graph.active() if a.subject == decision.id
    )


# ============================================================================
# Structured TradeOffs
# ============================================================================


def test_a_patterns_trade_offs_become_structured_nodes():
    graph = _build({
        "architecture_patterns": [{
            "name": "Circuit Breaker",
            "trade_offs": [{
                "name": "Consistency over availability",
                "gains": ["Consistency"],
                "sacrifices": ["Availability"],
                "rationale": "Authorizations must not double-charge.",
            }],
        }],
        "elements": [{"name": "Order Service", "element_type": "Container"}],
    })
    pattern = next(n for n in graph.nodes.values() if n.kind == "ArchitecturePattern")
    trade_off = next(n for n in graph.nodes.values() if n.kind == "TradeOff")
    assert trade_off.label == "Consistency over availability"

    assert any(
        a.subject == pattern.id and a.predicate == "has_trade_off" and a.object == trade_off.id
        for a in graph.active()
    )
    gains = {
        graph.nodes[a.object].label for a in graph.active()
        if a.subject == trade_off.id and a.predicate == "gains"
    }
    sacrifices = {
        graph.nodes[a.object].label for a in graph.active()
        if a.subject == trade_off.id and a.predicate == "sacrifices"
    }
    assert gains == {"Consistency"}
    assert sacrifices == {"Availability"}
    assert any(
        a.subject == trade_off.id and a.predicate == "rationale" and a.value
        for a in graph.active()
    )
