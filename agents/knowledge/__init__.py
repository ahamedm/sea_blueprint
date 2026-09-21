"""
Canonical knowledge model and serialisation.

The layer between extraction output and storage/views. Owns the transforms that
were previously unowned, so five representations cannot drift apart.

  model.py   — canonical graph: nodes, assertions, runs, provenance
  ingest.py  — extraction output -> canonical graph
  rdf.py     — canonical graph -> RDF (plain triples + assertion resources)
"""

from .model import (
    Assertion,
    ExtractionRun,
    KnowledgeGraph,
    Node,
    PassRecord,
    Provenance,
    make_assertion_id,
    make_node_id,
    slugify,
)
from .ingest import (
    CROSS_GRAPH_PREDICATES,
    completeness_note,
    graph_from_extraction,
)
from .rdf import QUERIES, run_query, to_jsonld, to_rdf, to_turtle

__all__ = [
    "KnowledgeGraph", "Node", "Assertion", "Provenance", "ExtractionRun",
    "PassRecord", "make_node_id", "make_assertion_id", "slugify",
    "graph_from_extraction", "completeness_note", "CROSS_GRAPH_PREDICATES",
    "to_rdf", "to_turtle", "to_jsonld", "run_query", "QUERIES",
]
