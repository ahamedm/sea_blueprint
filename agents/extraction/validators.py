"""
Deterministic validators (Option B).

Post-hoc checks over parsed output, independent of model behaviour and immune to
prompt dilution. They FLAG; they never drop or rewrite. Silently discarding
extracted content is worse than surfacing it for review.

SINGLE SOURCE OF TRUTH
----------------------
These validators read their vocabularies from the ONTOLOGY, not from hardcoded
copies. That matters: if a rule appears both as a schema constraint and as a
validator rule, the two are copies of one truth with no link, and they will
diverge silently on the next edit — producing validators that check rules the
schema no longer states ("green but wrong").

`check_schema_consistency()` compares the extraction schemas against the ontology
and reports drift. Run it in the test harness so a schema/ontology mismatch fails
loudly rather than being discovered in production output.
"""

import re
import typing
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import yaml


# ----------------------------------------------------------------------------
# Ontology access (the source of truth for vocabularies)
# ----------------------------------------------------------------------------

_ONTOLOGY_DIR = Path(__file__).resolve().parents[2] / "ontology"

_ONTOLOGY_FILES = (
    "sea_common",
    "enterprise_structure",
    "requirements_base",
    "governance_base",
    "architecture_base",
)


@lru_cache(maxsize=1)
def _load_ontology_docs() -> Dict[str, Dict[str, Any]]:
    docs: Dict[str, Dict[str, Any]] = {}
    for name in _ONTOLOGY_FILES:
        path = _ONTOLOGY_DIR / f"{name}.yaml"
        if path.exists():
            with open(path, "r") as fh:
                docs[name] = yaml.safe_load(fh) or {}
    return docs


@lru_cache(maxsize=None)
def ontology_enum(enum_name: str) -> frozenset:
    """Permissible values for an ontology enum, gathered across all layers."""
    values: set = set()
    for doc in _load_ontology_docs().values():
        spec = (doc.get("enums") or {}).get(enum_name)
        if spec:
            values.update((spec.get("permissible_values") or {}).keys())
    return frozenset(values)


@lru_cache(maxsize=None)
def ontology_classes() -> frozenset:
    values: set = set()
    for doc in _load_ontology_docs().values():
        values.update((doc.get("classes") or {}).keys())
    return frozenset(values)


@lru_cache(maxsize=None)
def _ontology_class_map() -> Dict[str, Dict[str, Any]]:
    """class name -> its spec, merged across every ontology layer."""
    out: Dict[str, Dict[str, Any]] = {}
    for doc in _load_ontology_docs().values():
        for name, spec in (doc.get("classes") or {}).items():
            if isinstance(spec, dict):
                out.setdefault(name, spec)
    return out


def _parents_of(spec: Dict[str, Any]) -> List[str]:
    """`is_a` as a list, whether the ontology writes one parent or several."""
    raw = spec.get("is_a")
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw]
    return [str(x) for x in raw if x]


@lru_cache(maxsize=None)
def ontology_slot_range(class_name: str, slot_name: str) -> str:
    """The declared `range` of `slot_name` on `class_name`, or "" when undeclared.

    Walks the `is_a` chain because the slot is usually INHERITED: a `DataStore`
    declares no `parent_system` of its own, it has `Container`'s. Reading only the
    named class would report the rule as absent and silently disable the check
    that depends on it.
    """
    seen: set = set()
    frontier = [class_name]
    while frontier:
        current = frontier.pop(0)
        if not current or current in seen:
            continue
        seen.add(current)
        spec = _ontology_class_map().get(current) or {}
        attr = (spec.get("attributes") or {}).get(slot_name)
        if isinstance(attr, dict) and attr.get("range"):
            return str(attr["range"])
        frontier.extend(_parents_of(spec))
    return ""


@lru_cache(maxsize=None)
def ontology_subclasses(class_name: str) -> frozenset:
    """`class_name` plus every class that transitively `is_a` it.

    Needed because a range names the PARENT class and a graph carries the concrete
    one: `Component.belongs_to_container` ranges over `Container`, and a `DataStore`
    is a Container, so a Component inside a DataStore is legitimate while a set
    built from the range alone would call it a violation.
    """
    out = {class_name}
    for name, spec in _ontology_class_map().items():
        seen: set = set()
        frontier = _parents_of(spec)
        while frontier:
            parent = frontier.pop(0)
            if not parent or parent in seen:
                continue
            if parent == class_name:
                out.add(name)
                break
            seen.add(parent)
            frontier.extend(_parents_of(_ontology_class_map().get(parent) or {}))
    return frozenset(out)


# ----------------------------------------------------------------------------
# Flag model
# ----------------------------------------------------------------------------

@dataclass
class Flag:
    """A single finding. `subject` identifies what was flagged, `reasons` why."""

    kind: str
    subject: str
    reasons: List[str]
    predicate: str = ""
    object: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "reasons": self.reasons,
        }


def as_record_dicts(records: Sequence[Any]) -> List[Dict[str, Any]]:
    """Normalise pass records to plain dicts.

    Public because the Design Assistant's validators consume the same records: a
    pass result is a Pydantic model on the structured path and a dict on the text
    path, and both a shared check and a profile-specific one need one view of it.
    """
    out = []
    for r in records or []:
        if isinstance(r, dict):
            out.append(r)
        elif hasattr(r, "model_dump"):
            out.append(r.model_dump())
    return out


# ----------------------------------------------------------------------------
# Object contract (Tier 3 backup for the Tier 2 schema description)
# ----------------------------------------------------------------------------

# Clause markers. Conservative — this flags for review, it does not reject.
_CLAUSE_MARKERS = (" shall ", " must ", " will ", " should ", " and ", " or ")

_MAX_NODE_CHARS = 40


def check_object_contract(triples: Sequence[Any]) -> List[Flag]:
    """Subjects and objects must NAME things, not describe behaviour.

    A clause becomes a node nothing can link to or query. Flags long or
    clause-shaped nodes and comma-lists that should have been split.
    """
    flags: List[Flag] = []
    for t in as_record_dicts(triples):
        reasons: List[str] = []
        for fieldname in ("subject", "object"):
            value = str(t.get(fieldname) or "").strip()
            if not value:
                continue
            if len(value) > _MAX_NODE_CHARS:
                reasons.append(f"{fieldname} is {len(value)} chars (clause-shaped)")
            if "," in value:
                reasons.append(f"{fieldname} is a comma-list — split one triple per item")
            padded = f" {value.lower()} "
            for marker in _CLAUSE_MARKERS:
                if marker in padded:
                    reasons.append(f"{fieldname} contains clause marker {marker.strip()!r}")
                    break
        if reasons:
            flags.append(Flag("object_contract", str(t.get("subject") or ""),
                              reasons, str(t.get("predicate") or ""),
                              str(t.get("object") or "")))
    return flags


# ----------------------------------------------------------------------------
# Architecture structure
# ----------------------------------------------------------------------------

_CONTAINED_TYPES = frozenset({"Container", "DataStore", "Component", "CodeElement"})

# Element type -> the ontology slot that names its parent. The RANGE is deliberately
# NOT written here: it is read from the ontology by `allowed_parent_kinds`, so a
# changed range cannot leave this table asserting a rule the ontology no longer
# states — the "green but wrong" divergence this module's docstring warns about.
# What IS local is the slot↔type pairing, because the extraction record carries a
# single `parent` field where the ontology has four differently-named slots.
_CONTAINMENT_PARENT_SLOT: Dict[str, str] = {
    "Container": "parent_system",
    "DataStore": "parent_system",
    "Component": "belongs_to_container",
    "CodeElement": "belongs_to_component",
}


@lru_cache(maxsize=None)
def allowed_parent_kinds(element_type: str) -> frozenset:
    """Element types the ontology permits as the container of `element_type`.

    The declared range plus its subclasses. Empty when the ontology states no slot
    or range for the type, which callers read as "no rule to check" rather than as
    "no parent allowed".
    """
    slot = _CONTAINMENT_PARENT_SLOT.get(element_type)
    if not slot:
        return frozenset()
    declared = ontology_slot_range(element_type, slot)
    if not declared:
        return frozenset()
    return ontology_subclasses(declared)


def check_containment_kinds(
    elements: Sequence[Any],
    inferred_parents: Sequence[str] = (),
) -> List[Flag]:
    """A contained element's parent must be the KIND of thing the ontology allows.

    `check_containment` proves a parent EXISTS and that a `part_of` edge backs the
    declaration. It does not prove the parent is the right LEVEL, so a Component
    attached to a SoftwareSystem passed every check while the ontology says
    `Component.belongs_to_container` ranges over `Container`. The result is a graph
    that reports a C4 hierarchy and is in fact flat: the component sits at context
    level, and every C4 reduction then has to guess which level it belongs to.

    `inferred_parents` names elements whose parent was ATTACHED by
    `repair_containment` rather than stated by the document. They are excluded
    because the repair already reports each one as `containment_repaired` — the
    same underlying cause counted twice is the double-reporting the C4 scorecard
    was already caught doing (three false positives on a complete run), and it
    makes a document gap look like two defects.
    """
    docs = [e for e in as_record_dicts(elements) if e.get("element_type")]
    if not docs:
        return []

    kinds = {
        str(e.get("name") or "").strip(): str(e.get("element_type") or "").strip()
        for e in docs
    }
    skip = {str(n).strip() for n in inferred_parents}

    flags: List[Flag] = []
    for e in docs:
        name = str(e.get("name") or "").strip()
        etype = str(e.get("element_type") or "").strip()
        parent = str(e.get("parent") or "").strip()

        if not (name and parent) or name in skip:
            continue
        allowed = allowed_parent_kinds(etype)
        # No ontology rule, or a parent this run never declared: the second is
        # `check_containment`'s finding, and reporting it twice would inflate the
        # count of a defect that is already visible.
        if not allowed or parent not in kinds:
            continue

        parent_kind = kinds[parent]
        if parent_kind not in allowed:
            flags.append(Flag(
                "containment_kind", name,
                [f"{etype} is contained by a {parent_kind} ({parent!r}); the ontology "
                 f"allows {'/'.join(sorted(allowed))} here — the element would sit at "
                 f"the wrong C4 level"],
                "part_of", parent,
            ))
    return flags


def check_containment(elements: Sequence[Any], triples: Sequence[Any]) -> List[Flag]:
    """C4 is a hierarchy; without containment the graph is a flat bag of nodes.

    Checks a contained element has a parent, that the parent exists, and that a
    `part_of` triple backs the declaration — both views must agree.
    """
    docs = [e for e in as_record_dicts(elements) if e.get("element_type")]
    if not docs:
        return []

    names = {e.get("name") for e in docs}
    parts: Dict[str, set] = {}
    for t in as_record_dicts(triples):
        if t.get("predicate") == "part_of":
            parts.setdefault(t.get("subject"), set()).add(t.get("object"))

    flags: List[Flag] = []
    for e in docs:
        reasons: List[str] = []
        parent = str(e.get("parent") or "").strip()
        etype = e.get("element_type")

        if etype in _CONTAINED_TYPES:
            if not parent:
                reasons.append(f"{etype} has no `parent` — containment lost")
            else:
                if parent not in names:
                    reasons.append(f"parent {parent!r} is not a declared element")
                if parent not in parts.get(e.get("name"), set()):
                    reasons.append(f"no `part_of` triple for declared parent {parent!r}")
        elif parent:
            reasons.append(f"{etype} is normally top-level but declares a parent")

        if reasons:
            flags.append(Flag("containment", str(e.get("name") or ""), reasons,
                              "containment", parent or "(none)"))
    return flags


def check_element_types(elements: Sequence[Any]) -> List[Flag]:
    """Element types must be actual classes in the ontology.

    Validated against `ontology_classes()` rather than a hardcoded list — the
    C4 element types are CLASSES (SoftwareSystem, Container, …), not members of
    an enum, so the ontology's class list is the source of truth.
    """
    classes = ontology_classes() or frozenset()
    if not classes:
        return []
    # Only the C4 element classes are valid here; classes from other layers
    # (Requirement, BusinessGoal, …) are not elements.
    allowed = classes & {
        "SoftwareSystem", "ExternalSystem", "Person", "Container",
        "DataStore", "Component", "CodeElement", "DeploymentNode",
    }
    if not allowed:
        return []
    flags: List[Flag] = []
    for e in as_record_dicts(elements):
        etype = e.get("element_type")
        if etype and etype not in allowed:
            flags.append(Flag("element_type", str(e.get("name") or ""),
                              [f"element_type {etype!r} is not a C4 element class "
                               f"(allowed: {sorted(allowed)})"]))
    return flags


def check_deployment_levels(elements: Sequence[Any]) -> List[Flag]:
    """Deployment nodes have no C4 level; forcing one puts them at CONTEXT."""
    flags: List[Flag] = []
    for e in as_record_dicts(elements):
        if e.get("element_type") == "DeploymentNode" and e.get("c4_level"):
            flags.append(Flag("deployment_level", str(e.get("name") or ""),
                              ["DeploymentNode carries a C4 level"]))
    return flags


def check_enum_membership(elements: Sequence[Any]) -> List[Flag]:
    """Classification fields must come from the ontology's vocabularies."""
    checks = (
        ("system_class", "SoftwareSystemClass"),
        ("origin", "SoftwareOrigin"),
        ("deployment_model", "SoftwareDeploymentModel"),
        ("c4_level", "C4Level"),
        # `container_type` is `required: true` on Container, so a value outside
        # ContainerType is worse than an absent one: it classifies the container as
        # something the ontology cannot name.
        ("container_type", "ContainerType"),
        # The ISO 25010 pair. On the ELEMENT record these are plain `str`, unlike the
        # `Literal`s on `DesignTechniqueRecord`, so nothing constrains them at the
        # decoder and nothing else checks them. Measured on the live scope: these two
        # predicates are 57 of the 486 assertions awaiting a human decision, and every
        # one of them carries a value the ontology already enumerates.
        ("quality_category", "QualityAttributeCategory"),
        ("subcharacteristic", "QualitySubcharacteristic"),
    )
    flags: List[Flag] = []
    for e in as_record_dicts(elements):
        reasons: List[str] = []
        for fieldname, enum_name in checks:
            value = str(e.get(fieldname) or "").strip()
            if not value:
                continue
            allowed = ontology_enum(enum_name)
            if allowed and value not in allowed:
                reasons.append(f"{fieldname}={value!r} not in {enum_name}")
        if reasons:
            flags.append(Flag("enum_membership", str(e.get("name") or ""), reasons))
    return flags


# ----------------------------------------------------------------------------
# Domain concept attributes — the logical data model
# ----------------------------------------------------------------------------

_CONCEPT_ATTRIBUTE_KIND = "ConceptAttribute"


def check_concept_attributes(entities: Sequence[Any], source: str = "") -> List[Flag]:
    """A concept's fields must be named, owned, stated in the document, and logical.

    `ConceptAttribute` was the class nothing could reach (YB-055): declared,
    documented, and never populated, because the ontology nests it and the
    extraction contract is flat. It now arrives in a flat, owned shape, and this is
    the deterministic half — the reason the shape is worth having. Every rule here
    is one a fluent output cannot be trusted to satisfy on its own:

    1. **Owned.** A `ConceptAttribute` emitted as an entity of its own has no
       owner, and a field name without its concept is not an identity. Measured on
       the saved PRD run before the shape existed: five such nodes
       ('Country of Transaction', 'Transaction Currency', …), each of which looked
       exactly like a concept.
    2. **Named**, and named ONCE per concept. A repeated field is a merge artefact
       or a copy-paste, and the second one silently wins on the node.
    3. **Anchored.** The name must appear in the document (reliability §3.6). An
       invented field is invisible otherwise — `Settlement Date` reads like every
       other field whether the document said it or the model supplied it.
    4. **Logical, not physical.** `data_type` must come from
       `ConceptAttributeDataType`. `VARCHAR(255)` classifies one store's
       implementation, not the field, and the vocabulary is read from the ontology
       rather than restated here so the two cannot drift.

    Flags, never drops — the posture every check in this module takes, and the
    reason `_resolve` already makes an unowned referent visible rather than
    silently binding it to something plausible.

    `source` is optional because a caller that has no document to check against
    (a design assistant working from the graph) still gets rules 1, 2 and 4.
    """
    records = as_record_dicts(entities)
    if not records:
        return []

    declared_kinds = ontology_classes()
    concept_kinds = ontology_subclasses("DomainConcept")
    allowed_types = ontology_enum("ConceptAttributeDataType")
    source_norm = _norm(source)

    flags: List[Flag] = []
    for e in records:
        name = str(e.get("name") or "").strip()
        kind = str(e.get("ontology_class") or e.get("entity_type") or "").strip()

        if kind == _CONCEPT_ATTRIBUTE_KIND:
            flags.append(Flag(
                "unowned_attribute", name,
                [f"{name!r} is declared as a concept attribute but belongs to no "
                 f"concept. An attribute's identity is (concept, name) — "
                 f"'Customer.email' and 'Order.email' are different fields — so emit "
                 f"it in the owning concept's `attributes`, not as an entity of its own"],
            ))
            continue

        attributes = [a for a in (e.get("attributes") or []) if isinstance(a, dict)]
        if not attributes:
            continue

        # The owner's kind, checked only when the base ontology knows the kind at
        # all. A domain pack subclasses `DomainConcept` with names the base layers
        # do not declare (`Card`, `Merchant`, …), and `ontology_classes()` cannot
        # see them — flagging those would report the pack's own vocabulary as a
        # defect, which is the false positive that makes a report unreadable.
        if kind and kind in declared_kinds and kind not in concept_kinds:
            flags.append(Flag(
                "attribute_owner", name,
                [f"{kind} is carrying `attributes`, but the ontology reserves them for "
                 f"DomainConcept and its subclasses ({'/'.join(sorted(concept_kinds))}) — "
                 f"a requirement, a stakeholder or a goal has no data model"],
            ))

        seen: set = set()
        for a in attributes:
            attr_name = str(a.get("name") or "").strip()
            if not attr_name:
                flags.append(Flag("concept_attribute", name,
                                  ["an attribute record on this concept has no name"]))
                continue
            key = attr_name.lower()
            if key in seen:
                flags.append(Flag(
                    "concept_attribute", attr_name,
                    [f"declared twice on {name!r} — the node is keyed on the name, so "
                     f"the two records collapse into one and one of them is lost"],
                ))
            seen.add(key)

            if source_norm and _norm(attr_name) not in source_norm:
                flags.append(Flag(
                    "unanchored_attribute", attr_name,
                    [f"the document never names this field of {name!r} — an invented "
                     f"attribute is indistinguishable from a stated one, and the "
                     f"whole difference between a data model and a plausible one is "
                     f"which fields the source actually gives"],
                ))

            data_type = str(a.get("data_type") or "").strip()
            if data_type and allowed_types and data_type not in allowed_types:
                flags.append(Flag(
                    "attribute_data_type", attr_name,
                    [f"data_type {data_type!r} is not a logical type (allowed: "
                     f"{', '.join(sorted(allowed_types))}). A physical type describes "
                     f"one store's implementation and changes when that store does"],
                ))
    return flags


# ----------------------------------------------------------------------------
# Connections — integration mechanisms
# ----------------------------------------------------------------------------


def check_connection_endpoints(
    connections: Sequence[Any],
    elements: Sequence[Any] = (),
    known_labels: Sequence[str] = (),
) -> List[Flag]:
    """Both ends of a connection must be things something actually declares.

    The connections prompt already states this in the strongest terms it has — both
    endpoints must be a named element, and a style, quality attribute, technique,
    category word or group of things "cannot be drawn" — but until now that rule had
    NO deterministic backstop. An off-list endpoint became a placeholder node through
    `_resolve` and surfaced only as a dangling edge in the C4 view, which is
    downstream of ingest and was itself measured producing three false positives on a
    complete run. A prompt rule with no check is the situation §3.B of the reliability
    brainstorm exists to end: shape the output, do not ask for it.

    `known_labels` is what a profile that HAS the graph passes in, and the Design
    Assistant is the reason it exists: it is told to REUSE an existing element by
    name rather than re-propose it, so a reused endpoint appears in no proposed
    element record, and flagging it would punish the behaviour the prompt asks for.
    An EXTRACTION run has no graph to consult — its input is a document — so it
    passes nothing, and a connection to an element that only an earlier run declared
    is reported. That is the honest reading: the document connected two things it
    never introduced, which is what a reviewer needs to see, and it is the same gap
    `_resolve` already makes visible as an unresolved reference.
    """
    declared = _declared_names(elements, known_labels)
    # Nothing to judge against: an empty declaration set would flag every endpoint
    # of every connection, which is a measurement of this run rather than of the
    # graph. Same posture as `check_element_types` when the ontology is unavailable.
    if not declared:
        return []

    flags: List[Flag] = []
    for c in as_record_dicts(connections):
        reasons: List[str] = []
        for fieldname in ("source", "target"):
            value = str(c.get(fieldname) or "").strip()
            if not value:
                reasons.append(f"{fieldname} is empty — a connection needs two ends")
            elif value.lower() not in declared:
                reasons.append(
                    f"{fieldname} {value!r} is not an element this run declared, and no "
                    f"known element carries that name"
                )
        if reasons:
            flags.append(Flag(
                "connection_endpoint",
                f"{c.get('source') or '?'} → {c.get('target') or '?'}",
                reasons, "connects_to", str(c.get("target") or ""),
            ))
    return flags


def _declared_names(elements: Sequence[Any], known_labels: Sequence[str] = ()) -> set:
    """The names something in this run actually declared, lowercased.

    Shared by the connection and attribution endpoint checks so the two cannot
    disagree about what counts as declared — two rules would drift, and the drift
    would read as one reference being fine to one check and invented to the other.
    """
    declared = {
        str(e.get("name") or "").strip().lower()
        for e in as_record_dicts(elements)
        if str(e.get("name") or "").strip()
    }
    declared |= {str(n).strip().lower() for n in known_labels if str(n).strip()}
    return declared


# The attribution lists a pass fills with ELEMENT NAMES. Each is a cross-reference by
# name, so each can name something no pass declared — and nothing checked them, which
# is how a category word became a node (ISS-1).
_ATTRIBUTION_FIELDS: Tuple[Tuple[str, str], ...] = (
    ("technology_stacks", "used_by"),
    ("architecture_styles", "adopted_by"),
    ("design_techniques", "applies_to"),
    ("engineering_conventions", "applies_to"),
)


def check_attribution_endpoints(
    collections: Mapping[str, Sequence[Any]],
    elements: Sequence[Any] = (),
    known_labels: Sequence[str] = (),
) -> List[Flag]:
    """An attribution list may only name an element something actually declared.

    `check_connection_endpoints` enforced this for connections and NOTHING enforced
    it for `used_by` / `adopted_by` / `applies_to`. That gap is measured: in
    `simple_architecture_partial.md`, "All Microservices are Stateless and
    Containerized with Docker" produced `concept:all_microservices`, reachable only
    by attribution edges, plus one node per phrasing of the same idea — and the run
    reported COMPLETE with 0 refusals (ISS-1).

    The prompt invites it, which is why a check is the fix rather than a wording
    change: the technique schema says *"Elements the technique is applied to.
    Usually several — statelessness and redundancy are platform-wide decisions"*.
    Ingest then resolves the name through `_resolve`, whose fallback kind is
    `Concept`.

    Flags rather than drops, and for the reason the other validators give: this is
    legitimate CONTENT in the wrong SHAPE. "All Microservices" is a real claim; the
    place for it is the platform or the `ArchitectureStyle`, not a node invented to
    stand for all of them. Naming the mis-shape is what gives a reviewer the
    decision, and a drop would silently lose the claim.
    """
    declared = _declared_names(elements, known_labels)
    if not declared:
        return []

    flags: List[Flag] = []
    for collection, fieldname in _ATTRIBUTION_FIELDS:
        for record in as_record_dicts(collections.get(collection) or []):
            for value in record.get(fieldname) or []:
                text = str(value).strip()
                if not text or text.lower() in declared:
                    continue
                flags.append(Flag(
                    "attribution_endpoint",
                    str(record.get("name") or "?"),
                    [f"{fieldname} {text!r} is not an element this run declared, and no "
                     f"known element carries that name — a group label becomes a node "
                     f"of its own kind rather than pointing at what it describes"],
                    fieldname, text,
                ))
    return flags


# ----------------------------------------------------------------------------
# Span anchoring — is this fact in the document at all?
# ----------------------------------------------------------------------------
#
# §3.6 of the reliability brainstorm: require an emitted value to be present in the
# source, and validate it deterministically. The two halves need DIFFERENT rules, and
# the measurements below are why rather than a preference.
#
# Both measurements are on `test_data/arch/payment_platform_arch.md` against the saved
# `data/output/test_arch.json` (30 elements, 15 connections), 2026-09-30.

_ANCHOR_WORD = re.compile(r"[a-z]{5,}")

# A NAME is meant to be the document's own word, so the rule is strict. Measured: 29 of
# 30 element names appear literally in the source, so the rule discriminates rather than
# flooding — and the one that does not ("Reconciliation Container" where the document
# says "Reconciliation") is exactly the synthesis worth a reviewer's attention.
#
# A DESCRIPTION is a SUMMARY, not a quotation, and a strict rule on it is unusable:
# measured 0 of 30 descriptions appear literally. What separates a summary from an
# invention is whether it uses the document's vocabulary — at a floor of 20% of the
# description's long words, those same 30 score min 0.60, mean 0.89, and none is
# flagged. A floor, not a target: the check exists to catch a description written from
# nothing, and a validator that reports a correct graph is worse than none.
_ANCHOR_MIN_WORD_SHARE = 0.20


def _norm(text: Any) -> str:
    """Lowercased with runs of whitespace collapsed, for literal containment.

    Whitespace matters because an extracted name can carry a line break from the
    document it was read out of, and `"a\\nb" in "a b"` is False for a reason that has
    nothing to do with whether the document says it.
    """
    return re.sub(r"\s+", " ", str(text or "")).strip().lower()


def check_names_are_anchored(elements: Sequence[Any], source: str) -> List[Flag]:
    """An element's name must appear in the document that is supposed to declare it.

    The deterministic form of a rule both prompts already state in words — "use the
    concrete name the document gives (PostgreSQL, Valkey, OpenShift)" and "never emit a
    bare category word … when the document names the specific thing". An invented name
    is invisible in the output: `External Services` looks exactly like a declared
    element, and only this check separates them.

    Extraction profiles only. A DESIGN is allowed to invent — proposing a container the
    document does not name is its job — so anchoring a design proposal would flag every
    legitimate element. That is the same line the model already draws between
    `SOURCE_EXTRACTION` and `SOURCE_DESIGN_ASSISTANT`: an extractor reports what a
    document said, a designer proposes what could be built.

    Connection endpoints are deliberately not re-checked here. An endpoint that is not a
    declared element is `check_connection_endpoints`' finding, and an endpoint that IS
    declared is that element's name — already checked, once.
    """
    source_norm = _norm(source)
    if not source_norm:
        return []
    flags: List[Flag] = []
    for e in as_record_dicts(elements):
        name = str(e.get("name") or "").strip()
        if name and _norm(name) not in source_norm:
            flags.append(Flag(
                "unanchored_name", name,
                [f"the document never uses this name — it is either a category word or "
                 f"a synthesis, and nothing else in the output distinguishes it from a "
                 f"declared element"],
            ))
    return flags


def check_quotations_are_grounded(
    elements: Sequence[Any],
    source: str,
    min_share: float = _ANCHOR_MIN_WORD_SHARE,
) -> List[Flag]:
    """A description must at least use the document's vocabulary.

    Catches a description written from nothing — the failure a reviewer cannot see,
    because a fluent invented description reads like every other description.

    Deliberately a FLOOR on shared vocabulary and not a containment test: see the
    measurements above the section. A description with no words long enough to judge is
    left alone rather than guessed at.
    """
    source_norm = _norm(source)
    if not source_norm:
        return []
    present = set(_ANCHOR_WORD.findall(source_norm))
    flags: List[Flag] = []
    for e in as_record_dicts(elements):
        description = str(e.get("description") or "").strip()
        if not description:
            continue
        words = _ANCHOR_WORD.findall(_norm(description))
        if not words:
            continue
        share = sum(1 for w in words if w in present) / len(words)
        if share < min_share:
            flags.append(Flag(
                "ungrounded_description", str(e.get("name") or ""),
                [f"only {share:.0%} of this description's words appear anywhere in the "
                 f"document (floor {min_share:.0%}) — it reads as written from nothing "
                 f"rather than summarised from the source"],
            ))
    return flags


# ----------------------------------------------------------------------------
# Completeness — the gap that let a diminished graph pass validation
# ----------------------------------------------------------------------------

def check_expected_present(
    elements: Sequence[Any],
    expected_names: Sequence[str],
) -> List[Flag]:
    """Assert content that SHOULD be present.

    Every other check here validates what survived. This one catches what went
    missing — the failure mode where a schema change silently drops a whole
    category of content and the output still looks well-formed, so every
    correctness check passes over a diminished graph.
    """
    present = {str(e.get("name") or "").strip().lower() for e in as_record_dicts(elements)}
    missing = [n for n in expected_names if n.strip().lower() not in present]
    if not missing:
        return []
    return [Flag("completeness", "(document)", [f"expected but absent: {m}" for m in missing])]


def check_nonempty_field(elements: Sequence[Any], fieldname: str,
                         applies_to: Optional[Sequence[str]] = None) -> List[Flag]:
    """Assert a field is populated for element types where it should be.

    Catches the observed failure where the model emits `"responsibilities": []`
    for every element — recognising the field and leaving it empty.
    """
    flags: List[Flag] = []
    for e in as_record_dicts(elements):
        if applies_to and e.get("element_type") not in applies_to:
            continue
        if not e.get(fieldname):
            flags.append(Flag("completeness", str(e.get("name") or ""),
                              [f"{fieldname} is empty"]))
    return flags


# ----------------------------------------------------------------------------
# Schema / ontology consistency
# ----------------------------------------------------------------------------

# Literal-valued fields to hold against the ontology, per schema. The pairing is
# local because a field name is the extraction record's choice, not the ontology's;
# the VOCABULARY is always the ontology's. `element_type` names an enum the
# architecture layer does not define (element types are CLASSES there), so its
# lookup returns nothing and the check simply skips it — see `check_element_types`
# for the check that does cover it.
FIELD_ENUMS: Dict[str, str] = {
    "element_type": "C4ElementType",
    "system_class": "SoftwareSystemClass",
    "origin": "SoftwareOrigin",
    "deployment_model": "SoftwareDeploymentModel",
}

# The connection schema's own vocabularies. Separate because they live on
# `ConnectionRecord`, and both were consumed by no Python at all before this.
CONNECTION_FIELD_ENUMS: Dict[str, str] = {
    "style": "IntegrationStyle",
    "protocol": "IntegrationProtocol",
}

# The technique and technology vocabularies. These four ARE `Literal`s, so the
# decoder constrains them — which is why nothing caught that no Python compared them
# to the ontology. Measured 2026-09-30: all four match exactly today (11/11, 10/10,
# 40/40, 10/10), so this guard is green on arrival and exists to keep it that way. A
# `Literal` and an ontology enum that were once the same fact will diverge silently on
# the next edit, which is the drift `check_schema_consistency` exists to catch.
TECHNIQUE_FIELD_ENUMS: Dict[str, str] = {
    "technique_category": "PatternCategory",
    "quality_category": "QualityAttributeCategory",
    "subcharacteristic": "QualitySubcharacteristic",
}

TECHNOLOGY_FIELD_ENUMS: Dict[str, str] = {
    "category": "TechnologyCategory",
}


def check_schema_consistency(
    schema: type,
    field_enums: Optional[Dict[str, str]] = None,
) -> List[Flag]:
    """Compare an extraction schema's Literal fields against the ontology.

    Prevents the drift described in the module docstring: a schema constraint
    and a validator rule that were once the same fact but have diverged.

    `field_enums` defaults to the element vocabulary. The connection vocabularies
    are passed in explicitly because they live on a different schema, and
    `IntegrationStyle` / `IntegrationProtocol` were the pair the default missed
    entirely: both are `Literal`s hand-copied from an ontology enum that no Python
    read, so editing either enum would have left the schema asserting the old
    vocabulary with nothing to notice.
    """
    if field_enums is None:
        field_enums = FIELD_ENUMS

    flags: List[Flag] = []
    for fieldname, enum_name in field_enums.items():
        field = getattr(schema, "model_fields", {}).get(fieldname)
        if field is None:
            continue
        literal_values = {
            a for a in typing.get_args(field.annotation) if isinstance(a, str)
        }
        if not literal_values:
            continue
        allowed = ontology_enum(enum_name)
        if not allowed:
            continue
        extra = literal_values - set(allowed) - {""}
        missing = set(allowed) - literal_values
        if extra:
            flags.append(Flag("schema_drift", fieldname,
                              [f"schema allows {sorted(extra)} not in ontology {enum_name}"]))
        if missing:
            flags.append(Flag("schema_drift", fieldname,
                              [f"ontology {enum_name} has {sorted(missing)} absent from schema"]))
    return flags
