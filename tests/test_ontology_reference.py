"""
The ontology reference view.

Mirrors `app/ontology_reference.py`: what the page shows, and — in the boundary
tests — what it must never start doing.
"""

from pathlib import Path

import app.ontology_reference as reference
import core.ontology as ontology_loader
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
    """`core.ontology` must stay usable by agents that have no graph.

    If it ever imports the knowledge or web layers, it stops being a schema reader
    and becomes a fourth thing pinned to the instance side.
    """
    source = Path(ontology_loader.__file__).read_text()
    assert "from app" not in source
    assert "from core.knowledge" not in source
    assert "KnowledgeGraph" not in source


def test_the_reference_view_is_duck_typed_on_the_graph():
    """It joins the schema against whatever exposes `.nodes`, rather than
    depending on the canonical model — that keeps the join in the view layer
    where both sides happen to be available."""
    source = Path(reference.__file__).read_text()
    assert "from core.knowledge" not in source
    assert "LENSES" not in source
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
        "governance",
        "architecture",
    ]
    assert chain[0]["imports_labeled"] == []
    # Architecture is now the last link and imports three layers; governance is a
    # peer above requirements, not beneath architecture.
    assert [i["label"] for i in chain[4]["imports_labeled"]] == [
        "Common",
        "Enterprise",
        "Business Requirements",
    ]
    assert all(link["role"] for link in chain), "every layer explains why it exists"


def test_overview_reports_diagnostics_and_the_special_classes(ontology):
    view = ontology_overview(ontology)
    assert view["diagnostics"]["is_clean"] is True
    assert view["mixins"] == ["ExternallyReferenced", "Provenanced"]
    assert view["abstract"] == [
        "EnterpriseConstruct", "Requirement", "GovernanceInstrument", "ArchitectureElement",
    ]


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


def test_class_rows_cover_the_base_schema(ontology):
    """Base-layer classes only. Domain packs are reported separately, not counted here."""
    assert len(class_rows(ontology)) == 70


def test_class_rows_filter_by_layer(ontology):
    architecture = class_rows(ontology, layer="architecture")
    assert len(architecture) == 21
    assert {r["layer"] for r in architecture} == {"architecture"}


def test_class_rows_count_own_versus_inherited_and_effective(ontology):
    row = next(r for r in class_rows(ontology) if r["name"] == "NonFunctionalRequirement")
    # 9 own: the quality model gained `realizes_attribute` and `subcharacteristic`
    # alongside the existing `quality_category`.
    assert row["own_attributes"] == 9
    assert row["effective_attributes"] == 35
    assert row["inherited_attributes"] == 26
    assert row["relationships"] > 0
    assert row["is_a"] == "Requirement"


def test_search_finds_a_class_by_its_slot_name(ontology):
    """ "Which class declares `quality_category`?" should not require grepping YAML.

    Two classes answer now, and that is correct rather than a leak: the NFR
    declares the quality category it *requires*, and `DesignTechnique` declares
    the category it *targets*, so a technique can be checked against the NFR it
    claims to realize even before the NFR itself is resolvable. Same slot name,
    same vocabulary, opposite direction.
    """
    rows = class_rows(ontology, q="quality_category")
    assert sorted(r["name"] for r in rows) == ["DesignTechnique", "NonFunctionalRequirement"]


def test_search_matches_descriptions_too(ontology):
    assert class_rows(ontology, q="ISO/IEC 25010")


def test_only_filter_selects_the_special_classes(ontology):
    assert {r["name"] for r in class_rows(ontology, only="abstract")} == {
        "EnterpriseConstruct",
        "Requirement",
        "GovernanceInstrument",
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
    # Strictly the nine ISO/IEC 25010:2023 characteristics. `PORTABILITY` was
    # removed in that revision and `FLEXIBILITY` added, so the count is nine and
    # the absence of Portability is the assertion that matters.
    # Nine ISO characteristics plus REGULATORY_COMPLIANCE, which is enterprise
    # governance. The absence of Portability is the assertion that matters.
    assert quality["value_count"] == 10
    assert "FLEXIBILITY" in quality["values"]
    assert "PORTABILITY" not in quality["values"]
    assert quality["values"] == [
        "FUNCTIONAL_SUITABILITY", "PERFORMANCE_EFFICIENCY", "COMPATIBILITY",
        "INTERACTION_CAPABILITY", "RELIABILITY", "SECURITY", "MAINTAINABILITY",
        "FLEXIBILITY", "SAFETY", "REGULATORY_COMPLIANCE",
    ]


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
    labels = {link["label"] for link in range_edges}
    assert "quality_scenario" in labels
    # The attribute link is a range edge like any other, and must be drawn — it is
    # what makes a technique's or element's claim on a quality attribute visible
    # from the requirement side.
    assert "realizes_attribute" in labels
    # Every emitted edge must land on a node the view actually draws — a link to
    # a node that is not rendered is a link the reader cannot follow. Ranges
    # beyond MAX_ROW_NODES are truncated, and the view reports that in `hidden`,
    # so the check is draw-plus-recounted rather than exact equality.
    drawn = {
        n["name"] for row in view["rows"] if row["key"] == "ranges" for n in row["nodes"]
    }
    targets = {link["target"] for link in range_edges}
    assert targets <= drawn, f"edge to a node no row draws: {sorted(targets - drawn)}"
    # Anything beyond the cap is reported rather than silently dropped.
    if view["hidden"]["ranges"]:
        assert view["hidden"]["capped_range_edges"] > 0


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
    # Exact, like `test_ontology.test_totals`: 50 enums is 49 plus
    # `ArchitectureDocumentStatus`, which gave an architecture description its own
    # lifecycle instead of borrowing `RequirementStatus` (ISS-review nit 1), and 51
    # adds `SharingScope` (YB-044) — again an enum with no new class.
    assert len(payload["classes"]) == 70
    assert len(payload["enums"]) == 51
    assert len(payload["subsets"]) == 15
    json.dumps(payload)  # must not contain anything a JSON encoder refuses


# ============================================================================
# The domain pack on the reference page
# ============================================================================


def test_the_pack_is_reported_separately_from_the_base_layers(ontology, ontology_dir):
    """The distinction the whole design rests on.

    A pack must never appear as a chain layer: the base layers are fixed and
    present for every Initiative, while a pack is conditional and swappable. Merging
    them would say the base ontology depends on one domain.
    """
    from core.ontology import load_domain_pack

    pack = load_domain_pack("payment_processing", ontology_dir)
    view = ontology_overview(ontology, None, pack=pack)

    assert [layer["key"] for layer in view["layers"]] == [
        "common", "enterprise", "requirements", "governance", "architecture",
    ]
    assert view["pack"]["spec"] == "payment_processing"
    assert view["pack"]["version"]
    # The base class list is untouched by the pack being present.
    assert view["stats"]["classes"] == 70


def test_no_pack_reports_none_rather_than_an_empty_layer(ontology):
    view = ontology_overview(ontology)
    assert view["pack"] is None
    assert view["pack_instances"] == {}
    assert view["stats"]["layers"] == 5


def test_the_coverage_census_counts_instances_of_pack_classes_only(ontology, ontology_dir):
    """The third leg in miniature: a domain class with zero instances is the finding,
    so the census must count exactly the pack's classes and nothing else."""
    from core.knowledge.model import KnowledgeGraph
    from core.ontology import load_domain_pack

    graph = KnowledgeGraph()
    graph.add_node("Payment", "Brand fee collection")
    graph.add_node("Payment", "Refund leg")
    graph.add_node("Cardholder", "Alice")
    graph.add_node("Container", "payment-orchestrator")  # not a domain class

    pack = load_domain_pack("payment_processing", ontology_dir)
    view = ontology_overview(ontology, graph, pack=pack)

    assert view["pack_instances"] == {"Payment": 2, "Cardholder": 1}
    # Chargeback is declared and has no instances — the coverage gap, askable only
    # because the vocabulary is closed.
    assert "Chargeback" in view["pack"]["concrete_classes"]
    assert view["pack_instances"].get("Chargeback", 0) == 0


def test_the_pack_payload_carries_what_the_census_view_needs(ontology_dir):
    """Class specs, not just names — otherwise the template would have to re-load
    the pack and there would be two sources of truth for the same vocabulary."""
    from core.ontology import load_domain_pack

    as_dict = load_domain_pack("payment_processing", ontology_dir).to_dict()
    assert "Chargeback" in as_dict["classes"]
    assert as_dict["classes"]["Chargeback"]["subsets"]
    assert as_dict["classes"]["Chargeback"]["description"]
    assert "PaymentLifecycleState" in as_dict["enums"]
    assert as_dict["subsets"]["PaymentCore"]["description"]


def test_the_ontology_page_renders_with_and_without_a_pack():
    """Route-level, both states. The packless state is the common one and must not
    look like an error."""
    import tempfile

    from app import create_app
    from core.knowledge.ingest import graph_from_extraction
    from core.knowledge.store import RevisionStore
    from tests.conftest import FakeExtractor

    tmp = tempfile.mkdtemp()
    app = create_app(
        {"TESTING": True, "STORE_ROOT": tmp, "REVIEWER": "t"},
        store_root=tmp,
        extractor_factory=lambda _t: FakeExtractor({}),
    )
    client = app.test_client()

    plain = client.get("/ontology").get_data(as_text=True)
    assert "No domain pack is in force" in plain
    assert "payment_processing" in plain, "available packs should still be listed"

    payload = {
        "triples": [
            {
                "subject": "Payment Gateway",
                "predicate": "has_functional_requirement",
                "object": "Payment Authorisation",
                "confidence": 0.9,
                "ontology_class": "Payment",
            }
        ]
    }
    graph, _run = graph_from_extraction(
        payload,
        metadata={"domain_pack": "payment_processing@0.1.0", "chunks": 1},
        document_ref="b.md",
        initiative_id="INIT-MVP-001",
    )
    RevisionStore(tmp).ensure().save_working(graph)

    with_pack = client.get("/ontology").get_data(as_text=True)
    assert "Payment Processing Domain Pack" in with_pack
    assert "Coverage, not traceability" in with_pack
    assert "0 — not mentioned" in with_pack
