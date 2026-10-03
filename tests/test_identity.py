"""
Typed identity — identifiers carry their KIND, and only some may be joined on.

The distinction this file defends:

- An identifier held in a **system of record** (a CMDB CI, a Jira key) is an
  enterprise identity and may be matched globally.
- A label read out of a **source document** (`NFR-PS-001` in a markdown file) is
  real and worth keeping — it is how a human finds the thing — but it is not an
  identity. Two documents may both number a requirement `FR-001`.

Before this, both arrived as bare strings and reconciliation treated either as a
definitive match. That produced confident WRONG cross-document joins, which are
worse than missing joins because they make the audit wrong rather than incomplete.

Three things are checked: that the kind survives the whole pipeline, that scope
gates matching, and that old revisions still load.
"""

from __future__ import annotations

import pytest

from core.knowledge.ingest import graph_from_extraction, merge_graphs
from core.knowledge.model import (
    MANAGED_REFERENCE_TYPES,
    IDENTITY_REASON_CITING_KEY,
    IDENTITY_REASON_EXTERNAL_REF,
    IDENTITY_SCOPE_DOCUMENT,
    IDENTITY_SCOPE_ENTERPRISE,
    READ_BOUND_REFERENCE_TYPES,
    ExternalReference,
    KnowledgeGraph,
    document_reference,
    reference_identity_reason,
    reference_targets_a_node,
)
from core.knowledge.realization import realization_edges, realization_state
from core.knowledge.reconcile import match_score, reference_candidates
from core.knowledge.serialise import node_from_dict, node_to_dict

# ============================================================================
# The type is preserved, not flattened
# ============================================================================


def test_a_document_label_records_where_it_came_from():
    """The label stays traceable to its source without being promoted into an
    identity it never had."""
    ref = document_reference("NFR-PS-001", "sample_requirements.md")
    assert ref.identifier == "NFR-PS-001"
    assert ref.system == "sample_requirements.md"
    assert ref.scope == IDENTITY_SCOPE_DOCUMENT
    assert ref.is_join_key is False


def test_a_document_label_is_not_typed_as_a_requirements_tooling_key():
    """`REQUIREMENT_KEY` means Jira / Azure DevOps / DOORS — a system of record.

    Typing a markdown heading the same way would make `reference_type` useless
    for separating the two, and the type is half of what decides match authority.
    """
    ref = document_reference("NFR-PS-001", "doc.md")
    assert ref.reference_type == "OTHER"
    assert ref.reference_type not in MANAGED_REFERENCE_TYPES


def test_a_managed_reference_is_enterprise_scoped_and_matchable():
    ref = ExternalReference(
        identifier="CI0004872", system="ServiceNow CMDB",
        reference_type="CMDB_CI", scope=IDENTITY_SCOPE_ENTERPRISE, is_authoritative=True,
    )
    assert ref.is_join_key is True


def test_an_identifier_with_a_named_system_is_inferred_enterprise_scoped():
    """A source that names a system of record has told us the scope, even if it
    did not say the word. Inferring the strong claim only when a system is named
    keeps an untyped identifier conservative."""
    payload = {
        "entities": [
            {
                "name": "Payment Acceptance",
                "ontology_class": "FunctionalRequirement",
                "external_references": [
                    {"identifier": "PAY-142", "system": "Jira",
                     "reference_type": "REQUIREMENT_KEY"}
                ],
            }
        ]
    }
    graph, _ = graph_from_extraction(payload, document_ref="req.md")
    node = graph.nodes["functionalrequirement:payment_acceptance"]
    assert [r.scope for r in node.external_references] == [IDENTITY_SCOPE_ENTERPRISE]
    assert node.matchable_refs() == ("PAY-142",)


def test_an_identifier_with_no_system_stays_document_scoped():
    """No system named means no claim to enterprise authority."""
    payload = {
        "entities": [
            {
                "name": "Payment Acceptance",
                "ontology_class": "FunctionalRequirement",
                "external_references": [{"identifier": "FR-PM-001"}],
            }
        ]
    }
    graph, _ = graph_from_extraction(payload, document_ref="req.md")
    node = graph.nodes["functionalrequirement:payment_acceptance"]
    assert [r.scope for r in node.external_references] == [IDENTITY_SCOPE_DOCUMENT]
    assert node.matchable_refs() == ()


def test_the_flat_list_is_still_kept_for_display():
    """Existing consumers (a C4 table, a search box) read `external_refs`."""
    graph = KnowledgeGraph()
    nid = graph.add_node(
        "FunctionalRequirement", "Payment Acceptance",
        external_references=[document_reference("FR-PM-001", "doc.md")],
    )
    assert graph.nodes[nid].external_refs == ["FR-PM-001"]


# ============================================================================
# The type survives every hop
# ============================================================================


def test_typing_survives_a_serialise_round_trip():
    graph = KnowledgeGraph()
    graph.add_node(
        "Container", "Payment Orchestrator",
        external_references=[
            ExternalReference(identifier="CI1", system="ServiceNow CMDB",
                              reference_type="CMDB_CI", scope=IDENTITY_SCOPE_ENTERPRISE,
                              uri="https://cmdb/CI1", is_authoritative=True)
        ],
    )
    node = next(iter(graph.nodes.values()))

    for rebuilt in (node_from_dict(node_to_dict(node)), node_from_dict(node.to_dict())):
        ref = rebuilt.external_references[0]
        assert (ref.identifier, ref.system, ref.reference_type, ref.scope) == (
            "CI1", "ServiceNow CMDB", "CMDB_CI", IDENTITY_SCOPE_ENTERPRISE,
        )
        assert ref.uri == "https://cmdb/CI1"
        assert ref.is_authoritative is True
        assert rebuilt.matchable_refs() == ("CI1",)


def test_typing_survives_a_merge():
    """`merge_graphs` copied only the flat list, so a freshly ingested enterprise
    key silently became indistinguishable from a document label the moment it
    merged — the typed records were dropped on every re-ingest."""
    incoming = KnowledgeGraph()
    incoming.add_node(
        "Container", "Payment Orchestrator",
        external_references=[
            ExternalReference(identifier="CI1", system="ServiceNow CMDB",
                              reference_type="CMDB_CI", scope=IDENTITY_SCOPE_ENTERPRISE)
        ],
    )
    merged = merge_graphs(KnowledgeGraph(), incoming)
    node = merged.nodes["container:payment_orchestrator"]
    assert [r.scope for r in node.external_references] == [IDENTITY_SCOPE_ENTERPRISE]
    assert node.matchable_refs() == ("CI1",)


def test_a_repeated_reference_folds_rather_than_duplicating():
    """Re-extraction must not accumulate duplicate identifiers."""
    graph = KnowledgeGraph()
    ref = document_reference("FR-001", "doc.md")
    graph.add_node("FunctionalRequirement", "A", external_references=[ref])
    graph.add_node("FunctionalRequirement", "A", external_references=[ref])
    node = graph.nodes["functionalrequirement:a"]
    assert len(node.external_references) == 1
    assert node.external_refs == ["FR-001"]


def test_folding_upgrades_what_the_new_reference_knows():
    """A later, better-informed reference may raise an existing one's scope."""
    graph = KnowledgeGraph()
    graph.add_node(
        "Container", "Orch",
        external_references=[ExternalReference(identifier="CI1")],
    )
    graph.add_node(
        "Container", "Orch",
        external_references=[
            ExternalReference(identifier="CI1", system="ServiceNow CMDB",
                              reference_type="CMDB_CI", scope=IDENTITY_SCOPE_ENTERPRISE)
        ],
    )
    refs = graph.nodes["container:orch"].external_references
    assert len(refs) == 1
    assert refs[0].scope == IDENTITY_SCOPE_ENTERPRISE
    assert graph.nodes["container:orch"].matchable_refs() == ("CI1",)


def test_a_bare_string_reference_never_gains_match_authority():
    """An identifier whose kind we do not know must not be granted authority, and
    `add_node` must not fabricate a system or a type for it."""
    graph = KnowledgeGraph()
    graph.add_node("FunctionalRequirement", "A", external_refs=["FR-001"])
    node = graph.nodes["functionalrequirement:a"]
    assert node.external_refs == ["FR-001"]
    assert node.matchable_refs() == ()


# ============================================================================
# Old revisions still load
# ============================================================================


def test_a_v1_node_without_typed_references_is_read_as_document_scoped():
    """Revisions are immutable; one written before typing carries only strings.

    Read conservatively as DOCUMENT-scoped: an old snapshot must not silently
    gain match authority it was never granted.
    """
    legacy = {
        "id": "functionalrequirement:y",
        "kind": "FunctionalRequirement",
        "label": "Y",
        "external_refs": ["FR-001", "FR-002"],
    }
    node = node_from_dict(legacy)
    assert node.external_refs == ["FR-001", "FR-002"]
    assert {r.scope for r in node.external_references} == {IDENTITY_SCOPE_DOCUMENT}
    assert node.matchable_refs() == ()


# ============================================================================
# Scope gates matching
# ============================================================================


def test_an_enterprise_key_outranks_wording_without_a_document():
    score, reason = match_score(
        "CI0004872", "Something Unrelated",
        [ExternalReference(identifier="CI0004872", system="ServiceNow CMDB",
                           reference_type="CMDB_CI", scope=IDENTITY_SCOPE_ENTERPRISE)],
    )
    assert (score, reason) == (1.0, "external_ref")


def test_a_document_label_scores_below_the_resolve_threshold_without_a_document():
    """Reported as evidence, not trusted as proof."""
    score, reason = match_score(
        "FR-001", "Something Unrelated",
        [document_reference("FR-001", "some_other_doc.md")],
        source_document="this_doc.md",
    )
    assert reason == "unscoped_ref"
    assert score < 0.75


def test_a_document_label_matches_within_its_own_document():
    """The case scoping must not break: one document stated both the label and
    the thing it labels, so the label identifies it."""
    score, reason = match_score(
        "FR-001", "Payment Acceptance",
        [document_reference("FR-001", "both.md")],
        source_document="both.md",
    )
    assert (score, reason) == (1.0, "external_ref")


def test_an_enterprise_key_is_preferred_over_a_document_label_of_the_same_name():
    """Reference order must not decide the outcome."""
    refs = [
        document_reference("FR-001", "brief.md"),
        ExternalReference(identifier="FR-001", system="Jira",
                          reference_type="REQUIREMENT_KEY", scope=IDENTITY_SCOPE_ENTERPRISE),
    ]
    assert match_score("FR-001", "Unrelated", refs, "brief.md") == (1.0, "external_ref")
    # And in the other order.
    assert match_score("FR-001", "Unrelated", list(reversed(refs)), "brief.md") == (
        1.0, "external_ref"
    )


def test_the_requirements_key_joins_end_to_end():
    """The fix, as a user would hit it.

    The requirements document numbers `FR-PM-001`; the architecture document
    cites it. The citation IS the claim `implements_requirement` makes, so it is a
    link rather than a sub-threshold suggestion a reviewer has to confirm.
    Refusing this join was the deepest form of the defect — even a
    verbatim-preserved requirement id could not join across documents.

    What survives is narrower: an identifier the architecture profile recorded as
    a plain document label (`OTHER`) still cannot bind, which the companion test
    below covers.
    """
    requirements = {
        "entities": [
            {"name": "Payment Acceptance", "ontology_class": "FunctionalRequirement",
             "requirement_id": "FR-PM-001"}
        ],
        "triples": [],
    }
    architecture = {
        "elements": [{"name": "Payment Orchestrator", "element_type": "Container"}],
        "triples": [
            {"subject": "Payment Orchestrator", "predicate": "implements_requirement",
             "object": "FR-PM-001", "confidence": 0.6}
        ],
    }
    req_graph, _ = graph_from_extraction(
        requirements, {"document_type": "requirements"}, document_ref="req.md"
    )
    arch_graph, _ = graph_from_extraction(
        architecture, {"document_type": "architecture"}, document_ref="arch.md"
    )
    merged = merge_graphs(req_graph, arch_graph)

    assert reference_candidates(merged) == []
    assert merged.unresolved_references() == []
    linked = [
        a for a in merged.active()
        if a.predicate == "implements_requirement" and a.value == "FR-PM-001"
    ]
    assert len(linked) == 1
    assert linked[0].confidence == 0.6
    assert linked[0] not in merged.unresolved_references()
    assert ["%s" % n.id for _a, n in realization_edges(merged)] == [
        "functionalrequirement:payment_acceptance"
    ]


def test_a_document_local_identifier_is_not_a_join_key():
    """The distinction the typed reference exists for, end to end.

    A label the requirements document states as `OTHER` — a heading, not a
    `requirement_id` — is not an identity. Two documents may use the same words,
    so the reference stays a proposal a human must accept.
    """
    requirements = {
        "entities": [
            {"name": "High Availability", "ontology_class": "NonFunctionalRequirement",
             "external_references": [{"identifier": "Availability", "system": "req.md"}]}
        ],
        "triples": [],
    }
    architecture = {
        "elements": [{"name": "Payment Orchestrator", "element_type": "Container"}],
        "triples": [
            {"subject": "Payment Orchestrator", "predicate": "traces_to_goal",
             "object": "Availability", "confidence": 0.6}
        ],
    }
    req_graph, _ = graph_from_extraction(requirements, document_ref="req.md")
    arch_graph, _ = graph_from_extraction(architecture, document_ref="arch.md")
    merged = merge_graphs(req_graph, arch_graph)

    # A bare label is recognised only by its match quality, never by identity:
    # the requirement is a NEAR MISS of the wrong kind, so nothing binds.
    proposal = reference_candidates(merged)[0]
    assert proposal.best is None
    assert proposal.best_near_miss is not None
    assert proposal.best_near_miss.score < 1.0


# ============================================================================
# One identity rule, two policies on it
# ============================================================================


def test_the_identity_reason_is_one_rule_with_one_vocabulary():
    """`match_score` and `reference_targets_a_node` judge a match by the SAME rule.

    The rule used to exist twice — once inside `match_score`, once as a
    `reference_type == "REQUIREMENT_KEY"` comparison in `reference_targets_a_node` —
    so "is this identifier identity or resemblance?" had two implementations free to
    drift, and the two verdict names were bare string literals in two modules. The
    function is now the single answer and the constants are its vocabulary.
    """
    join_key = ExternalReference(identifier="CI0004872", system="ServiceNow CMDB",
                                reference_type="CMDB_CI", scope=IDENTITY_SCOPE_ENTERPRISE)
    requirement_key = document_reference("FR-PM-001", "req.md")
    requirement_key.reference_type = "REQUIREMENT_KEY"
    plain_label = document_reference("Availability", "req.md")

    # A join key is identity under ANY predicate — it is unique beyond its document.
    assert reference_identity_reason(join_key, "traces_to_goal") == IDENTITY_REASON_EXTERNAL_REF

    # A requirement key is identity only where citing the key IS the claim.
    assert reference_identity_reason(requirement_key, "implements_requirement") == (
        IDENTITY_REASON_CITING_KEY
    )
    assert reference_identity_reason(requirement_key, "traces_to_goal") == ""

    # One document stating both the label and the thing it labels is identity...
    assert reference_identity_reason(plain_label, "traces_to_goal", "req.md") == (
        IDENTITY_REASON_EXTERNAL_REF
    )
    # ...but a different document's label is evidence, and says so by returning nothing.
    assert reference_identity_reason(plain_label, "traces_to_goal", "other.md") == ""


def _citing_requirement(rtype: str, scope: str, system: str):
    """A container citing `PA-77`, and the requirement that publishes it."""
    graph = KnowledgeGraph()
    graph.add_node("SoftwareSystem", "Payment Gateway Platform")
    container = graph.add_node("Container", "Payment Orchestrator")
    requirement = graph.add_node(
        "FunctionalRequirement", "Payment Acceptance",
        external_references=[
            ExternalReference(identifier="PA-77", system=system,
                              reference_type=rtype, scope=scope)
        ],
    )
    claim = graph.add_assertion(container, "implements_requirement", value="PA-77",
                                confidence=0.8)
    return graph, claim, requirement


def test_a_published_join_key_is_proposed_definitively_but_not_yet_bound():
    """The boundary between PROPOSING identity and BINDING on it, pinned.

    An enterprise key is the most reliable join available, so the matcher scores it
    1.0 `external_ref` and reports the reference as resolvable. It still does not
    bind on read: `reference_targets_a_node` requires the identifier be of a type
    the other side PUBLISHED (`READ_BOUND_REFERENCE_TYPES`), and `EA_REPOSITORY_ID`
    is not one of those today.

    The consequence is visible and deliberate: `/realization` calls the requirement
    unanswered while `/reconcile` calls it certain at 1.00, and one `bulk_resolve`
    pass closes the gap. Widening `READ_BOUND_REFERENCE_TYPES` to `is_join_key`
    would bind it on read instead — a real decision about how much authority to
    grant an enterprise register, which this test exists to make explicit rather
    than accidental.
    """
    graph, claim, requirement = _citing_requirement(
        "EA_REPOSITORY_ID", IDENTITY_SCOPE_ENTERPRISE, "EA-Repo"
    )

    # Proposed: definitive, and offered for binding.
    score, reason = match_score(claim.target, requirement,
                                graph.nodes[requirement].external_references, "",
                                claim.predicate)
    assert (score, reason) == (1.0, IDENTITY_REASON_EXTERNAL_REF)
    assert len(reference_candidates(graph)) == 1

    # Bound: not yet, and the coverage verdict says so honestly.
    assert reference_targets_a_node(graph, claim) is None
    assert graph.unresolved_references() == [claim]
    coverage = {r.node_id: r.coverage for r in realization_state(graph)}
    assert coverage[requirement] == "none"


def test_a_published_requirement_key_is_identity_for_both_readers():
    """The case that must agree: a key the requirements side really published.

    `implements_requirement -> FR-PM-001` where the requirement carries
    `FR-PM-001` as its `REQUIREMENT_KEY`. The matcher calls it identity and the read
    layer binds it, so the requirement reads as answered with no human step — which
    is the join `IDENTITY_BY_CITATION_PREDICATES` exists for. A graph where the two
    readers disagreed HERE would be the defect, and both now call one rule.
    """
    graph, claim, requirement = _citing_requirement(
        "REQUIREMENT_KEY", IDENTITY_SCOPE_DOCUMENT, "req.md"
    )

    assert reference_identity_reason(
        graph.nodes[requirement].external_references[0], claim.predicate, ""
    ) == IDENTITY_REASON_CITING_KEY
    assert reference_targets_a_node(graph, claim) is not None
    assert graph.unresolved_references() == []
    assert "REQUIREMENT_KEY" in READ_BOUND_REFERENCE_TYPES
    coverage = {r.node_id: r.coverage for r in realization_state(graph)}
    assert coverage[requirement] == "full"
