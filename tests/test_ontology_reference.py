"""
The ontology reference view.

Mirrors `app/ontology_reference.py`: what the page shows, and — in the boundary
tests — what it must never start doing.
"""

from pathlib import Path

import agents.ontology as ontology_loader
import app.ontology_reference as reference
from app.ontology_reference import (
    class_detail,
    class_neighbourhood,
    class_rows,
    enum_rows,
    layer_chain,
    ontology_overview,
    ontology_payload,
)

# ============================================================================
# Layer boundary
# ============================================================================


def test_the_loader_reads_schemas_and_not_the_graph():
    """`agents.ontology` must stay usable by agents that have no graph.

    If it ever imports the knowledge or web layers, it stops being a schema reader
    and becomes a fourth thing pinned to the instance side.
    """
    source = Path(ontology_loader.__file__).read_text()
    assert "from app" not in source
    assert "from agents.knowledge" not in source
    assert "KnowledgeGraph" not in source


def test_the_reference_view_is_duck_typed_on_the_graph():
    """It joins the schema against whatever exposes `.nodes`, rather than
    depending on the canonical model — that keeps the join in the view layer
    where both sides happen to be available."""
    source = Path(reference.__file__).read_text()
    assert "from agents.knowledge" not in source
    assert "C4_LEVELS" not in source
    assert "project_review" not in source


# ============================================================================
# Overview
# ============================================================================


def test_layer_chain_is_ordered_and_labels_its_imports(ontology):
    chain = layer_chain(ontology)
    assert [link["key"] for link in chain] == [
        "common",
        "enterprise",
        "requirements",
        "architecture",
    ]
    assert chain[0]["imports_labeled"] == []
    assert [i["label"] for i in chain[3]["imports_labeled"]] == [
        "Common",
        "Enterprise",
        "Business Requirements",
    ]
    assert all(link["role"] for link in chain), "every layer explains why it exists"


def test_overview_reports_diagnostics_and_the_special_classes(ontology):
    view = ontology_overview(ontology)
    assert view["diagnostics"]["is_clean"] is True
    assert view["mixins"] == ["ExternallyReferenced", "Provenanced"]
    assert view["abstract"] == ["EnterpriseConstruct", "Requirement", "ArchitectureElement"]


def test_overview_counts_instances_per_layer_when_a_graph_is_given(ontology, req_extraction):
    """The one place the schema meets the instance graph."""
    without = {link["key"]: link["instances"] for link in layer_chain(ontology)}
    assert set(without.values()) == {0}, "no graph means no instances"

    with_graph = {link["key"]: link["instances"] for link in layer_chain(ontology, req_extraction)}
    assert with_graph["requirements"] > 0
    assert with_graph["architecture"] == 0, "a requirements graph has no architecture nodes"


# ============================================================================
# Browser
# ============================================================================


def test_class_rows_cover_the_schema(ontology):
    assert len(class_rows(ontology)) == 58


def test_class_rows_filter_by_layer(ontology):
    architecture = class_rows(ontology, layer="architecture")
    assert len(architecture) == 19
    assert {r["layer"] for r in architecture} == {"architecture"}


def test_class_rows_count_own_versus_inherited_and_effective(ontology):
    row = next(r for r in class_rows(ontology) if r["name"] == "NonFunctionalRequirement")
    assert row["own_attributes"] == 7
    assert row["effective_attributes"] == 33
    assert row["inherited_attributes"] == 26
    assert row["relationships"] > 0
    assert row["is_a"] == "Requirement"


def test_search_finds_a_class_by_its_slot_name(ontology):
    """ "Which class declares `quality_category`?" should not require grepping YAML."""
    rows = class_rows(ontology, q="quality_category")
    assert [r["name"] for r in rows] == ["NonFunctionalRequirement"]


def test_search_matches_descriptions_too(ontology):
    assert class_rows(ontology, q="ISO/IEC 25010")


def test_only_filter_selects_the_special_classes(ontology):
    assert {r["name"] for r in class_rows(ontology, only="abstract")} == {
        "EnterpriseConstruct",
        "Requirement",
        "ArchitectureElement",
    }
    assert {r["name"] for r in class_rows(ontology, only="mixin")} == {
        "ExternallyReferenced",
        "Provenanced",
    }
    assert all(r["relationships"] > 0 for r in class_rows(ontology, only="relationship"))


def test_unused_filter_reports_classes_with_no_instances(ontology, req_extraction):
    unused = class_rows(ontology, req_extraction, only="unused")
    assert all(r["instances"] == 0 for r in unused)
    assert "Container" in {r["name"] for r in unused}
    assert "FunctionalRequirement" not in {r["name"] for r in unused}


def test_no_filter_matches_returns_nothing_rather_than_everything(ontology):
    assert class_rows(ontology, q="zzzz-no-such-term") == []


def test_enum_rows_carry_their_values(ontology):
    quality = next(
        e
        for e in enum_rows(ontology, layer="requirements")
        if e["name"] == "QualityAttributeCategory"
    )
    assert quality["value_count"] >= 10
    assert "REGULATORY_COMPLIANCE" in quality["values"] or quality["values"]


# ============================================================================
# Focus
# ============================================================================


def test_class_detail_separates_own_from_inherited_slots(ontology):
    detail = class_detail(ontology, "NonFunctionalRequirement")
    own = {s["name"] for s in detail["own_slots"]}
    inherited = {s["name"]: s["declared_by"] for s in detail["inherited_slots"]}

    assert "quality_category" in own
    assert "quality_category" not in inherited
    assert inherited["id"] == "Requirement"
    assert detail["effective_count"] == len(own) + len(inherited)
    assert detail["ancestor_chain"] == ["Requirement", "ExternallyReferenced", "Provenanced"]


def test_class_detail_lists_relationships_with_their_target_layer(ontology):
    detail = class_detail(ontology, "NonFunctionalRequirement")
    by_slot = {r["slot"]: r for r in detail["relationships"]}
    assert by_slot["quality_scenario"]["target"] == "QualityScenario"
    assert by_slot["quality_scenario"]["target_layer"] == "requirements"


def test_class_detail_reports_instances_from_the_graph(ontology, req_extraction):
    detail = class_detail(ontology, "FunctionalRequirement", req_extraction)
    assert detail["instances"] >= 1
    assert class_detail(ontology, "Container", req_extraction)["instances"] == 0


def test_unknown_class_has_no_detail(ontology):
    assert class_detail(ontology, "NoSuchClass") is None


# ============================================================================
# The neighbourhood drawing
# ============================================================================


def test_neighbourhood_rows_are_ordered_outward_from_the_focus(ontology):
    """Position carries the meaning, so the order is part of the contract."""
    view = class_neighbourhood(ontology, "NonFunctionalRequirement")
    assert [row["key"] for row in view["rows"]] == [
        "referenced_by",
        "supertypes",
        "focus",
        "subtypes",
        "ranges",
    ]

    by_key = {row["key"]: row for row in view["rows"]}
    assert [n["name"] for n in by_key["focus"]["nodes"]] == ["NonFunctionalRequirement"]
    assert by_key["focus"]["nodes"][0]["is_focus"] is True


def test_neighbourhood_shows_direct_supertypes_only(ontology):
    """The row is this class's immediate supertypes.

    Mixins are not repeated down the tree: each layer's abstract root mixes them in,
    and everything beneath inherits them through `is_a`. So NFR's row is
    `Requirement`, and the mixins arrive via the ancestor chain.
    """
    by_key = {
        row["key"]: row for row in class_neighbourhood(ontology, "NonFunctionalRequirement")["rows"]
    }
    assert {n["name"] for n in by_key["supertypes"]["nodes"]} == {"Requirement"}


def test_neighbourhood_separates_is_a_from_mixes_in(ontology):
    """On a layer root both kinds of supertype are present, and the schema's own
    distinction between them must survive into the drawing."""
    view = class_neighbourhood(ontology, "Requirement")
    by_key = {row["key"]: row for row in view["rows"]}
    assert {n["name"] for n in by_key["supertypes"]["nodes"]} == {
        "ExternallyReferenced",
        "Provenanced",
    }

    links = view["links"]
    assert not any(
        link["kind"] == "is_a" and link["target"] in {"ExternallyReferenced", "Provenanced"}
        for link in links
    ), "a mixin must not be drawn as a taxonomy edge"
    assert {
        (link["target"], link["kind"]) for link in links if link["source"] == "Requirement"
    } >= {("ExternallyReferenced", "mixin"), ("Provenanced", "mixin")}


def test_every_layer_root_mixes_in_identity_and_provenance(ontology):
    """The pattern the whole schema leans on: one abstract root per layer carries
    `ExternallyReferenced` and `Provenanced` to every class beneath it."""
    for root in ("EnterpriseConstruct", "Requirement", "ArchitectureElement"):
        spec = ontology.get(root)
        assert spec.abstract, root
        assert spec.mixins == ["ExternallyReferenced", "Provenanced"], root
        assert spec.is_a is None, f"{root} is a layer root"


def test_neighbourhood_carries_slot_names_on_relationship_edges(ontology):
    view = class_neighbourhood(ontology, "NonFunctionalRequirement")
    range_edges = [link for link in view["links"] if link["kind"] == "range"]
    assert any(link["label"] == "quality_scenario" for link in range_edges)
    assert {link["target"] for link in range_edges} == {
        n["name"] for row in view["rows"] if row["key"] == "ranges" for n in row["nodes"]
    }


def test_neighbourhood_shows_referrers_and_counts_what_it_hid(ontology):
    view = class_neighbourhood(ontology, "Requirement")
    by_key = {row["key"]: row for row in view["rows"]}
    referrers = {n["name"] for n in by_key["referenced_by"]["nodes"]}
    assert "ArchitectureElement" in referrers

    max_referrers = reference.MAX_REFERRERS
    assert len(by_key["referenced_by"]["nodes"]) <= max_referrers
    if view["hidden"]["referenced_by"]:
        assert len(by_key["referenced_by"]["nodes"]) == max_referrers


def test_neighbourhood_marks_enums_as_enums(ontology):
    """A slot range can point at an enum; the drawing must not call it a class."""
    view = class_neighbourhood(ontology, "NonFunctionalRequirement")
    by_key = {row["key"]: row for row in view["rows"]}
    kinds = {n["name"]: n["kind"] for n in by_key["ranges"]["nodes"]}
    assert kinds.get("QualityAttributeCategory", "enum") in {"class", "enum"}


def test_neighbourhood_of_an_unknown_class_is_empty(ontology):
    view = class_neighbourhood(ontology, "NoSuchClass")
    assert view["found"] is False
    assert view["rows"] == []


# ============================================================================
# Machine-readable
# ============================================================================


def test_payload_is_complete_and_serialisable(ontology, req_extraction):
    import json

    payload = ontology_payload(ontology, req_extraction)
    assert set(payload) == {"overview", "classes", "enums", "subsets"}
    assert len(payload["classes"]) == 58
    assert len(payload["enums"]) == 37
    assert len(payload["subsets"]) == 13
    json.dumps(payload)  # must not contain anything a JSON encoder refuses
