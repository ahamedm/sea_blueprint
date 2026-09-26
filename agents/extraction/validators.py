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

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml


# ----------------------------------------------------------------------------
# Ontology access (the source of truth for vocabularies)
# ----------------------------------------------------------------------------

_ONTOLOGY_DIR = Path(__file__).resolve().parents[2] / "ontology"

_ONTOLOGY_FILES = (
    "sea_common",
    "enterprise_structure",
    "requirements_base",
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

def check_schema_consistency(schema: type) -> List[Flag]:
    """Compare an extraction schema's Literal fields against the ontology.

    Prevents the drift described in the module docstring: a schema constraint
    and a validator rule that were once the same fact but have diverged.
    """
    import typing

    FIELD_ENUMS = {
        "element_type": "C4ElementType",
        "system_class": "SoftwareSystemClass",
        "origin": "SoftwareOrigin",
        "deployment_model": "SoftwareDeploymentModel",
    }

    flags: List[Flag] = []
    for fieldname, enum_name in FIELD_ENUMS.items():
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
