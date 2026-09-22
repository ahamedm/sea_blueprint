"""
Canonical knowledge model and serialisation.

The layer between extraction output and storage/views. Owns the transforms that
were previously unowned, so five representations cannot drift apart.

  model.py      — canonical graph: nodes, assertions, runs, provenance
  serialise.py  — canonical graph <-> JSON (the round trip the store depends on)
  ingest.py     — extraction output -> canonical graph, and graph -> graph merge
  review.py     — human review decisions and the audit trail they produce
  reconcile.py  — unresolved cross-graph reference -> bound link
  store.py      — working set, immutable revisions, baseline freezing
  rdf.py        — canonical graph -> RDF (plain triples + assertion resources)

The dependency direction is one-way: model knows nothing, serialise/review know
model, reconcile knows review, store knows serialise + review. Nothing here
imports the web layer.
"""

from .ingest import (
    CROSS_GRAPH_PREDICATES,
    completeness_note,
    graph_from_extraction,
    merge_graphs,
)
from .model import (
    Assertion,
    ExtractionRun,
    GraphDelta,
    KnowledgeGraph,
    Node,
    PassRecord,
    Provenance,
    apply_delta,
    compute_graph_delta,
    make_assertion_id,
    make_node_id,
    slugify,
)
from .rdf import QUERIES, run_query, to_jsonld, to_rdf, to_turtle
from .reconcile import (
    DEFAULT_MATCH_THRESHOLD,
    EXPECTED_TARGET_KINDS,
    BulkResolveResult,
    Candidate,
    ReconcileError,
    ReferenceCandidates,
    bulk_resolve,
    match_score,
    normalise,
    reference_candidates,
    resolve_reference,
    significant_tokens,
)
from .review import (
    ACTION_CORRECT,
    ACTION_DISPUTE,
    ACTION_PROMOTE,
    ACTION_RESET,
    ACTION_RESOLVE,
    ACTION_VERIFY,
    Decision,
    PromotionResult,
    ReviewError,
    ReviewLog,
    ReviewProgress,
    apply_decisions,
    bulk_verify,
    correct,
    dispute,
    promote_to_baseline,
    reset,
    review_progress,
    verify,
)
from .serialise import (
    graph_from_dict,
    graph_to_dict,
)
from .store import (
    BaselineNotReady,
    Revision,
    RevisionStore,
    Snapshot,
)

__all__ = [
    # model
    "KnowledgeGraph",
    "Node",
    "Assertion",
    "Provenance",
    "ExtractionRun",
    "PassRecord",
    "GraphDelta",
    "make_node_id",
    "make_assertion_id",
    "slugify",
    "compute_graph_delta",
    "apply_delta",
    # ingest
    "graph_from_extraction",
    "merge_graphs",
    "completeness_note",
    "CROSS_GRAPH_PREDICATES",
    # serialisation
    "graph_to_dict",
    "graph_from_dict",
    # review
    "Decision",
    "ReviewLog",
    "ReviewProgress",
    "ReviewError",
    "PromotionResult",
    "apply_decisions",
    "verify",
    "correct",
    "dispute",
    "reset",
    "bulk_verify",
    "promote_to_baseline",
    "review_progress",
    "ACTION_VERIFY",
    "ACTION_CORRECT",
    "ACTION_DISPUTE",
    "ACTION_RESET",
    "ACTION_PROMOTE",
    "ACTION_RESOLVE",
    # reconcile
    "reference_candidates",
    "resolve_reference",
    "bulk_resolve",
    "match_score",
    "normalise",
    "significant_tokens",
    "ReferenceCandidates",
    "Candidate",
    "BulkResolveResult",
    "ReconcileError",
    "DEFAULT_MATCH_THRESHOLD",
    "EXPECTED_TARGET_KINDS",
    # store
    "RevisionStore",
    "Revision",
    "Snapshot",
    "BaselineNotReady",
    # rdf
    "to_rdf",
    "to_turtle",
    "to_jsonld",
    "run_query",
    "QUERIES",
]
