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


def test_all_layers_load_in_dependency_order(ontology):
    assert [layer.key for layer in ontology.layers] == [
        "common",
        "enterprise",
        "requirements",
        "governance",
        "architecture",
    ]


def test_layer_filenames_match_the_directory(ontology_dir):
    for _key, filename, _role in LAYER_ORDER:
        assert (ontology_dir / filename).exists(), filename


def test_totals(ontology):
    """Exact counts, deliberately brittle.

    A schema change should have to be acknowledged here rather than flowing
    through silently. An added class or enum is never an accident worth missing —
    it changes what extraction is told to look for, and therefore what the whole
    graph can contain.

    Current counts include the design-technique and convention layer
    (+2 classes: `DesignTechnique`, `EngineeringConvention`; +2 enums:
    `ConventionType`, `ConventionEnforcement`) and four new `PatternCategory`
    values, which do not change the enum count.

    `ConceptAttributeDataType` is the newest enum (+1, no new class). It is the
    logical data-type vocabulary for `ConceptAttribute`, added when that class
    finally became reachable in the flat shape — see
    `docs/todos/entries/YB-055-data-element-design.md`. It adds an enum and not a
    class because the class was already declared and merely unreachable, and the
    type axis is a closed list of ten rather than a hierarchy.

    `Regulation` and `Standard` are the newest two, and they add no enum: their
    slots are free text on purpose, because jurisdictions and issuing bodies are
    named by sources in more ways than a closed list can hold. Without them a
    document naming GDPR or PCI-DSS v4.0 had nowhere to put the instrument but
    `Concept` — the graph's own marker for "unclassified".

    The governance layer is the newest: +7 classes (`GovernanceInstrument`, and
    `Strategy`, `Principle`, `Policy`, `Control`, `Risk`, `StandardClause` beneath
    it), +6 enums, +2 subsets, and a fifth base layer. It is the enterprise's own
    instruments, as distinct from the external `Regulation`/`Standard` referents
    one layer down — see `ontology/governance_base.yaml`.
    """
    stats = ontology.stats()
    assert stats["classes"] == 70
    # 50 since `ArchitectureDocumentStatus` joined: an architecture description is
    # not DEFERRED or REJECTED the way a requirement is, so it stopped borrowing
    # `RequirementStatus`. 51 since `SharingScope` (YB-044) — an instance's sharing
    # topology is a property of the deployment, not of the software class.
    assert stats["enums"] == 51
    assert stats["subsets"] == 15
    assert stats["layers"] == 5
    assert stats["abstract"] == 4
    assert stats["mixins"] == 2


def test_classes_are_attributed_to_the_layer_that_declares_them(ontology):
    assert ontology.stats()["classes_per_layer"] == {
        "common": 4,
        "enterprise": 7,
        "requirements": 31,
        "governance": 7,
        "architecture": 21,
    }
    assert ontology.get("Provenance").layer == "common"
    assert ontology.get("Product").layer == "enterprise"
    assert ontology.get("NonFunctionalRequirement").layer == "requirements"
    # The named instruments a requirement answers to are generic enough to sit in
    # the base requirements layer: GDPR, ISO 27001 and PCI-DSS are not payment
    # vocabulary, and putting them in a domain pack would shape the base ontology
    # around one domain — the failure the layer split exists to prevent.
    assert ontology.get("Regulation").layer == "requirements"
    assert ontology.get("Standard").layer == "requirements"
    # The enterprise's OWN instruments are a layer above: a policy mandates a
    # requirement, and architecture conforms to a clause or realises a control.
    assert ontology.get("Policy").layer == "governance"
    assert ontology.get("Control").layer == "governance"
    assert ontology.get("StandardClause").layer == "governance"
    assert ontology.get("Container").layer == "architecture"
    assert ontology.get("DesignTechnique").layer == "architecture"
    assert ontology.get("EngineeringConvention").layer == "architecture"


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
    assert abstract == {
        "EnterpriseConstruct", "Requirement", "ArchitectureElement", "GovernanceInstrument",
    }


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
        # The quality model first: what it is about, then how it is classified.
        "realizes_attribute",
        "quality_category",
        "subcharacteristic",
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
    assert quality_category.range == "QualityAttributeCategory"
    assert quality_category.range_kind == "enum"
    assert quality_category.range_layer == "requirements"
    # Deliberately NOT required any more. It was, which meant a model that could
    # not classify an NFR failed schema validation and retried to the turn cap,
    # losing the whole pass over a classification field. The primary quality link
    # is now `realizes_attribute`, with the enum as the always-fillable fallback.
    assert quality_category.required is False

    realizes = next(
        s
        for s in ontology.own_attributes("NonFunctionalRequirement")
        if s.name == "realizes_attribute"
    )
    assert realizes.range == "QualityAttribute"
    assert realizes.range_kind == "class"
    assert realizes.range_layer == "requirements"


def test_identifier_slots_are_recognised(ontology):
    ids = [s.name for s in ontology.own_attributes("Requirement") if s.identifier]
    assert ids == ["id"]


def test_a_concept_attribute_has_a_flat_owner_and_a_logical_type(ontology):
    """YB-055: the class stops being reachable only through a nested slot.

    `ConceptAttribute` was declared and unpopulatable — `DomainConcept.key_attributes`
    is `inlined_as_list` and the extraction contract is flat, so nothing could ever
    emit one. The flat shape is the `concept` back-link, and the type axis is a
    closed logical vocabulary rather than free text, because the guard that checks
    it reads the vocabulary from here (the single-source-of-truth rule in
    `agents/extraction/validators.py`).

    Both halves are asserted together on purpose: a `concept` slot with no
    vocabulary would let `VARCHAR(255)` pass as a logical type, and a vocabulary
    with no owner slot leaves the class exactly as unreachable as it was.
    """
    slots = {slot.name: slot.range for slot in ontology.own_attributes("ConceptAttribute")}
    assert slots["concept"] == "DomainConcept"
    assert set(ontology.children("DomainConcept")) >= {"Regulation", "Standard"}

    data_types = ontology.enums["ConceptAttributeDataType"].values
    assert "STRING" in data_types and "IDENTIFIER" in data_types
    assert not any("(" in name or "VARCHAR" in name for name in data_types), (
        "a physical type in the logical vocabulary would make the guard that reads "
        "it enforce the wrong rule"
    )


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
    across every class is the real guarantee.

    One view per LAYER, not one for the whole chain. Governance and architecture are
    peers — architecture does not import governance, because it has no conformance
    slot yet and importing it would put the policy vocabulary into every
    architecture prompt (YB-007). So no single root reaches every class, and each
    class is checked against the layer that declares it.
    """
    linkml = pytest.importorskip("linkml_runtime")

    views = {}
    for _key, filename, _role in LAYER_ORDER:
        view = linkml.SchemaView(str(ontology_dir / filename))
        view.merge_imports()
        views[filename] = view
    layer_file = {layer.key: layer.filename for layer in ontology.layers}

    ancestor_mismatches = []
    slot_mismatches = []

    for name in sorted(ontology.classes):
        schema_view = views[layer_file[ontology.get(name).layer]]
        if set(schema_view.class_ancestors(name)) != set(ontology.ancestors(name)):
            ancestor_mismatches.append(name)

        theirs = {slot.name for slot in schema_view.class_induced_slots(name)}
        ours = {slot.name for slot in ontology.effective_attributes(name)}
        if theirs != ours:
            slot_mismatches.append((name, sorted(ours ^ theirs)[:5]))

    assert ancestor_mismatches == []
    assert slot_mismatches == []


# ============================================================================
# Design techniques and engineering conventions
# ============================================================================


def test_the_three_kinds_of_design_choice_are_distinct(ontology):
    """Style, technique, pattern and convention are four different questions.

    The temptation is to widen one enum and hold them all — which would destroy
    the property that makes `ArchitectureStyleName` useful (a comparable
    taxonomy). Each class is asserted to exist separately so a future merge has
    to be deliberate.
    """
    for name in ("ArchitectureStyle", "ArchitecturePattern", "DesignTechnique",
                 "EngineeringConvention"):
        assert ontology.get(name) is not None, name
        assert ontology.get(name).layer == "architecture"


def test_a_technique_realizes_an_nfr_across_the_layer_boundary(ontology):
    """The load-bearing link. It points from ARCH-G into REQ-G, which the one-way
    import rule permits (architecture imports requirements) and which is what
    makes an NFR realization checkable rather than merely asserted."""
    slot = next(
        s for s in ontology.get("DesignTechnique").attributes
        if s.name == "realizes_quality_attributes"
    )
    assert slot.range == "NonFunctionalRequirement"
    assert slot.range_kind == "class"
    assert slot.range_layer == "requirements"
    assert slot.multivalued


def test_both_patterns_and_techniques_claim_quality_attributes(ontology):
    """A pattern is adopted from a catalogue and a technique is a property the
    design exhibits, but both answer an NFR, so both carry the link."""
    for cls in ("ArchitecturePattern", "DesignTechnique"):
        names = {s.name for s in ontology.get(cls).attributes}
        assert "realizes_quality_attributes" in names, cls
        assert "mechanism" in names, cls


def test_a_technique_targets_a_quality_category_without_the_nfr(ontology):
    """At extraction time the NFR often is not resolvable, so the technique must
    be able to record the category it aims at — that is what lets the auditor
    flag a technique aimed at the wrong family."""
    slot = next(
        s for s in ontology.get("DesignTechnique").attributes if s.name == "quality_category"
    )
    assert slot.range == "QualityAttributeCategory"
    assert slot.range_layer == "requirements"


def test_a_convention_is_verifiable_where_a_technique_is_not(ontology):
    """The distinction that justifies a separate class: a convention has a
    machine-checkable `pattern`, so conformance is decidable. A technique has a
    `mechanism`, which is prose and needs judgement."""
    convention = {s.name for s in ontology.get("EngineeringConvention").attributes}
    assert {"pattern", "examples", "conformance", "enforcement", "applies_to_level"} <= convention

    technique = {s.name for s in ontology.get("DesignTechnique").attributes}
    assert "pattern" not in technique, "a technique is not a naming rule"
    assert "conformance" not in technique


def test_enforcement_and_conformance_are_separate_axes(ontology):
    """What is EXPECTED of every element in scope versus what was OBSERVED on
    each one. Collapsing them would make a mandatory rule with no violations
    indistinguishable from an advisory one nobody checked."""
    c = ontology.get("EngineeringConvention")
    enforcement = next(s for s in c.attributes if s.name == "enforcement")
    conformance = next(s for s in c.attributes if s.name == "conformance")
    assert enforcement.range == "ConventionEnforcement"
    assert conformance.range_kind == "primitive", "kept open so an unknown source value survives"
    assert ontology.enums["ConventionEnforcement"].values == [
        "MANDATORY", "RECOMMENDED", "ADVISORY", "TOOL_ENFORCED", "LEGACY_EXEMPT",
    ]


def test_availability_and_scalability_are_pattern_categories(ontology):
    """The categories 'Redundancy / Replicas' and 'Stateless Services' need.

    Without them the nearest home was DEPLOYMENT, which describes how a release
    is rolled out, not why a system survives a node loss.
    """
    values = ontology.enums["PatternCategory"].values
    for needed in ("AVAILABILITY", "SCALABILITY", "PERFORMANCE", "STANDARDS_CONFORMANCE"):
        assert needed in values, needed


def test_technique_category_reuses_pattern_category(ontology):
    """Reusing `PatternCategory` rather than adding a parallel enum: the families
    are the same, and a second axis would need keeping aligned for no gain."""
    slot = next(
        s for s in ontology.get("DesignTechnique").attributes if s.name == "technique_category"
    )
    assert slot.range == "PatternCategory"
    assert slot.range_kind == "enum"


# ============================================================================
# Predicate vocabulary — the axis that was never sent
# ============================================================================


def test_the_vocabulary_is_compiled_not_hand_written(ontology):
    """It comes from the schema's own relationship slots, so it cannot drift from
    the ontology the way a hand-maintained list does — which is exactly what the
    prompt's older predicate examples demonstrate, naming `traces_to_goal` while
    the schema declares `traces_to_goals`."""
    from core.ontology import relationship_predicates

    vocabulary = relationship_predicates(ontology)
    declared = {
        slot.name
        for spec in ontology.classes.values()
        for slot in spec.attributes
        if slot.range_kind == "class"
    }
    assert set(vocabulary) <= declared
    assert len(vocabulary) > 20, "the vocabulary is suspiciously small"


def test_field_like_slots_are_excluded_from_the_vocabulary(ontology):
    """A class-ranged slot is not automatically an edge.

    `assumptions`, `applications` and `activities` are RECORD FIELDS holding lists
    on one node, not links between nodes. Injecting them would teach the model to
    emit `--assumptions-->`, which is noise and wrong. All three would be offered
    by a naive filter, so this is the guard that keeps the heuristic honest.
    """
    from core.ontology import relationship_predicates

    vocabulary = relationship_predicates(ontology)
    for field_like in ("assumptions", "applications", "activities", "stakeholders"):
        assert field_like not in vocabulary, f"{field_like} is a record field, not an edge"


def test_the_vocabulary_carries_target_kinds(ontology):
    """A predicate whose range is known is checkable — and the reconciler already
    refuses a link to the wrong kind of thing, so telling the model the allowed
    target is cheaper than correcting it afterwards."""
    from core.ontology import relationship_predicates

    vocabulary = relationship_predicates(ontology)
    assert vocabulary["traces_to_goals"] == ["BusinessGoal"]
    assert vocabulary["realizes_attribute"] == ["QualityAttribute"]
    assert vocabulary["binds_to_system"] == ["System"]
    assert all(targets for targets in vocabulary.values())


def test_every_routed_predicate_named_in_the_prompt_is_actually_declared(ontology):
    """The prompt must not promise a name the ontology does not declare.

    `CORE_ROUTED_PREDICATES` is duplicated in `core/ontology.py` rather than
    imported from the knowledge layer, because the loader must stay importable
    without it. This is the test that stops the duplication drifting into a lie.
    """
    from core.ontology import CORE_ROUTED_PREDICATES, relationship_predicates

    vocabulary = relationship_predicates(ontology)
    undeclared = sorted(p for p in CORE_ROUTED_PREDICATES if p not in vocabulary)
    assert undeclared == [], f"prompt names predicates the schema does not declare: {undeclared}"


def test_the_core_list_covers_what_reconciliation_routes_on(ontology):
    """No predicate the prompt teaches may be silently unrouted (YB-031).

    THIS TEST USED TO COMPARE THE TWO VOCABULARIES AFTER `rstrip("s")`. That strips
    exactly the letter separating the taught plural (`implements_requirements`) from
    the routed singular (`implements_requirement`), so it passed while ingest —
    which compares literal strings — turned every plural edge into a local one. It
    asserted the drift was *tolerable*, which was never the claim: the claim is that
    the alias map the prompt describes is the map ingest resolves with.

    A predicate is acceptable if it canonicalises onto something routed, or is
    documented as deliberately local. Nothing else — an unrouted taught name is a
    traceability edge that exists in the graph and reaches no consumer.
    """
    from core.knowledge.model import CROSS_GRAPH_PREDICATES
    from core.ontology import (
        CORE_ROUTED_PREDICATES,
        LOCAL_PREDICATES,
        canonical_predicate,
    )

    unrouted = sorted(
        name for name in CORE_ROUTED_PREDICATES
        if canonical_predicate(name) not in CROSS_GRAPH_PREDICATES
        and name not in LOCAL_PREDICATES
    )
    assert unrouted == [], (
        f"the prompt teaches predicates nothing routes: {unrouted} — these become "
        f"local edges invisible to reconciliation, the map and the census"
    )


def test_the_taught_vocabulary_resolves_onto_the_routed_one(ontology):
    """The acceptance criterion, stated directly: taught ⇒ routed.

    Every name the prompt offers with a target must resolve into the router's own
    set under `canonical_predicate` — the same function ingest calls. Before YB-031
    three relationships failed this: the join itself, and two traceability axes.
    """
    from core.knowledge.model import CROSS_GRAPH_PREDICATES
    from core.ontology import (
        CORE_ROUTED_PREDICATES,
        LOCAL_PREDICATES,
        canonical_predicate,
        relationship_predicates,
    )

    vocabulary = relationship_predicates(ontology)
    # Every taught-and-routed name, not just the core list: the "Also declared"
    # block is taught too, and a relationship is no less lost for being listed
    # second.
    for name in sorted(vocabulary):
        resolved = canonical_predicate(name)
        if resolved == name:
            continue
        assert resolved in CROSS_GRAPH_PREDICATES, (
            f"{name} canonicalises to {resolved}, which the router does not route"
        )
    for name in sorted(LOCAL_PREDICATES):
        assert canonical_predicate(name) not in CROSS_GRAPH_PREDICATES
    assert CORE_ROUTED_PREDICATES  # the list is not vacuous


def test_declared_aliases_are_ones_the_router_actually_accepts(ontology):
    """An alias the prompt offers must be a spelling reconciliation truly accepts,
    or the prompt teaches a name that silently becomes a local edge."""
    from core.knowledge.model import CROSS_GRAPH_PREDICATES
    from core.knowledge.reconcile import EXPECTED_TARGET_KINDS
    from core.ontology import ROUTING_ALIASES

    routed = set(CROSS_GRAPH_PREDICATES) | set(EXPECTED_TARGET_KINDS)
    offered = {alias for aliases in ROUTING_ALIASES.values() for alias in aliases}
    unsupported = sorted(a for a in offered if a not in routed)
    assert unsupported == [], f"prompt offers aliases the router ignores: {unsupported}"
