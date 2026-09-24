"""
Realization reporting: which requirements have an architectural answer.

The whole point of this layer is that the audit is bidirectional, so most of these
tests assert one direction and then its inverse on the same graph. A report that
can only say "this reference is unresolved" answers half the question the platform
exists to ask.

The synthetic end-to-end graph below mirrors the real failure the item was opened
for: requirement identifiers ARE preserved, an architecture element cites one by
id, and another cites one by a paraphrase that no lexical match can bridge.
"""

import pytest

from core.knowledge import (
    COVERAGE_FULL,
    COVERAGE_NONE,
    COVERAGE_PARTIAL,
    COVERAGE_UNRESOLVED,
    KnowledgeGraph,
    Provenance,
    bulk_resolve,
    graph_from_extraction,
    realization_coverage,
    realization_edges,
    realization_report,
    realization_state,
    reference_candidates,
    resolve_reference,
    unbound_claims,
    unmet_obligations,
    unrealized_requirements,
)
from core.knowledge.model import SOURCE_EXTRACTION


@pytest.fixture
def requirement_graph():
    """A REQ-shaped extraction with stable identifiers and an Initiative."""
    return graph_from_extraction(
        {
            "entities": [
                {
                    "name": "Payment Acceptance",
                    "ontology_class": "FunctionalRequirement",
                    "requirement_id": "FR-PM-001",
                    "requirement_type": "FUNCTIONAL",
                    "initiative_refs": ["INIT-2024-001"],
                },
                {
                    "name": "Settlement Initialization",
                    "ontology_class": "FunctionalRequirement",
                    "requirement_id": "FR-PM-002",
                    "requirement_type": "FUNCTIONAL",
                    "initiative_refs": ["INIT-2024-001"],
                },
                {
                    "name": "High Availability",
                    "ontology_class": "NonFunctionalRequirement",
                    "requirement_id": "NFR-AV-001",
                    "requirement_type": "NON_FUNCTIONAL",
                    "initiative_refs": ["INIT-2024-001"],
                },
                {
                    "name": "Nowhere Claimed Requirement",
                    "ontology_class": "FunctionalRequirement",
                    "requirement_id": "FR-ZZ-009",
                    "requirement_type": "FUNCTIONAL",
                    "initiative_refs": ["INIT-2024-001"],
                },
            ],
            "triples": [],
        },
        {"document_type": "requirements", "model_id": "t"},
        document_ref="prd.md",
    )[0]


@pytest.fixture
def architecture_graph():
    """Two elements: one citing an id, one naming something never extracted."""
    return graph_from_extraction(
        {
            "elements": [
                {
                    "name": "Payment Orchestrator",
                    "element_type": "Container",
                    "initiative_id": "INIT-2024-001",
                },
                {
                    "name": "Unknown Integration",
                    "element_type": "Container",
                    "initiative_id": "INIT-2024-001",
                },
                {"name": "Reporting Module", "element_type": "Container"},
            ],
            "triples": [
                {
                    "subject": "Payment Orchestrator",
                    "predicate": "implements_requirement",
                    "object": "FR-PM-001",
                    "confidence": 0.9,
                    "source_text": "handles Request Acceptance and Validation",
                },
                {
                    "subject": "Unknown Integration",
                    "predicate": "implements_requirement",
                    "object": "made up requirement text",
                    "confidence": 0.6,
                },
            ],
        },
        {"document_type": "architecture", "model_id": "t"},
        document_ref="arch.md",
    )[0]


@pytest.fixture
def merged(requirement_graph, architecture_graph):
    from core.knowledge import merge_graphs

    return merge_graphs(requirement_graph, architecture_graph)


# ============================================================================
# Requirement side — "which requirements have no architectural answer?"
# ============================================================================


def test_every_requirement_is_listed_even_when_nothing_claims_it(merged):
    """Absence has to be a row, not a missing row.

    A requirement nothing mentions cannot be found by searching for its links —
    that is exactly why the report starts from the requirement side.
    """
    labels = {r.label for r in unrealized_requirements(merged)}
    assert "Nowhere Claimed Requirement" in labels
    assert len(realization_state(merged)) == 4


def test_citing_the_documents_own_requirement_key_is_a_link(merged):
    """Acceptance: every resolved realization edge points at a real REQ-G node.

    A requirement document numbers its requirements; an architecture element
    citing `FR-PM-001` is quoting that key. The identifier survives ingest
    (`requirement_id` -> external reference) and the citation names the node
    carrying it — no reconciliation step needed, because there is nothing
    ambiguous left to reconcile. This is the deepest form of the defect the item
    was opened for: without it, even a verbatim-preserved id could not join.
    """
    edges = realization_edges(merged)
    assert len(edges) == 1
    assertion, node = edges[0]
    assert assertion.predicate == "implements_requirement"
    assert node.id in merged.nodes
    assert node.kind == "FunctionalRequirement"
    assert node.label == "Payment Acceptance"
    assert node.external_refs == ["FR-PM-001"]

    # Only the claim naming nothing extracted is still open, and it is open in
    # both directions — unresolved reference and unmet obligation.
    unresolved = merged.unresolved_references()
    assert [a.value for a in unresolved] == ["made up requirement text"]


def test_a_citation_is_not_offered_for_reconciliation_it_already_resolved(merged):
    """A reference that names a node is not work: offering it would ask a human
    to bind something already bound, and inflate every "open references" count."""
    ids = {rc.assertion_id for rc in reference_candidates(merged)}
    targets = {a.value for a in merged.unresolved_references() if a.id in ids}
    assert "FR-PM-001" not in targets


def test_coverage_moves_from_none_to_full_only_when_a_link_binds(merged):
    """The coverage verdict is driven by binding, so a claim binds it and an
    unrelated requirement stays where it was."""
    from core.knowledge import ReviewLog

    before = {r.label: r.coverage for r in realization_state(merged)}
    assert before["Payment Acceptance"] == COVERAGE_FULL   # cited by its own key
    assert before["Settlement Initialization"] == COVERAGE_NONE

    # Binding the one open claim cannot happen — it names nothing extracted — so
    # what moves is nothing, and a requirement nothing claimed stays `none`.
    result = bulk_resolve(merged, ReviewLog(), actor="tester")
    assert result.resolved == []

    after = {r.label: r.coverage for r in realization_state(merged)}
    assert after["Settlement Initialization"] == COVERAGE_NONE
    assert after["Nowhere Claimed Requirement"] == COVERAGE_NONE


def test_a_requirement_with_a_bound_link_is_realized(merged):
    from core.knowledge import ReviewLog

    bulk_resolve(merged, ReviewLog(), actor="tester")
    state = {r.label: r for r in realization_state(merged)}
    assert state["Payment Acceptance"].is_realized is True
    assert state["Nowhere Claimed Requirement"].is_realized is False


# ============================================================================
# Architecture side — "what claims a requirement it never bound?"
# ============================================================================


def test_an_architecture_element_with_an_unresolvable_claim_is_reported(merged):
    """Acceptance: an architecture element answering no requirement can be listed.

    The paraphrase names nothing extracted, so it can never be attributed — but it
    must not vanish either. It is a claim a human can see and adjudicate, which is
    the posture the whole item adopted.
    """
    obligations = unmet_obligations(merged)
    assert [o["source_label"] for o in obligations] == ["Unknown Integration"]
    assert obligations[0]["predicate"] == "implements_requirement"
    assert obligations[0]["target_label"] == "made up requirement text"
    assert obligations[0]["bound"] is False

    # The identifier citation is NOT an obligation: it names something that was
    # extracted, so it is a link, not a claim about something that does not exist.
    assert all(o["target_label"] != "FR-PM-001" for o in obligations)


def test_an_element_that_makes_no_claim_is_not_accused_of_answering_nothing(merged):
    """A container with no requirement claim is not a finding.

    Reporting every unclaimed element would make the report useless noise — most
    architecture is structure, not requirement satisfaction.
    """
    assert all(o["source_label"] != "Reporting Module" for o in unmet_obligations(merged))


def test_unbound_claims_are_a_subset_of_unresolved_references(merged):
    unresolved = {a.id for a in merged.unresolved_references()}
    claims = unbound_claims(merged)
    assert {a.id for a in claims} <= unresolved
    assert len(claims) == 1
    assert claims[0].value == "made up requirement text"


def test_ordinary_structural_edges_are_not_claims():
    """`part_of` says nothing about requirements; counting it would report every
    deployment node as answering nothing."""
    graph = KnowledgeGraph()
    parent = graph.add_node("SoftwareSystem", "Platform")
    child = graph.add_node("Container", "Orchestrator")
    graph.add_node("FunctionalRequirement", "Some Requirement")
    graph.add_assertion(child, "part_of", obj=parent, confidence=1.0)

    report = realization_report(graph)
    assert report["summary"]["unbound_claims"] == 0
    assert report["summary"]["bound_edges"] == 0
    assert unmet_obligations(graph) == []


# ============================================================================
# The report both directions come from
# ============================================================================


def test_report_summarises_both_directions(merged):
    report = realization_report(merged)
    summary = report["summary"]

    assert summary["requirements"] == 4
    assert summary["realized"] == 1
    assert summary["unrealized"] == 3
    assert summary["coverage"][COVERAGE_FULL] == 1
    assert summary["coverage"][COVERAGE_NONE] == 3
    assert summary["bound_edges"] == 1
    assert summary["unbound_claims"] == 1
    # The unmatched paraphrase shares a couple of words with a requirement label,
    # so a weak proposal exists for it while scoring far below the threshold —
    # "a human could look at this" is a different state from "the matcher cannot
    # see it at all", and the summary keeps them apart.
    assert summary["proposed_claims"] == 1
    assert summary["unproposed_claims"] == 0
    assert summary["obliged_elements"] == 1
    assert summary["requirement_kinds"] == ["FunctionalRequirement", "NonFunctionalRequirement"]

    assert len(report["unrealized"]) == 3
    assert len(report["obligations"]) == 1
    assert len(report["claims"]) == 1


def test_a_claim_naming_a_requirement_by_label_is_already_a_link(merged):
    """Ingest keeps cross-graph targets as literals, but a literal that names an
    existing node is a link in substance — reporting it as unresolved would ask a
    reviewer to bind something already bound.

    This is also why the report has no separate "unbound but attributed" list: by
    the one rule both `unresolved_references` and this module use, that state
    cannot exist.
    """
    from core.knowledge import merge_graphs

    requirements = {
        "entities": [{"name": "Settlement Initialization",
                      "ontology_class": "FunctionalRequirement"}],
        "triples": [],
    }
    architecture = {
        "elements": [{"name": "Payment Orchestrator", "element_type": "Container"}],
        "triples": [
            {"subject": "Payment Orchestrator", "predicate": "traces_to_goal",
             "object": "Settlement Initialization", "confidence": 0.5},
        ],
    }
    req_graph, _ = graph_from_extraction(
        requirements, {"document_type": "requirements"}, document_ref="req.md"
    )
    arch_graph, _ = graph_from_extraction(
        architecture, {"document_type": "architecture"}, document_ref="arch.md"
    )
    graph = merge_graphs(req_graph, arch_graph)

    assert unbound_claims(graph) == []
    assert graph.unresolved_references() == []
    assert len(realization_edges(graph)) == 1
    state = {r.label: r for r in realization_state(graph)}
    assert state["Settlement Initialization"].coverage == COVERAGE_FULL


def test_coverage_can_be_half_answered(merged):
    """Partial is its own state on purpose: a bound answer AND a dangling guess
    is a different action from either alone.

    Note the unbound claim has to come FROM a requirement — that is the direction
    in which we know which requirement it is about. A container's unmatched claim
    is an obligation, not a statement about a requirement, and belongs in the
    other list.
    """
    from core.knowledge import ReviewLog

    bulk_resolve(merged, ReviewLog(), actor="tester")
    state = {r.label: r for r in realization_state(merged)}
    # The identifier citation bound, so this requirement is answered.
    assert state["Payment Acceptance"].coverage == COVERAGE_FULL
    assert state["Payment Acceptance"].is_realized is True

    # A second requirement, bound by citing its own key...
    merged.add_assertion(
        "container:payment_orchestrator",
        "implements_requirement",
        value="FR-PM-002",
        confidence=0.5,
        provenance=Provenance(source_type=SOURCE_EXTRACTION, run_id="r"),
    )
    state = {r.label: r for r in realization_state(merged)}
    assert state["Settlement Initialization"].coverage == COVERAGE_FULL

    # ...and now an unmatched claim made BY that requirement (the requirements
    # document pointing at something the architecture graph does not contain).
    merged.add_assertion(
        "functionalrequirement:settlement_initialization",
        "implements_requirement",
        value="zzz architecture that does not exist",
        confidence=0.5,
        provenance=Provenance(source_type=SOURCE_EXTRACTION, run_id="r"),
    )
    state = {r.label: r for r in realization_state(merged)}
    assert state["Settlement Initialization"].coverage == COVERAGE_PARTIAL
    assert state["Settlement Initialization"].is_realized is True
    assert len(state["Settlement Initialization"].unbound) == 1
    assert state["Settlement Initialization"].bound[0]["target_label"] == "Settlement Initialization"


def test_an_unbound_claim_makes_its_requirement_unresolved(merged):
    """A requirement may cite its own architectural answer — the requirements
    document is allowed to reference outward too. When that reference names
    nothing extracted, the requirement is NOT answered, and it has to say so on
    the requirement side rather than only as an orphan reference."""
    from core.knowledge import merge_graphs

    requirements = {
        "entities": [
            {"name": "High Availability", "ontology_class": "NonFunctionalRequirement"}
        ],
        "triples": [
            {"subject": "High Availability", "predicate": "implements_requirement",
             "object": "ha-missing", "confidence": 0.5},
        ],
    }
    architecture = {"elements": [{"name": "Availability Design", "element_type": "Container"}]}
    req_graph, _ = graph_from_extraction(
        requirements, {"document_type": "requirements"}, document_ref="req.md"
    )
    arch_graph, _ = graph_from_extraction(
        architecture, {"document_type": "architecture"}, document_ref="arch.md"
    )
    graph = merge_graphs(req_graph, arch_graph)

    state = {r.label: r for r in realization_state(graph)}
    assert state["High Availability"].coverage == COVERAGE_UNRESOLVED
    assert state["High Availability"].is_realized is False
    assert state["High Availability"].unbound[0]["target_label"] == "ha-missing"
    # And the other direction still has it: the same claim is an unmet obligation.
    assert [o["source_label"] for o in unmet_obligations(graph)] == ["High Availability"]


def test_report_is_empty_but_valid_on_an_empty_graph():
    report = realization_report(KnowledgeGraph())
    assert report["summary"]["requirements"] == 0
    assert report["unrealized"] == []
    assert report["obligations"] == []
    assert realization_coverage(KnowledgeGraph())["bound_edges"] == 0


def test_resolution_never_invents_the_requirement(merged):
    """The finding stays a finding: reconciliation binds to what was extracted."""
    from core.knowledge import ReconcileError, ReviewLog

    claim = unbound_claims(merged)[0]
    with pytest.raises(ReconcileError):
        resolve_reference(merged, ReviewLog(), claim.id, "functionalrequirement:made_up_requirement_text")
    assert unrealized_requirements(merged)  # still unmet, still reported


# ============================================================================
# The real fixture — the number the item was opened with
# ============================================================================


def test_the_real_fixture_reports_both_directions():
    """Measured on `data/output/`, the graph ADR-0006 was written from.

    22 requirements, 13 architecture references, and 20 requirements with no
    architectural answer. The two that count as realized got there by wording
    coincidence — the item's own evidence that shared labels are not links.
    """
    import json
    from pathlib import Path

    from core.knowledge import merge_graphs

    root = Path(__file__).resolve().parents[1] / "data" / "output"
    arch_path, req_path = root / "test_arch.json", root / "test_req_prd.json"
    if not arch_path.exists() or not req_path.exists():
        pytest.skip("saved extraction fixtures are absent")

    req = json.loads(req_path.read_text())
    arch = json.loads(arch_path.read_text())
    req_graph, _ = graph_from_extraction(req, req.get("metadata", {}), document_ref="req.md")
    arch_graph, _ = graph_from_extraction(arch, arch.get("metadata", {}), document_ref="arch.md")
    merged = merge_graphs(req_graph, arch_graph)

    report = realization_report(merged)
    summary = report["summary"]

    assert summary["requirements"] == 22
    assert summary["unrealized"] == 20
    assert summary["coverage"][COVERAGE_FULL] == 2
    assert summary["coverage"][COVERAGE_NONE] == 20
    assert summary["unbound_claims"] == 13
    assert summary["proposed_claims"] == 4
    assert summary["unproposed_claims"] == 9
    assert len(report["obligations"]) == 13
    assert len(report["unrealized"]) == 20

    # Both directions are stated, not inferred from each other: a requirement
    # with nothing claiming it, and an architecture reference with nothing to
    # bind to, in one report.
    assert any(not r["bound"] and not r["unbound"] for r in report["unrealized"])
    assert all(o["bound"] is False for o in report["obligations"])
    # The fixture still carries no requirement identifiers (it predates capture),
    # so every requirement node's reference list is empty and every binding has
    # to be lexical — which is exactly why 9 of the 13 references cannot even be
    # proposed.
    assert all(not r["external_refs"] for r in report["requirements"])
