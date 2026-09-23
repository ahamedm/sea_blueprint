"""
The ISO/IEC 25010:2023 quality model.

Three things are defended here, and they fail in different directions:

1. **The taxonomy matches the standard we claim.** The enum says "ISO/IEC
   25010:2023" in its own description, so a value left over from the 2011 model
   is a false claim, not a cosmetic drift. `PORTABILITY` was exactly that.

2. **The table and the enums cannot disagree.** The characteristic →
   sub-characteristic grouping lives in `core.ontology.ISO_25010_2023`, because
   an enum cannot express grouping. If it drifts from the enums, the view
   confidently mis-groups a quality concern and nothing anywhere fails.

3. **A quality attribute is a node, not just an enum value.** Without the node,
   realization could only ever point at a requirement instance — so a technique
   could never be justified by an attribute no requirement states, which is the
   coverage gap the auditor exists to find.
"""

from __future__ import annotations

import pytest

from core.ontology import (
    ISO_25010_2023,
    NON_ISO_QUALITY_CONCERNS,
    SUBCHARACTERISTIC_PARENT,
    load_ontology,
    quality_attribute_catalog,
    quality_attribute_payload,
    quality_characteristics,
    quality_iso_characteristics,
    quality_model_findings,
    quality_non_iso_characteristics,
    quality_subcharacteristics,
)

# The nine characteristics of ISO/IEC 25010:2023, in the standard's order.
ISO_2023_CHARACTERISTICS = [
    "FUNCTIONAL_SUITABILITY",
    "PERFORMANCE_EFFICIENCY",
    "COMPATIBILITY",
    "INTERACTION_CAPABILITY",
    "RELIABILITY",
    "SECURITY",
    "MAINTAINABILITY",
    "FLEXIBILITY",
    "SAFETY",
]


@pytest.fixture(scope="module")
def ontology_by_path():
    from tests.conftest import REPO_ROOT

    return load_ontology(REPO_ROOT / "ontology")


# ============================================================================
# The taxonomy matches the standard
# ============================================================================


def test_the_iso_characteristics_are_the_standard_nine():
    assert quality_iso_characteristics() == ISO_2023_CHARACTERISTICS


def test_the_enum_carries_the_nine_plus_the_governance_concern(ontology):
    """Ten values, nine of them ISO. REGULATORY_COMPLIANCE is the extra one and is
    labelled as governance rather than silently counted as product quality."""
    assert quality_characteristics(ontology) == ISO_2023_CHARACTERISTICS + [
        "REGULATORY_COMPLIANCE"
    ]
    assert quality_non_iso_characteristics(ontology) == ["REGULATORY_COMPLIANCE"]


def test_portability_is_gone_and_flexibility_is_present(ontology):
    """The 2023 revision replaced Portability with Flexibility.

    This is the assertion that keeps the schema's own version claim honest. Under
    the 2011 model the nearest home for "horizontal scaling" was PORTABILITY,
    which is simply wrong under 2023 — scalability is a Flexibility
    sub-characteristic, and leaving the old name would silently mis-classify it.
    """
    values = quality_characteristics(ontology)
    assert "FLEXIBILITY" in values
    assert "PORTABILITY" not in values


def test_interaction_capability_is_the_2023_name(ontology):
    """Usability was renamed in 2023. Both names appearing would be a split
    taxonomy — the same concern reachable by two values."""
    values = quality_characteristics(ontology)
    assert "INTERACTION_CAPABILITY" in values
    assert "USABILITY" not in values


def test_regulatory_compliance_is_kept_but_not_claimed_as_iso(ontology):
    """It is an enterprise-governance concern we need — PCI-DSS, GDPR, internal
    standards — and the standard does not define it. Folding it into an enum
    whose description says "ISO 25010" would make that description false, so the
    concern class records where it actually comes from."""
    assert "REGULATORY_COMPLIANCE" in quality_characteristics(ontology)
    assert NON_ISO_QUALITY_CONCERNS["REGULATORY_COMPLIANCE"] == "ENTERPRISE_GOVERNANCE"

    concern = ontology.enums["QualityConcernClass"].values
    assert concern == ["ISO_25010_2023", "ENTERPRISE_GOVERNANCE"]


def test_the_subcharacteristic_count_is_right(ontology):
    subs = quality_subcharacteristics(ontology)
    assert len(subs) == 40
    assert len(set(subs)) == 40, "duplicate sub-characteristic values"


def test_the_specific_subcharacteristics_that_drive_design_choices(ontology):
    """The ones that separate concerns met by entirely different designs.

    "within 500ms" and "1000 TPS with horizontal scaling" are both
    PERFORMANCE_EFFICIENCY at the top level, but they are TIME_BEHAVIOUR and
    SCALABILITY — and one is answered by caching while the other needs
    statelessness plus replication. A model that stops at the top level cannot
    tell those apart.
    """
    subs = quality_subcharacteristics(ontology)
    for needed in ("TIME_BEHAVIOUR", "SCALABILITY", "AVAILABILITY",
                   "FAULT_TOLERANCE", "CONFIDENTIALITY", "RECOVERABILITY"):
        assert needed in subs, needed


# ============================================================================
# The table and the enums cannot disagree
# ============================================================================


def test_the_table_and_the_enums_agree(ontology):
    """The check that makes the grouping trustworthy.

    The grouping is declared in code because an enum cannot express it. That is
    exactly why it needs asserting: drift produces a page that puts Scalability
    under the wrong characteristic and a reader with no reason to doubt it.
    """
    assert quality_model_findings(ontology) == []


def test_every_subcharacteristic_has_exactly_one_parent(ontology):
    """`dict(ISO_25010_2023)` would silently drop a duplicate, so check the raw
    pairs: a sub-characteristic claimed by two characteristics is a taxonomy
    defect that a lookup table cannot represent."""
    pairs = [sub for _char, subs in ISO_25010_2023 for sub in subs]
    assert len(pairs) == len(set(pairs)), "a sub-characteristic is claimed twice"
    assert set(SUBCHARACTERISTIC_PARENT) == set(pairs)


def test_the_table_covers_every_iso_subcharacteristic(ontology):
    """Every sub-characteristic that is not from ISO must be explained.

    Guards the reverse drift: a value added to the enum but claimed by no
    characteristic would be unreachable from the grouped view.
    """
    declared = set(quality_subcharacteristics(ontology))
    assert set(SUBCHARACTERISTIC_PARENT) == declared


def test_the_model_reports_drift_rather_than_hiding_it():
    """A deliberately broken model must produce findings.

    Asserted against a stub rather than the real schema, so the guard itself is
    tested — a validator that can only ever return [] proves nothing.
    """

    class _Enum:
        def __init__(self, values):
            self.values = values

    class _Stub:
        enums = {
            "QualityAttributeCategory": _Enum(["RELIABILITY"]),  # missing eight
            "QualitySubcharacteristic": _Enum(["AVAILABILITY", "NOT_A_REAL_ONE"]),
        }

    findings = quality_model_findings(_Stub())
    assert any("not in the enum" in f for f in findings)
    assert any("NOT_A_REAL_ONE" in f for f in findings)


def test_the_catalog_groups_subcharacteristics_under_their_characteristic(ontology):
    catalog = {entry["characteristic"]: entry for entry in quality_attribute_catalog(ontology)}

    assert catalog["FLEXIBILITY"]["subcharacteristics"] == [
        "ADAPTABILITY", "SCALABILITY", "INSTALLABILITY", "REPLACEABILITY",
    ]
    assert catalog["PERFORMANCE_EFFICIENCY"]["subcharacteristics"] == [
        "TIME_BEHAVIOUR", "RESOURCE_UTILIZATION", "CAPACITY",
    ]
    # Reliability is the one that carries Availability — the attribute the
    # redundancy technique exists to deliver.
    assert "AVAILABILITY" in catalog["RELIABILITY"]["subcharacteristics"]


def test_the_payload_is_serialisable_and_complete(ontology):
    import json

    payload = quality_attribute_payload(ontology)
    json.dumps(payload)
    assert payload["subcharacteristic_count"] == 40
    assert payload["attribute_class_declared"] is True
    assert payload["findings"] == []
    # Regulatory compliance appears in the catalog but is labelled as not-ISO.
    reg = next(e for e in payload["catalog"] if e["characteristic"] == "REGULATORY_COMPLIANCE")
    assert reg["concern_class"] == "ENTERPRISE_GOVERNANCE"


# ============================================================================
# A quality attribute is a node
# ============================================================================


def test_quality_attribute_exists_and_is_declared_at_the_right_layer(ontology):
    spec = ontology.get("QualityAttribute")
    assert spec is not None
    assert spec.layer == "requirements"


def test_an_nfr_points_at_an_attribute_not_only_an_enum(ontology):
    """The primary quality link is a node reference.

    An enum value is not a thing anything can point at, so realization had to
    hang off the requirement INSTANCE. Two NFRs about the same attribute then
    looked unrelated, and a technique could not be justified by an attribute no
    requirement stated.
    """
    slot = next(
        s for s in ontology.get("NonFunctionalRequirement").attributes
        if s.name == "realizes_attribute"
    )
    assert slot.range == "QualityAttribute"
    assert slot.range_kind == "class"


def test_the_attribute_keeps_the_enum_as_a_fallback(ontology):
    """Extraction can always fill an enum and only sometimes resolve a node, so
    both are kept — the enum survives when the attribute node is not materialised."""
    nfr = {s.name: s for s in ontology.get("NonFunctionalRequirement").attributes}
    assert nfr["quality_category"].range == "QualityAttributeCategory"
    assert nfr["subcharacteristic"].range == "QualitySubcharacteristic"


def test_the_attribute_carries_both_iso_axes(ontology):
    attrs = {s.name: s for s in ontology.get("QualityAttribute").attributes}
    assert attrs["characteristic"].range == "QualityAttributeCategory"
    assert attrs["subcharacteristic"].range == "QualitySubcharacteristic"
    assert attrs["concern_class"].range == "QualityConcernClass"


def test_an_attribute_can_be_defined_at_either_iso_level(ontology):
    """`Reliability` (a characteristic) and `Availability` (its
    sub-characteristic) are both instances, related by `is_a`, so the taxonomy is
    a graph rather than a flat list — and an organisation can hang its own
    attribute beneath a standard one."""
    attrs = {s.name: s for s in ontology.get("QualityAttribute").attributes}
    assert attrs["parent_attribute"].range == "QualityAttribute"


def test_the_attribute_links_across_graphs_by_reference_not_by_range(ontology):
    """`realized_by` and `realized_by_techniques` name ARC-G concepts.

    They must stay strings: `ArchitectureElement` and `DesignTechnique` live in
    the architecture layer, ABOVE this one, and a class range would invert the
    one-way import rule. Same treatment as the domain pack's cross-graph links —
    reconciled at the graph level, never baked into the schema.
    """
    attrs = {s.name: s for s in ontology.get("QualityAttribute").attributes}
    for name in ("realized_by", "realized_by_techniques"):
        assert attrs[name].range_kind == "primitive", name
        assert attrs[name].multivalued


def test_the_architecture_layer_makes_the_typed_links(ontology):
    """The typed links live on the architecture side, where importing downward is
    legal: an element delivers an attribute, a technique delivers an attribute."""
    element = {s.name: s for s in ontology.get("ArchitectureElement").attributes}
    assert element["satisfies_attributes"].range == "QualityAttribute"
    assert element["satisfies_attributes"].range_kind == "class"

    technique = {s.name: s for s in ontology.get("DesignTechnique").attributes}
    assert "realizes_quality_attributes" in technique


def test_coverage_is_a_value_so_an_unknown_state_survives(ontology):
    """Kept as a string, the same posture as extraction statuses: a source that
    reports a state we did not anticipate should not be rejected."""
    attrs = {s.name: s for s in ontology.get("QualityAttribute").attributes}
    assert attrs["covered"].range_kind == "primitive"
    assert attrs["coverage_note"].range_kind == "primitive"


def test_quality_scenario_now_names_the_attribute_it_operationalises(ontology):
    """A scenario that cannot be matched to the attribute it measures is a story,
    not evidence."""
    attrs = {s.name: s for s in ontology.get("QualityScenario").attributes}
    assert attrs["attribute"].range == "QualityAttribute"


def test_the_scenario_class_is_honest_about_being_unpopulated(ontology):
    """Recorded rather than removed, but the description must say so — otherwise
    a reader assumes it works and wonders why every scenario list is empty."""
    description = ontology.get("QualityScenario").description
    assert "never populated" in description


# ============================================================================
# The taxonomy reaches extraction
# ============================================================================


def test_the_iso_taxonomy_is_injected_into_the_extraction_prompt(ontology_dir):
    """Naming the enum is not enough: a bare list of enum values carries no
    grouping, so the model cannot know SCALABILITY sits under FLEXIBILITY and
    either stops at the top level or invents a placement."""
    import yaml

    from agents.base_agent import console
    from agents.knowledge_extraction.agent import AgentConfig, KnowledgeExtractionAgent
    from core.ontology import load_domain_pack

    root = ontology_dir
    config = AgentConfig(
        name="probe", description="probe",
        ontology_path=str(root / "requirements_base.yaml"),
        ontology_dir=str(root),
    )
    agent = KnowledgeExtractionAgent.__new__(KnowledgeExtractionAgent)
    agent.config = config
    agent.console = console
    agent.ontology = yaml.safe_load((root / "requirements_base.yaml").read_text())
    agent.domain_pack = load_domain_pack(None, root)
    agent.agent = None

    context = agent._format_ontology_context()
    assert "ISO/IEC 25010:2023" in context
    assert "FLEXIBILITY: ADAPTABILITY, SCALABILITY" in context
    assert "RELIABILITY: FAULTLESSNESS, AVAILABILITY" in context
    # The non-ISO concern is labelled, so the model does not treat it as standard.
    assert "NOT ISO 25010" in context
    assert "PORTABILITY" not in context


def test_the_extraction_schema_carries_the_quality_fields():
    """The fields the model must fill, and the reason they are not `required`.

    `quality_category` was `required: true` in the ontology, which meant an NFR
    the model could not classify failed schema validation and retried to the turn
    cap — losing the entire pass over a classification field. Optional, with the
    enum as the always-fillable fallback, is strictly better than a lost pass.
    """
    from agents.knowledge_extraction.agent import ExtractedEntity

    fields = ExtractedEntity.model_fields
    for name in ("quality_category", "subcharacteristic", "quality_attribute"):
        assert name in fields, name
        assert fields[name].is_required() is False, name
