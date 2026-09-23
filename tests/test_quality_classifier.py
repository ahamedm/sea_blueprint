"""
Deterministic quality classification.

Measured motivation: with the full ISO/IEC 25010:2023 taxonomy in the prompt, the
model left `quality_category`, `subcharacteristic` and `quality_attribute` empty on
**every** NFR, and invented its own attribute names (`Latency`, `Throughput`,
`Usability`, `Observability`) which landed as untyped `Concept` nodes outside the
vocabulary the auditor reads. Mapping wording onto a closed taxonomy is keyword and
shape matching, so it is done deterministically here.

The tests that matter are the false positives. A classifier that labels everything
looks effective and is worse than none, because its output becomes the category an
audit reasons over. Several of these assert that a requirement is left
UNCLASSIFIED, and the reasons are recorded because each was a real misclassification
found by running against the fixture.
"""

from __future__ import annotations

import pytest

from agents.extraction.quality import (
    MIN_SCORE_FUNCTIONAL,
    QUALITY_SIGNALS,
    classify_requirement,
    enrich_entities,
    humanise,
    is_requirement_entity,
    source_passage,
)

REPO_ROOT_SOURCE = "test_data/prd/payment_platform_brief.md"


@pytest.fixture(scope="module")
def brief():
    from tests.conftest import REPO_ROOT

    return (REPO_ROOT / REPO_ROOT_SOURCE).read_text()


# ============================================================================
# The taxonomy the classifier targets
# ============================================================================


def test_every_target_is_a_real_iso_subcharacteristic(ontology):
    """A classifier that invents its own categories is no better than the model
    inventing attribute names — it would put terms in the graph that the reference
    view cannot resolve."""
    real = set(ontology.enums["QualitySubcharacteristic"].values)
    unknown = sorted(set(QUALITY_SIGNALS) - real)
    assert unknown == [], f"not ISO 25010 sub-characteristics: {unknown}"


def test_every_signal_pattern_compiles():
    import re

    for subcharacteristic, patterns in QUALITY_SIGNALS.items():
        for pattern in patterns:
            re.compile(pattern), f"{subcharacteristic}: {pattern}"
        assert patterns, f"{subcharacteristic} has no signals"


def test_the_characteristic_is_derived_from_the_ontology_taxonomy():
    """Not restated here, so the sub-characteristic→characteristic mapping has one
    definition and cannot drift from the enum."""
    verdict = classify_requirement("The system must achieve 99.99% uptime.")
    assert verdict.subcharacteristic == "AVAILABILITY"
    assert verdict.characteristic == "RELIABILITY"


def test_humanised_names_read_as_the_ontology_names_them():
    assert humanise("TIME_BEHAVIOUR") == "Time Behaviour"
    assert humanise("NON_REPUDIATION") == "Non Repudiation"


# ============================================================================
# It classifies real requirement text
# ============================================================================


@pytest.mark.parametrize(
    "text,expected",
    [
        ("95% of all payment authorization requests must complete within 500 milliseconds.",
         "TIME_BEHAVIOUR"),
        ("Processing a minimum of 1000 transactions per second with horizontal scaling.",
         "SCALABILITY"),
        ("All Cardholder Data must be encrypted in transit and at rest using AES-256.",
         "CONFIDENTIALITY"),
        ("Role-Based Access Control (RBAC) must be enforced across all interfaces.",
         "ACCOUNTABILITY"),
        ("The system must provide uptime of 99.99% with active-active redundancy.",
         "AVAILABILITY"),
        ("Requirements must be traceable and auditable end to end.",
         "ACCOUNTABILITY"),
        ("Comprehensive logging, metrics and monitoring across all services.",
         "ANALYSABILITY"),
        ("The platform must integrate with external providers over gRPC.",
         "INTEROPERABILITY"),
    ],
)
def test_representative_requirements_classify_correctly(text, expected):
    assert classify_requirement(text).subcharacteristic == expected


def test_scalability_outranks_throughput_when_both_are_mentioned():
    """The measured misclassification that forced weighted signals.

    "processing a minimum of [X] transactions per second (TPS) with horizontal
    scaling capabilities" hit `throughput`, `per second`, `tps` and `seconds` —
    four signals for TIME_BEHAVIOUR — against one for SCALABILITY. Counting terms
    picked the wrong axis: the requirement NAMES scaling and mentions throughput
    as a quantity. Weighting the precise signal over the incidental one fixes it.
    """
    verdict = classify_requirement(
        "The platform must process 1000 transactions per second with horizontal scaling."
    )
    assert verdict.subcharacteristic == "SCALABILITY"


# ============================================================================
# The false positives — the tests that keep it honest
# ============================================================================


@pytest.mark.parametrize(
    "text,reason",
    [
        ("Orders are dispatched when ready.",
         "no quality signal at all"),
        ("",
         "empty text is not a category"),
        ("The system handles things.",
         "ordinary prose contains no quality concern"),
    ],
)
def test_text_with_no_quality_signal_is_left_unclassified(text, reason):
    """Reporting "cannot classify" beats a confident wrong category, because the
    wrong one silently becomes the answer an audit reasons over."""
    verdict = classify_requirement(text)
    assert not verdict.is_classified, f"should be unclassified: {reason}"


@pytest.mark.parametrize(
    "text,reason",
    [
        ("The PGP shall track the current state of every payment request "
         "(PENDING, AUTHORIZED, CAPTURED, FAILED, SETTLED).",
         "ACCOUNTABILITY matched `authoriz` in AUTHORIZED — a payment STATE, not access control"),
        ("Initiate settlement requests based on transaction volume and time triggers.",
         "CAPACITY matched `volume`, which describes a trigger, not a capacity goal"),
    ],
)
def test_a_functional_requirement_is_not_classified_on_one_incidental_word(text, reason):
    """The measured false positives.

    Both are domain vocabulary mistaken for quality vocabulary — which is the
    failure mode of keyword matching, and precisely why a functional requirement
    must clear a higher bar. Asserted through `enrich_entities`, which is where
    the entity's KIND is known; `classify_requirement` alone cannot tell an FR
    from an NFR and so applies the lighter rule.
    """
    entity = {
        "name": "FR-X-001",
        "ontology_class": "FunctionalRequirement",
        "requirement_id": "FR-X-001",
    }
    # The passage is the entity's own text, so the classifier sees exactly the
    # sentence under test.
    out = enrich_entities([entity], text.replace("FR-X-001", ""))
    assert not out[0].get("subcharacteristic"), f"should be unclassified: {reason}"


def test_a_functional_requirement_needs_a_stronger_signal_than_an_nfr(brief):
    """Functional requirements carry no quality attribute by definition, so one
    keyword should not attach one. Measured: three FRs were classified on a single
    incidental word each."""
    entities = [
        {"name": "FR-X-001", "ontology_class": "FunctionalRequirement",
         "requirement_id": "FR-X-001"},
        {"name": "NFR-X-001", "ontology_class": "NonFunctionalRequirement",
         "requirement_id": "NFR-X-001"},
    ]
    source = "FR-X-001 track payment state. NFR-X-001 comprehensive logging and metrics."
    enriched = {e["requirement_id"]: e for e in enrich_entities(entities, source)}

    # The FR's single weak signal does not qualify.
    assert not enriched["FR-X-001"].get("subcharacteristic")
    # The NFR's purpose IS to state a quality concern, so its signals count.
    assert enriched["NFR-X-001"].get("subcharacteristic") == "ANALYSABILITY"
    assert MIN_SCORE_FUNCTIONAL > 1


def test_observability_lands_on_an_axis_that_can_hold_it(brief):
    """ISO 25010 has no "Observability" characteristic.

    Measured: an NFR about logging, metrics and monitoring was classified
    TIME_BEHAVIOUR on the single word `real-time`. It belongs under
    MAINTAINABILITY/ANALYSABILITY — what makes a system analysable in production —
    and placing it there is better than leaving a clear NFR unclassified.
    """
    passage = source_passage(brief, "NFR-UM-002")
    verdict = classify_requirement(passage)
    assert verdict.characteristic == "MAINTAINABILITY"
    assert verdict.subcharacteristic == "ANALYSABILITY"


# ============================================================================
# Locating a requirement's text
# ============================================================================


def test_the_passage_does_not_leak_into_the_next_requirement(brief):
    """One requirement's keywords must not classify its neighbour."""
    passage = source_passage(brief, "NFR-PS-001")
    assert "NFR-PS-002" not in passage
    assert "500 milliseconds" in passage


def test_the_passage_survives_its_own_identifier_being_repeated(brief):
    """Measured bug: `**NFR-SC-001 (PCI-DSS Compliance):** ... PCI-DSS` mentions its
    identifier twice on one line, and a naive cut at the next identifier returned
    `"**NFR-SC-001 ("` — classified as nothing, from text that is almost entirely
    security keywords. The first line is never a boundary."""
    passage = source_passage(brief, "NFR-SC-001")
    assert "PCI-DSS" in passage
    assert "Cardholder Data" in passage
    assert len(passage) > 100


def test_the_passage_includes_the_heading_above_it(brief):
    """Section headings are often the clearest signal — `### Performance
    Requirements` says more than the sentence under it."""
    passage = source_passage(brief, "NFR-PS-001")
    assert passage.strip()


def test_an_absent_requirement_yields_no_passage():
    assert source_passage("some text", "NFR-ZZ-999") == ""
    assert source_passage("", "FR-1") == ""


def test_only_requirement_entities_are_touched():
    assert is_requirement_entity({"ontology_class": "NonFunctionalRequirement"})
    assert is_requirement_entity({"requirement_id": "FR-PM-001"})
    assert not is_requirement_entity({"ontology_class": "Container"})
    assert not is_requirement_entity({"ontology_class": "System", "name": "PGP"})


# ============================================================================
# Enrichment semantics
# ============================================================================


def test_the_models_own_classification_is_never_overwritten():
    """Where the model committed to a category it saw the document, and that is
    better evidence than a keyword score. This exists because it commits to
    nothing — not to overrule it."""
    entity = {
        "name": "NFR-PS-001",
        "ontology_class": "NonFunctionalRequirement",
        "requirement_id": "NFR-PS-001",
        "quality_category": "RELIABILITY",
        "subcharacteristic": "AVAILABILITY",
        "quality_attribute": "Availability",
    }
    out = enrich_entities([entity], "95% within 500 milliseconds latency")
    assert out[0]["subcharacteristic"] == "AVAILABILITY"
    assert out[0].get("quality_classification_score") is None


def test_non_requirement_entities_are_untouched(brief):
    entities = [
        {"name": "Payment Gateway Platform", "ontology_class": "System"},
        {"name": "PostgreSQL", "ontology_class": "DataStore"},
    ]
    out = enrich_entities(entities, brief)
    assert all(not e.get("subcharacteristic") for e in out)


def test_the_full_fixture_classifies_every_nfr_and_no_functional_requirement(brief):
    """The end-to-end claim on real data.

    All eight NFRs gain a classification, and no functional requirement does —
    which is the property that makes the output trustworthy. A classifier that
    also labelled FRs would be attaching quality attributes to requirements that
    do not carry them.
    """
    import re

    ids = sorted(set(re.findall(r"\b(?:FR|NFR)-[A-Z]+-\d+\b", brief)))
    entities = [
        {
            "name": i,
            "ontology_class": (
                "NonFunctionalRequirement" if i.startswith("NFR") else "FunctionalRequirement"
            ),
            "requirement_id": i,
        }
        for i in ids
    ]
    enriched = {e["requirement_id"]: e for e in enrich_entities(entities, brief)}

    nfrs = [i for i in ids if i.startswith("NFR")]
    frs = [i for i in ids if not i.startswith("NFR")]

    missing = [i for i in nfrs if not enriched[i].get("subcharacteristic")]
    assert missing == [], f"NFRs left unclassified: {missing}"

    wrongly = [i for i in frs if enriched[i].get("subcharacteristic")]
    assert wrongly == [], f"functional requirements given a quality attribute: {wrongly}"

    # And the security NFRs land on security, which is the result a reader checks.
    for security in ("NFR-SC-001", "NFR-SC-002", "NFR-SC-003"):
        assert enriched[security]["quality_category"] == "SECURITY"


# ============================================================================
# The agent applies it
# ============================================================================


def test_the_extraction_agent_classifies_what_it_extracts(monkeypatch):
    """The integration, not just the function.

    A working classifier that nothing calls is the same inert-layer failure this
    project has already hit twice (the `domain` field, then the predicate names).
    This drives `KnowledgeExtractionAgent.run` with the model stubbed out and
    asserts the entities come back classified.
    """
    import yaml

    from agents.base_agent import AgentConfig
    from agents.knowledge_extraction.agent import (
        ExtractedEntity,
        ExtractionResult,
        KnowledgeExtractionAgent,
    )

    source = (
        "### Performance Requirements\n"
        "NFR-PS-001 (Latency): 95% of requests must complete within 500 milliseconds.\n"
        "### Security Requirements\n"
        "NFR-SC-001 (PCI-DSS): All Cardholder Data must be encrypted using AES-256.\n"
    )

    payload = ExtractionResult(
        triples=[],
        entities=[
            ExtractedEntity(name="NFR-PS-001", ontology_class="NonFunctionalRequirement",
                            requirement_id="NFR-PS-001"),
            ExtractedEntity(name="NFR-SC-001", ontology_class="NonFunctionalRequirement",
                            requirement_id="NFR-SC-001"),
        ],
    )

    agent = KnowledgeExtractionAgent.__new__(KnowledgeExtractionAgent)
    agent.config = AgentConfig(
        name="probe", description="probe",
        ontology_path="ontology/requirements_base.yaml", ontology_dir="ontology",
        use_structured_output=True,
    )
    from agents.base_agent import console

    agent.console = console
    agent.ontology = yaml.safe_load(open("ontology/requirements_base.yaml"))
    agent.domain_pack = None
    agent.agent = None
    agent.confidence_threshold = 0.7

    # Structured output returns the payload; nothing reaches a model.
    monkeypatch.setattr(agent, "invoke_structured", lambda *_a, **_k: payload)

    result = agent.run({"document": source, "document_type": "requirements"})
    assert result.success
    entities = (result.output or {}).get("entities") or []
    by_id = {e.get("requirement_id"): e for e in entities}

    assert by_id["NFR-PS-001"]["quality_category"] == "PERFORMANCE_EFFICIENCY"
    assert by_id["NFR-PS-001"]["subcharacteristic"] == "TIME_BEHAVIOUR"
    assert by_id["NFR-SC-001"]["quality_category"] == "SECURITY"
    assert by_id["NFR-SC-001"]["subcharacteristic"] == "CONFIDENTIALITY"
    # The readable attribute name, not just the enum — this is what becomes the
    # QualityAttribute node in the graph.
    assert by_id["NFR-SC-001"]["quality_attribute"] == "Confidentiality"


# ============================================================================
# The inventory — why classification does not depend on the model
# ============================================================================


def test_the_inventory_finds_every_requirement_the_source_identifies(brief):
    """Read from the source, not requested from the model.

    Measured on two runs of this same document with the same config: one emitted
    18 of 18 identifiers, the next emitted 0 of 18. Anything that keys off the
    model's id output is therefore non-deterministic by construction.
    """
    import re

    from agents.extraction.quality import requirement_inventory

    inventory = requirement_inventory(brief)
    in_source = set(re.findall(r"\b(?:FR|NFR)-[A-Z]+-\d+\b", brief))
    # Asserted against the identifiers the SOURCE contains, not a guessed range:
    # the fixture has FR-PM-001..003 and FR-TR-001..004, so a `range(1, 5)`
    # expectation invented two requirements that were never written.
    assert {r.identifier for r in inventory} == in_source
    assert len(inventory) == 18


def test_the_inventory_reads_both_document_shapes(brief):
    """A Markdown table row and an indented bullet, which the fixtures use."""
    from agents.extraction.quality import requirement_inventory

    inventory = {r.identifier: r for r in requirement_inventory(brief)}
    # Table row: | **FR-SR-003** | **Reporting** | ... |
    assert inventory["FR-SR-003"].name == "Reporting"
    # Bullet: *   **NFR-PS-001 (Latency):** ...
    assert inventory["NFR-PS-001"].name == "Latency"


def test_the_inventory_classifies_every_nfr_and_no_functional_requirement(brief):
    """The property that makes the output trustworthy, asserted on the inventory
    rather than on the model's entities — so it holds on a run where the model
    emitted nothing useful."""
    from agents.extraction.quality import requirement_inventory

    inventory = requirement_inventory(brief)
    nfr_missing = [
        r.identifier for r in inventory
        if r.identifier.startswith("NFR") and not r.classification.is_classified
    ]
    fr_classified = [
        r.identifier for r in inventory
        if not r.identifier.startswith("NFR") and r.classification.is_classified
    ]
    assert nfr_missing == [], f"NFRs unclassified: {nfr_missing}"
    assert fr_classified == [], f"FRs given a quality attribute: {fr_classified}"


@pytest.mark.parametrize(
    "token,reason",
    [
        ("AES-256", "a cipher, not a requirement key"),
        ("TLS-1.2", "a protocol version"),
        ("PCI-DSS", "a standard"),
        ("v2", "not identifier-shaped"),
    ],
)
def test_non_requirement_identifiers_are_rejected(token, reason):
    """Measured: when the model was asked to supply identifiers it emitted `CHD`
    and `PGP`. Doing this deterministically must not repeat that — a standard's
    name in the identifier column is worse than an empty one."""
    from agents.extraction.quality import _plausible_identifier

    assert not _plausible_identifier(token), reason


def test_an_entity_named_without_an_id_is_matched_to_its_source_requirement(brief):
    """The failing-run case.

    One run produced entities named `Latency` and `Data Encryption` with no
    identifiers, so nothing was classified. Name matching against the inventory
    recovers both the identifier and the category.
    """
    from agents.extraction.quality import enrich_entities

    entities = [
        {"name": "Latency", "ontology_class": "NonFunctionalRequirement"},
        {"name": "Data Encryption", "ontology_class": "NonFunctionalRequirement"},
    ]
    enriched = {e["name"]: e for e in enrich_entities(entities, brief)}

    assert enriched["Latency"]["requirement_id"] == "NFR-PS-001"
    assert enriched["Latency"]["subcharacteristic"] == "TIME_BEHAVIOUR"
    assert enriched["Data Encryption"]["requirement_id"] == "NFR-SC-002"
    assert enriched["Data Encryption"]["subcharacteristic"] == "CONFIDENTIALITY"


def test_an_unrelated_entity_is_not_matched_to_a_requirement(brief):
    """The overlap threshold must not attach a requirement's category to an
    entity that merely shares a word with it."""
    from agents.extraction.quality import enrich_entities, match_to_inventory, requirement_inventory

    inventory = requirement_inventory(brief)
    for name in ("Payment Gateway Platform", "PostgreSQL", "B2C Storefront"):
        assert match_to_inventory({"name": name}, inventory) is None, name

    out = enrich_entities(
        [{"name": "Payment Gateway Platform", "ontology_class": "System"}], brief
    )
    assert not out[0].get("subcharacteristic")
    assert not out[0].get("requirement_id")


def test_the_classification_records_that_it_was_deterministic(brief):
    """Provenance matters here: a reader should be able to tell a keyword
    classification from one the model asserted."""
    from agents.extraction.quality import enrich_entities

    out = enrich_entities(
        [{"name": "NFR-SC-002", "ontology_class": "NonFunctionalRequirement",
          "requirement_id": "NFR-SC-002"}],
        brief,
    )
    assert out[0]["quality_classification_source"] == "deterministic_source_pass"
