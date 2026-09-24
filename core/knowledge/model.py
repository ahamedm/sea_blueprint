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
precondition for the correction-merge design (`YB-009` §9b).

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
    # A design technique naming the NFR it is the mechanism for. The NFR lives in
    # REQ-G, so this is a cross-graph link like the rest — and it is the one that
    # makes an NFR realization checkable rather than merely asserted.
    "realizes_quality_attribute", "realizes_quality_attributes",
})


# Node kinds that ARE requirements. One definition, because three places need to
# agree on it: reconciliation scopes `implements_*` targets to these kinds,
# ingest only classifies a record's `requirement_type` when its kind is here, and
# the realization report lists exactly these as the requirement side. Three
# copies would drift and the drift would be silent — a requirement that quietly
# stops being reported as uncovered.
REQUIREMENT_KINDS = frozenset({
    "Requirement",
    "BusinessRequirement",
    "FunctionalRequirement",
    "NonFunctionalRequirement",
    "ConstraintRequirement",
    "PlatformExtensibilityRequirement",
    "PlatformMultiTenancyRequirement",
    "PlatformCompatibilityRequirement",
})


# The ontology class each cross-graph predicate realizes, when the predicate
# itself implies one. Declared next to the predicate set so ingest (writing the
# extraction-time reference) and reconciliation (writing the bound link) cannot
# disagree about what an `implements_*` edge IS.
PREDICATE_ONTOLOGY_CLASS = {
    # `implements_*` bound to a requirement IS the ontology's
    # RequirementRealization — the REQ<->ARC join, modelled as a first-class
    # resource precisely so the link can carry evidence and a coverage verdict.
    "implements_requirement": "RequirementRealization",
    "implements_functional_requirement": "RequirementRealization",
    "implements_non_functional_requirement": "RequirementRealization",
    # Authorization: the Initiative scoping edges that make the cross-graph join
    # tractable when requirement identifiers are lost or paraphrased.
    "delivers_initiative": "InitiativeDelivery",
    "authorised_by_initiative": "RequirementAuthorization",
}


def ontology_class_for_predicate(predicate: str) -> Optional[str]:
    """The class a cross-graph predicate's edge realizes, or None.

    Only meaningful for predicates in `CROSS_GRAPH_PREDICATES`; an ordinary local
    edge carries whatever class extraction gave it.
    """
    if predicate not in CROSS_GRAPH_PREDICATES:
        return None
    return PREDICATE_ONTOLOGY_CLASS.get(predicate)


# Provenance sources that outrank agent output on conflict.
HUMAN_SOURCES = frozenset({
    SOURCE_HUMAN_ARCHITECT, SOURCE_HUMAN_ANALYST, SOURCE_HUMAN_REVIEWER,
})


@dataclass
class Provenance:
    """Where one assertion came from.

    `run_id` is what makes re-extraction auditable: assertions from an earlier
    run remain attributable after a newer run supersedes them.

    `domain_pack` is the vocabulary that was in force when the assertion was made
    (`payment_processing@0.1.0`, or empty for the base vocabulary). Recorded per
    assertion rather than read from configuration, because those diverge the moment
    an Initiative is re-extracted under a different pack — and the question an
    auditor asks is which vocabulary produced *this* fact, not which pack is
    selected now. Without it, a vocabulary change is indistinguishable from a
    content change.
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
    domain_pack: str = ""

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
    # What KIND of document this run read — `requirements` or `architecture`.
    # Recorded because reconciliation has to tell the two sides apart: a
    # cross-graph predicate claims its referent lives in the OTHER graph, so a
    # candidate from the opposite side outranks one from the same side. Without
    # this the side was unknowable and every candidate was ranked on wording
    # alone. Empty means "not recorded" (older revisions) and is not treated as
    # either side.
    document_type: str = ""
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

# Identity scope — how far an identifier's authority extends, and therefore
# whether it may be used as a JOIN KEY. Mirrors `IdentityScope` in sea_common.
#
# The distinction that matters: `NFR-PS-001` read out of a markdown file is a
# real label and worth keeping, but it is not an enterprise identity. Two
# documents may both number a requirement `FR-001`. Matching on such a label
# globally produces confident WRONG joins — worse than missing joins, because it
# makes the audit wrong rather than incomplete.
SCOPE_ENTERPRISE = "ENTERPRISE"
SCOPE_INITIATIVE = "INITIATIVE"
SCOPE_DOCUMENT = "DOCUMENT"
SCOPE_RUN = "RUN"

# Scopes whose identifiers are unique beyond the document that stated them, and
# so are safe to match on. DOCUMENT and RUN are deliberately excluded.
GLOBALLY_MATCHABLE_SCOPES = frozenset({SCOPE_ENTERPRISE})

# Reference types that denote a system of record rather than a source document.
# An identifier read out of a document is given the document as its `system`.
#
# REQUIREMENT_KEY is in here deliberately, and it is the subtle one: a
# requirements-tooling key (Jira, Azure DevOps, DOORS) IS an enterprise identity,
# while `NFR-PS-001` typed as a heading in a markdown file is not. The type alone
# cannot separate them — the `system` does — so a document label is typed `OTHER`
# rather than REQUIREMENT_KEY, and REQUIREMENT_KEY plus a named tooling system is
# unambiguously the managed case.
MANAGED_REFERENCE_TYPES = frozenset({
    "CMDB_CI", "EA_REPOSITORY_ID", "ASSET_ID", "CLOUD_RESOURCE_ID", "PPM_ID",
    "REQUIREMENT_KEY", "TECH_REGISTRY_ID", "CATALOG_ENTRY",
})


@dataclass
class ExternalReference:
    """One identifier a construct is known by, WITH its kind.

    Recorded as a structure rather than a bare string because the string throws
    away exactly what decides how the identifier may be used:

    - `reference_type` — a CMDB CI and a document heading are not the same claim
    - `system` — which system of record holds it (the source document, for a label)
    - `scope` — whether it may be matched on at all
    - `is_authoritative` — which of several identifiers is the source of truth

    Flattening these to `List[str]` was the previous design, and it made two
    identifiers of different kinds indistinguishable at the point of use — which
    is why reconciliation could not tell an enterprise key from a document label
    and treated both as definitive.
    """

    identifier: str
    system: str = ""
    reference_type: str = "OTHER"
    scope: str = SCOPE_DOCUMENT
    uri: str = ""
    is_authoritative: bool = False
    attribute_scope: List[str] = field(default_factory=list)
    notes: str = ""

    @property
    def key(self) -> str:
        """Canonical display form. `identifier` alone is ambiguous across systems."""
        if not self.system:
            return self.identifier
        return f"{self.system}:{self.identifier}"

    @property
    def is_join_key(self) -> bool:
        """Whether reconciliation may match on this identifier."""
        return self.scope in GLOBALLY_MATCHABLE_SCOPES

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in ("", None, [], False)}


def document_reference(identifier: str, document_ref: str = "", scope: str = SCOPE_DOCUMENT) -> ExternalReference:
    """A label read out of a source document.

    Typed `OTHER`, NOT `REQUIREMENT_KEY`, and the distinction is load-bearing:
    `REQUIREMENT_KEY` means an identifier in requirements tooling (Jira, DOORS),
    which is an enterprise identity. A heading in a markdown file is not. Typing
    both the same would make `reference_type` useless for telling them apart, and
    the type is half of what decides match authority.

    `system` is the document, so the label stays traceable to where it came from
    without being promoted into an identity it never had.
    """
    return ExternalReference(
        identifier=str(identifier).strip(),
        system=document_ref or "",
        reference_type="OTHER",
        scope=scope,
        is_authoritative=True,
    )


@dataclass
class Node:
    """Identity only. Every fact about it is an assertion."""

    id: str
    kind: str
    label: str
    # Typed identifiers. Prefer this.
    external_references: List[ExternalReference] = field(default_factory=list)
    # Flat identifier strings, kept in sync for display and for code that only
    # needs "what is this thing called elsewhere?" (a C4 table, a search box).
    # Derived from `external_references` on write, so the two cannot disagree;
    # it carries NO scope, so never match on it — see `matchable_refs`.
    external_refs: List[str] = field(default_factory=list)

    def add_external_reference(self, ref: ExternalReference) -> None:
        """Merge a reference, keyed on system+identifier.

        Folding rather than appending: re-extraction must not accumulate
        duplicate identifiers for the same construct. An existing entry gains
        anything the new one knows (a scope, a type, authority) without losing
        what it already had.
        """
        if not ref.identifier:
            return
        for existing in self.external_references:
            if existing.identifier != ref.identifier:
                continue
            # Same identifier. An unknown system (empty) matches anything, since
            # "we do not know where this came from" is not evidence that it came
            # from somewhere else — folding lets the better-informed reference
            # fill in what the first one could not.
            existing_system = (existing.system or "").lower()
            new_system = (ref.system or "").lower()
            if existing_system and new_system and existing_system != new_system:
                continue
            if existing.reference_type in ("", "OTHER") and ref.reference_type:
                existing.reference_type = ref.reference_type
            if existing.scope in ("", SCOPE_DOCUMENT) and ref.scope:
                existing.scope = ref.scope
            if not existing.system and ref.system:
                existing.system = ref.system
            existing.is_authoritative = existing.is_authoritative or ref.is_authoritative
            if not existing.uri and ref.uri:
                existing.uri = ref.uri
            self._sync_flat_refs()
            return
        self.external_references.append(ref)
        self._sync_flat_refs()

    def _sync_flat_refs(self) -> None:
        # Prefer the bare identifier: it is what a human searches for and what
        # the UI already renders. `key` (system:identifier) is available on the
        # typed record for callers that need to disambiguate.
        flat = [r.identifier for r in self.external_references if r.identifier]
        for value in self.external_refs:
            if value not in flat:
                # Never drop a ref that arrived as a plain string; it may predate
                # the typed field or come from a source we cannot type.
                flat.append(value)
        self.external_refs = flat

    def matchable_refs(self) -> Tuple[str, ...]:
        """Only the identifiers reconciliation may match on.

        The whole point of typing them. A document-local `FR-001` is a
        legitimate label and a terrible join key.
        """
        return tuple(r.identifier for r in self.external_references if r.is_join_key)

    def to_dict(self) -> Dict[str, Any]:
        # `external_references` needs the manual form: `asdict` would recurse the
        # dataclass but cannot drop empties, and the typed records are the ones
        # that most need keeping small in a stored revision.
        data = asdict(self)
        data["external_references"] = [r.to_dict() for r in self.external_references]
        return data


# Predicates under which citing a document-local identifier IS identity.
#
# A requirements document numbers its requirements; an architecture document that
# cites `FR-PM-001` is quoting that key, not inventing one. Restricted to the
# `implements_*` family, which asserts exactly that ("this architecture answers
# THAT requirement"). Every other cross-graph predicate joins on meaning, where a
# shared label across two documents is a coincidence to assess rather than a fact.
#
# Defined here because BOTH the matcher (which may return a definitive score) and
# the graph queries (which decide whether a reference already names a node) need
# it, and two copies would drift into two different answers to "is this bound?".
IDENTITY_BY_CITATION_PREDICATES = frozenset(
    {
        "implements_requirement",
        "implements_functional_requirement",
        "implements_non_functional_requirement",
    }
)


def reference_targets_a_node(graph: "KnowledgeGraph", a: Assertion) -> Optional[Node]:
    """The node a cross-graph reference ALREADY names, or None.

    Three ways a reference can name a node, and all three count:

    1. **It holds the node id** — a bound link, the shape reconciliation writes.
    2. **Its text equals a node's label.** Ingest keeps a cross-graph target as a
       literal because it refuses to invent a local node for it; when a node with
       exactly that label already exists, the reference is a link in substance and
       there is nothing to reconcile. Refusing to see that reported working links
       as unresolved — and it is why the label check has always been here.
    3. **Its text equals a node's external identifier, under an `implements_*`
       predicate** (`IDENTITY_BY_CITATION_PREDICATES`). This is the citation case:
       `implements_requirement -> FR-PM-001` names the requirement carrying
       `FR-PM-001` as its key. Without it, a verbatim-preserved requirement id
       could still not join across documents, which was the defect this item
       existed to fix.

    When more than one node carries the identifier, the strongest claim wins: a
    key held in a system of record outranks one numbered by a document. Only then
    does document order decide, and a wrong pick stays reviewable and reversible.

    Shared by `KnowledgeGraph.unresolved_references`, the matcher and the
    realization report so they cannot disagree about whether a claim is bound.
    """
    if a.object:
        return graph.nodes.get(a.object)

    text = (a.value or "").strip().lower()
    if not text:
        return None

    for node in graph.nodes.values():
        if node.label.strip().lower() == text:
            return node

    if a.predicate not in IDENTITY_BY_CITATION_PREDICATES:
        return None

    # Requirement kinds only, and only a REQUIREMENT_KEY: the predicate's range
    # says the referent IS a requirement, and the reference type decides whether
    # the identifier is one. An identifier the architecture profile recorded as a
    # plain document label (`OTHER`) is a string two documents may legitimately
    # share, so it stays evidence. Without this the citation rule would bind on
    # any shared identifier — the confident wrong join this module was typed to
    # prevent — and would reach across kinds as well.
    #
    # A key held in a system of record wins over a document-local one, because two
    # nodes really can carry `FR-PM-001` (one in Jira, one numbered by a brief)
    # and the enterprise key is the stronger claim. Only then does document order
    # decide, and a wrong pick stays reviewable and reversible.
    matches = [
        node
        for node in graph.nodes.values()
        if node.kind in REQUIREMENT_KINDS
        and any(
            ref.identifier.strip().lower() == text
            and ref.reference_type.strip().upper() == "REQUIREMENT_KEY"
            for ref in node.external_references
        )
    ]
    if not matches:
        return None
    matches.sort(key=lambda node: min(
        (0 if ref.is_join_key else 1)
        for ref in node.external_references
        if ref.identifier.strip().lower() == text
    ))
    return matches[0]


@dataclass
class KnowledgeGraph:
    """The canonical form. Everything else derives from this."""
    nodes: Dict[str, Node] = field(default_factory=dict)
    assertions: Dict[str, Assertion] = field(default_factory=dict)
    runs: Dict[str, ExtractionRun] = field(default_factory=dict)
    # node id -> the `document_type` of the run that DECLARED it. Which side of
    # the cross-graph reconciliation a node belongs to is a property of where it
    # was read from, not of the node, and a requirement extracted without a
    # single triple has no assertion to infer that from — so it is recorded at
    # declaration. Empty for nodes that were only ever inferred from a triple
    # endpoint, which is honest: we know they were mentioned, not what declared
    # them.
    declared_by: Dict[str, str] = field(default_factory=dict)
    
    # Versioning fields for the "Living System"
    version_id: str = ""                # Unique ID for this graph state (e.g., hash or UUID)
    parent_version_id: str = ""         # ID of the graph this was derived from
    label: str = ""                     # Human-readable label (e.g., "Baseline v1.2", "INIT-001 Draft")

    # -- construction ------------------------------------------------------

    def add_node(
        self,
        kind: str,
        label: str,
        external_refs: Optional[List[str]] = None,
        external_references: Optional[List[ExternalReference]] = None,
    ) -> str:
        """Add or merge a node. Merging never overwrites a non-empty field.

        Both identifier forms are accepted. A bare string is a reference whose
        kind we do not know, so it is typed as a document-local label rather than
        assumed to be an enterprise key — assuming the stronger claim is how a
        `FR-001` from one document ends up matched against `FR-001` from another.
        """
        nid = make_node_id(kind, label)
        existing = self.nodes.get(nid)
        if existing is None:
            existing = Node(id=nid, kind=kind, label=label)
            self.nodes[nid] = existing

        for ref in external_references or []:
            existing.add_external_reference(ref)

        for value in external_refs or []:
            if not value:
                continue
            text = str(value).strip()
            if text and text not in existing.external_refs:
                # Deliberately NO typed record here. A bare string is an
                # identifier whose kind we do not know, and synthesising a typed
                # entry for it would fabricate a claim (a system, a scope) that
                # the caller never made — and would then collide with the real
                # typed record when one arrives. Left untyped, it contributes
                # nothing to `matchable_refs`, which is the conservative reading.
                existing.external_refs.append(text)
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
        that is the correction-merge requirement (`YB-009` §9b) handled at the
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

        "Not a node" is decided by `reference_targets_a_node`, the same rule the
        realization report uses. Two rules would eventually disagree, and the
        disagreement would be a claim that is unresolved to one reader and bound
        to the other — exactly the pair of readings this module exists to keep
        apart.
        """
        out = []
        for a in self.active():
            if a.predicate not in CROSS_GRAPH_PREDICATES:
                continue
            if reference_targets_a_node(self, a) is not None:
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
