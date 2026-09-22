"""
The ontology loader.

These tests are unusually concrete about counts and names because the subject
*matters*: get inheritance wrong and the reference view confidently mis-describes
the vocabulary the whole platform reasons in. The strongest test here is
`test_resolution_matches_linkml` — my own resolution is checked against LinkML's
authoritative parser across every class, rather than against my own expectations.
"""

import pytest

from core.ontology import LAYER_ORDER, OntologyError, load_ontology

# ============================================================================
# Shape of the whole thing
# ============================================================================


def test_all_four_layers_load_in_dependency_order(ontology):
    assert [layer.key for layer in ontology.layers] == [
        "common",
        "enterprise",
        "requirements",
        "architecture",
    ]


def test_layer_filenames_match_the_directory(ontology_dir):
    for _key, filename, _role in LAYER_ORDER:
        assert (ontology_dir / filename).exists(), filename


def test_totals(ontology):
    stats = ontology.stats()
    assert stats["classes"] == 58
    assert stats["enums"] == 37
    assert stats["subsets"] == 13
    assert stats["layers"] == 4
    assert stats["abstract"] == 3
    assert stats["mixins"] == 2


def test_classes_are_attributed_to_the_layer_that_declares_them(ontology):
    assert ontology.stats()["classes_per_layer"] == {
        "common": 4,
        "enterprise": 7,
        "requirements": 28,
        "architecture": 19,
    }
    assert ontology.get("Provenance").layer == "common"
    assert ontology.get("Product").layer == "enterprise"
    assert ontology.get("NonFunctionalRequirement").layer == "requirements"
    assert ontology.get("Container").layer == "architecture"


def test_imports_point_only_downward(ontology):
    """The one-way import rule is the reason the layers exist.

    If a lower layer could import a higher one, the vocabulary split would be
    circular and the two graphs could no longer share enterprise constructs.
    """
    order = {layer.key: index for index, layer in enumerate(ontology.layers)}
    for layer in ontology.layers:
        for imported in layer.imports:
            assert order[imported] < order[layer.key], f"{layer.key} imports {imported}"


def test_linkml_types_is_treated_as_external(ontology):
    """`linkml:types` is not one of our layers, so it must not appear as one."""
    for layer in ontology.layers:
        assert "linkml:types" not in layer.imports
        assert "linkml:types" in layer.external_imports


def test_the_schema_is_internally_consistent(ontology):
    """A reference view that hid its own dangling references would be worse than
    useless, so the loader collects them instead — and there are none."""
    assert ontology.unresolved_parents == []
    assert ontology.unresolved_ranges == []
    assert ontology.duplicate_names == []


# ============================================================================
# Taxonomy, mixins, inheritance
# ============================================================================


def test_abstract_classes_exist_only_to_be_inherited(ontology):
    abstract = {c.name for c in ontology.classes.values() if c.abstract}
    assert abstract == {"EnterpriseConstruct", "Requirement", "ArchitectureElement"}


def test_mixins_are_marked_as_mixins_not_supertypes(ontology):
    mixins = {c.name for c in ontology.classes.values() if c.mixin}
    assert mixins == {"ExternallyReferenced", "Provenanced"}
    # A mixin is not part of a taxonomy, so nothing should declare it as is_a.
    assert not any(c.is_a in mixins for c in ontology.classes.values())


def test_requirement_hierarchy(ontology):
    assert ontology.get("Requirement").abstract is True
    assert ontology.children("Requirement") == [
        "BusinessRequirement",
        "ConstraintRequirement",
        "FunctionalRequirement",
        "NonFunctionalRequirement",
    ]


def test_ancestors_include_mixins(ontology):
    """Mixins contribute slots, so they are supertypes for resolution purposes —
    while the view keeps them visually distinct from `is_a`."""
    assert ontology.ancestors("NonFunctionalRequirement") == [
        "NonFunctionalRequirement",
        "Requirement",
        "ExternallyReferenced",
        "Provenanced",
    ]


def test_a_root_class_has_only_itself_as_ancestor(ontology):
    assert ontology.ancestors("Actor") == ["Actor"]


def test_unknown_class_has_no_ancestors(ontology):
    assert ontology.ancestors("NoSuchClass") == []
    assert ontology.parents("NoSuchClass") == []


def test_inherited_slots_name_the_class_that_declares_them(ontology):
    """Where a slot comes from is part of reading a schema: `id` arriving from
    `EnterpriseConstruct` is a different fact from a locally-declared slot."""
    inherited = dict(
        (slot.name, owner)
        for owner, slot in ontology.inherited_attributes("NonFunctionalRequirement")
    )
    assert inherited["id"] == "Requirement"
    assert inherited["external_references"] == "ExternallyReferenced"
    assert inherited["provenance"] == "Provenanced"
    assert "quality_category" not in inherited, "that one is declared locally"


def test_effective_attributes_are_own_plus_inherited_without_duplicates(ontology):
    own = [s.name for s in ontology.own_attributes("NonFunctionalRequirement")]
    effective = [s.name for s in ontology.effective_attributes("NonFunctionalRequirement")]

    assert own == [
        "quality_category",
        "quality_scenario",
        "measurable",
        "target_value",
        "threshold_value",
        "measurement_unit",
        "measurement_method",
    ]
    assert len(effective) == len(set(effective))
    assert effective[: len(own)] == own, "own slots come first"


# ============================================================================
# Slots and relationships
# ============================================================================


def test_slot_metadata_is_preserved(ontology):
    quality_category = next(
        s
        for s in ontology.own_attributes("NonFunctionalRequirement")
        if s.name == "quality_category"
    )
    assert quality_category.required is True
    assert quality_category.range == "QualityAttributeCategory"
    assert quality_category.range_kind == "enum"
    assert quality_category.range_layer == "requirements"


def test_identifier_slots_are_recognised(ontology):
    ids = [s.name for s in ontology.own_attributes("Requirement") if s.identifier]
    assert ids == ["id"]


def test_relationships_are_slots_whose_range_is_a_class(ontology):
    # `relationships` returns (slot, target) pairs, so key by slot name explicitly.
    relationships = {
        slot.name: target for slot, target in ontology.relationships("BusinessRequirement")
    }
    assert relationships["traces_to_goals"] == "BusinessGoal"
    assert relationships["originates_from"] == "Initiative"
    assert (
        "business_justification" not in relationships
    ), "a primitive-ranged slot is not a relationship"

    for slot, target in ontology.relationships("BusinessRequirement"):
        assert slot.range_kind == "class"
        assert ontology.is_class(target), f"{slot.name} -> {target} is not a class"


def test_referenced_by_finds_the_requirement_realization_join(ontology):
    """`RequirementRealization` is the REQ<->ARC join, and the schema should make
    that visible through the slots that point at it."""
    point_at_requirement = dict(ontology.referenced_by("Requirement"))
    assert point_at_requirement.get("ArchitectureElement") == "implements_requirements"


def test_binding_classes_break_the_circular_import(ontology):
    """`System` lives in `enterprise_structure`, `BusinessCapability` in
    `requirements_base`. The upward link is declared in the higher layer as an
    explicit binding class rather than by importing downwards."""
    binding = ontology.get("SystemCapabilityBinding")
    assert binding is not None
    assert binding.layer == "requirements"
    bindings = {
        slot.name: target for slot, target in ontology.relationships("SystemCapabilityBinding")
    }
    assert bindings["system"] == "System"
    scopes = {slot.name: target for slot, target in ontology.relationships("SubProductScope")}
    assert scopes["sub_product"] == "SubProduct"


def test_every_slot_range_resolves_to_something_known(ontology):
    for spec in ontology.classes.values():
        for slot in spec.attributes:
            assert slot.range_kind in {"class", "enum", "primitive", "unknown"}
    # ...and none is 'unknown', asserted separately so a failure says which.
    unknown = [
        (c.name, s.name, s.range)
        for c in ontology.classes.values()
        for s in c.attributes
        if s.range_kind == "unknown"
    ]
    assert unknown == []


# ============================================================================
# Failure modes
# ============================================================================


def test_a_missing_directory_raises_clearly(tmp_path):
    with pytest.raises(OntologyError, match="ontology directory not found"):
        load_ontology(tmp_path / "nope")


def test_a_missing_layer_raises_clearly(tmp_path):
    (tmp_path / "sea_common.yaml").write_text("id: x\nclasses: {}\n")
    with pytest.raises(OntologyError, match="missing ontology layer"):
        load_ontology(tmp_path)


def test_invalid_yaml_raises_clearly(tmp_path):
    for _key, filename, _role in LAYER_ORDER:
        (tmp_path / filename).write_text("id: x\nclasses: {}\n")
    (tmp_path / "requirements_base.yaml").write_text("id: [unclosed\n")
    with pytest.raises(OntologyError, match="not valid YAML"):
        load_ontology(tmp_path)


# ============================================================================
# The oracle: my resolution versus LinkML's
# ============================================================================


def test_resolution_matches_linkml(ontology, ontology_dir):
    """Check every class against LinkML's own parser.

    Asserting against my own expectations would only prove I am consistently wrong.
    This is the authoritative implementation of the same semantics, so agreement
    across all 58 classes is the real guarantee.
    """
    schema_view = pytest.importorskip("linkml_runtime").SchemaView(
        str(ontology_dir / "architecture_base.yaml")
    )
    schema_view.merge_imports()

    ancestor_mismatches = []
    slot_mismatches = []

    for name in sorted(ontology.classes):
        if set(schema_view.class_ancestors(name)) != set(ontology.ancestors(name)):
            ancestor_mismatches.append(name)

        theirs = {slot.name for slot in schema_view.class_induced_slots(name)}
        ours = {slot.name for slot in ontology.effective_attributes(name)}
        if theirs != ours:
            slot_mismatches.append((name, sorted(ours ^ theirs)[:5]))

    assert ancestor_mismatches == []
    assert slot_mismatches == []
