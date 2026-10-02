"""
Graph -> prompt digest.

The digest is the Design Assistant's "document". Three things decide whether it is
usable, and each is a test here:

1. **Deterministic** — the same graph renders the same text, so a diff between two
   runs is a diff between two models, not two renderings.
2. **A document, not a dump** — node ids must not leak (`qualityattribute:time_behaviour`
   is not English), and the identifier a design must cite has to be present.
3. **Honest about its own limits** — completeness travels with it, and a budget cut
   is recorded rather than silently taken.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.runner import resolve_design_baseline
from core.knowledge import KnowledgeGraph, graph_from_extraction, merge_graphs
from core.knowledge.digest import (
    architecture_digest,
    design_input,
    requirements_digest,
)
from core.knowledge.model import SCOPE_BASELINE


def requirement_graph() -> object:
    graph, _run = graph_from_extraction(
        {
            "initiatives": [{"id": "INIT-1", "name": "Payments"}],
            "entities": [
                {
                    "name": "Payment Request Validation",
                    "ontology_class": "FunctionalRequirement",
                    "requirement_id": "FR-PM-001",
                    "source_text": "The platform shall validate every request.",
                },
                {
                    "name": "Payment Authorization Latency",
                    "ontology_class": "NonFunctionalRequirement",
                    "requirement_id": "NFR-PS-001",
                    "quality_attribute": "Time Behaviour",
                    "quality_category": "PERFORMANCE_EFFICIENCY",
                    "subcharacteristic": "TIME_BEHAVIOUR",
                },
                {
                    "name": "Horizontal Scaling",
                    "ontology_class": "ConstraintRequirement",
                    "requirement_id": "NFR-PM-002",
                    "quality_attribute": "Scalability",
                },
            ],
        },
        {"model_id": "fake", "document_type": "requirements"},
        document_ref="req.md",
        document_text="req body",
    )
    return graph


def architecture_graph() -> object:
    graph, _run = graph_from_extraction(
        {
            "elements": [
                {"name": "Payment Gateway Platform", "element_type": "SoftwareSystem"},
                {
                    "name": "Payment Orchestrator",
                    "element_type": "Container",
                    "parent": "Payment Gateway Platform",
                    "responsibilities": ["Acceptance and validation of payment requests"],
                },
            ],
            "design_techniques": [
                {
                    "name": "Redundancy / Replicas",
                    "satisfies_attributes": ["Availability"],
                }
            ],
            "references": [
                {
                    "element": "Payment Orchestrator",
                    "relationship": "implements_requirement",
                    "reference": "FR-PM-001",
                }
            ],
        },
        {"model_id": "fake", "document_type": "architecture"},
        document_ref="arch.md",
        document_text="arch body",
    )
    return graph


def merged() -> object:
    return merge_graphs(requirement_graph(), architecture_graph())


# ============================================================================
# Determinism and shape
# ============================================================================


def test_the_same_graph_renders_the_same_text():
    graph = merged()
    assert requirements_digest(graph).text == requirements_digest(graph).text
    assert design_input(graph).text == design_input(graph).text


def test_the_digest_is_a_document_not_a_database_dump():
    """Node ids are not English; a prompt full of them is a prompt full of noise."""
    text = design_input(merged()).text
    assert "qualityattribute:" not in text
    assert "functionalrequirement:" not in text
    assert "softwaresystem:" not in text


def test_requirement_identifiers_travel_with_the_requirement():
    """They are the join keys a design cites; losing them makes the draft unlinkable."""
    text = requirements_digest(merged()).text
    assert "FR-PM-001" in text
    assert "NFR-PS-001" in text
    assert "NFR-PM-002" in text


def test_the_quality_classification_travels():
    text = requirements_digest(merged()).text
    assert "TIME_BEHAVIOUR" in text
    assert "Time Behaviour" in text


def test_the_document_quotes_the_requirement_where_the_graph_stored_it():
    """Nothing populates requirement source text today, so the line is simply absent.

    Pinned deliberately: if the requirements profile starts carrying the statement
    through, this is where the digest should begin quoting it.
    """
    graph = merged()
    requirement = next(
        n for n in graph.nodes.values() if n.label == "Payment Request Validation"
    )
    assert not any(
        a.source_text for a in graph.active() if a.subject == requirement.id
    )
    text = requirements_digest(graph).text
    assert "source:" not in text


# ============================================================================
# What the design actually needs from the architecture
# ============================================================================


def test_the_digest_says_which_requirements_are_already_answered():
    text = architecture_digest(merged()).text
    assert "already answers" in text
    assert "Payment Request Validation" in text
    assert "Payment Orchestrator" in text


def test_the_digest_lists_the_unanswered_requirements_as_the_worklist():
    text = architecture_digest(merged()).text
    assert "NO architectural answer" in text
    assert "Payment Authorization Latency" in text
    assert "Horizontal Scaling" in text


def test_existing_elements_and_techniques_are_present_to_extend():
    text = architecture_digest(merged()).text
    assert "Payment Orchestrator" in text
    assert "Redundancy / Replicas" in text


# ============================================================================
# Honesty
# ============================================================================


def test_the_completeness_caveat_travels_with_the_input():
    """Absence of a requirement is not evidence until the run says it is."""
    digest = design_input(merged())
    assert any("Extraction" in c for c in digest.caveats), digest.caveats
    assert "Extraction" in digest.text


def test_no_baseline_is_stated_rather_than_assumed():
    digest = design_input(merged())
    assert digest.base_ref == "working (no frozen baseline)"
    assert any("no frozen baseline" in c for c in digest.caveats)


def test_a_baseline_is_used_and_named_when_one_is_given():
    baseline = architecture_graph()
    digest = design_input(requirement_graph(), baseline=baseline, base_ref="rev_baseline")
    assert digest.base_ref == "rev_baseline"
    assert "rev_baseline" in digest.text
    assert "Payment Orchestrator" in digest.text
    assert not any("no frozen baseline" in c for c in digest.caveats)


def test_a_baseline_with_no_architecture_is_announced_rather_than_passed_over():
    """The frozen baseline is what a design EXTENDS, so an empty one has to say so.

    Freezing REQ-G before any design exists is the greenfield path and is perfectly
    legitimate. The case that must not pass silently is the narrower one: the
    enterprise holds architecture in the working set that never reached a baseline.
    The design cannot see it — `architecture_source` is the baseline and nothing
    else — so it extends nothing and may duplicate elements that already exist,
    with no caveat and no finding to explain the proposal.
    """
    digest = design_input(merged(), baseline=requirement_graph(), base_ref="rev_req_v1")
    # `merged()` is the requirement graph merged with three architecture nodes.
    caveat = next(c for c in digest.caveats if "names no architecture" in c)

    assert "working set holds 3 architecture element(s)" in caveat
    assert "duplicate" in caveat
    assert "greenfield" not in caveat, "this is the hazard, not the benign case"
    assert caveat in digest.text, "the caveat must reach the prompt, not only the object"


def test_a_greenfield_baseline_is_stated_as_expected_not_as_a_hazard():
    """No architecture anywhere is a first design, not a mistake.

    A single alarmist message would cry wolf on the journey's own stage 4 -> 5
    ("Baseline REQ-G", then design), which is exactly how a caveat gets ignored.
    """
    digest = design_input(requirement_graph(), baseline=requirement_graph(),
                          base_ref="rev_req_v1")
    caveat = next(c for c in digest.caveats if "names no architecture" in c)

    assert "greenfield" in caveat
    assert "working set holds" not in caveat, "nothing is being hidden here"
    assert "duplicate" not in caveat


def test_a_baseline_that_carries_architecture_gets_no_such_caveat():
    """The other half of the contract: a correct baseline stays quiet.

    Without this, the check could be unconditional and every design run would carry
    a warning about architecture — which trains a reviewer to ignore the header.
    """
    digest = design_input(requirement_graph(), baseline=architecture_graph(),
                          base_ref="rev_arc_v1")

    assert not any("names no architecture" in c for c in digest.caveats), digest.caveats
    # And the architecture it does carry is what the design is shown.
    assert "Payment Orchestrator" in digest.text


def test_a_budget_cut_is_recorded_and_ordered():
    """A design that silently saw half the requirements is a wrong design."""
    detail_cut = requirements_digest(merged(), budget_chars=250)
    assert detail_cut.caveats, "a cut must be reported"
    assert "dropped" in detail_cut.caveats[0]
    assert len(detail_cut.text) <= 250

    truncated = requirements_digest(merged(), budget_chars=120)
    assert any("TRUNCATED" in c for c in truncated.caveats)
    assert len(truncated.text) <= 120

    # The architecture degrades in its own order: responsibilities first.
    arch_cut = architecture_digest(merged(), budget_chars=600)
    assert any("responsibilities" in c for c in arch_cut.caveats), arch_cut.caveats
    assert len(arch_cut.text) <= 600


def test_a_generous_budget_cuts_nothing():
    section = requirements_digest(merged(), budget_chars=100000)
    assert section.caveats == []
    assert "FR-PM-001" in section.text


def test_the_quality_section_reports_coverage_not_just_names():
    text = design_input(merged()).text
    assert "Quality attributes and their coverage" in text
    assert "Stated and delivered" in text or "stated" in text.lower()


# ============================================================================
# What the design is told already exists
#
# An element the model cannot see is an element it will propose again. These are
# the two ways that happened: a kind the renderer did not know, and a baseline
# the run was never handed.
# ============================================================================


def _node_graph(*pairs) -> object:
    """A graph of bare nodes, for the renderer — which walks nodes, not assertions."""
    graph = KnowledgeGraph()
    for kind, label in pairs:
        graph.add_node(kind, label)
    return graph


def test_an_enterprise_platform_is_shown_as_an_element_to_extend():
    """`Platform` is how an O365 subscription arrives from a requirements document.

    Leaving the kind out of the architecture sections made every such element
    invisible: the design was asked to integrate with a platform it could not see,
    so it proposed a new external system instead of linking the one that existed.
    """
    section = architecture_digest(
        _node_graph(("Platform", "Enterprise Microsoft O365 Subscription"))
    )

    assert "Enterprise Microsoft O365 Subscription" in section.text


def test_a_platform_does_not_repeat_a_system_that_shares_its_name():
    """The same element often arrives under both kinds.

    "Payment Gateway Platform" and "Storefront" each exist as a Platform and as a
    C4-shaped node. Rendering the pair would show one element twice in a prompt
    whose whole purpose is to stop the model proposing a duplicate, so the
    C4-shaped kind keeps the name.
    """
    section = architecture_digest(
        _node_graph(("SoftwareSystem", "Payment Gateway Platform"),
                    ("Platform", "Payment Gateway Platform"))
    )

    assert section.text.count("Payment Gateway Platform") == 1


def test_the_dedupe_has_something_to_dedupe():
    """Two nodes in, one line out — otherwise the assertion above proves nothing."""
    graph = _node_graph(("SoftwareSystem", "Payment Gateway Platform"),
                        ("Platform", "Payment Gateway Platform"))

    assert len([n for n in graph.nodes.values()
                if n.label == "Payment Gateway Platform"]) == 2


def test_a_platform_that_shares_no_name_is_still_rendered():
    """The dedupe must not become a blanket exclusion — that was the original bug."""
    section = architecture_digest(
        _node_graph(("SoftwareSystem", "Payment Gateway Platform"),
                    ("Platform", "Enterprise Microsoft O365 Subscription"))
    )

    assert "Payment Gateway Platform" in section.text
    assert "Enterprise Microsoft O365 Subscription" in section.text


def _partly_promoted() -> object:
    """One element review has promoted, one still only proposed."""
    graph, _run = graph_from_extraction(
        {
            "elements": [
                {"name": "Accepted Service", "element_type": "Container",
                 "parent": "Payment Platform"},
                {"name": "Proposed Service", "element_type": "Container",
                 "parent": "Payment Platform"},
            ]
        },
        {"model_id": "fake", "document_type": "architecture"},
        document_ref="arch.md",
        document_text="arch body",
        initiative_id="INIT-1",
    )
    for assertion in graph.assertions.values():
        if "accepted" in str(assertion.subject):
            assertion.scope = SCOPE_BASELINE
    return graph


def test_the_promoted_scope_is_a_projection_not_a_copy_of_everything():
    """What review accepted, and only that."""
    projected = _partly_promoted().scoped(SCOPE_BASELINE)
    labels = {n.label for n in projected.nodes.values()}

    assert "Accepted Service" in labels
    assert "Proposed Service" not in labels, (
        "an unpromoted element must not be offered as something the design already "
        "covers — that is how a proposal claims to extend what it cannot see"
    )
    assert projected.assertions
    assert all(a.scope == SCOPE_BASELINE for a in projected.assertions.values())


class _StubStore:
    """Just enough store for the resolution order, which is the thing under test."""

    def __init__(self, revision=None, graph=None):
        self._revision = revision
        self._graph = graph or KnowledgeGraph()

    def baselines(self):
        return [self._revision] if self._revision else []

    def load_revision(self, revision_id):
        return SimpleNamespace(graph=self._graph)


def test_the_promoted_baseline_is_used_when_no_revision_was_frozen():
    """A promoted baseline is a baseline even though nothing was frozen.

    Before this, a design was handed the working set instead — extending a draft
    nobody had signed off, with facts still under review mixed into the
    architecture it was told already existed.
    """
    resolved = resolve_design_baseline(_StubStore(), _partly_promoted())

    assert resolved.promoted is True
    assert resolved.ref == SCOPE_BASELINE
    assert resolved.graph is not None
    labels = {n.label for n in resolved.graph.nodes.values()}
    assert "Accepted Service" in labels
    assert "Proposed Service" not in labels


def test_a_frozen_revision_outranks_the_promoted_baseline():
    """A snapshot with a frozen-at guarantee beats the live accepted set."""
    frozen = _node_graph(("Container", "Frozen Container"))
    store = _StubStore(revision=SimpleNamespace(id="rev_arc_v1", label="ARC-G v1"),
                       graph=frozen)

    resolved = resolve_design_baseline(store, _partly_promoted())

    assert resolved.ref == "rev_arc_v1"
    assert resolved.label == "ARC-G v1"
    assert resolved.promoted is False
    assert {n.label for n in resolved.graph.nodes.values()} == {"Frozen Container"}


def test_nothing_promoted_and_nothing_frozen_resolves_to_no_baseline():
    """Rather than quietly presenting the working set as if it were accepted."""
    graph, _run = graph_from_extraction(
        {"elements": [{"name": "Draft Service", "element_type": "Container"}]},
        {"model_id": "fake", "document_type": "architecture"},
        document_ref="arch.md",
        document_text="arch body",
        initiative_id="INIT-1",
    )

    resolved = resolve_design_baseline(_StubStore(), graph)

    assert resolved.graph is None
    assert resolved.ref == ""
    assert resolved.promoted is False


def test_a_promoted_baseline_is_named_as_promoted_rather_than_frozen():
    """The two promise different things, so the prompt may not blur them."""
    graph = _partly_promoted()
    digest = design_input(graph, baseline=graph.scoped(SCOPE_BASELINE),
                          base_ref=SCOPE_BASELINE, promoted_baseline=True)

    assert digest.base_ref == SCOPE_BASELINE
    assert any("PROMOTED baseline" in c for c in digest.caveats), digest.caveats
    assert "PROMOTED baseline" in digest.text, "the caveat must reach the prompt"


def test_a_frozen_baseline_gets_no_promoted_caveat():
    """Or every frozen run would carry a warning that does not apply to it."""
    baseline = architecture_graph()
    digest = design_input(requirement_graph(), baseline=baseline,
                          base_ref="rev_arc_v1")

    assert not any("PROMOTED baseline" in c for c in digest.caveats), digest.caveats
