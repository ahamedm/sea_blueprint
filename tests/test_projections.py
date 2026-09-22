"""
Graph projection: pure functions of (graph, log, filters). No Flask, no browser.

The C4 *viewpoint* has its own test module (`test_viewpoint_c4.py`). The two are
deliberately apart because they are different layers; one test module would
re-suggest the fusion this split removed.
"""

from pathlib import Path

import app.projections as projections
from agents.knowledge import ReviewLog, apply_decisions
from agents.knowledge.model import compute_graph_delta
from app.projections import (
    LOW_CONFIDENCE,
    ReviewFilters,
    confidence_band,
    project_assertion,
    project_dashboard,
    project_decisions,
    project_delta,
    project_gap_report,
    project_node_index,
    project_review_rows,
    project_review_summary,
)


def test_confidence_bands_partition_the_range():
    assert confidence_band(1.0) == "high"
    assert confidence_band(0.85) == "high"
    assert confidence_band(0.84) == "medium"
    assert confidence_band(LOW_CONFIDENCE) == "medium"
    assert confidence_band(LOW_CONFIDENCE - 0.01) == "low"
    assert confidence_band(0.0) == "low"


def test_filters_default_to_the_review_queue_order(req_extraction):
    rows = project_review_rows(req_extraction, ReviewLog())
    confidences = [r["confidence"] for r in rows]
    assert confidences == sorted(confidences), "lowest confidence must surface first"
    assert len(rows) == len(list(req_extraction.active()))


def test_filters_parse_from_request_like_args():
    f = ReviewFilters.from_args({"sort": "subject", "min_confidence": "0.5", "q": "pay"})
    assert f.sort == "subject"
    assert f.min_confidence == 0.5
    assert f.q == "pay"
    assert not f.is_default


def test_filters_tolerate_garbage_numbers():
    f = ReviewFilters.from_args({"min_confidence": "abc"})
    assert f.min_confidence is None


def test_query_string_round_trips_filters():
    f = ReviewFilters(only="pending", sort="subject")
    query = f.to_query()
    assert "only=pending" in query and "sort=subject" in query
    assert "status=" not in query, "empty filters should be omitted"


def test_filter_by_status(req_extraction):
    log = ReviewLog()
    target = next(iter(req_extraction.active()))
    apply_decisions(req_extraction, log, target.id, "verify", actor="tester")

    verified = project_review_rows(req_extraction, log, ReviewFilters(status="VERIFIED"))
    assert [r["id"] for r in verified] == [target.id]


def test_filter_pending_excludes_settled_assertions(req_extraction):
    log = ReviewLog()
    target = next(iter(req_extraction.active()))
    apply_decisions(req_extraction, log, target.id, "verify", actor="tester")

    pending = project_review_rows(req_extraction, log, ReviewFilters(only="pending"))
    assert target.id not in {r["id"] for r in pending}
    assert pending, "the rest of the fixture is still unverified"


def test_filter_low_confidence_and_band(req_extraction):
    rows = project_review_rows(req_extraction, ReviewLog(), ReviewFilters(only="low_confidence"))
    assert rows
    assert all(r["confidence"] < LOW_CONFIDENCE for r in rows)


def test_filter_by_confidence_window(req_extraction):
    rows = project_review_rows(
        req_extraction, ReviewLog(), ReviewFilters(min_confidence=0.6, max_confidence=0.8)
    )
    assert rows
    assert all(0.6 <= r["confidence"] <= 0.8 for r in rows)


def test_filter_unresolved_references(req_extraction):
    rows = project_review_rows(req_extraction, ReviewLog(), ReviewFilters(only="unresolved"))
    assert rows
    assert all(r["target_kind"] == "reference" for r in rows)


def test_filter_by_subject_kind(req_extraction):
    rows = project_review_rows(
        req_extraction, ReviewLog(), ReviewFilters(kind="FunctionalRequirement")
    )
    assert rows
    assert all(r["subject_kind"] == "FunctionalRequirement" for r in rows)


def test_text_search_spans_source_text(req_extraction):
    rows = project_review_rows(req_extraction, ReviewLog(), ReviewFilters(q="RBAC"))
    assert [r["predicate"] for r in rows] == ["enforces"]


def test_search_with_no_match_returns_nothing(req_extraction):
    assert (
        project_review_rows(req_extraction, ReviewLog(), ReviewFilters(q="zzzz-no-such-term")) == []
    )


def test_superseded_rows_are_hidden_unless_requested(req_extraction):
    log = ReviewLog()
    target = next(iter(req_extraction.active()))
    apply_decisions(req_extraction, log, target.id, "correct", actor="tester", target="New")

    default = project_review_rows(req_extraction, log)
    assert target.id not in {r["id"] for r in default}

    everything = project_review_rows(req_extraction, log, include_superseded=True)
    assert target.id in {r["id"] for r in everything}
    assert any(r["status"] == "SUPERSEDED" for r in everything)


def test_assertion_detail_carries_provenance_and_history(req_extraction):
    log = ReviewLog()
    target = next(iter(req_extraction.active()))
    apply_decisions(req_extraction, log, target.id, "verify", actor="tester", note="ok")

    detail = project_assertion(req_extraction, log, target.id)
    assert detail["status"] == "VERIFIED"
    assert detail["is_human"] is True
    assert detail["decision_count"] == 1
    assert detail["decisions"][0]["label"] == "Verified"
    assert detail["decisions"][0]["note"] == "ok"
    assert detail["confidence_pct"] == 100


def test_assertion_detail_for_unknown_id_is_empty(req_extraction):
    assert project_assertion(req_extraction, ReviewLog(), "a_nope") == {}


def test_summary_counts_and_gate(req_extraction):
    log = ReviewLog()
    summary = project_review_summary(req_extraction, log)
    assert summary["progress"]["unverified"] == summary["progress"]["total"]
    assert summary["progress"]["is_auditable"] is False
    assert summary["needs_review"] == summary["progress"]["total"]
    assert summary["unresolved"] == len(req_extraction.unresolved_references())
    assert summary["by_band"]["high"] >= 1


def test_node_index_counts_facts_and_sorts_by_them(arch_extraction):
    rows = project_node_index(arch_extraction)
    assert rows[0]["facts"] >= rows[-1]["facts"]
    assert all({"id", "kind", "label", "facts"} <= set(r) for r in rows)


def test_gap_report_flags_unknown_completeness_and_closed_gate(req_extraction):
    report = project_gap_report(req_extraction)
    assert report["completeness"] == "UNKNOWN", "no pass records means UNKNOWN, not COMPLETE"
    assert report["is_auditable"] is False
    assert "NOT evidence" in report["auditability"]
    assert report["unresolved_count"] == len(req_extraction.unresolved_references())
    assert report["references"]
    assert report["progress"]["unverified"] > 0


def test_gap_report_lists_elements_without_facts(req_extraction):
    report = project_gap_report(req_extraction)
    assert isinstance(report["elements_without_facts"], list)


def test_gap_report_groups_unresolved_by_predicate(req_extraction):
    report = project_gap_report(req_extraction)
    assert sum(report["unresolved_by_predicate"].values()) == report["unresolved_count"]


def test_delta_projection_resolves_labels_and_totals(arch_extraction, req_extraction):
    delta = compute_graph_delta(req_extraction, arch_extraction)
    view = project_delta(delta, req_extraction, arch_extraction)

    assert view["is_empty"] is False
    assert view["totals"]["facts_added"] == len(delta.added_assertions)
    assert view["added_nodes"]
    assert all(n["label"] for n in view["added_nodes"])
    assert all(a["subject"] for a in view["added_assertions"])


def test_delta_projection_of_identical_graphs_is_empty(req_extraction):
    delta = compute_graph_delta(req_extraction, req_extraction)
    view = project_delta(delta, req_extraction, req_extraction)
    assert view["is_empty"] is True
    assert view["totals"] == {
        "nodes_added": 0,
        "nodes_removed": 0,
        "facts_added": 0,
        "facts_removed": 0,
        "facts_changed": 0,
    }


def test_delta_projection_reports_changed_fields(req_extraction):
    log = ReviewLog()
    target = next(iter(req_extraction.active()))
    apply_decisions(req_extraction, log, target.id, "verify", actor="tester")

    import copy

    before = copy.deepcopy(req_extraction)
    after = copy.deepcopy(req_extraction)
    after.assertions[target.id].confidence = 0.42

    view = project_delta(compute_graph_delta(before, after), before, after)
    assert view["changed_assertions"]
    assert "confidence" in view["changed_assertions"][0]["fields"]


def test_decision_projection_is_newest_first(req_extraction):
    log = ReviewLog()
    ids = [a.id for a in list(req_extraction.active())]
    apply_decisions(req_extraction, log, ids[0], "verify", actor="first")
    apply_decisions(req_extraction, log, ids[1], "dispute", actor="second")

    rows = project_decisions(req_extraction, log)
    assert rows[0]["action"] == "dispute"
    assert rows[1]["action"] == "verify"
    assert rows[0]["actor"] == "second"


def test_dashboard_reports_empty_state():
    from agents.knowledge import KnowledgeGraph

    view = project_dashboard(KnowledgeGraph(), ReviewLog())
    assert view["empty"] is True
    assert view["revisions"] == 0


def test_dashboard_summarises_a_loaded_graph(req_extraction):
    log = ReviewLog()
    view = project_dashboard(
        req_extraction, log, revisions=[], working_meta={"initiative_id": "INIT-1"}
    )
    assert view["empty"] is False
    assert view["stats"]["nodes"] == len(req_extraction.nodes)
    assert view["documents"] == ["req.md"]
    assert view["top_predicates"]
    assert view["low_confidence"] >= 1
    assert view["working_meta"]["initiative_id"] == "INIT-1"


# ============================================================================
# Layer boundary
# ============================================================================


def test_projection_layer_does_not_own_architecture_notation():
    """The split is only real while the modules stay on their sides.

    If C4 levels reappear here, the two concepts have fused again and the next
    notation will be added by widening this module instead of adding a viewpoint.
    """
    source = Path(projections.__file__).read_text()
    # The docstring may *name* the viewpoint to explain the split; what must not
    # appear is notation defined here, or a dependency pointing the wrong way.
    assert "C4_LEVELS =" not in source
    assert "from app.viewpoints" not in source
    assert not hasattr(projections, "c4_view")


# ============================================================================
# Primitives every viewpoint composes
# ============================================================================


def test_node_records_can_be_restricted_to_kinds(arch_extraction):
    everything = projections.node_records(arch_extraction)
    only_systems = projections.node_records(arch_extraction, kinds={"SoftwareSystem"})
    assert len(only_systems) < len(everything)
    assert {r["kind"] for r in only_systems} == {"SoftwareSystem"}
    assert {"id", "label", "kind", "external_refs"} <= set(only_systems[0])


def test_edge_records_only_include_selected_nodes(arch_extraction):
    systems = {n.id for n in arch_extraction.nodes.values() if n.kind == "SoftwareSystem"}
    assert (
        projections.edge_records(arch_extraction, systems) == []
    ), "a lone system has nothing to connect to"

    everything = {n.id for n in arch_extraction.nodes.values()}
    assert projections.edge_records(arch_extraction, everything)


def test_edge_records_deduplicate_parallel_assertions(arch_extraction):
    """Two runs asserting the same link is one relationship, not two."""
    ids = {n.id for n in arch_extraction.nodes.values()}
    first = next(iter(arch_extraction.nodes))
    other = next(n.id for n in arch_extraction.nodes.values() if n.id != first)

    arch_extraction.add_assertion(first, "connects_to", obj=other, confidence=0.4)
    arch_extraction.add_assertion(first, "connects_to", obj=other, confidence=0.9)

    edges = [
        e for e in projections.edge_records(arch_extraction, ids) if e["predicate"] == "connects_to"
    ]
    assert len(edges) == 1
    assert edges[0]["id"] == f"{first}->{other}:connects_to"


def test_literal_facts_are_separated_from_edges(arch_extraction):
    """Descriptions are properties of an element, not relationships."""
    ids = {n.id for n in arch_extraction.nodes.values()}
    facts = projections.literal_facts(arch_extraction, ids)

    assert facts, "the fixture asserts descriptions"
    assert not any(
        e["predicate"] == "description" for e in projections.edge_records(arch_extraction, ids)
    )
