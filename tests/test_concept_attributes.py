"""
A domain concept's fields reach the graph, owned by the concept (YB-055).

WHAT THIS PINS, AND WHY IT NEEDED ITS OWN FILE. `ConceptAttribute` has been
declared since the ontology was written and has never had an instance: the
ontology nests it (`DomainConcept.key_attributes`, `inlined_as_list`) and the
extraction contract is flat, so the class was reachable from nowhere. The aspect
audit that produced ADR-0032 found it as the one core aspect of an architecture
with no ontology class the pipeline could populate, no pass rule and no guard.

It now arrives as a flat list on the entity that owns it, and the three claims
worth testing are the ones a later change could quietly break:

  1. The fields reach the graph, each joined to the concept that has it — the
     "model did it right and we lost it" seam that YB-051 was.
  2. An attribute's identity is (concept, field). `Customer.email` and
     `Order.email` are two fields, and a label based on the bare name would fold
     them into one node carrying two owners, which no later check could separate.
  3. The guard is deterministic and reads its vocabulary from the ontology, so an
     unowned, invented, repeated or physically-typed field is a finding rather
     than a fact a reviewer has to notice by reading.
"""

from __future__ import annotations

import yaml
from rich.console import Console

from agents.base_agent import AgentConfig
from agents.extraction import check_concept_attributes
from agents.knowledge_extraction.agent import (
    ExtractedConceptAttribute,
    ExtractedEntity,
    ExtractionResult,
    ExtractedTriple,
    KnowledgeExtractionAgent,
)
from core.knowledge import graph_from_extraction

# The document the guard anchors against. Deliberately short: what matters is which
# names appear in it, and one name that does not.
SOURCE = (
    "The Customer has a Customer ID, an Email and a Loyalty Tier. "
    "The Order has an Email and an Item Weight."
)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def agent(**config) -> KnowledgeExtractionAgent:
    """A bare agent with no model — the pattern the chunking tests use."""
    a = KnowledgeExtractionAgent.__new__(KnowledgeExtractionAgent)
    a.config = AgentConfig(
        name="probe", description="probe",
        ontology_path="ontology/requirements_base.yaml", ontology_dir="ontology",
        model_id="test-model", **config,
    )
    a.console = Console()
    a.ontology = yaml.safe_load(open("ontology/requirements_base.yaml"))
    a.domain_pack = None
    a.agent = None
    a.confidence_threshold = 0.7
    return a


def concept(name, *fields, ontology_class="DomainConcept"):
    """An entity record carrying `fields`, each as (name, data_type)."""
    return {
        "name": name,
        "ontology_class": ontology_class,
        "attributes": [{"name": f, "data_type": t} for f, t in fields],
    }


def ingest(output, source=SOURCE):
    graph, _run = graph_from_extraction(
        output,
        metadata={"run_id": "run_concept_attr_test", "document_type": "requirements"},
        document_ref="req.md",
        document_text=source,
    )
    return graph


def attribute_nodes(graph):
    return {n.label: n for n in graph.nodes.values() if n.kind == "ConceptAttribute"}


def targets(graph, subject_id, predicate):
    """Labels at the far end of `predicate` from `subject_id` (node or literal)."""
    out = []
    for a in graph.active():
        if a.subject == subject_id and a.predicate == predicate:
            out.append(graph.nodes[a.object].label if a.object else a.target)
    return out


def node_named(graph, label):
    return next(n for n in graph.nodes.values() if n.label == label)


# ----------------------------------------------------------------------------
# 1. The fields reach the graph, owned by their concept
# ----------------------------------------------------------------------------


def test_a_concepts_fields_become_nodes_owned_by_that_concept():
    """The whole point of YB-055: `ConceptAttribute` stops being unreachable.

    Before this, the class had zero instances in every graph the pipeline could
    produce — the ontology declared it, nothing emitted it, and no view or report
    could ask about a field.
    """
    graph = ingest({"entities": [concept("Customer", ("Customer ID", "IDENTIFIER"))]})

    nodes = attribute_nodes(graph)
    assert "Customer.Customer ID" in nodes, sorted(nodes)

    # Resolving, not duplicating: the attribute points at the SAME concept node the
    # entity record created, and there is exactly one of it.
    assert targets(graph, nodes["Customer.Customer ID"].id, "attribute_of") == ["Customer"]
    assert len([n for n in graph.nodes.values() if n.label == "Customer"]) == 1


def test_the_field_records_its_type_requirement_and_constraints():
    """A field is worth a node because it has facts an edge has nowhere to put.

    `data_type` is the one that makes the graph reason about a DATA MODEL rather
    than about names, and `is_required` is emitted only when true — `false` is the
    model's default and would read as "optional" for every field the document
    never characterised.
    """
    output = {
        "entities": [{
            "name": "Customer",
            "ontology_class": "DomainConcept",
            "attributes": [{
                "name": "Customer ID", "data_type": "IDENTIFIER",
                "description": "the primary key", "is_required": True,
                "constraints": ["must be unique"],
            }],
        }],
    }

    graph = ingest(output)
    attr = attribute_nodes(graph)["Customer.Customer ID"]

    assert targets(graph, attr.id, "data_type") == ["IDENTIFIER"]
    assert targets(graph, attr.id, "is_required") == ["true"]
    assert targets(graph, attr.id, "constraint") == ["must be unique"]
    assert targets(graph, attr.id, "description") == ["the primary key"]


def test_an_optional_field_does_not_assert_that_it_is_optional():
    """`is_required: false` is silence, not a stated negative.

    The same posture `carries_sensitive_data` takes (ADR-0032): asserting `false`
    for every field would make "the document says optional" and "the document says
    nothing" indistinguishable to the reviewer who has to decide.
    """
    graph = ingest({"entities": [concept("Customer", ("Email", "STRING"))]})
    attr = attribute_nodes(graph)["Customer.Email"]

    assert targets(graph, attr.id, "is_required") == []
    assert targets(graph, attr.id, "data_type") == ["STRING"]


# ----------------------------------------------------------------------------
# 2. Identity: (concept, field), never the bare field name
# ----------------------------------------------------------------------------


def test_two_concepts_with_a_field_of_the_same_name_keep_two_nodes():
    """The wrong join this shape has to make impossible.

    `make_node_id` keys on (kind, label). Labelling an attribute by its bare name
    would collapse `Customer.Email` and `Order.Email` into ONE node with two
    `attribute_of` edges — a node that belongs to two concepts, which no downstream
    check can tell from a shared field. Qualifying the label by the owner is what
    prevents it, so this is the test that says the qualification is load-bearing.
    """
    graph = ingest({"entities": [
        concept("Customer", ("Email", "STRING")),
        concept("Order", ("Email", "STRING")),
    ]})

    nodes = attribute_nodes(graph)
    assert sorted(nodes) == ["Customer.Email", "Order.Email"], sorted(nodes)
    assert nodes["Customer.Email"].id != nodes["Order.Email"].id

    customer = node_named(graph, "Customer")
    order = node_named(graph, "Order")
    assert targets(graph, nodes["Customer.Email"].id, "attribute_of") == ["Customer"]
    assert targets(graph, nodes["Order.Email"].id, "attribute_of") == ["Order"]
    assert customer.id != order.id


def test_a_field_name_cannot_collide_with_a_concept_of_the_same_name():
    """Identity is (kind, label) — the dot does not widen it, and does not need to.

    A concept literally named 'Customer.Email' exists beside the field, and the two
    stay apart because they are different kinds. The qualifier's job is narrower:
    it keeps a FIELD from colliding with another field, which a bare name cannot do.
    """
    graph = ingest({"entities": [
        concept("Customer", ("Email", "STRING")),
        {"name": "Customer.Email", "ontology_class": "DomainConcept"},
    ]})

    same_label = [n for n in graph.nodes.values() if n.label == "Customer.Email"]
    assert sorted(n.kind for n in same_label) == ["ConceptAttribute", "DomainConcept"]
    assert len({n.id for n in same_label}) == 2

    field = next(n for n in same_label if n.kind == "ConceptAttribute")
    assert targets(graph, field.id, "attribute_of") == ["Customer"]


# ----------------------------------------------------------------------------
# 3. The guard — deterministic, vocabulary from the ontology
# ----------------------------------------------------------------------------


def test_the_run_reports_attribute_findings_under_their_own_key():
    """Findings about fields are not object-contract violations.

    `contract_violations` is read by the extraction harness as a RATIO OVER TRIPLES
    ("clause-shaped nodes < 20%"), so counting attribute findings there would move a
    calibrated metric by an amount that has nothing to do with what it measures —
    and a data-model gap would read as an object-contract regression.
    """
    a = agent(use_structured_output=True)
    a.invoke_structured = lambda prompt, schema: ExtractionResult(
        triples=[ExtractedTriple(subject="Customer", predicate="places", object="Order",
                                 confidence=0.9)],
        entities=[ExtractedEntity(
            name="Customer", ontology_class="DomainConcept",
            attributes=[ExtractedConceptAttribute(name="Risk Score", data_type="VARCHAR(20)")],
        )],
    )
    a.invoke = lambda *x, **k: ""

    result = a.run({"document": SOURCE})

    assert result.success, result.errors
    findings = result.output["attribute_findings"]
    kinds = {f["kind"] for f in findings}
    # "Risk Score" is nowhere in SOURCE, and "VARCHAR(20)" is not a logical type.
    assert kinds == {"unanchored_attribute", "attribute_data_type"}, findings
    # The contract metric is untouched by them.
    assert result.output["contract_violations"] == []


def test_a_concept_attribute_emitted_as_an_entity_is_a_finding():
    """The shape the model produced before this existed, and still might.

    Measured on the saved PRD run: five `ConceptAttribute` entities belonging to no
    concept ('Country of Transaction', 'Transaction Currency', …). Each looked
    exactly like a concept in the graph, because nothing distinguished an unowned
    field from a thing.
    """
    findings = check_concept_attributes(
        [{"name": "Country of Transaction", "ontology_class": "ConceptAttribute"}],
        SOURCE,
    )
    assert [f.kind for f in findings] == ["unowned_attribute"]
    assert "belongs to no concept" in findings[0].reasons[0]


def test_a_field_the_document_never_names_is_a_finding():
    """Reliability §3.6: an emitted value must be in the source.

    The failure a reviewer cannot see otherwise — `Loyalty Tier` reads exactly like
    `Customer ID` whether the document supplied it or the model did.
    """
    findings = check_concept_attributes(
        [concept("Customer", ("Customer ID", "IDENTIFIER"), ("Risk Score", "STRING"))],
        SOURCE,
    )

    assert [f.kind for f in findings] == ["unanchored_attribute"]
    assert findings[0].subject == "Risk Score"


def test_a_physical_type_is_a_finding_and_a_logical_type_is_not():
    """The vocabulary is read from the ontology, and it is a LOGICAL one.

    A store's spelling ('VARCHAR(20)') classifies an implementation, not the field
    the business owns — and it changes when the store does. The allowed set comes
    from `ConceptAttributeDataType` rather than a list in this module, so the check
    cannot drift from the schema the way a hand-written one would.
    """
    findings = check_concept_attributes(
        [concept("Customer",
                 ("Customer ID", "IDENTIFIER"),
                 ("Email", "STRING"),
                 ("Loyalty Tier", "VARCHAR(20)"))],
        SOURCE,
    )

    assert [f.kind for f in findings] == ["attribute_data_type"]
    assert "VARCHAR(20)" in findings[0].reasons[0]


def test_a_repeated_field_on_one_concept_is_a_finding():
    """One node per (concept, field): the second record is silently the same node."""
    findings = check_concept_attributes(
        [concept("Customer", ("Email", "STRING"), ("Email", "STRING"))],
        SOURCE,
    )

    assert [f.kind for f in findings] == ["concept_attribute"]
    assert "twice" in findings[0].reasons[0]


def test_fields_on_something_that_is_not_a_concept_are_a_finding():
    """`ConceptAttribute.concept` ranges over `DomainConcept`, so a requirement
    carrying fields states a data model the ontology does not give it."""
    findings = check_concept_attributes(
        [concept("Payment Acceptance", ("Item Weight", "DECIMAL"),
                 ontology_class="FunctionalRequirement")],
        SOURCE,
    )

    assert [f.kind for f in findings] == ["attribute_owner"]
    assert "FunctionalRequirement" in findings[0].reasons[0]


def test_a_pack_concept_is_not_flagged_for_a_kind_the_base_layers_do_not_declare():
    """A false positive is worse than a missing check.

    A domain pack subclasses `DomainConcept` with names the five base layers do not
    declare, and `ontology_classes()` cannot see them — so a check that flagged
    every kind it does not recognise would report the active pack's own vocabulary
    as a defect on every run.
    """
    findings = check_concept_attributes(
        [concept("Loyalty Account", ("Points Balance", "INTEGER"),
                 ontology_class="LoyaltyAccount")],
        "The Loyalty Account has a Points Balance.",
    )

    assert findings == []


def test_without_a_document_the_ownership_and_type_rules_still_run():
    """A caller with no source — a design proposal working from the graph — still
    gets the ownership, duplicate and type rules, and no anchoring findings, which
    it has no evidence to make."""
    findings = check_concept_attributes(
        [concept("Customer", ("Anything At All", "NOPE"))],
        source="",
    )

    assert [f.kind for f in findings] == ["attribute_data_type"]


# ----------------------------------------------------------------------------
# 4. The seams that could silently drop a field
# ----------------------------------------------------------------------------


def test_fields_from_two_chunks_on_one_concept_both_survive():
    """Cross-chunk merging must UNION the nested list, not pick a winner.

    `merge_records` folds list-valued fields together; a scalar-style "first
    non-empty wins" would lose whichever chunk mentioned the field second, which is
    the "the model did it right and we lost it" defect — invisible, because the
    surviving record looks complete.
    """
    a = agent(use_structured_output=True)
    calls = {"n": 0}

    def invoke_structured(prompt, schema):
        calls["n"] += 1
        field = ("Customer ID", "IDENTIFIER") if calls["n"] == 1 else ("Email", "STRING")
        return ExtractionResult(
            triples=[ExtractedTriple(subject="A", predicate="traces_to_goal",
                                     object="B", confidence=0.9)],
            entities=[ExtractedEntity(name="Customer", ontology_class="DomainConcept",
                                      attributes=[ExtractedConceptAttribute(name=field[0],
                                                                            data_type=field[1])])],
        )

    a.invoke_structured = invoke_structured
    a.invoke = lambda *x, **k: ""
    # A document long enough to be chunked; every chunk reports the same concept
    # with a different field.
    document = "".join(
        f"\n## Section {i}\n\nThe Customer shall be identified. " + ("Detail. " * 40)
        for i in range(40)
    )

    result = a.run({"document": document})

    assert result.success, result.errors
    assert calls["n"] > 1, "the document has to be chunked for this to test anything"
    assert len(result.output["entities"]) == 1
    names = {attr["name"] for attr in result.output["entities"][0]["attributes"]}
    assert names == {"Customer ID", "Email"}, names


def test_the_text_fallback_carries_attributes():
    """The path that rebuilds a record field by field drops whatever it forgets.

    Losing `requirement_id` here once left a PRD run with zero identifiers while the
    structured path captured sixteen (YB-005); an attribute is the same hazard, and
    a model that answers in prose rather than in the schema is exactly when it
    happens.
    """
    a = agent()

    parsed = a._parse_entity_dict({
        "name": "Customer",
        "ontology_class": "DomainConcept",
        "attributes": [
            {"name": "Customer ID", "data_type": "IDENTIFIER", "is_required": True},
            # A bare string is accepted too, because models write both shapes.
            "Email",
        ],
    })

    assert [attr.name for attr in parsed.attributes] == ["Customer ID", "Email"]
    assert parsed.attributes[0].data_type == "IDENTIFIER"
    assert parsed.attributes[0].is_required is True


def test_the_worked_example_stops_teaching_an_unowned_attribute():
    """The prompt is the other half of the fix.

    The generic worked example taught `{"ontology_class": "ConceptAttribute"}` as a
    TRIPLE OBJECT — three of them, belonging to nothing — which is precisely the
    shape that produced five unowned nodes in the saved PRD run. The example is
    where the model learns the shape, so it has to show the owned one.
    """
    from agents.knowledge_extraction.agent import _GENERIC_WORKED_EXAMPLE

    assert '"ontology_class": "ConceptAttribute"' not in _GENERIC_WORKED_EXAMPLE
    assert '"attributes"' in _GENERIC_WORKED_EXAMPLE

    prompt = agent()._build_extraction_prompt(SOURCE, "requirements", "generic")
    assert "attributes" in prompt
    assert "Never emit `ConceptAttribute` as an entity" in prompt
