"""JSON round trip: a graph that survives save/load badly is worse than one never saved."""

import json

import pytest

from core.knowledge import KnowledgeGraph, graph_from_dict, graph_to_dict
from core.knowledge.serialise import SCHEMA_VERSION


def test_round_trip_preserves_identity(req_extraction):
    restored = graph_from_dict(graph_to_dict(req_extraction))
    assert set(restored.nodes) == set(req_extraction.nodes)
    assert set(restored.assertions) == set(req_extraction.assertions)
    assert set(restored.runs) == set(req_extraction.runs)


def test_round_trip_preserves_every_review_relevant_field(req_extraction):
    """`superseded_by` and `status` are the two that matter most.

    Dropping `superseded_by` resurrects a fact a human deleted; dropping
    `status` turns a confirmed decision back into an agent guess.
    """
    from core.knowledge import ReviewLog, apply_decisions

    log = ReviewLog()
    first = next(iter(req_extraction.assertions))
    apply_decisions(req_extraction, log, first, "verify", actor="tester", note="ok")
    corrected = apply_decisions(
        req_extraction,
        log,
        first,
        "correct",
        actor="tester",
        target="Corrected Value",
        note="doc says so",
    )

    restored = graph_from_dict(json.loads(json.dumps(graph_to_dict(req_extraction))))

    old = restored.assertions[first]
    assert old.status == "SUPERSEDED"
    assert old.superseded_by == corrected.replacement_id

    new = restored.assertions[corrected.replacement_id]
    assert new.status == "CORRECTED"
    assert new.value == "Corrected Value"
    assert new.provenance.source_type == "HUMAN_REVIEWER"
    assert new.provenance.asserted_by == "tester"
    assert new.provenance.correction_note == "doc says so"
    assert new.is_human


def test_round_trip_preserves_scope_and_initiative(req_extraction):
    restored = graph_from_dict(graph_to_dict(req_extraction))
    for aid, a in req_extraction.assertions.items():
        b = restored.assertions[aid]
        assert b.scope == a.scope
        assert b.initiative_id == a.initiative_id
        assert b.confidence == a.confidence
        assert b.source_text == a.source_text
        assert b.ontology_class == a.ontology_class


def test_round_trip_preserves_node_external_refs(req_extraction):
    restored = graph_from_dict(graph_to_dict(req_extraction))
    for nid, node in req_extraction.nodes.items():
        assert restored.nodes[nid].external_refs == node.external_refs
        assert restored.nodes[nid].kind == node.kind
        assert restored.nodes[nid].label == node.label


def test_round_trip_preserves_run_completeness(req_extraction):
    restored = graph_from_dict(graph_to_dict(req_extraction))
    for rid, run in req_extraction.runs.items():
        assert restored.runs[rid].completeness == run.completeness
        assert len(restored.runs[rid].passes) == len(run.passes)


def test_newer_schema_is_refused_rather_than_misread():
    with pytest.raises(ValueError, match="newer than this build"):
        graph_from_dict({"schema_version": SCHEMA_VERSION + 1})


def test_empty_graph_round_trips():
    restored = graph_from_dict(graph_to_dict(KnowledgeGraph()))
    assert restored.nodes == {} and restored.assertions == {} and restored.runs == {}


def test_superseded_lineage_survives_merge_and_reload(req_extraction):
    """Lineage is the one field `add_assertion` does not own, so it is easy to lose."""
    from core.knowledge import merge_graphs

    first = next(iter(req_extraction.assertions))
    req_extraction.assertions[first].superseded_by = "a_deadbeef"
    merged = merge_graphs(KnowledgeGraph(), req_extraction)
    assert merged.assertions[first].superseded_by == "a_deadbeef"
    assert graph_from_dict(graph_to_dict(merged)).assertions[first].superseded_by == "a_deadbeef"


def test_the_document_type_and_side_survive_a_round_trip():
    """Both are load-bearing for cross-graph ordering, so a snapshot that drops
    them silently re-ranks every candidate after the next save."""
    from core.knowledge import graph_from_extraction, merge_graphs

    requirements = {
        "entities": [{"name": "Payment Acceptance", "ontology_class": "FunctionalRequirement"}],
        "triples": [],
    }
    req_graph, _ = graph_from_extraction(
        requirements, {"document_type": "requirements"}, document_ref="req.md"
    )
    arch_graph, _ = graph_from_extraction(
        {"elements": [{"name": "Payment Orchestrator", "element_type": "Container"}]},
        {"document_type": "architecture"},
        document_ref="arch.md",
    )
    merged = merge_graphs(req_graph, arch_graph)

    assert set(merged.declared_by.values()) == {"requirements", "architecture"}
    assert {r.document_type for r in merged.runs.values()} == {"requirements", "architecture"}

    restored = graph_from_dict(json.loads(json.dumps(graph_to_dict(merged))))
    assert restored.declared_by == merged.declared_by
    assert {r.document_type for r in restored.runs.values()} == {"requirements", "architecture"}


def test_a_revision_without_a_recorded_side_reads_as_unknown(req_extraction):
    """Older snapshots have neither field. Empty is the conservative reading:
    unknown scope must not re-rank candidates, and must not fail the load."""
    data = graph_to_dict(req_extraction)
    data.pop("declared_by", None)
    for run in data["runs"].values():
        run.pop("document_type", None)

    restored = graph_from_dict(data)
    assert restored.declared_by == {}
    assert all(r.document_type == "" for r in restored.runs.values())
