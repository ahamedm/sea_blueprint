"""
Canonical knowledge model.

WHY THIS EXISTS
---------------
Five representations of the same knowledge were in play with no owner for the
transformations between them:

    documents  -> Markdown
    extraction -> Pydantic models (per-pass, per-chunk)
    ontology   -> LinkML
    storage    -> RDF
    UI         -> graph views

That gap (`extraction -> ??? -> RDF`) is the highest-risk component in the
system, because every extraction change breaks it and it is the only thing that
can guarantee a well-formed graph. This module is the `???`.

DESIGN DECISIONS
----------------
**Everything is an assertion.** A node holds only identity (kind + label). Every
other fact — a classification, a containment, a responsibility — is an
`Assertion`, and every assertion carries its own provenance and verification
state. That is what makes "asserted by an agent" structurally distinguishable
from "confirmed by a human", rather than a convention someone has to remember.

**Verification is per-assertion, not per-node.** A reviewer may accept
`Payment Orchestrator --part_of--> PGP` without vouching for that element's
responsibilities. The model is fine-grained; the UI can offer coarse-grained
affordances (confirm everything on this element) on top without the model losing
the distinction.

**Node identity is stable and derived, never run-scoped.** `kind:label` slugged.
If identity were assigned per run, re-extraction would duplicate the whole graph
and human corrections would have nothing durable to attach to. Stability is the
precondition for the correction-merge design (TODO item 9b).

**Incompleteness is first-class.** A run records which passes failed or came back
empty. Without this, a partial extraction is indistinguishable from a complete
one, and the auditor reports extraction artifacts as architectural gaps —
confidently wrong, which is worse than no audit.
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


# ============================================================================
# Identity
# ============================================================================

_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    return _SLUG.sub("_", (text or "").strip().lower()).strip("_")


def make_node_id(kind: str, label: str) -> str:
    """Stable, derived node identity.

    Deliberately deterministic: the same kind and label in a different run
    produce the same id, so re-extraction converges rather than duplicating, and
    a human correction has something durable to attach to.
    """
    return f"{slugify(kind)}:{slugify(label)}"


def make_assertion_id(subject: str, predicate: str, obj: Optional[str],
                      value: Optional[str]) -> str:
    """Assertion identity, content-addressed.

    Two observations of the same fact must collapse to one assertion — that is
    what lets a re-run raise confidence or add source text without creating a
    parallel duplicate.
    """
    raw = "\u0000".join([subject, predicate, obj or "", value or ""])
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"a_{digest}"


# ============================================================================
# Provenance and verification
# ============================================================================

# Kept as plain strings rather than enums so unknown values from a source are
# preserved rather than rejected — the same posture as the extraction schemas.
# Provenance sources
SOURCE_EXTRACTION = "EXTRACTION_AGENT"
SOURCE_HUMAN_ARCHITECT = "HUMAN_ARCHITECT"
SOURCE_HUMAN_ANALYST = "HUMAN_ANALYST"
SOURCE_HUMAN_REVIEWER = "HUMAN_REVIEWER"
SOURCE_ENRICHMENT = "ENRICHMENT"
SOURCE_IMPORTED = "IMPORTED"
SOURCE_BASELINE_MERGE = "BASELINE_MERGE"  # Fact promoted from Initiative to System Baseline

# Assertion Lifecycle / Scope
SCOPE_INITIATIVE = "INITIATIVE_PROPOSAL"  # A change proposed by a specific initiative
SCOPE_BASELINE = "SYSTEM_BASELINE"        # Established, persistent system truth
SCOPE_DOMAIN = "DOMAIN_TRUTH"             # Universal domain concept (e.g., "PAN is sensitive")

STATUS_UNVERIFIED = "UNVERIFIED"
STATUS_VERIFIED = "VERIFIED"
STATUS_CORRECTED = "CORRECTED"
STATUS_DISPUTED = "DISPUTED"
STATUS_SUPERSEDED = "SUPERSEDED"

# Predicates whose object is a REFERENCE into another graph rather than a node
# in this one — `traces_to_goal`, `implements_requirement` and friends point at
# requirements, goals and capabilities that live in a different graph.
#
# Defined here rather than in ingest.py because the integrity queries need the
# same definition; two copies would drift.
CROSS_GRAPH_PREDICATES = frozenset({
    "implements_requirement", "implements_functional_requirement",
    "implements_non_functional_requirement",
    "traces_to_goal", "traces_to_goals", "traces_to_capability",
    "traces_to_capabilities", "traces_to_process", "traces_to_processes",
    "satisfies_quality_attribute", "supports_capability", "supports_business_capability",
    "delivers_initiative", "governed_by_rule", "governed_by_rules", "addresses_goal",
})


# Provenance sources that outrank agent output on conflict.
HUMAN_SOURCES = frozenset({
    SOURCE_HUMAN_ARCHITECT, SOURCE_HUMAN_ANALYST, SOURCE_HUMAN_REVIEWER,
})


@dataclass
class Provenance:
    """Where one assertion came from.

    `run_id` is what makes re-extraction auditable: assertions from an earlier
    run remain attributable after a newer run supersedes them.
    """

    source_type: str = SOURCE_EXTRACTION
    run_id: str = ""
    pass_name: str = ""
    model_id: str = ""
    chunk_label: str = ""
    asserted_at: str = ""
    asserted_by: str = ""
    derived_from: str = ""
    correction_note: str = ""

    @property
    def is_human(self) -> bool:
        return self.source_type in HUMAN_SOURCES

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in ("", None)}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ============================================================================
# Assertion
# ============================================================================

@dataclass
class Assertion:
    """One claim about the world, with its own provenance, scope, and review state."""

    id: str
    subject: str                       # NodeId
    predicate: str
    object: Optional[str] = None       # NodeId, when the object is a node
    value: Optional[str] = None        # literal, when the object is not a node
    confidence: float = 0.0
    source_text: str = ""
    ontology_class: Optional[str] = None
    provenance: Provenance = field(default_factory=Provenance)
    status: str = STATUS_UNVERIFIED
    superseded_by: Optional[str] = None
    
    # Living System fields
    scope: str = SCOPE_INITIATIVE      # INITIATIVE_PROPOSAL | SYSTEM_BASELINE | DOMAIN_TRUTH
    initiative_id: Optional[str] = None # If scope is INITIATIVE_PROPOSAL, which one?

    @property
    def is_human(self) -> bool:
        return self.provenance.is_human

    @property
    def is_active(self) -> bool:
        """Superseded assertions stay in the graph for lineage, but are excluded
        from queries and audits by default."""
        return self.superseded_by is None and self.status != STATUS_SUPERSEDED

    @property
    def target(self) -> str:
        return self.object if self.object is not None else (self.value or "")

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["provenance"] = self.provenance.to_dict()
        return d


# ============================================================================
# Extraction run — where incompleteness lives
# ============================================================================

RUN_COMPLETE = "COMPLETE"
RUN_PARTIAL = "PARTIAL"
RUN_FAILED = "FAILED"
RUN_UNKNOWN = "UNKNOWN"
"""No pass-level data was available.

Deliberately distinct from both FAILED and COMPLETE. Reporting UNKNOWN as FAILED
is a false alarm; reporting it as COMPLETE is a false assurance, and the second
is the dangerous one — it makes a graph of unknown completeness look safe to
audit against. Consumers must treat UNKNOWN as NOT evidence of absence."""


@dataclass
class PassRecord:
    """Outcome of one pass over one chunk. Failures are data, not log lines."""

    pass_name: str
    chunk_label: str
    outcome: str                      # ok | empty | failed
    path: str = ""                    # structured | text | none
    elapsed: float = 0.0
    error: str = ""
    triples_produced: int = 0


@dataclass
class ExtractionRun:
    """One extraction of one document.

    `completeness` is the field that stops a partial run being mistaken for a
    complete one. A consumer that ignores it will report missing content as
    architectural gaps.
    """

    id: str
    document_ref: str = ""
    document_hash: str = ""
    document_chars: int = 0
    model_id: str = ""
    started_at: str = ""
    completed_at: str = ""
    chunk_count: int = 0
    passes: List[PassRecord] = field(default_factory=list)
    completeness: str = RUN_COMPLETE

    def compute_completeness(self) -> str:
        if not self.passes:
            # Absence of pass records is ignorance, not failure. Older outputs
            # (and profiles that do not emit per-pass metadata) land here.
            return RUN_UNKNOWN
        failed = sum(1 for p in self.passes if p.outcome == "failed")
        empty = sum(1 for p in self.passes if p.outcome == "empty")
        if failed == len(self.passes):
            return RUN_FAILED
        if failed or empty:
            return RUN_PARTIAL
        return RUN_COMPLETE

    @property
    def failed_passes(self) -> List[PassRecord]:
        return [p for p in self.passes if p.outcome == "failed"]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["passes"] = [asdict(p) for p in self.passes]
        return d


# ============================================================================
# Node and graph
# ============================================================================

@dataclass
class Node:
    """Identity only. Every fact about it is an assertion."""

    id: str
    kind: str
    label: str
    external_refs: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class KnowledgeGraph:
    """The canonical form. Everything else derives from this."""

    nodes: Dict[str, Node] = field(default_factory=dict)
    assertions: Dict[str, Assertion] = field(default_factory=dict)
    runs: Dict[str, ExtractionRun] = field(default_factory=dict)
    
    # Versioning fields for the "Living System"
    version_id: str = ""                # Unique ID for this graph state (e.g., hash or UUID)
    parent_version_id: str = ""         # ID of the graph this was derived from
    label: str = ""                     # Human-readable label (e.g., "Baseline v1.2", "INIT-001 Draft")

    # -- construction ------------------------------------------------------

    def add_node(self, kind: str, label: str, external_refs: Optional[List[str]] = None) -> str:
        """Add or merge a node. Merging never overwrites a non-empty field."""
        nid = make_node_id(kind, label)
        existing = self.nodes.get(nid)
        if existing is None:
            self.nodes[nid] = Node(id=nid, kind=kind, label=label,
                                   external_refs=list(external_refs or []))
        elif external_refs:
            for ref in external_refs:
                if ref and ref not in existing.external_refs:
                    existing.external_refs.append(ref)
        return nid

    def add_assertion(
        self,
        subject: str,
        predicate: str,
        obj: Optional[str] = None,
        value: Optional[str] = None,
        confidence: float = 0.0,
        source_text: str = "",
        ontology_class: Optional[str] = None,
        provenance: Optional[Provenance] = None,
        status: str = STATUS_UNVERIFIED,
        scope: str = SCOPE_INITIATIVE,
        initiative_id: Optional[str] = None,
    ) -> Assertion:
        """Add or fold an assertion.

        Folding rules matter: re-observing the same fact must raise confidence
        and keep the fuller source text, never create a parallel duplicate. And a
        human assertion must not be silently overwritten by a later agent run —
        that is the correction-merge requirement (TODO item 9b) handled at the
        point of write rather than as an afterthought.
        """
        aid = make_assertion_id(subject, predicate, obj, value)
        prov = provenance or Provenance()
        new = Assertion(id=aid, subject=subject, predicate=predicate, object=obj,
                        value=value, confidence=confidence, source_text=source_text,
                        ontology_class=ontology_class, provenance=prov, status=status,
                        scope=scope, initiative_id=initiative_id)

        existing = self.assertions.get(aid)
        if existing is None:
            self.assertions[aid] = new
            return new

        # A human assertion outranks an agent re-observation: keep the human
        # provenance and status, but let a fuller source text through.
        if existing.is_human and not prov.is_human:
            if len(source_text) > len(existing.source_text):
                existing.source_text = source_text
            return existing

        if confidence > existing.confidence:
            existing.confidence = confidence
        if len(source_text) > len(existing.source_text):
            existing.source_text = source_text
        if not existing.ontology_class and ontology_class:
            existing.ontology_class = ontology_class
        if prov.is_human:
            existing.provenance = prov
            existing.status = status if status != STATUS_UNVERIFIED else existing.status
        return existing

    # -- queries -----------------------------------------------------------

    def active(self) -> Iterable[Assertion]:
        return (a for a in self.assertions.values() if a.is_active)

    def find(self, predicate: Optional[str] = None, subject: Optional[str] = None,
             obj: Optional[str] = None) -> List[Assertion]:
        out = []
        for a in self.active():
            if predicate and a.predicate != predicate:
                continue
            if subject and a.subject != subject:
                continue
            if obj and a.object != obj:
                continue
            out.append(a)
        return out

    def assertions_of(self, node_id: str) -> List[Assertion]:
        return self.find(subject=node_id)

    def nodes_of_kind(self, kind: str) -> List[Node]:
        return [n for n in self.nodes.values() if n.kind == kind]

    def predicates(self) -> Set[str]:
        return {a.predicate for a in self.active()}

    def human_assertions(self) -> List[Assertion]:
        return [a for a in self.active() if a.is_human]

    # -- integrity ---------------------------------------------------------

    def dangling_assertions(self) -> List[Assertion]:
        """Assertions pointing at a node id that does not exist.

        A reference to something never extracted is different from a reference
        that was extracted and dropped, and both differ from a genuine gap. This
        is the query that separates them.
        """
        out = []
        for a in self.active():
            if a.subject not in self.nodes:
                out.append(a)
            elif a.object and a.object not in self.nodes:
                out.append(a)
        return out

    def unresolved_references(self) -> List[Assertion]:
        """Cross-graph links whose target is not a node in THIS graph.

        Unresolved is the expected state, not a defect: establishing the link is
        the extraction phase's job and resolving it against the other graph is a
        later reconciliation step. Surfacing them is what gives reconciliation
        something to work on instead of silently dropping the references.

        Note these assert with `value`, not `object` — the target is deliberately
        kept as a reference rather than turned into a local node, so an earlier
        version of this query (which checked only `object`) reported zero while
        nine references sat there unresolvable.
        """
        labels = {n.label.strip().lower() for n in self.nodes.values()}
        ids = set(self.nodes)
        out = []
        for a in self.active():
            if a.predicate not in CROSS_GRAPH_PREDICATES:
                continue
            target = a.target
            if target in ids or target.strip().lower() in labels:
                continue
            out.append(a)
        return out

    def stats(self) -> Dict[str, Any]:
        kinds: Dict[str, int] = {}
        for n in self.nodes.values():
            kinds[n.kind] = kinds.get(n.kind, 0) + 1
        active = list(self.active())
        return {
            "nodes": len(self.nodes),
            "assertions": len(active),
            "superseded": len(self.assertions) - len(active),
            "human_assertions": len(self.human_assertions()),
            "dangling": len(self.dangling_assertions()),
            "unresolved_references": len(self.unresolved_references()),
            "node_kinds": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
            "runs": len(self.runs),
            "run_completeness": [r.completeness for r in self.runs.values()],
            "version_id": self.version_id,
            "parent_version_id": self.parent_version_id,
            "label": self.label,
        }


# ============================================================================
# Versioning and Diffing
# ============================================================================

@dataclass
class GraphDelta:
    """The difference between two graph versions."""
    added_nodes: List[Node] = field(default_factory=list)
    removed_nodes: List[Node] = field(default_factory=list)
    added_assertions: List[Assertion] = field(default_factory=list)
    removed_assertions: List[Assertion] = field(default_factory=list)
    changed_assertions: List[Tuple[Assertion, Assertion]] = field(default_factory=list) # (old, new)


def compute_graph_delta(old_graph: KnowledgeGraph, new_graph: KnowledgeGraph) -> GraphDelta:
    """Compute the structural difference between two graph versions.
    
    This is the core of the "Living System" merge logic. It allows us to see
    exactly what an Initiative changed relative to the Baseline.
    """
    delta = GraphDelta()
    
    # Node diff
    old_node_ids = set(old_graph.nodes.keys())
    new_node_ids = set(new_graph.nodes.keys())
    
    for nid in new_node_ids - old_node_ids:
        delta.added_nodes.append(new_graph.nodes[nid])
    for nid in old_node_ids - new_node_ids:
        delta.removed_nodes.append(old_graph.nodes[nid])
        
    # Assertion diff (using content-addressed IDs)
    old_assertion_ids = set(old_graph.assertions.keys())
    new_assertion_ids = set(new_graph.assertions.keys())
    
    for aid in new_assertion_ids - old_assertion_ids:
        delta.added_assertions.append(new_graph.assertions[aid])
    for aid in old_assertion_ids - new_assertion_ids:
        delta.removed_assertions.append(old_graph.assertions[aid])
        
    # Check for changes in existing assertions (e.g., status or confidence updates)
    for aid in old_assertion_ids & new_assertion_ids:
        old_a = old_graph.assertions[aid]
        new_a = new_graph.assertions[aid]
        if old_a != new_a:
            delta.changed_assertions.append((old_a, new_a))
            
    return delta


def apply_delta(graph: KnowledgeGraph, delta: GraphDelta) -> KnowledgeGraph:
    """Apply a GraphDelta to a KnowledgeGraph to produce a new version."""
    import copy
    new_graph = copy.deepcopy(graph)
    
    # Apply node changes
    for node in delta.added_nodes:
        new_graph.nodes[node.id] = node
    for node in delta.removed_nodes:
        new_graph.nodes.pop(node.id, None)
        
    # Apply assertion changes
    for assertion in delta.added_assertions:
        new_graph.assertions[assertion.id] = assertion
    for assertion in delta.removed_assertions:
        new_graph.assertions.pop(assertion.id, None)
    for old_a, new_a in delta.changed_assertions:
        new_graph.assertions[new_a.id] = new_a
        
    return new_graph
