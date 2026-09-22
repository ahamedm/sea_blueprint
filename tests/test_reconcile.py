"""
Reconciliation: proposing and binding cross-graph reference targets.

The matching fixture is built so the *lexically closest* node is deliberately the
wrong kind, which is what the real ARC-G output does: unscoped best-match picks
`Concept:'Card Payment Processing'` over the correct
`BusinessCapability:'Unified Payment Processing'`.
"""

from types import SimpleNamespace

import pytest

from core.knowledge import (
    DEFAULT_MATCH_THRESHOLD,
    KnowledgeGraph,
    Provenance,
    ReconcileError,
    ReviewLog,
    bulk_resolve,
    graph_from_extraction,
    match_score,
    merge_graphs,
    normalise,
    reference_candidates,
    resolve_reference,
    review_progress,
)
from core.knowledge.model import (
    SOURCE_EXTRACTION,
    STATUS_SUPERSEDED,
    STATUS_VERIFIED,
)

# ============================================================================
# Fixture: references whose best lexical match is the wrong kind
# ============================================================================


@pytest.fixture
def refs():
    graph = KnowledgeGraph()
    gateway = graph.add_node("SoftwareSystem", "Payment Gateway Platform")
    orchestrator = graph.add_node("Container", "Payment Orchestrator")

    nodes = {
        # Right kind for the assertions below.
        "capability": graph.add_node("BusinessCapability", "Unified Payment Processing"),
        "requirement": graph.add_node("FunctionalRequirement", "Settlement Initialization"),
        # The document's own key survived — this is the authoritative signal.
        "keyed": graph.add_node("FunctionalRequirement", "Payment Acceptance", ["FR-PM-001"]),
        # Decoys of the wrong kind that score *higher* lexically.
        "concept": graph.add_node("Concept", "Card Payment Processing"),
        "domain": graph.add_node("DomainConcept", "Payment Request Validation"),
    }

    prov = Provenance(source_type=SOURCE_EXTRACTION, run_id="run_1")
    refs = {
        # resolvable: 0.92 as BusinessCapability, but the Concept decoy scores 0.94
        "capability": graph.add_assertion(
            orchestrator,
            "supports_capability",
            value="Payment Processing",
            confidence=0.6,
            provenance=prov,
            source_text="supports payment processing",
        ),
        # resolvable by the document key, not by wording
        "by_key": graph.add_assertion(
            orchestrator,
            "implements_requirement",
            value="FR-PM-001",
            confidence=0.5,
            provenance=prov,
        ),
        # a candidate exists, but weakly
        "weak": graph.add_assertion(
            gateway,
            "supports_capability",
            value="Multi-tenant Payment Processing",
            confidence=0.4,
            provenance=prov,
        ),
        # no candidate of the expected kind (BusinessGoal), strong near miss
        "mislabel": graph.add_assertion(
            orchestrator,
            "traces_to_goal",
            value="Settlement Request Initialization",
            confidence=0.7,
            provenance=prov,
        ),
        # nothing resembles it at all
        "hopeless": graph.add_assertion(
            gateway,
            "traces_to_goal",
            value="Zzzz Entirely Unrelated",
            confidence=0.3,
            provenance=prov,
        ),
    }
    return SimpleNamespace(
        graph=graph, nodes=nodes, refs=refs, gateway=gateway, orchestrator=orchestrator
    )


def _proposal(graph, assertion_id):
    return next(rc for rc in reference_candidates(graph) if rc.assertion_id == assertion_id)


# ============================================================================
# Matching
# ============================================================================


def test_normalise_strips_punctuation_and_case():
    assert normalise("PCI-DSS Compliance!") == "pci dss compliance"


def test_exact_match_scores_one():
    score, reason = match_score("Settlement Initialization", "Settlement Initialization")
    assert (score, reason) == (1.0, "exact")


def test_external_reference_scores_one_and_wins_over_wording():
    score, reason = match_score("FR-PM-001", "Payment Acceptance", ["FR-PM-001"])
    assert (score, reason) == (1.0, "external_ref")


def test_containment_scores_below_exact_but_above_tokens():
    contained, _ = match_score("Payment Processing", "Unified Payment Processing")
    exact, _ = match_score("Unified Payment Processing", "Unified Payment Processing")
    assert exact > contained > 0.0


def test_token_overlap_is_symmetric_and_bounded():
    # Deliberately not a substring pair, so this exercises the token branch.
    a, reason_a = match_score(
        "Settlement Request Initialization", "Initialization of Settlement Requests"
    )
    b, reason_b = match_score(
        "Initialization of Settlement Requests", "Settlement Request Initialization"
    )
    assert (reason_a, reason_b) == ("tokens", "tokens")
    assert a == b
    assert 0.0 < a < 0.75


def test_unrelated_text_scores_zero():
    score, reason = match_score("Zzzz Entirely Unrelated", "Unified Payment Processing")
    assert (score, reason) == (0.0, "")


# ============================================================================
# The identifier path must actually reach the graph
# ============================================================================


def test_requirement_ids_are_recorded_as_external_references():
    """`requirement_id` is the requirements profile's stable key.

    Ingest used to ignore it, reading only the architecture profile's
    `external_references`. That made the strongest reconciliation signal
    unreachable no matter how good the extractor got.
    """
    output = {
        "entities": [
            {
                "name": "Payment Acceptance",
                "ontology_class": "FunctionalRequirement",
                "requirement_id": "FR-PM-001",
            },
            {
                "name": "Latency Budget",
                "ontology_class": "NonFunctionalRequirement",
                "external_references": [{"identifier": "NFR-PE-004", "system": "JIRA"}],
            },
        ],
        "triples": [],
    }
    graph, _run = graph_from_extraction(output, document_ref="req.md")

    assert graph.nodes["functionalrequirement:payment_acceptance"].external_refs == ["FR-PM-001"]
    assert graph.nodes["nonfunctionalrequirement:latency_budget"].external_refs == ["NFR-PE-004"]


def test_a_preserved_id_joins_two_documents_end_to_end():
    """The whole reconciliation problem in miniature: the architecture names a
    requirement by its document key, and the key survived extraction."""
    requirements = {
        "entities": [
            {
                "name": "Payment Acceptance",
                "ontology_class": "FunctionalRequirement",
                "requirement_id": "FR-PM-001",
            }
        ],
        "triples": [],
    }
    architecture = {
        "elements": [{"name": "Payment Orchestrator", "element_type": "Container"}],
        "triples": [
            {
                "subject": "Payment Orchestrator",
                "predicate": "implements_requirement",
                "object": "FR-PM-001",
                "confidence": 0.6,
            }
        ],
    }
    req_graph, _ = graph_from_extraction(requirements, document_ref="req.md")
    arch_graph, _ = graph_from_extraction(architecture, document_ref="arch.md")
    merged = merge_graphs(req_graph, arch_graph)

    proposals = reference_candidates(merged)
    assert len(proposals) == 1
    assert proposals[0].best.reason == "external_ref"
    assert proposals[0].best.score == 1.0
    assert proposals[0].best.node_id == "functionalrequirement:payment_acceptance"

    log = ReviewLog()
    result = bulk_resolve(merged, log, actor="tester")
    assert len(result.resolved) == 1
    assert merged.unresolved_references() == []
    assert (
        merged.assertions[log.entries[0].replacement_id].ontology_class == "RequirementRealization"
    )


def test_scoping_picks_the_right_kind_over_the_closer_string(reference_best):
    """The decoy is lexically closer; only the kind check rejects it."""
    proposal = reference_best("capability")
    decoy = next(c for c in proposal.near_misses if c.kind == "Concept")
    assert decoy.score > proposal.best.score, "the fixture must keep the decoy ahead"

    assert proposal.best.kind == "BusinessCapability"
    assert proposal.best.label == "Unified Payment Processing"
    assert proposal.status() == "resolvable"


@pytest.fixture
def reference_best(refs):
    def lookup(name):
        return _proposal(refs.graph, refs.refs[name].id)

    return lookup


# ============================================================================
# Proposals
# ============================================================================


def test_one_proposal_per_unresolved_reference(refs):
    assert len(reference_candidates(refs.graph)) == len(refs.graph.unresolved_references())
    assert len(reference_candidates(refs.graph)) == 5


def test_document_key_resolves_exactly(reference_best):
    proposal = reference_best("by_key")
    assert proposal.best.score == 1.0
    assert proposal.best.reason == "external_ref"
    assert proposal.best.node_id == "functionalrequirement:payment_acceptance"


def test_no_expected_kind_candidate_is_reported_not_hidden(reference_best):
    proposal = reference_best("mislabel")
    assert proposal.best is None
    assert proposal.status() == "no_candidate"
    assert proposal.best_near_miss.kind == "FunctionalRequirement"


def test_a_strong_wrong_kind_match_is_flagged_as_a_suspected_mislabel(reference_best):
    """Six of the thirteen real ARC-G references look exactly like this."""
    assert reference_best("mislabel").mislabel_suspected() is True
    assert reference_best("hopeless").mislabel_suspected() is False


def test_a_reference_never_proposes_its_own_subject(refs):
    for proposal in reference_candidates(refs.graph):
        assert all(c.node_id != proposal.source_id for c in proposal.candidates)
        assert all(c.node_id != proposal.source_id for c in proposal.near_misses)


def test_proposals_are_ordered_by_best_score(refs):
    scores = [(rc.best.score if rc.best else 0.0) for rc in reference_candidates(refs.graph)]
    assert scores == sorted(scores, reverse=True)


def test_candidate_list_can_be_limited(refs):
    proposal = _proposal(refs.graph, refs.refs["capability"].id)
    limited = next(
        rc
        for rc in reference_candidates(refs.graph, limit=1)
        if rc.assertion_id == proposal.assertion_id
    )
    assert len(limited.candidates) == 1


# ============================================================================
# Resolving one reference
# ============================================================================


def test_resolve_binds_the_literal_to_a_real_node(refs):
    log = ReviewLog()
    target = refs.refs["by_key"]
    decision = resolve_reference(
        refs.graph, log, target.id, refs.nodes["keyed"], actor="tester", note="§2.1"
    )

    old = refs.graph.assertions[target.id]
    assert old.status == STATUS_SUPERSEDED
    assert old.superseded_by == decision.replacement_id
    assert not old.is_active

    resolved = refs.graph.assertions[decision.replacement_id]
    assert resolved.object == refs.nodes["keyed"]
    assert resolved.value is None
    assert resolved.predicate == "implements_requirement"
    assert resolved.status == STATUS_VERIFIED
    assert resolved.is_human
    assert "FR-PM-001" in resolved.provenance.correction_note
    assert resolved.provenance.asserted_by == "tester"


def test_resolve_records_the_transition_in_the_audit_trail(refs):
    log = ReviewLog()
    target = refs.refs["by_key"]
    decision = resolve_reference(refs.graph, log, target.id, refs.nodes["keyed"], actor="tester")

    assert decision.action == "resolve"
    assert decision.label == "Resolved"
    assert decision.before["object"] is None
    assert decision.before["target"] == "FR-PM-001"
    assert decision.after["object"] == refs.nodes["keyed"]
    assert sorted(decision.changed_fields) == ["object", "status", "target"]
    assert log.entries == [decision]


def test_resolve_marks_a_requirement_link_as_a_requirement_realization(refs):
    log = ReviewLog()
    decision = resolve_reference(
        refs.graph, log, refs.refs["by_key"].id, refs.nodes["keyed"], actor="tester"
    )
    assert refs.graph.assertions[decision.replacement_id].ontology_class == "RequirementRealization"


def test_resolve_keeps_the_source_text_as_evidence(refs):
    refs.graph.assertions[refs.refs["capability"].id].source_text = "the doc said this"
    log = ReviewLog()
    decision = resolve_reference(
        refs.graph, log, refs.refs["capability"].id, refs.nodes["capability"], actor="tester"
    )
    assert refs.graph.assertions[decision.replacement_id].source_text == "the doc said this"


def test_resolving_removes_one_unresolved_reference(refs):
    before = len(refs.graph.unresolved_references())
    log = ReviewLog()
    resolve_reference(
        refs.graph, log, refs.refs["capability"].id, refs.nodes["capability"], actor="tester"
    )
    assert len(refs.graph.unresolved_references()) == before - 1


def test_resolve_never_invents_the_target(refs):
    """Resolution asserts the referent was already extracted, merely not joined."""
    before = len(refs.graph.nodes)
    with pytest.raises(ReconcileError, match="does not exist"):
        resolve_reference(
            refs.graph,
            ReviewLog(),
            refs.refs["capability"].id,
            "softwaresystem:not_extracted",
            actor="tester",
        )
    assert len(refs.graph.nodes) == before


def test_resolve_refuses_a_wrong_kind_without_an_explicit_override(refs):
    with pytest.raises(ReconcileError, match="not an expected target"):
        resolve_reference(
            refs.graph,
            ReviewLog(),
            refs.refs["capability"].id,
            refs.nodes["concept"],
            actor="tester",
        )


def test_resolve_allows_a_deliberate_cross_kind_binding(refs):
    log = ReviewLog()
    target = refs.refs["mislabel"]
    decision = resolve_reference(
        refs.graph,
        log,
        target.id,
        refs.nodes["requirement"],
        actor="tester",
        note="the referent is a function, not a goal",
        allow_kind_override=True,
    )

    resolved = refs.graph.assertions[decision.replacement_id]
    assert resolved.object == refs.nodes["requirement"]
    assert "[kind override]" in resolved.provenance.correction_note
    assert "the referent is a function" in resolved.provenance.correction_note


def test_resolve_refuses_an_ordinary_assertion(refs):
    """`review.correct()` owns those; resolution must not become a general edit path."""
    ordinary = refs.graph.add_assertion(refs.gateway, "description", value="a gateway")
    with pytest.raises(ReconcileError, match="not a cross-graph predicate"):
        resolve_reference(refs.graph, ReviewLog(), ordinary.id, refs.nodes["capability"])


def test_resolve_refuses_an_already_resolved_reference(refs):
    log = ReviewLog()
    resolve_reference(refs.graph, log, refs.refs["by_key"].id, refs.nodes["keyed"], actor="tester")
    with pytest.raises(ReconcileError, match="is SUPERSEDED"):
        resolve_reference(refs.graph, log, refs.refs["by_key"].id, refs.nodes["keyed"])


def test_resolve_refuses_unknown_and_non_string_ids(refs):
    with pytest.raises(ReconcileError, match="unknown assertion"):
        resolve_reference(refs.graph, ReviewLog(), "a_nope", refs.nodes["keyed"])
    with pytest.raises(ReconcileError, match="expected an assertion id string"):
        resolve_reference(refs.graph, ReviewLog(), refs.refs["by_key"], refs.nodes["keyed"])


def test_resolved_links_do_not_reopen_the_review_gate(refs):
    """A human-established link arrives VERIFIED, so reconciliation cannot leave
    the graph with more unreviewed knowledge than it found."""
    before = review_progress(refs.graph)
    log = ReviewLog()
    for name in ("capability", "by_key"):
        target_node = refs.nodes["capability"] if name == "capability" else refs.nodes["keyed"]
        resolve_reference(refs.graph, log, refs.refs[name].id, target_node, actor="tester")

    after = review_progress(refs.graph)
    assert after.verified == before.verified + 2
    assert after.unverified == before.unverified - 2
    assert after.superseded == before.superseded + 2


# ============================================================================
# Bulk resolve
# ============================================================================


def test_bulk_binds_only_what_clears_the_threshold(refs):
    log = ReviewLog()
    result = bulk_resolve(refs.graph, log, actor="tester")

    assert len(result.resolved) == 2  # capability + by_key
    assert len(result.below_threshold) == 1  # weak
    assert len(result.no_candidate) == 2  # mislabel + hopeless
    assert result.considered == 5
    assert all(score < DEFAULT_MATCH_THRESHOLD for _id, score in result.below_threshold)
    assert all(d.action == "resolve" for d in log.entries)


def test_bulk_never_binds_across_kinds_even_at_zero_threshold(refs):
    """The strongest match for `mislabel` is a FunctionalRequirement; bulk must
    not take it, because `traces_to_goal` may not point at a function."""
    log = ReviewLog()
    result = bulk_resolve(refs.graph, log, min_score=0.0, actor="tester")

    assert refs.refs["mislabel"].id in result.no_candidate
    assert refs.refs["mislabel"].id not in result.resolved
    assert refs.graph.assertions[refs.refs["mislabel"].id].is_active


def test_bulk_reports_a_stale_selection_instead_of_silently_skipping(refs):
    log = ReviewLog()
    resolve_reference(
        refs.graph, log, refs.refs["capability"].id, refs.nodes["capability"], actor="tester"
    )

    result = bulk_resolve(
        refs.graph, log, assertion_ids=[refs.refs["capability"].id], actor="tester"
    )
    assert result.resolved == []
    assert result.unknown_ids == [refs.refs["capability"].id]


def test_bulk_with_explicit_ids_touches_only_those(refs):
    log = ReviewLog()
    result = bulk_resolve(refs.graph, log, assertion_ids=[refs.refs["by_key"].id], actor="tester")
    assert result.resolved == [refs.refs["by_key"].id]
    assert refs.graph.assertions[refs.refs["capability"].id].is_active


def test_bulk_with_an_empty_selection_does_nothing(refs):
    log = ReviewLog()
    result = bulk_resolve(refs.graph, log, assertion_ids=[], actor="tester")
    assert result.to_dict() == {
        "resolved": 0,
        "below_threshold": 0,
        "no_candidate": 0,
        "unknown_ids": 0,
        "considered": 0,
        "threshold": DEFAULT_MATCH_THRESHOLD,
    }
    assert log.entries == []


def test_bulk_is_idempotent(refs):
    log = ReviewLog()
    first = bulk_resolve(refs.graph, log, actor="tester")
    second = bulk_resolve(refs.graph, log, actor="tester")

    assert len(first.resolved) == 2
    assert second.resolved == []
    assert second.considered == 3
    assert len(log.entries) == 2, "a second pass must not re-record decisions"


def test_bulk_applies_the_audit_note(refs):
    log = ReviewLog()
    bulk_resolve(refs.graph, log, actor="tester", note="sprint 12 reconciliation")
    assert all(d.note == "sprint 12 reconciliation" for d in log.entries)


# ============================================================================
# Projection
# ============================================================================


def test_projection_summarises_what_it_declines_as_well_as_what_it_binds(refs):
    from app.projections import project_reconciliation

    view = project_reconciliation(refs.graph)
    summary = view["summary"]

    assert summary["total"] == 5
    assert summary["resolvable"] == 2
    assert summary["below_threshold"] == 1
    assert summary["no_candidate"] == 2
    assert summary["mislabel_suspected"] == 1
    assert summary["by_predicate"]["supports_capability"] == 2
    assert summary["by_predicate"]["traces_to_goal"] == 2
    assert summary["by_predicate"]["implements_requirement"] == 1
    assert len(view["rows"]) == 5


def test_projection_still_offers_near_misses_when_the_kind_has_no_candidate(refs):
    """Otherwise the override path would be unreachable from the UI."""
    from app.projections import project_reconciliation

    view = project_reconciliation(refs.graph)
    row = next(r for r in view["rows"] if r["assertion_id"] == refs.refs["mislabel"].id)

    assert row["status"] == "no_candidate"
    assert row["mislabel_suspected"] is True
    assert row["options"], "near misses must be offered as options"
    assert any(not option["in_expected_kind"] for option in row["options"])


def test_projection_threshold_moves_the_resolvable_set(refs):
    from app.projections import project_reconciliation

    strict = project_reconciliation(refs.graph, threshold=1.0)["summary"]
    loose = project_reconciliation(refs.graph, threshold=0.0)["summary"]

    assert strict["resolvable"] == 1  # only the document-key match scores 1.0
    assert loose["resolvable"] == 3  # every expected-kind candidate
    assert strict["below_threshold"] > loose["below_threshold"]


def test_projection_after_a_bulk_pass_shows_only_what_remains(refs):
    from app.projections import project_reconciliation

    bulk_resolve(refs.graph, ReviewLog(), min_score=0.0, actor="tester")
    view = project_reconciliation(refs.graph)

    remaining = {r["assertion_id"] for r in view["rows"]}
    assert remaining == {refs.refs["mislabel"].id, refs.refs["hopeless"].id}
    assert view["summary"]["resolvable"] == 0
    assert view["summary"]["no_candidate"] == 2


def test_projection_is_empty_once_every_reference_is_bound(refs):
    from app.projections import project_reconciliation

    log = ReviewLog()
    for name, node in (
        ("capability", "capability"),
        ("by_key", "keyed"),
        ("weak", "capability"),
        ("mislabel", "requirement"),
        ("hopeless", "domain"),
    ):
        resolve_reference(
            refs.graph,
            log,
            refs.refs[name].id,
            refs.nodes[node],
            actor="tester",
            allow_kind_override=True,
        )

    view = project_reconciliation(refs.graph)
    assert view["rows"] == []
    assert view["summary"]["total"] == 0
    assert view["summary"]["average_best_score"] == 0.0
