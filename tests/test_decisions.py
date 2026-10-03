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


def test_the_architecture_extraction_profile_runs_a_decisions_pass():
    """A report from the field: an architecture ingest produced no decisions.

    The decisions pass was added to the DESIGN profile, so an `/ingest` of an
    architecture document could never emit `architecture_decisions` — the graph's
    decision count stayed at zero and nothing failed. This is the assertion that
    would have said so.
    """
    from agents.architecture_extraction.passes import ARCHITECTURE_PASSES

    names = [spec.name for spec in ARCHITECTURE_PASSES]
    assert "decisions" in names, f"the extraction profile runs {names}"


def test_the_decisions_pass_emits_keys_ingest_consumes():
    """A pass whose output key nothing reads is the defect `test_output_consumption`
    exists for, and it is worth asserting here too: the whole point of adding the pass
    is that the decision reaches the graph."""
    from agents.architecture_extraction.passes import ARCHITECTURE_PASSES

    spec = next(s for s in ARCHITECTURE_PASSES if s.name == "decisions")
    assert "architecture_decisions" in spec.output_keys.values()


def test_an_extracted_decision_keeps_the_two_status_axes_apart():
    """`status` is the decision's life in the enterprise; provenance and the assertion's
    status are the REVIEW state. A document sounding settled does not make the
    extraction verified, and conflating the two is how the document's confidence
    becomes the platform's."""
    from agents.architecture_extraction.passes import ArchitectureDecisionRecord

    record = ArchitectureDecisionRecord(title="Synchronous gateway calls")

    assert record.status == "ACCEPTED", "the default is what a document asserts"
    assert record.status not in ("VERIFIED", "UNVERIFIED"), (
        "the review state must not be expressible in this field"
    )


def test_a_decision_payload_needs_no_triples_to_become_nodes_and_edges():
    """The decisions pass asks for NO triples, because ingest derives every edge from the
    record fields. This is what makes that safe, and it is the assertion that would have
    caught the opposite: a pass asking for a large redundant payload, timing out on a
    3.3k-character document, and losing every decision it was meant to record."""
    output = {
        "architecture_decisions": [{
            "title": "Rule-based routing over round-robin",
            "context": "Routing criteria vary by country and currency.",
            "decision": "Route with a configurable rule engine.",
            "status": "ACCEPTED",
            "consequences": ["A second component to operate"],
            "alternatives_considered": ["Static round-robin"],
            "affects_elements": ["Payment Orchestrator Service"],
            "supersedes": ["Naive routing"],
        }],
        # deliberately NO "triples" key at all
    }
    graph, _ = graph_from_extraction(output, {"model_id": "stub"},
                                     document_ref="arch.md", document_text="arch")

    decision = next(n for n in graph.nodes.values() if n.kind == "ArchitectureDecision")
    facts = {a.predicate: a for a in graph.active() if a.subject == decision.id}

    assert facts["decision"].value == "Route with a configurable rule engine."
    assert facts["status"].value == "ACCEPTED"
    assert facts["consequence"].value == "A second component to operate"
    assert facts["alternative"].value == "Static round-robin"
    # the two EDGES, which is the part a triple would have duplicated
    assert facts["affects_element"].object, "affects_elements must become an edge"
    assert facts["supersedes"].object, "supersedes must become an edge"


def test_the_decisions_pass_declares_no_triples_output():
    """A pass whose declared output the agent cannot consume is the YB-051 shape; a pass
    whose declared output is DERIVABLE is wasted model effort that failed in the field."""
    from agents.architecture_extraction.passes import ARCHITECTURE_PASSES

    spec = next(s for s in ARCHITECTURE_PASSES if s.name == "decisions")

    assert "triples" not in set(spec.output_keys.values())
    assert "triples" not in spec.schema.model_fields
