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
import uuid
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
# A design PROPOSAL, not an extraction. Kept separate from `SOURCE_EXTRACTION`
# because the difference is the one a reviewer has to see: an extractor reports
# what a document said, and a design agent proposes what could be built. Both are
# agent output and both are unreviewed, but conflating them would make "the
# architecture document said this" and "a model suggested this" indistinguishable
# in the audit trail.
SOURCE_DESIGN_ASSISTANT = "DESIGN_ASSISTANT"
SOURCE_BASELINE_MERGE = "BASELINE_MERGE"  # Fact promoted from Initiative to System Baseline

# Assertion Lifecycle / Scope
#
# These say WHERE A FACT STANDS — proposed by an initiative, established system
# truth, universal domain truth. They are NOT the identity scope below
# (`IDENTITY_SCOPE_*`), which says how far an identifier's authority reaches.
#
# The two were once both named `SCOPE_INITIATIVE`, and because the identity
# declaration came later in this file it silently won: ingest wrote the identity
# value `"INITIATIVE"` while this declaration, `Assertion.scope`'s default and
# `serialise`'s load default all said `"INITIATIVE_PROPOSAL"`. See
# `INITIATIVE_SCOPES` for what that cost and how it is handled.
SCOPE_INITIATIVE = "INITIATIVE_PROPOSAL"  # A change proposed by a specific initiative
SCOPE_BASELINE = "SYSTEM_BASELINE"        # Established, persistent system truth
SCOPE_DOMAIN = "DOMAIN_TRUTH"             # Universal domain concept (e.g., "PAN is sensitive")

# Every value a persisted assertion may carry meaning "proposed by an initiative".
#
# `"INITIATIVE"` is what graphs written before the collision above actually hold,
# because that is the value the shadowing constant produced. Those graphs are real,
# and a fact not recognised as an initiative proposal can NEVER be merged into the
# baseline: `promote_to_baseline` skips it without counting it, which is
# indistinguishable from there being nothing to promote. So both values are accepted
# on READ, and only `SCOPE_INITIATIVE` is ever written.
INITIATIVE_SCOPES = frozenset({SCOPE_INITIATIVE, "INITIATIVE"})


def is_initiative_scope(scope: str) -> bool:
    """Whether an assertion's scope means "proposed by an initiative"."""
    return scope in INITIATIVE_SCOPES

STATUS_UNVERIFIED = "UNVERIFIED"
STATUS_VERIFIED = "VERIFIED"
STATUS_CORRECTED = "CORRECTED"
STATUS_DISPUTED = "DISPUTED"
STATUS_SUPERSEDED = "SUPERSEDED"
STATUS_RETIRED = "RETIRED"

# What `superseded_by` holds when a human removes a fact outright, rather than
# replacing it with a corrected version.
#
# A plain `""` would not survive: `merge_graphs` carries the lineage only when it
# is truthy, so a re-extraction would fold the fresh observation back in and leave
# the fact active — the removal would silently undo itself on the next run. A
# non-empty sentinel is what makes "removed" durable, and it still says plainly
# that this assertion was retired rather than replaced.
RETIREMENT_MARK = "(retired)"

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
    # A named pattern the requirement MANDATES ("must be microservices"). Routed
    # for the reason the ontology gives: it is what lets the auditor check that a
    # mandated pattern is actually present, and an unrouted mandate is a claim
    # nothing can verify.
    "mandated_by",
})


# Predicates where `X <predicate> X` cannot be true of anything, whatever the
# domain. Not a closed vocabulary — the graph mints free-form predicates — but
# the set the write boundary refuses to store.
#
# WHY REFUSE AT ALL: a local model emitted `X part_of X` for the document's own
# system, four times across two independent runs, and bulk review then made all
# four VERIFIED (YB-052). A reflexive containment is not a borderline judgement:
# in C4 it makes the element its own containment root, and the emitted Structurizr
# DSL declares a system inside itself and is rejected by the parser. Nothing can
# be part of itself, so refusing it overrides no reviewer's judgement.
#
# Node ids are `slugify(kind):slugify(label)`, so a predicate whose subject and
# object must be different KINDS can never match here. That is why the set holds
# element-to-element structural, dependency and runtime edges only.
IRREFLEXIVE_PREDICATES = frozenset({
    # containment — the synonyms `CONTAINMENT_PREDICATES` in ingest.py routes
    "part_of", "belongs_to", "composed_of", "contains",
    # hosting and dependency
    "hosts", "hosted_on", "depends_on", "depends_on_components",
    "depends_on_systems", "depends_on_applications",
    # runtime coupling
    "connects_to", "calls", "invokes",
    # one element realizing another
    "implements",
    # a decision replacing an earlier one — never itself
    "supersedes",
    # One deployment INSTANCE serving a system. An instance serving itself is
    # impossible, and the write boundary is the cheapest place to say so (YB-044).
    "serves",
})


def reflexive_violation(subject: str, predicate: str, obj: Optional[str]) -> str:
    """The reason `subject <predicate> subject` is impossible, or "" when it is not.

    Returns a sentence rather than a bool because a refusal has to be *reported*,
    and a caller that only learns "no" cannot say which rule it hit.
    """
    if not subject or not obj or subject != obj:
        return ""
    if predicate not in IRREFLEXIVE_PREDICATES:
        return ""
    return (f"{predicate!r} is irreflexive — an element cannot be its own target; "
            f"refused at {subject!r}")


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


def new_revision_id() -> str:
    """A revision id that sorts by creation and does not collide within a second.

    Lives here rather than in one backend because every backend has to mint the same
    shape: a revision committed through the file store and one committed through the
    database must be interchangeable in an index, a diff or a `parent_id` chain.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"rev_{stamp}_{uuid.uuid4().hex[:4]}"


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
        from queries and audits by default.

        Three ways a fact leaves the active set, and they mean different things:
        `SUPERSEDED` (replaced by a correction or a resolution), `RETIRED` (removed
        by a human, with nothing put in its place), and any assertion carrying
        `superseded_by` — which is the lineage pointing at what replaced it, or at
        `RETIREMENT_MARK` when nothing did.
        """
        return self.superseded_by is None and self.status not in (
            STATUS_SUPERSEDED,
            STATUS_RETIRED,
        )

    @property
    def target(self) -> str:
        return self.object if self.object is not None else (self.value or "")

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["provenance"] = self.provenance.to_dict()
        return d


@dataclass
class RefusedAssertion:
    """A fact the graph declined to store, and why — a refusal is data, not a log line.

    Silently dropping is how four reflexive containments went unnoticed until the
    C4 view reported them, and then bulk review made them VERIFIED. Keeping the
    refusal on the run is what makes "the extractor asserted something impossible"
    a fact the run reports, rather than one a reader has to infer from an absence.
    """

    subject: str
    predicate: str
    object: str = ""
    value: str = ""
    reason: str = ""
    source_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v not in ("", None)}


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
    temperature: Optional[float] = None
    """The per-pass temperature override, when there was one. None means the pass
    ran at the agent's temperature. Recorded because a proposal whose quality is
    later questioned should be traceable to the sampler that produced it, and
    because it is the one sampling knob that varies BETWEEN passes of one run."""


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
    # Token usage for the whole run, as the provider reported it. A hosted model
    # bills per token, and a run that cannot say what it consumed cannot be
    # budgeted or compared against a cheaper one — which is the first question
    # asked when moving off a local server onto a paid endpoint. Empty means the
    # provider reported nothing (older runs, or a text-only path); that is not the
    # same as zero, so it is not defaulted to a number.
    usage: Dict[str, Any] = field(default_factory=dict)
    completeness: str = RUN_COMPLETE
    # ---- what the pipeline did with the output that produced this run ----
    #
    # `output_counts` is per output key: the records the profile EMITTED and
    # whether a consumer read them. `unconsumed_keys` names every record
    # collection nothing reads — including the ones the pipeline deliberately does
    # not read for a stated reason, because the field is a measurement and not a
    # filtered alarm; the caller decides which of them to warn about.
    # `stored_facts` is what actually reached the graph. All three exist because a
    # whole pass produced `connections` records that ingest never read, for the
    # life of the pass, and the per-pass `triples_produced` counter could not show
    # it — that pass emitted triples too, so its count was non-zero while its
    # connections were discarded (YB-051). A counter that cannot see the loss is
    # not a guard.
    output_counts: Dict[str, Dict[str, int]] = field(default_factory=dict)
    unconsumed_keys: List[str] = field(default_factory=list)
    # The facts this run actually stored. `emitted` summed over `output_counts` is
    # what the profile produced; this is what survived the boundary, so the pair
    # is the "emitted N, stored M" report rather than a claim that nothing was lost.
    stored_facts: int = 0
    # Facts the graph declined. Same reason they are on the run and not only in
    # the log: a refusal has to be part of what the run reports about itself.
    refusals: List[Dict[str, Any]] = field(default_factory=list)

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
# Named `IDENTITY_SCOPE_*` and not `SCOPE_*` because the bare name collided with the
# ASSERTION lifecycle scope above, and the collision was silent: whichever
# declaration came last won, so `SCOPE_INITIATIVE` meant two different things
# depending on which line of this file you read. The value is unchanged, because the
# ontology's `IdentityScope` enum fixes it.
#
# The distinction that matters: `NFR-PS-001` read out of a markdown file is a
# real label and worth keeping, but it is not an enterprise identity. Two
# documents may both number a requirement `FR-001`. Matching on such a label
# globally produces confident WRONG joins — worse than missing joins, because it
# makes the audit wrong rather than incomplete.
IDENTITY_SCOPE_ENTERPRISE = "ENTERPRISE"
IDENTITY_SCOPE_INITIATIVE = "INITIATIVE"
IDENTITY_SCOPE_DOCUMENT = "DOCUMENT"
IDENTITY_SCOPE_RUN = "RUN"

# Scopes whose identifiers are unique beyond the document that stated them, and
# so are safe to match on. DOCUMENT and RUN are deliberately excluded.
GLOBALLY_MATCHABLE_SCOPES = frozenset({IDENTITY_SCOPE_ENTERPRISE})

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
    scope: str = IDENTITY_SCOPE_DOCUMENT
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


def document_reference(identifier: str, document_ref: str = "", scope: str = IDENTITY_SCOPE_DOCUMENT) -> ExternalReference:
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
            if existing.scope in ("", IDENTITY_SCOPE_DOCUMENT) and ref.scope:
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

# The names of the identity reasons. Defined once, next to the rule that produces
# them, because they are a VOCABULARY two modules share: `match_score` reports one
# as the reason it scored a candidate definitively, and this module's
# `reference_targets_a_node` uses it to decide a reference is already a link. Two
# copies of the strings would eventually be two names for one decision.
IDENTITY_REASON_EXTERNAL_REF = "external_ref"
IDENTITY_REASON_CITING_KEY = "citing_document_key"


def reference_identity_reason(
    ref: ExternalReference,
    predicate: str,
    citing_document: str = "",
) -> str:
    """Why a MATCHING identifier IDENTIFIES its referent, or "" when it is evidence.

    THE ONE DEFINITION OF THE IDENTITY RULE. The caller has already established
    that `ref.identifier` equals the name it is being compared against; this
    answers the separate question of whether that agreement is *identity* or
    *resemblance*. Deterministic, and three ways it can be identity — each
    independently justified, and none of them wording:

    1. **It is a join key** (`is_join_key`) — held in a system of record, so it is
       unique beyond the document that stated it. The strongest join available,
       and the reason references are typed at all.
    2. **The citing document stated it** — the reference's own `system` names the
       document the citing assertion came from, so one source stated both.
    3. **It is a cited requirement key** — under an `implements_*` predicate
       (`IDENTITY_BY_CITATION_PREDICATES`), where citing the document's own key
       IS the claim the predicate makes. Every other predicate joins on meaning,
       where a shared label like "Payment Processing" in two documents is a
       coincidence to assess rather than a fact.

    WHAT THIS DOES *NOT* DECIDE. `match_score` treats all three as definitive,
    because proposing a candidate is not asserting it. `reference_targets_a_node`
    additionally requires the identifier be of a type the other side actually
    published — see `READ_BOUND_REFERENCE_TYPES`. That narrowing is a deliberate
    write-side policy, not an oversight: the matcher may propose on a document's
    wording, but binding may not rest on it.
    """
    if ref.is_join_key:
        return IDENTITY_REASON_EXTERNAL_REF
    if citing_document and (ref.system or "").strip() == citing_document.strip():
        return IDENTITY_REASON_EXTERNAL_REF
    if predicate in IDENTITY_BY_CITATION_PREDICATES:
        return IDENTITY_REASON_CITING_KEY
    return ""


# What a citation reference needs IN ADDITION to a reason before this module will
# report it as an already-bound link rather than an open proposal.
#
# A reason says the identifier AGREES with a name. It does not say the other side
# published that identifier as a key — and binding on an identifier nobody published
# as one is the confident wrong join this module was typed to prevent: it makes the
# audit wrong rather than incomplete. An identifier the architecture profile
# recorded as a plain document label (`OTHER`) is a string two documents may
# legitimately share, so it stays evidence a human accepts — which is why
# `match_score` still reports it as a 1.0 proposal, for exactly that act.
#
# DELIBERATELY NOT `is_join_key`. A join key (`scope=ENTERPRISE`, e.g. a `PPM_ID`
# or `EA_REPOSITORY_ID` held by a requirements tool) is arguably a STRONGER claim
# than a document-local requirement key, and today it does NOT bind here: it is
# offered as a 1.0 `external_ref` proposal and `bulk_resolve` binds it one pass
# later. Widening this to `is_join_key` would bind it on read instead. That is a
# real decision about how much authority to grant an enterprise register, not an
# oversight, so it is left to a human and named here so it can be made in one
# place. Measured: on the stored corpus no reference is ENTERPRISE-scoped at all,
# so the choice changes nothing until a graph carries a typed join key.
READ_BOUND_REFERENCE_TYPES = frozenset({"REQUIREMENT_KEY"})


def reference_targets_a_node(graph: "KnowledgeGraph", a: Assertion) -> Optional[Node]:
    """The node a cross-graph reference ALREADY names, or None.

    THE ONE ANSWER TO "is this bound?". Used by
    `KnowledgeGraph.unresolved_references`, `realization_edges`/`realization_state`
    and (for the input set it scores) `reference_candidates`, so those three cannot
    hold different opinions about whether a claim is a link.

    Three ways a reference can name a node, and all three count:

    1. **It holds the node id** — a bound link, the shape reconciliation writes.
    2. **Its text equals a node's label.** Ingest keeps a cross-graph target as a
       literal because it refuses to invent a local node for it; when a node with
       exactly that label already exists, the reference is a link in substance and
       there is nothing to reconcile. Refusing to see that reported working links
       as unresolved — and it is why the label check has always been here.
    3. **Its text equals a node's external identifier, under an `implements_*`
       predicate** (`IDENTITY_BY_CITATION_PREDICATES`), and that identifier is of a
       type the other side published as a key (`READ_BOUND_REFERENCE_TYPES`). This
       is the citation case: `implements_requirement -> FR-PM-001` names the
       requirement carrying `FR-PM-001` as its key. Without it, a
       verbatim-preserved requirement id could still not join across documents,
       which was the defect this item existed to fix.

    NOT THE MATCHER'S RULE. `match_score` scores a candidate 1.0 on the same
    identity reasons but does NOT narrow to type-backed identifiers, and it also
    recognises a same-document label. So the matcher's 1.0 set is a SUPERSET of
    what binds here, and `bulk_resolve` is what closes the gap: a reference this
    function calls unbound is offered as a proposal, and accepting it writes the
    object-valued link. That is the intended propose-then-decide split, not a
    disagreement — the one question with one answer is whether a claim is bound,
    and this function is it.

    When more than one node carries the identifier, the strongest claim wins: a
    key held in a system of record outranks one numbered by a document. Only then
    does document order decide, and a wrong pick stays reviewable and reversible.
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

    # Requirement kinds only. The predicate's range says the referent IS a
    # requirement, so a node of another kind carrying the same string is a
    # different thing that happens to share a name — and without this the citation
    # rule would reach across kinds as well as across documents.
    #
    # The predicate guard above is branch 3 of `reference_identity_reason`: under a
    # citation predicate a matching identifier IS an identity reason, which is why
    # nothing here calls that function — the only gate that narrows further is the
    # TYPE. The other side must have PUBLISHED this identifier as a key
    # (`READ_BOUND_REFERENCE_TYPES`); a plain document label (`OTHER`) is a string
    # two documents may legitimately share, so it stays evidence a human accepts
    # rather than a link this function reports as already made.
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
            and ref.reference_type.strip().upper() in READ_BOUND_REFERENCE_TYPES
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
    # Facts this write boundary refused, in the order it refused them. Diagnostics
    # rather than knowledge, which is why they are NOT serialised with the graph:
    # ingest copies them onto the `ExtractionRun` that produced them, where they
    # sit next to the passes and the completeness verdict. Kept on the graph at
    # all so every caller of `add_assertion` gets the same rule and so a test can
    # see what was refused without reaching into a log.
    refusals: List["RefusedAssertion"] = field(default_factory=list)
    
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
    ) -> Optional[Assertion]:
        """Add or fold an assertion, or REFUSE it and return None.

        Folding rules matter: re-observing the same fact must raise confidence
        and keep the fuller source text, never create a parallel duplicate. And a
        human assertion must not be silently overwritten by a later agent run —
        that is the correction-merge requirement (`YB-009` §9b) handled at the
        point of write rather than as an afterthought.

        The one refusal is a reflexive fact on an irreflexive predicate
        (`part_of`, `hosts`, `depends_on`, …). Every extracted fact crosses here,
        so this is the boundary that makes "nothing can be part of itself"
        structural rather than a prompt rule the model may ignore. The refusal is
        appended to `self.refusals` — never dropped silently, because a silent
        drop is indistinguishable from "the model never said it".
        """
        reason = reflexive_violation(subject, predicate, obj)
        if reason:
            self.refusals.append(RefusedAssertion(
                subject=subject, predicate=predicate, object=obj or "",
                value=value or "", reason=reason, source_text=source_text,
            ))
            return None

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

    def scoped(self, scope: str) -> "KnowledgeGraph":
        """This graph restricted to one scope's active facts, and the nodes they name.

        An assertion's scope is what the enterprise has ACCEPTED: facts promoted into
        `SYSTEM_BASELINE` are established system truth, while the rest are what an
        initiative has proposed and nobody has signed off. Reading the baseline scope
        is therefore how a caller gets the architecture it is meant to extend rather
        than the draft in progress — the distinction `promote_to_baseline` creates.

        A node travels only when an included assertion names it. A node whose every
        fact lives outside the scope is deliberately absent: offering its label as
        something already covered is how a proposal ends up claiming to extend
        architecture it cannot see.

        `refusals` are dropped: they are this write boundary's diagnostics, not
        knowledge, and a view of accepted facts has no refusals of its own.
        """
        keep = {a.id: a for a in self.active() if a.scope == scope}
        named: Set[str] = set()
        for a in keep.values():
            named.add(a.subject)
            if a.object:
                named.add(a.object)
        return KnowledgeGraph(
            nodes={i: n for i, n in self.nodes.items() if i in named},
            assertions=keep,
            runs=dict(self.runs),
            declared_by={i: d for i, d in self.declared_by.items() if i in named},
            version_id=self.version_id,
            parent_version_id=self.parent_version_id,
            label=self.label,
        )

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
