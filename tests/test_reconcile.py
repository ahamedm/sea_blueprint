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
    SCOPE_DOCUMENT,
    SCOPE_ENTERPRISE,
    ExternalReference,
    SOURCE_EXTRACTION,
    STATUS_SUPERSEDED,
    STATUS_VERIFIED,
    document_reference,
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
        # An identifier held in a system of record: the one scope that may be
        # matched on globally. Typed deliberately — a bare string would be read
        # as a document-local label and correctly refused as a join key.
        "keyed": graph.add_node(
            "FunctionalRequirement", "Payment Acceptance",
            external_references=[
                ExternalReference(
                    identifier="FR-PM-001", system="Jira", reference_type="REQUIREMENT_KEY",
                    scope=SCOPE_ENTERPRISE, is_authoritative=True,
                )
            ],
        ),
        # Carries a DOCUMENT-scoped key, which is the case that has to be
        # PROPOSED rather than recognised: the key is a real label, and no
        # citing document may bind it without review.
        "pe_requirement": graph.add_node(
            "NonFunctionalRequirement", "Performance Envelope",
            external_references=[document_reference("NFR-PE-004", "prd.md")],
        ),
        # The same identifier as `keyed`, but stated only in a source document.
        # Real as a label, useless as a join key: another document may number its
        # own requirement FR-PM-001 and mean something else entirely.
        "document_local": graph.add_node(
            "FunctionalRequirement", "Payment Acceptance (document-local)",
            external_references=[
                ExternalReference(
                    identifier="FR-PM-001", system="brief.md",
                    reference_type="REQUIREMENT_KEY", scope=SCOPE_DOCUMENT,
                )
            ],
        ),
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
            value="NFR-PE-004",
            confidence=0.5,
            provenance=prov,
        ),
        # A citation of a key whose scope DOES carry match authority: recognised
        # outright, so it is never offered as a proposal. The counterpart of
        # `by_key`, and the reason the two cases must both exist in one fixture.
        "citation": graph.add_assertion(
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


def test_an_enterprise_reference_scores_one_and_wins_over_wording():
    """An identifier a system of record holds is the strongest join available."""
    score, reason = match_score(
        "FR-PM-001", "Payment Acceptance",
        [ExternalReference(identifier="FR-PM-001", system="Jira",
                           reference_type="REQUIREMENT_KEY", scope=SCOPE_ENTERPRISE)],
    )
    assert (score, reason) == (1.0, "external_ref")


def test_a_document_local_reference_cannot_win_on_identity_alone():
    """The distinction that makes scoping worth modelling.

    Two documents may both number a requirement `FR-PM-001` and mean different
    things. Treating the label as a definitive match produces a confident wrong
    join, which is worse than a missing one — it makes the audit wrong rather
    than incomplete. So a document-scoped identifier is evidence, reported as
    such, but it has to be beaten or confirmed, not trusted.
    """
    score, reason = match_score(
        "FR-PM-001", "Payment Acceptance",
        [ExternalReference(identifier="FR-PM-001", system="brief.md",
                           reference_type="REQUIREMENT_KEY", scope=SCOPE_DOCUMENT)],
    )
    assert reason == "unscoped_ref"
    assert score < 1.0

    # And an untyped string is read the same way, never as an enterprise key.
    string_score, string_reason = match_score("FR-PM-001", "Payment Acceptance", ["FR-PM-001"])
    assert (string_score, string_reason) == (score, reason)


def test_an_enterprise_reference_outranks_a_document_label():
    """Two nodes carrying the same identifier: only one may be matched on."""
    refs = [
        ExternalReference(identifier="FR-PM-001", system="brief.md", scope=SCOPE_DOCUMENT),
        ExternalReference(identifier="FR-PM-001", system="Jira", scope=SCOPE_ENTERPRISE),
    ]
    assert match_score("FR-PM-001", "Something Else", refs) == (1.0, "external_ref")


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


def test_a_document_local_id_does_NOT_join_a_node_of_another_kind():
    """The residual risk the citation rule leaves, stated as a test.

    An `implements_*` reference now recognises a requirement document's key
    wherever it finds it. But the predicate's range is a REQUIREMENT: a
    document-local identifier carried by a capability or a container is not one,
    so the reference stays open rather than binding to whatever happens to share
    the string. And a requirement document is the only side whose numbering
    `implements_requirement` claims to quote — two documents that each number
    their own requirements are the ambiguity a human adjudicates.
    """
    architecture_a = {
        "elements": [{"name": "Payment Orchestrator", "element_type": "Container"}],
        "triples": [
            {"subject": "Payment Orchestrator", "predicate": "implements_requirement",
             "object": "CAP-001", "confidence": 0.6},
        ],
    }
    architecture_b = {
        "entities": [
            {"name": "Unified Payment Processing", "ontology_class": "BusinessCapability",
             "requirement_id": "CAP-001"},
        ],
        "triples": [],
    }
    a_graph, _ = graph_from_extraction(architecture_a, document_ref="arch_a.md")
    b_graph, _ = graph_from_extraction(architecture_b, document_ref="arch_b.md")
    merged = merge_graphs(a_graph, b_graph)

    proposals = reference_candidates(merged)
    assert len(proposals) == 1
    proposal = proposals[0]
    # Reported, not hidden. Nothing is bound: the capability is not a requirement,
    # and `CAP-001` carries no lexical resemblance either, so there is no
    # candidate of the expected kind to accept — only a near miss a reviewer
    # would have to override deliberately.
    assert proposal.best is None
    assert proposal.best_near_miss is not None
    assert proposal.best_near_miss.kind == "BusinessCapability"
    assert proposal.status() == "no_candidate"

    log = ReviewLog()
    result = bulk_resolve(merged, log, actor="tester")
    assert result.resolved == []
    assert len(merged.unresolved_references()) == 1


def test_a_requirement_key_joins_across_documents_by_citation():
    """The citation case, and the one the item was opened for.

    An architecture document citing `FR-PM-001` is quoting the requirements
    document's own key — that IS the claim `implements_requirement` makes — so it
    is a link without a human having to reconcile two documents' numbering
    schemes. Without this rule a verbatim-preserved requirement id still could
    not join across documents, which is the defect in full.
    """
    requirements = {
        "entities": [
            {"name": "Payment Acceptance", "ontology_class": "FunctionalRequirement",
             "requirement_id": "FR-PM-001"},
        ],
        "triples": [],
    }
    architecture = {
        "elements": [{"name": "Payment Orchestrator", "element_type": "Container"}],
        "triples": [
            {"subject": "Payment Orchestrator", "predicate": "implements_requirement",
             "object": "FR-PM-001", "confidence": 0.6},
        ],
    }
    req_graph, _ = graph_from_extraction(
        requirements, {"document_type": "requirements"}, document_ref="prd.md"
    )
    arch_graph, _ = graph_from_extraction(
        architecture, {"document_type": "architecture"}, document_ref="arch.md"
    )
    merged = merge_graphs(req_graph, arch_graph)

    # Nothing left to reconcile: the citation already names the node.
    assert reference_candidates(merged) == []
    assert merged.unresolved_references() == []

    from core.knowledge import realization_edges

    edges = realization_edges(merged)
    assert len(edges) == 1
    assertion, node = edges[0]
    assert node.id == "functionalrequirement:payment_acceptance"
    assert assertion.predicate == "implements_requirement"
    assert assertion.ontology_class == "RequirementRealization"


def test_a_document_local_requirement_key_joins_within_its_own_document():
    """One document states both the requirement and its identifier, so the label
    genuinely identifies that requirement. Scoping must not throw this away — it
    is the strongest signal available when the source is consistent with itself.

    The named `requirement_id` is the requirements profile's own key, so it binds
    outright rather than being offered as a sub-threshold suggestion.
    """
    single = {
        "entities": [
            {
                "name": "Payment Acceptance",
                "ontology_class": "FunctionalRequirement",
                "requirement_id": "FR-PM-001",
            }
        ],
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
    graph, _ = graph_from_extraction(single, document_ref="both.md")

    # Already a link: nothing left to reconcile, and it points at the node
    # carrying the key.
    assert reference_candidates(graph) == []
    assert graph.unresolved_references() == []

    from core.knowledge import realization_edges

    edges = realization_edges(graph)
    assert len(edges) == 1
    assertion, node = edges[0]
    assert node.id == "functionalrequirement:payment_acceptance"
    assert assertion.ontology_class == "RequirementRealization"

    # A document label that is NOT a requirements key still cannot bind itself:
    # the same words in another document are a different claim.
    untyped = {
        "entities": [
            {
                "name": "Latency Budget",
                "ontology_class": "NonFunctionalRequirement",
                "external_references": [{"identifier": "NFR-PE-004", "system": "prd.md"}],
            }
        ],
        "elements": [{"name": "Payment Orchestrator", "element_type": "Container"}],
        "triples": [
            {"subject": "Payment Orchestrator", "predicate": "implements_requirement",
             "object": "NFR-PE-004", "confidence": 0.6},
        ],
    }
    loose, _ = graph_from_extraction(untyped, document_ref="both.md")
    assert reference_candidates(loose), "an untyped label must still be proposed"


def test_an_enterprise_reference_joins_across_documents():
    """A key held in a system of record IS safe to match globally — that is the
    distinction, and this is the case it must not break."""
    requirements = {
        "entities": [
            {
                "name": "Payment Acceptance",
                "ontology_class": "FunctionalRequirement",
                "external_references": [
                    {
                        "identifier": "PAY-142",
                        "system": "Jira",
                        "reference_type": "REQUIREMENT_KEY",
                    }
                ],
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
                "object": "PAY-142",
                "confidence": 0.6,
            }
        ],
    }
    req_graph, _ = graph_from_extraction(requirements, document_ref="req.md")
    arch_graph, _ = graph_from_extraction(architecture, document_ref="arch.md")
    merged = merge_graphs(req_graph, arch_graph)

    # An enterprise key is recognised outright — that is the whole reason the
    # typed reference exists — so there is nothing to propose and nothing to
    # review before the link is usable.
    assert reference_candidates(merged) == []
    assert merged.unresolved_references() == []

    from core.knowledge import realization_edges

    edges = realization_edges(merged)
    assert [node.id for _a, node in edges] == ["functionalrequirement:payment_acceptance"]


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
    """One row per open reference, and the cited enterprise key is not among them.

    `citation` carries `FR-PM-001`, which the Jira-keyed node holds — so it is
    already a link and offering it for binding would be busywork. The
    document-local node carrying the same string does not change that: the
    enterprise key is the stronger claim, and the ambiguity is settled by
    preference rather than by listing both.
    """
    assert len(reference_candidates(refs.graph)) == len(refs.graph.unresolved_references())
    assert len(reference_candidates(refs.graph)) == 5
    proposal_ids = {rc.assertion_id for rc in reference_candidates(refs.graph)}
    assert refs.refs["citation"].id not in proposal_ids
    assert refs.refs["by_key"].id in proposal_ids


def test_document_key_resolves_exactly(reference_best):
    proposal = reference_best("by_key")
    assert proposal.best.score == 1.0
    assert proposal.best.reason == "citing_document_key"
    assert proposal.best.node_id == "nonfunctionalrequirement:performance_envelope"


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
    assert "NFR-PE-004" in resolved.provenance.correction_note
    assert resolved.provenance.asserted_by == "tester"
    # A key that IS the target node's own identifier is identity, so the link
    # keeps full confidence — the manual path must not record a weaker reason
    # than a proposal would have carried (`citing_document_key`).
    assert resolved.confidence == 1.0


def test_resolve_records_the_transition_in_the_audit_trail(refs):
    log = ReviewLog()
    target = refs.refs["by_key"]
    decision = resolve_reference(refs.graph, log, target.id, refs.nodes["keyed"], actor="tester")

    assert decision.action == "resolve"
    assert decision.label == "Resolved"
    assert decision.before["object"] is None
    assert decision.before["target"] == "NFR-PE-004"
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
    # Every binding reports the assertion it produced, so a caller can see what
    # the decision actually became rather than re-deriving it.
    assert len(result.bound) == 2
    assert {a_id for a_id, _replacement in result.bound} == set(result.resolved)


# ============================================================================
# Ordering signals: the same Initiative, then the other graph
# ============================================================================


def _two_capability_graph():
    """Two same-kind candidates for one reference, told apart only by Initiative.

    Both are the right kind and both clear the threshold, so kind scoping cannot
    separate them — which is precisely the situation YB-005's Initiative anchor
    exists for.
    """
    graph = KnowledgeGraph()
    element = graph.add_node("Container", "Payment Orchestrator")
    initiative = graph.add_node("Initiative", "Payment Modernisation")
    home = graph.add_node("BusinessCapability", "Payment Processing Platform")
    away = graph.add_node("BusinessCapability", "Unified Payment Processing")
    graph.add_assertion(element, "delivers_initiative", obj=initiative, confidence=1.0)
    graph.add_assertion(home, "authorised_by_initiative", obj=initiative, confidence=1.0)
    return graph, element, home, away


def test_a_same_initiative_candidate_outranks_a_higher_scoring_one():
    """YB-005's anchor, made concrete.

    `Unified Payment Processing` scores marginally higher against `Payment
    Processing` than `Payment Processing Platform` does, and both clear the
    threshold — so lexical scoring alone picks the element in another Initiative's
    scope. Ordering by Initiative puts the one this element actually delivers
    first. Scores are untouched: this is a preference among *acceptable*
    candidates, not a licence to bind a bad match.
    """
    graph, element, home, away = _two_capability_graph()
    prov = Provenance(source_type=SOURCE_EXTRACTION, run_id="run_1")
    graph.add_assertion(
        element, "supports_capability", value="Payment Processing", confidence=0.5, provenance=prov,
    )

    proposal = reference_candidates(graph)[0]
    ranked = {c.node_id: c for c in proposal.candidates}
    assert proposal.source_initiative == "Payment Modernisation"
    assert ranked[away].score > ranked[home].score, "the fixture must keep distance ahead"
    assert proposal.best.node_id == home
    assert proposal.best.same_initiative is True
    # Both remain bindable: the ranking moved the order, not the scores.
    assert min(proposal.best.score, ranked[away].score) >= DEFAULT_MATCH_THRESHOLD


def test_an_initiative_preference_never_promotes_a_weak_match(refs):
    """The guard on the ordering signal.

    If the candidate this element delivers scores below the threshold while a
    different one clears it, the acceptable candidate still wins. Preferring the
    Initiative here would trade a defensible link for an indefensible one, and a
    wrong traceability link is worse than a missing one.
    """
    graph = KnowledgeGraph()
    element = graph.add_node("Container", "Payment Orchestrator")
    initiative = graph.add_node("Initiative", "Payment Modernisation")
    weak_home = graph.add_node("BusinessCapability", "Payment Handling Support")
    strong_away = graph.add_node("BusinessCapability", "Payment Processing Service")
    graph.add_assertion(element, "delivers_initiative", obj=initiative, confidence=1.0)
    graph.add_assertion(weak_home, "authorised_by_initiative", obj=initiative, confidence=1.0)
    prov = Provenance(source_type=SOURCE_EXTRACTION, run_id="run_1")
    graph.add_assertion(
        element, "supports_capability", value="Payment Processing", confidence=0.5, provenance=prov
    )

    proposal = reference_candidates(graph)[0]
    ranked = {c.node_id: c for c in proposal.candidates}
    assert ranked[weak_home].same_initiative is True
    assert ranked[home_ok := strong_away].score >= DEFAULT_MATCH_THRESHOLD
    assert proposal.best.node_id == home_ok
    assert proposal.status() == "resolvable"


def test_the_other_graph_is_ranked_first_when_both_name_it():
    """A cross-graph predicate CLAIMS the referent lives in the other graph.

    Two nodes are called `Payment Processing`, one read out of each document. The
    predicate's own meaning says which one it means, and the document type each
    node came from is what makes that knowable.
    """
    from core.knowledge import merge_graphs

    # Each document declares its OWN capability, and the two share no wording
    # with each other. The one the architecture document declares scores higher
    # lexically, which is exactly the trap: a cross-graph predicate says the
    # referent is the OTHER document's capability.
    req_output = {"entities": [
        {"name": "Unified Payment Processing", "ontology_class": "BusinessCapability"},
    ], "triples": []}
    arch_output = {
        "elements": [
            {"name": "Payment Orchestrator", "element_type": "Container"},
            {"name": "Card Payment Processing", "element_type": "BusinessCapability"},
        ],
        "triples": [
            {"subject": "Payment Orchestrator", "predicate": "supports_capability",
             "object": "Payment Processing", "confidence": 0.6},
        ],
    }
    req_graph, _ = graph_from_extraction(
        req_output, {"document_type": "requirements"}, document_ref="req.md"
    )
    arch_graph, _ = graph_from_extraction(
        arch_output, {"document_type": "architecture"}, document_ref="arch.md"
    )
    merged = merge_graphs(req_graph, arch_graph)

    proposal = next(
        rc for rc in reference_candidates(merged) if rc.predicate == "supports_capability"
    )
    assert proposal.source_side == "architecture"
    assert len(proposal.candidates) == 2
    home = next(c for c in proposal.candidates if not c.other_side)
    away = next(c for c in proposal.candidates if c.other_side)
    assert home.score > away.score, "the fixture must keep the same-document match ahead"
    assert away.node_id == "businesscapability:unified_payment_processing"
    assert proposal.best.node_id == away.node_id


def test_ranking_is_inert_when_no_initiative_or_document_type_is_known(refs):
    """Unknown scope must not reorder anything: guessing a side or an Initiative
    would re-rank candidates on evidence that is not there."""
    for proposal in reference_candidates(refs.graph):
        assert proposal.source_initiative == ""
        assert proposal.source_side == ""
        assert all(c.same_initiative is False for c in proposal.candidates)
        assert all(c.other_side is False for c in proposal.candidates)


# ============================================================================
# Confidence is a band, not a constant
# ============================================================================


def test_an_exact_proposal_keeps_full_confidence(refs):
    log = ReviewLog()
    by_key = _proposal(refs.graph, refs.refs["by_key"].id)
    decision = resolve_reference(
        refs.graph, log, by_key.assertion_id, by_key.best.node_id,
        actor="tester", proposed_score=by_key.best.score, match_reason=by_key.best.reason,
    )
    resolved = refs.graph.assertions[decision.replacement_id]
    assert resolved.confidence == 1.0
    assert by_key.best.reason in resolved.provenance.correction_note


def test_a_lexical_proposal_keeps_a_band_reflecting_its_strength(refs):
    """A human accepted a *proposal*; the proposal's own strength says how much
    of a risk that was, and the link should carry it rather than claiming 1.0."""
    log = ReviewLog()
    proposal = _proposal(refs.graph, refs.refs["capability"].id)
    decision = resolve_reference(
        refs.graph, log, proposal.assertion_id, proposal.best.node_id,
        actor="tester", proposed_score=proposal.best.score,
        match_reason=proposal.best.reason,
    )
    resolved = refs.graph.assertions[decision.replacement_id]
    assert 0.9 <= resolved.confidence < 1.0
    assert resolved.status == STATUS_VERIFIED
    assert resolved.is_human


def test_a_manual_binding_is_the_reviewers_own_claim(refs):
    """A target nobody proposed is a judgement, not a match, so it is not
    discounted for a match quality that does not exist."""
    log = ReviewLog()
    decision = resolve_reference(
        refs.graph, log, refs.refs["hopeless"].id, refs.nodes["domain"],
        actor="tester", allow_kind_override=True,
    )
    assert refs.graph.assertions[decision.replacement_id].confidence == 1.0


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
    # Reported even for a pass that bound nothing: a bulk resolve that declined
    # everything still has to say what the requirement side looks like, or
    # "0 resolved" reads as "nothing to do". The fixture's three requirement
    # nodes have no architecture claiming them.
    assert result.to_dict() == {
        "resolved": 0,
        "below_threshold": 0,
        "no_candidate": 0,
        "unknown_ids": 0,
        "considered": 0,
        "threshold": DEFAULT_MATCH_THRESHOLD,
        "unrealized_requirements": 3,
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
