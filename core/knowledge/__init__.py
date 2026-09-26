"""
Canonical knowledge model and serialisation.

The layer between extraction output and storage/views. Owns the transforms that
were previously unowned, so five representations cannot drift apart.

  model.py      — canonical graph: nodes, assertions, runs, provenance
  serialise.py  — canonical graph <-> JSON (the round trip the store depends on)
  ingest.py     — extraction output -> canonical graph, and graph -> graph merge
  review.py     — human review decisions and the audit trail they produce
  reconcile.py  — unresolved cross-graph reference -> bound link
  realization.py— which requirements have an architectural answer, both directions
  store.py      — working set, immutable revisions, baseline freezing
  rdf.py        — canonical graph -> RDF (plain triples + assertion resources)

The dependency direction is one-way: model knows nothing, serialise/review know
model, reconcile knows review, realization knows model (+ reconcile's vocabulary),
store knows serialise + review. Nothing here imports the web layer.
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
    REQUIREMENT_KINDS,
    SOURCE_DESIGN_ASSISTANT,
    apply_delta,
    compute_graph_delta,
    make_assertion_id,
    make_node_id,
    ontology_class_for_predicate,
    slugify,
)
from .rdf import QUERIES, run_query, to_jsonld, to_rdf, to_turtle
from .realization import (
    COVERAGE_FULL,
    COVERAGE_NONE,
    COVERAGE_PARTIAL,
    COVERAGE_UNRESOLVED,
    REALIZATION_PREDICATES,
    RequirementRealization,
    realization_coverage,
    realization_edges,
    realization_report,
    realization_state,
    requirement_nodes,
    unbound_claims,
    unmet_obligations,
    unrealized_requirements,
)
from .drafts import DesignDraft, DesignDraftStore
from .quality import (
    COVERAGE_ANSWERED,
    COVERAGE_ARCHITECTURE_GAP,
    COVERAGE_UNADDRESSED,
    COVERAGE_UNASKED,
    QUALITY_ATTRIBUTE_KIND,
    AttributeCoverage,
    attribute_nodes,
    quality_report,
    quality_state,
)
from .reconcile import (
    DEFAULT_MATCH_THRESHOLD,
    EXPECTED_TARGET_KINDS,
    PLAUSIBLE_TARGET_KINDS,
    SIDE_ARCHITECTURE,
    SIDE_REQUIREMENTS,
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
    "REQUIREMENT_KINDS",
    "ontology_class_for_predicate",
    "SOURCE_DESIGN_ASSISTANT",
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
    "PLAUSIBLE_TARGET_KINDS",
    "REQUIREMENT_KINDS",
    "SIDE_REQUIREMENTS",
    "SIDE_ARCHITECTURE",
    # realization
    "realization_edges",
    "realization_state",
    "realization_report",
    "realization_coverage",
    "requirement_nodes",
    "unrealized_requirements",
    "unbound_claims",
    "unmet_obligations",
    "RequirementRealization",
    "REALIZATION_PREDICATES",
    "COVERAGE_NONE",
    "COVERAGE_UNRESOLVED",
    "COVERAGE_PARTIAL",
    "COVERAGE_FULL",
    # quality — the attribute-shaped census
    "quality_state",
    "quality_report",
    "attribute_nodes",
    "AttributeCoverage",
    "QUALITY_ATTRIBUTE_KIND",
    "COVERAGE_ANSWERED",
    "COVERAGE_ARCHITECTURE_GAP",
    "COVERAGE_UNASKED",
    "COVERAGE_UNADDRESSED",
    # store
    "RevisionStore",
    "Revision",
    "Snapshot",
    "BaselineNotReady",
    # design drafts — a proposal staged before it is applied
    "DesignDraftStore",
    "DesignDraft",
    # rdf
    "to_rdf",
    "to_turtle",
    "to_jsonld",
    "run_query",
    "QUERIES",
]
