"""
RDF serialisation of the canonical graph.

DESIGN: emit BOTH forms, from one source
----------------------------------------
Every assertion is written twice:

  1. a **plain triple** — `<subject> <predicate> <object>`. Cheap to traverse;
     the form 90% of SPARQL will use.
  2. an **assertion resource** — a named node carrying confidence, source text,
     provenance and verification state.

The alternative is RDF reification (4 triples per assertion) or RDF-star
(support varies). Modelling the assertion as a resource is the shape our ontology
already uses — `RequirementRealization` and `Provenance` are reified assertions,
not bare triples.

Emitting both risks the two drifting apart, which is why they are generated from
the same model in one pass rather than written independently. The gain is that a
query author chooses whether provenance matters, without paying for it when it
does not.

Note on the choice of RDF at all: the per-assertion metadata is exactly the thing
RDF handles awkwardly, and it is why a property graph is superficially attractive.
It is a real cost, paid here explicitly rather than hidden.
"""

from typing import Optional, Tuple

import rdflib
from rdflib import Literal, Namespace, RDF, RDFS, XSD, URIRef

from ..ontology import load_ontology
from .model import Assertion, KnowledgeGraph

SEA = Namespace("https://sea.platform/ontology/kg/")
PROV = Namespace("https://sea.platform/ontology/kg/provenance/")
DEFAULT_BASE = "https://sea.platform/kg/"

# Predicates that carry a human-readable string rather than a node reference.
# Everything else is treated as a node reference when `object` is set.


def _class_hierarchy(ontology_dir: str) -> Tuple[Tuple[str, str], ...]:
    """`(subclass, superclass)` for every declared `is_a`, from the ontology.

    Read rather than restated, for the reason this repo keeps recording: a
    hand-written table of superclasses is a second copy of the ontology, free to
    drift from it. `load_ontology` is cached per resolved path, so this costs one
    parse for the process.

    Returns `()` when the ontology cannot be read. The export is then a faithful
    graph without a hierarchy, which is a degradation rather than a failure — the
    same one `family_of` documents for a colour.
    """
    try:
        model = load_ontology(ontology_dir)
    except Exception:
        return ()
    return tuple(
        (name, spec.is_a)
        for name, spec in model.classes.items()
        if spec.is_a and spec.is_a in model.classes
    )


def _node_uri(node_id: str, base: str) -> URIRef:
    return URIRef(base + node_id)


def _pred_uri(predicate: str) -> URIRef:
    return SEA[predicate]


def _assertion_uri(assertion_id: str, base: str) -> URIRef:
    return URIRef(base + assertion_id)


def to_rdf(
    graph: KnowledgeGraph,
    base: str = DEFAULT_BASE,
    include_superseded: bool = True,
    include_run_metadata: bool = True,
    include_class_hierarchy: bool = True,
    ontology_dir: str = "ontology",
) -> rdflib.Graph:
    """Serialise the canonical graph to RDF.

    Args:
        graph: the canonical knowledge graph.
        base: base URI for node and assertion resources.
        include_superseded: keep superseded assertions for lineage. Turning this
            off produces a "current state" graph; keeping it on makes the graph
            auditable.
        include_run_metadata: emit extraction runs, including completeness. A
            consumer that ignores this cannot tell a partial extraction from a
            complete one.
        include_class_hierarchy: emit `rdfs:subClassOf` for the ontology's `is_a`
            edges. Off by default would be the wrong default — see below.
        ontology_dir: where to read the hierarchy from.

    WHY THE HIERARCHY IS EMITTED

    Without it, `?req a sea:Requirement` matches **nothing**: the ontology's
    `Requirement` is abstract, nodes are typed with their concrete class, and the
    hierarchy is two levels deep (`PlatformMultiTenancyRequirement is_a
    NonFunctionalRequirement is_a Requirement`). A query author then writes a
    `UNION` over the subclasses they know about, which is a second statement of the
    ontology — free to drift, and silently missing every subclass added later. A
    measured example: a `UNION` over the four direct subclasses of `Requirement`
    finds 14 rows where `?kind rdfs:subClassOf* sea:Requirement` finds 15.

    `is_a` only. `mixins` are also supertypes for slot inheritance, but they are
    cross-cutting aspects rather than a taxonomy (`ExternallyReferenced`,
    `Provenanced`), and asserting them as `rdfs:subClassOf` would put them in every
    subclass closure — which is exactly the distinction the ontology viewer keeps
    visible.

    A missing ontology degrades to no hierarchy rather than raising: the export is
    still a faithful graph, and the degradation is the same one `family_of`
    documents. Pass `ontology=` when the hierarchy must be guaranteed.
    """
    g = rdflib.Graph()
    g.bind("sea", SEA)
    g.bind("prov", PROV)
    g.bind("rdfs", RDFS)

    # ---- the class hierarchy, so a property path can reach subclasses ----
    if include_class_hierarchy:
        for sub, sup in _class_hierarchy(ontology_dir):
            g.add((SEA[sub], RDFS.subClassOf, SEA[sup]))

    # ---- nodes ----
    for node in graph.nodes.values():
        subject = _node_uri(node.id, base)
        g.add((subject, RDF.type, SEA[node.kind]))
        g.add((subject, RDFS.label, Literal(node.label)))
        for ref in node.external_refs:
            g.add((subject, SEA.externalReference, Literal(ref)))

    # ---- assertions ----
    for assertion in graph.assertions.values():
        if not assertion.is_active and not include_superseded:
            continue

        subject = _node_uri(assertion.subject, base)
        predicate = _pred_uri(assertion.predicate)

        # Form 1: the plain triple, so traversal does not need to know about
        # assertions at all.
        if assertion.object:
            g.add((subject, predicate, _node_uri(assertion.object, base)))
        elif assertion.value:
            g.add((subject, predicate, Literal(assertion.value)))

        # Form 2: the assertion resource carrying the metadata.
        a = _assertion_uri(assertion.id, base)
        g.add((a, RDF.type, SEA.Assertion))
        g.add((a, SEA.assertsSubject, subject))
        g.add((a, SEA.assertsPredicate, predicate))
        if assertion.object:
            g.add((a, SEA.assertsObject, _node_uri(assertion.object, base)))
        elif assertion.value:
            g.add((a, SEA.assertsValue, Literal(assertion.value)))
        g.add((a, SEA.confidence, Literal(assertion.confidence, datatype=XSD.decimal)))
        g.add((a, SEA.verificationStatus, SEA[assertion.status]))
        if assertion.source_text:
            g.add((a, SEA.sourceText, Literal(assertion.source_text)))
        if assertion.ontology_class:
            g.add((a, SEA.ontologyClass, SEA[assertion.ontology_class]))
        if assertion.superseded_by:
            g.add((a, SEA.supersededBy, _assertion_uri(assertion.superseded_by, base)))

        # Provenance — the part that makes agent-asserted distinguishable from
        # human-confirmed in a query, not just by convention.
        p = assertion.provenance
        g.add((a, PROV.sourceType, Literal(p.source_type)))
        if p.run_id:
            g.add((a, PROV.runId, Literal(p.run_id)))
        if p.pass_name:
            g.add((a, PROV.passName, Literal(p.pass_name)))
        if p.model_id:
            g.add((a, PROV.modelId, Literal(p.model_id)))
        if p.chunk_label:
            g.add((a, PROV.chunkLabel, Literal(p.chunk_label)))
        if p.asserted_at:
            g.add((a, PROV.assertedAt, Literal(p.asserted_at)))
        if p.derived_from:
            g.add((a, PROV.derivedFrom, Literal(p.derived_from)))
        if p.correction_note:
            g.add((a, PROV.correctionNote, Literal(p.correction_note)))

    # ---- extraction runs, including completeness ----
    if include_run_metadata:
        for run in graph.runs.values():
            r = URIRef(base + run.id)
            g.add((r, RDF.type, SEA.ExtractionRun))
            g.add((r, SEA.completeness, SEA[run.completeness]))
            if run.document_ref:
                g.add((r, SEA.documentRef, Literal(run.document_ref)))
            if run.document_hash:
                g.add((r, SEA.documentHash, Literal(run.document_hash)))
            if run.model_id:
                g.add((r, SEA.modelId, Literal(run.model_id)))
            g.add((r, SEA.chunkCount, Literal(run.chunk_count, datatype=XSD.integer)))
            for rec in run.passes:
                pr = URIRef(base + f"{run.id}_{rec.pass_name}_{rec.chunk_label or 'x'}"
                                    .replace(" ", "_").replace("/", "_"))
                g.add((pr, RDF.type, SEA.PassRecord))
                g.add((pr, SEA.passName, Literal(rec.pass_name)))
                g.add((pr, SEA.passOutcome, Literal(rec.outcome)))
                if rec.chunk_label:
                    g.add((pr, SEA.chunkLabel, Literal(rec.chunk_label)))
                if rec.path:
                    g.add((pr, SEA.extractionPath, Literal(rec.path)))
                if rec.error:
                    g.add((pr, SEA.passError, Literal(rec.error[:500])))
                g.add((r, SEA.hasPass, pr))

    return g


def to_turtle(graph: KnowledgeGraph, **kwargs) -> str:
    return to_rdf(graph, **kwargs).serialize(format="turtle")


def to_jsonld(graph: KnowledgeGraph, **kwargs) -> str:
    return to_rdf(graph, **kwargs).serialize(format="json-ld")


# ----------------------------------------------------------------------------
# Reference queries — the audit class SPARQL makes declarative
# ----------------------------------------------------------------------------

_PREFIXES = """PREFIX sea: <https://sea.platform/ontology/kg/>
PREFIX prov: <https://sea.platform/ontology/kg/provenance/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
"""


QUERY_MISSING_ACTIVE = _PREFIXES + """
# Requirements with no BOUND implementing element.
#
# DELIBERATELY NOT REGISTERED IN `QUERIES` BELOW. Three reasons, all measured:
#
#   1. IT CANNOT SEPARATE THE STATES THIS PLATFORM DISTINGUISHES. A requirement
#      nothing ever cited and one whose claim is still a literal reference look
#      identical to graph matching, because binding a reference is reconciliation's
#      job (`reference_targets_a_node`), not a SPARQL pattern's. Measured on
#      `payments_v2`: this returns 14 where `realization_report` reports 3 with no
#      claim and 10 unresolved — two findings with two different fixes. The
#      platform's answer to "does this requirement have an architecture?" is
#      `core.knowledge.realization.realization_report`. Keep it there.
#   2. ITS PRECONDITION IS A COMPLETE RUN, and a query cannot enforce that. On a
#      PARTIAL graph it reports extraction failures as architectural gaps.
#      `project_gap_report` carries the gate and says which of the two it is.
#   3. `include_superseded=False` IS PART OF ITS MEANING. The plain triple form is
#      emitted regardless of status, so a RETIRED implementer counts as an
#      implementer unless the caller asks for the current-state graph. That cannot
#      be fixed from inside the query, which is a third reason the caller owns it.
#
# The subclass check is a property path rather than a `UNION` over subclasses: the
# hierarchy is two levels deep (`PlatformMultiTenancyRequirement is_a
# NonFunctionalRequirement is_a Requirement`), so a UNION over the direct children
# silently misses every grandchild. That path works because `to_rdf` emits
# `rdfs:subClassOf`; before it did, this query matched nothing at all.
SELECT ?req ?label WHERE {
  ?req a ?kind .
  ?kind rdfs:subClassOf* sea:Requirement .
  ?req rdfs:label ?label .
  FILTER NOT EXISTS {
    ?el sea:implements_requirement ?x .
    FILTER(?x = ?req || ?x = ?label)
  }
}
"""

QUERY_UNVERIFIED = _PREFIXES + """
# Assertions still asserted by an agent and never confirmed by a human.
SELECT ?subject ?predicate ?confidence WHERE {
  ?a a sea:Assertion ;
     sea:assertsSubject ?subject ;
     sea:assertsPredicate ?predicate ;
     sea:confidence ?confidence ;
     prov:sourceType "EXTRACTION_AGENT" ;
     sea:verificationStatus sea:UNVERIFIED .
}
ORDER BY DESC(?confidence)
"""

QUERY_HUMAN_OVERRIDES = _PREFIXES + """
# Where a human has corrected an assertion — the audit trail for review work.
SELECT ?subject ?predicate ?status WHERE {
  ?a a sea:Assertion ;
     sea:assertsSubject ?subject ;
     sea:assertsPredicate ?predicate ;
     sea:verificationStatus ?status ;
     prov:sourceType ?src .
  FILTER(?src IN ("HUMAN_ARCHITECT","HUMAN_ANALYST","HUMAN_REVIEWER"))
}
"""

QUERY_PARTIAL_RUNS = _PREFIXES + """
# Extraction runs that were not complete. A graph from one of these is not
# evidence of absence for anything it failed to produce.
SELECT ?run ?completeness ?pass ?outcome WHERE {
  ?run a sea:ExtractionRun ; sea:completeness ?completeness .
  OPTIONAL { ?run sea:hasPass ?p . ?p sea:passName ?pass ; sea:passOutcome ?outcome }
  FILTER(?completeness != sea:COMPLETE)
}
"""

QUERIES = {
    "unverified": QUERY_UNVERIFIED,
    "human_overrides": QUERY_HUMAN_OVERRIDES,
    "partial_runs": QUERY_PARTIAL_RUNS,
}


def run_query(rdflib_graph: rdflib.Graph, name_or_sparql: str) -> list:
    """Execute a named reference query, or a raw SPARQL string.

    Rows are converted by label rather than `dict(row)`, which only works for
    two-variable results and raises otherwise.
    """
    sparql = QUERIES.get(name_or_sparql, name_or_sparql)
    if "PREFIX" not in sparql:
        sparql = _PREFIXES + sparql
    return [
        {str(var): row[var] for var in row.labels}
        for row in rdflib_graph.query(sparql)
    ]
