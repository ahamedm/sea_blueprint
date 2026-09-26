"""
Ontology reference — reading the foundational LinkML schemas.

THE THIRD KIND OF VIEW
----------------------
This project has three things that are loosely called "a view", and they read from
different sources. Keeping them apart is not tidiness; each answers a different
question and changes for different reasons.

| Layer | Reads | Answers |
|---|---|---|
| `app.projections` | the instance graph (ABox) | what does our architecture knowledge contain? |
| `app.viewpoints` | the instance graph + a notation | how is that architecture described? (C4) |
| `core.ontology` + `app.ontology_reference` |
  the LinkML schemas (TBox) | what concepts exist, and how do they relate? |

This module is the third: **the schema, not the data**. It parses `ontology/*.yaml`
and knows nothing about extracted graphs, nodes, assertions or confidence. It
imports nothing from `core.knowledge` and nothing from `app`, so it can be used
by agents — the Ontology Engineer and Domain Context agents will need exactly this
— without dragging the web layer along.

WHAT "UNDERSTANDING THE ONTOLOGY" REQUIRES
------------------------------------------
A list of class names is not comprehension. The things that actually explain these
schemas are structural:

- the **one-way import chain**, which is why the layers exist at all;
- **`is_a` vs `mixins`** — the taxonomy versus the cross-cutting aspects, a
  distinction the schema uses deliberately (`ExternallyReferenced`,
  `Provenanced` are mixed in, not inherited);
- **inheritance** — `NonFunctionalRequirement` carries 33 slots, of which 8 are its
  own; without resolving `is_a` a reader sees a fifth of the truth;
- **slot ranges that point at other classes** — the actual relationships between
  concepts, as opposed to the hierarchy;
- the **binding classes** (`SubProductScope`, `SystemCapabilityBinding`) that exist
  only to break a circular import.

So the model below resolves inheritance and range references rather than handing
back raw YAML.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

import re
import warnings

import yaml

# Ranges that are not classes or enums. Anything outside the loaded schema that is
# also not here is reported as `unknown` rather than quietly assumed primitive.
PRIMITIVE_RANGES = frozenset(
    {
        "string",
        "integer",
        "boolean",
        "float",
        "double",
        "decimal",
        "date",
        "datetime",
        "time",
        "uris",
        "curie",
        "uri",
        "ncname",
        "object",
    }
)

EXTERNAL_IMPORTS = frozenset({"linkml:types", "linkml:meta"})

# Layer definitions, in dependency order. `key` is what the UI and the model use.
# The order is not cosmetic: it is the one-way import rule the ontology README
# states, and `requirements_base` may not reference `architecture_base`.
LAYER_ORDER: Tuple[Tuple[str, str, str], ...] = (
    (
        "common",
        "sea_common.yaml",
        "Identity and provenance — what every other concept must be able to reference "
        "and attribute.",
    ),
    (
        "enterprise",
        "enterprise_structure.yaml",
        "Organisational constructs — Product, SubProduct, System, Application, Platform. "
        "Shared by both graphs so they speak one vocabulary.",
    ),
    (
        "requirements",
        "requirements_base.yaml",
        "REQ-G — the requirements hierarchy, business context, and the ISO 25010 quality " "model.",
    ),
    (
        "architecture",
        "architecture_base.yaml",
        "ARC-G — C4-aligned architecture elements and RequirementRealization, the "
        "REQ to ARC join.",
    ),
)

LAYER_LABELS = {
    "common": "Common",
    "enterprise": "Enterprise",
    "requirements": "Business Requirements",
    "architecture": "Architecture",
}

# The layer key every class declared by a domain pack is attributed to. A pack is
# NOT an entry in LAYER_ORDER: LAYER_ORDER is the fixed, always-present base chain,
# while a pack is conditional and swappable per Initiative. Folding a pack into
# LAYER_ORDER would make the loader demand one specific domain forever and would
# defeat per-Initiative selection, because `_load_cached` is keyed by directory
# alone. See docs/domain-ontology-integration.md §2.3.
DOMAIN_LAYER = "domain"
LAYER_LABELS[DOMAIN_LAYER] = "Domain Pack"

# Where packs live, relative to the ontology root.
DOMAIN_PACK_DIRNAME = "domains"

# The class a pack must specialise. A pack that subclasses nothing is not an
# overlay, it is a second base ontology wearing a pack's filename.
DOMAIN_PACK_ROOT_CLASS = "DomainConcept"

# ============================================================================
# ISO/IEC 25010:2023 quality model — the grouping the standard defines
# ============================================================================
#
# The standard's structure is: 9 characteristics, each with a set of
# sub-characteristics. Both are enums in the schema; this table records WHICH
# sub-characteristics belong to WHICH characteristic, which an enum cannot say.
#
# It is declared here, in code, rather than as hand-written prose in a prompt or
# a doc, so it is a single checkable fact: `validate_quality_model` asserts that
# every sub-characteristic named below exists in the enum, that every
# sub-characteristic in the enum is claimed by exactly one characteristic, and
# that the characteristic set matches `QualityAttributeCategory` exactly. A
# taxonomy that drifts from its own enum is worse than no taxonomy, because the
# view would confidently mis-group a quality concern.
#
# The 2023 revision is what makes this table worth asserting rather than
# trusting: Usability became INTERACTION_CAPABILITY, and Portability was
# replaced by FLEXIBILITY — which is where Scalability now lives.
ISO_25010_2023: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    (
        "FUNCTIONAL_SUITABILITY",
        ("FUNCTIONAL_COMPLETENESS", "FUNCTIONAL_CORRECTNESS", "FUNCTIONAL_APPROPRIATENESS"),
    ),
    (
        "PERFORMANCE_EFFICIENCY",
        ("TIME_BEHAVIOUR", "RESOURCE_UTILIZATION", "CAPACITY"),
    ),
    (
        "COMPATIBILITY",
        ("CO_EXISTENCE", "INTEROPERABILITY"),
    ),
    (
        "INTERACTION_CAPABILITY",
        (
            "APPROPRIATENESS_RECOGNIZABILITY", "LEARNABILITY", "OPERABILITY",
            "USER_ERROR_PROTECTION", "USER_ENGAGEMENT", "INCLUSIVITY",
            "USER_ASSISTANCE", "SELF_DESCRIPTIVENESS",
        ),
    ),
    (
        "RELIABILITY",
        ("FAULTLESSNESS", "AVAILABILITY", "FAULT_TOLERANCE", "RECOVERABILITY"),
    ),
    (
        "SECURITY",
        (
            "CONFIDENTIALITY", "INTEGRITY", "NON_REPUDIATION", "ACCOUNTABILITY",
            "AUTHENTICITY", "RESISTANCE",
        ),
    ),
    (
        "MAINTAINABILITY",
        ("MODULARITY", "REUSABILITY", "ANALYSABILITY", "MODIFIABILITY", "TESTABILITY"),
    ),
    (
        "FLEXIBILITY",
        ("ADAPTABILITY", "SCALABILITY", "INSTALLABILITY", "REPLACEABILITY"),
    ),
    (
        "SAFETY",
        (
            "OPERATIONAL_CONSTRAINT", "RISK_IDENTIFICATION", "FAIL_SAFE",
            "HAZARD_WARNING", "SAFE_INTEGRATION",
        ),
    ),
)

# Characteristics we carry that are NOT from ISO 25010, and which model they are
# from instead. Kept explicit so nothing silently claims to be ISO.
NON_ISO_QUALITY_CONCERNS = {
    "REGULATORY_COMPLIANCE": "ENTERPRISE_GOVERNANCE",
}

# Sub-characteristic → characteristic, derived from the table above.
SUBCHARACTERISTIC_PARENT: Dict[str, str] = {
    sub: characteristic
    for characteristic, subs in ISO_25010_2023
    for sub in subs
}

QUALITY_CHARACTERISTIC_ENUM = "QualityAttributeCategory"
QUALITY_SUBCHARACTERISTIC_ENUM = "QualitySubcharacteristic"
QUALITY_ATTRIBUTE_CLASS = "QualityAttribute"

# The vocabulary names a reader's label has to be normalised INTO. Two catalogues
# now do this — the ISO 25010 quality model (`core.quality`) and the architecture
# pattern catalogue (`core.patterns`) — and a second copy of the rule is how two
# spellings of one concept start resolving differently.
_NON_ALNUM = re.compile(r"[^A-Za-z0-9]+")


def enum_name(label: str) -> str:
    """`High Availability` -> `HIGH_AVAILABILITY`. The shape the enums use."""
    return _NON_ALNUM.sub("_", (label or "").strip()).strip("_").upper()



class OntologyError(Exception):
    """The ontology could not be read."""


class OntologyIntegrityError(OntologyError):
    """Something the schema references does not resolve.

    Deliberately fatal rather than a warning. A slot range or an import that
    silently resolves to nothing is exactly how a vocabulary layer becomes inert
    while every test still passes — the failure mode that let the `domain` field
    sit unused in the extraction pipeline. If a reference cannot be resolved, the
    vocabulary handed to the model is *wrong*, not merely incomplete, so this
    refuses to load rather than degrading quietly.
    """


# ============================================================================
# Model
# ============================================================================


@dataclass
class SlotSpec:
    """One attribute of a class."""

    name: str
    range: str = "string"
    required: bool = False
    multivalued: bool = False
    identifier: bool = False
    inlined: bool = False
    description: str = ""
    ifabsent: str = ""
    # Resolved after the whole schema is loaded: class | enum | primitive | unknown.
    range_kind: str = "primitive"
    range_layer: str = ""

    @property
    def is_relationship(self) -> bool:
        """A slot whose range is another class is a relationship between concepts."""
        return self.range_kind == "class"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "range": self.range,
            "range_kind": self.range_kind,
            "range_layer": self.range_layer,
            "required": self.required,
            "multivalued": self.multivalued,
            "identifier": self.identifier,
            "inlined": self.inlined,
            "ifabsent": self.ifabsent,
            "description": self.description,
            "is_relationship": self.is_relationship,
        }


@dataclass
class ClassSpec:
    name: str
    layer: str
    description: str = ""
    is_a: Optional[str] = None
    mixins: List[str] = field(default_factory=list)
    abstract: bool = False
    mixin: bool = False
    attributes: List[SlotSpec] = field(default_factory=list)
    subsets: List[str] = field(default_factory=list)
    # Which file declared it. Empty for base layers (the layer implies the file);
    # set for domain-pack classes, because a pack is identified by its path and a
    # class must be attributable to the pack that contributed it.
    origin: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "layer": self.layer,
            "layer_label": LAYER_LABELS.get(self.layer, self.layer),
            "description": self.description,
            "is_a": self.is_a,
            "mixins": list(self.mixins),
            "abstract": self.abstract,
            "mixin": self.mixin,
            "subsets": list(self.subsets),
            "attribute_count": len(self.attributes),
            "attributes": [a.to_dict() for a in self.attributes],
        }


@dataclass
class EnumSpec:
    name: str
    layer: str
    description: str = ""
    values: List[str] = field(default_factory=list)
    value_descriptions: Dict[str, str] = field(default_factory=dict)
    origin: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "layer": self.layer,
            "layer_label": LAYER_LABELS.get(self.layer, self.layer),
            "description": self.description,
            "values": list(self.values),
            "value_descriptions": dict(self.value_descriptions),
            "value_count": len(self.values),
        }


@dataclass
class SubsetSpec:
    name: str
    layer: str
    description: str = ""


@dataclass
class OntologyLayer:
    key: str
    filename: str
    role: str
    schema_id: str = ""
    title: str = ""
    version: str = ""
    prefix: str = ""
    description: str = ""
    imports: List[str] = field(default_factory=list)
    external_imports: List[str] = field(default_factory=list)
    classes: List[str] = field(default_factory=list)
    enums: List[str] = field(default_factory=list)
    subsets: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return LAYER_LABELS.get(self.key, self.key)

    @property
    def class_count(self) -> int:
        return len(self.classes)

    @property
    def enum_count(self) -> int:
        return len(self.enums)

    @property
    def subset_count(self) -> int:
        return len(self.subsets)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "filename": self.filename,
            "role": self.role,
            "schema_id": self.schema_id,
            "title": self.title,
            "version": self.version,
            "prefix": self.prefix,
            "description": self.description,
            "imports": list(self.imports),
            "external_imports": list(self.external_imports),
            "classes": list(self.classes),
            "enums": list(self.enums),
            "subsets": list(self.subsets),
            "class_count": self.class_count,
            "enum_count": self.enum_count,
            "subset_count": self.subset_count,
        }


@dataclass
class OntologyModel:
    """The readable form of the foundational ontologies."""

    layers: List[OntologyLayer] = field(default_factory=list)
    classes: Dict[str, ClassSpec] = field(default_factory=dict)
    enums: Dict[str, EnumSpec] = field(default_factory=dict)
    subsets: Dict[str, SubsetSpec] = field(default_factory=dict)
    # Integrity findings, surfaced rather than hidden: a schema that silently
    # references a class it does not define is exactly the kind of thing a
    # reference view should tell you.
    unresolved_parents: List[Tuple[str, str]] = field(default_factory=list)
    unresolved_ranges: List[Tuple[str, str, str]] = field(default_factory=list)
    duplicate_names: List[str] = field(default_factory=list)
    root: str = ""

    # -- lookups -----------------------------------------------------------

    def layer(self, key: str) -> Optional[OntologyLayer]:
        return next((layer for layer in self.layers if layer.key == key), None)

    def get(self, name: str) -> Optional[ClassSpec]:
        return self.classes.get(name)

    def is_class(self, name: str) -> bool:
        return name in self.classes

    def is_enum(self, name: str) -> bool:
        return name in self.enums

    def classes_in(self, layer_key: Optional[str] = None) -> List[ClassSpec]:
        found = [c for c in self.classes.values() if layer_key in (None, c.layer)]
        return sorted(found, key=lambda c: (c.layer, c.name.lower()))

    def enums_in(self, layer_key: Optional[str] = None) -> List[EnumSpec]:
        found = [e for e in self.enums.values() if layer_key in (None, e.layer)]
        return sorted(found, key=lambda e: (e.layer, e.name.lower()))

    # -- structure ---------------------------------------------------------

    def parents(self, name: str) -> List[str]:
        """Direct supertypes: `is_a` plus `mixins`.

        Both are supertypes. They mean different things — `is_a` is the taxonomy,
        mixins are cross-cutting aspects — but for slot inheritance both contribute,
        so the resolver treats them the same way and the *view* keeps them distinct.
        """
        spec = self.classes.get(name)
        if spec is None:
            return []
        out = [spec.is_a] if spec.is_a else []
        out.extend(spec.mixins)
        return [p for p in out if p in self.classes]

    def ancestors(self, name: str) -> List[str]:
        """`name` followed by every supertype, breadth-first, deduplicated.

        Matches LinkML's own `class_ancestors` (which also walks mixins); the test
        suite asserts that equivalence rather than trusting this implementation.
        """
        if name not in self.classes:
            return []
        seen: List[str] = [name]
        queue = [name]
        while queue:
            for parent in self.parents(queue.pop(0)):
                if parent not in seen:
                    seen.append(parent)
                    queue.append(parent)
        return seen

    def children(self, name: str) -> List[str]:
        return sorted(c.name for c in self.classes.values() if c.is_a == name or name in c.mixins)

    def own_attributes(self, name: str) -> List[SlotSpec]:
        spec = self.classes.get(name)
        return list(spec.attributes) if spec else []

    def inherited_attributes(self, name: str) -> List[Tuple[str, SlotSpec]]:
        """Slots from ancestors, tagged with the class that declares them.

        Provenance of a slot matters when reading a schema: `id` coming from
        `Requirement` is a different fact from `quality_category` being local.
        """
        out: List[Tuple[str, SlotSpec]] = []
        seen = set()
        for ancestor in self.ancestors(name)[1:]:
            for slot in self.own_attributes(ancestor):
                if slot.name in seen:
                    continue
                seen.add(slot.name)
                out.append((ancestor, slot))
        return out

    def effective_attributes(self, name: str) -> List[SlotSpec]:
        """Own slots first, then inherited — the full set the class actually carries."""
        own = self.own_attributes(name)
        have = {s.name for s in own}
        return own + [s for _owner, s in self.inherited_attributes(name) if s.name not in have]

    def relationships(self, name: str) -> List[Tuple[SlotSpec, str]]:
        """Slots whose range is another class: (slot, target class).

        This is the relationship layer of the schema, as opposed to the hierarchy.
        """
        return [
            (slot, slot.range) for slot in self.effective_attributes(name) if slot.is_relationship
        ]

    def referenced_by(self, name: str) -> List[Tuple[str, str]]:
        """(owner class, slot name) pairs whose range points at `name`."""
        out = [
            (c.name, slot.name)
            for c in self.classes.values()
            for slot in c.attributes
            if slot.range == name
        ]
        return sorted(out)

    def stats(self) -> Dict[str, Any]:
        kinds: Dict[str, int] = {}
        for c in self.classes.values():
            kinds[c.layer] = kinds.get(c.layer, 0) + 1
        return {
            "layers": len(self.layers),
            "classes": len(self.classes),
            "enums": len(self.enums),
            "subsets": len(self.subsets),
            "abstract": sum(1 for c in self.classes.values() if c.abstract),
            "mixins": sum(1 for c in self.classes.values() if c.mixin),
            "slots": sum(len(c.attributes) for c in self.classes.values()),
            "relationships": sum(
                1 for c in self.classes.values() for s in c.attributes if s.is_relationship
            ),
            "classes_per_layer": kinds,
            "unresolved_parents": len(self.unresolved_parents),
            "unresolved_ranges": len(self.unresolved_ranges),
            "duplicate_names": len(self.duplicate_names),
            "root": self.root,
        }


# ============================================================================
# Loading
# ============================================================================


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if v]
    return []


def _parse_slots(raw: Any) -> List[SlotSpec]:
    """Parse a class's `attributes` (this schema defines slots inline, no top-level slots)."""
    if not isinstance(raw, dict):
        return []
    slots: List[SlotSpec] = []
    for slot_name, definition in raw.items():
        definition = definition if isinstance(definition, dict) else {}
        slots.append(
            SlotSpec(
                name=str(slot_name),
                range=str(definition.get("range") or "string"),
                required=bool(definition.get("required")),
                multivalued=bool(definition.get("multivalued")),
                identifier=bool(definition.get("identifier")),
                inlined=bool(definition.get("inlined") or definition.get("inlined_as_list")),
                description=str(definition.get("description") or "").strip(),
                ifabsent=str(definition.get("ifabsent") or ""),
            )
        )
    return slots


def _parse_enum(name: str, definition: Any, layer: str) -> EnumSpec:
    definition = definition if isinstance(definition, dict) else {}
    raw_values = definition.get("permissible_values") or {}
    values: List[str] = []
    descriptions: Dict[str, str] = {}

    if isinstance(raw_values, dict):
        for value, meta in raw_values.items():
            values.append(str(value))
            if isinstance(meta, dict) and meta.get("description"):
                descriptions[str(value)] = str(meta["description"]).strip()
    elif isinstance(raw_values, (list, tuple)):
        values = [str(v) for v in raw_values]

    return EnumSpec(
        name=name,
        layer=layer,
        description=str(definition.get("description") or "").strip(),
        values=values,
        value_descriptions=descriptions,
    )


def load_ontology(root: str | Path = "ontology") -> OntologyModel:
    """Read the foundational ontologies into a resolved model.

    Cached per resolved path. The model is **treated as immutable** by every
    consumer: projections build new dicts rather than handing out references into
    it, so a shared instance cannot be mutated out from under another caller. That
    matters because the web app loads it at startup and the agents will load it
    per run — re-parsing four schemas for each is pure waste.
    """
    return _load_cached(str(Path(root).resolve()))


@lru_cache(maxsize=8)
def _load_cached(resolved_root: str) -> OntologyModel:
    base = Path(resolved_root)
    if not base.is_dir():
        raise OntologyError(f"ontology directory not found: {base}")

    model = OntologyModel(root=str(base))
    by_stem: Dict[str, str] = {
        stem: key for key, filename, _role in LAYER_ORDER for stem in [Path(filename).stem]
    }

    raw_by_layer: Dict[str, Dict[str, Any]] = {}

    for key, filename, role in LAYER_ORDER:
        path = base / filename
        if not path.exists():
            raise OntologyError(f"missing ontology layer: {path}")
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise OntologyError(f"{path} is not valid YAML: {exc}") from exc
        raw_by_layer[key] = document
        model.layers.append(
            OntologyLayer(
                key=key,
                filename=filename,
                role=role,
                schema_id=str(document.get("id") or ""),
                title=str(document.get("title") or document.get("name") or ""),
                version=str(document.get("version") or ""),
                prefix=str(document.get("default_prefix") or ""),
                description=str(document.get("description") or "").strip(),
                classes=[],
                enums=[],
                subsets=sorted((document.get("subsets") or {}).keys()),
            )
        )

    # -- first pass: collect names so ranges can be resolved in a second pass --

    for layer in model.layers:
        document = raw_by_layer[layer.key]

        for class_name, definition in (document.get("classes") or {}).items():
            definition = definition if isinstance(definition, dict) else {}
            if class_name in model.classes:
                model.duplicate_names.append(class_name)
                continue
            model.classes[class_name] = ClassSpec(
                name=class_name,
                layer=layer.key,
                description=str(definition.get("description") or "").strip(),
                is_a=(str(definition["is_a"]) if definition.get("is_a") else None),
                mixins=_as_list(definition.get("mixins")),
                abstract=bool(definition.get("abstract")),
                mixin=bool(definition.get("mixin")),
                attributes=_parse_slots(definition.get("attributes")),
                subsets=_as_list(definition.get("in_subset")),
            )
            layer.classes.append(class_name)

        for enum_name, definition in (document.get("enums") or {}).items():
            if enum_name in model.enums:
                model.duplicate_names.append(enum_name)
                continue
            model.enums[enum_name] = _parse_enum(enum_name, definition, layer.key)
            layer.enums.append(enum_name)

        for subset_name, definition in (document.get("subsets") or {}).items():
            definition = definition if isinstance(definition, dict) else {}
            model.subsets[subset_name] = SubsetSpec(
                name=subset_name,
                layer=layer.key,
                description=str(definition.get("description") or "").strip(),
            )

        for imported in _as_list(document.get("imports")):
            if imported in EXTERNAL_IMPORTS or imported.startswith("linkml:"):
                layer.external_imports.append(imported)
            else:
                target = by_stem.get(imported)
                if target:
                    layer.imports.append(target)
                else:
                    layer.external_imports.append(imported)

    # -- second pass: resolve parents and slot ranges ------------------------

    for spec in model.classes.values():
        if spec.is_a and spec.is_a not in model.classes:
            model.unresolved_parents.append((spec.name, spec.is_a))
        for mixin in spec.mixins:
            if mixin not in model.classes:
                model.unresolved_parents.append((spec.name, mixin))

        for slot in spec.attributes:
            if slot.range in model.classes:
                slot.range_kind = "class"
                slot.range_layer = model.classes[slot.range].layer
            elif slot.range in model.enums:
                slot.range_kind = "enum"
                slot.range_layer = model.enums[slot.range].layer
            elif slot.range in PRIMITIVE_RANGES:
                slot.range_kind = "primitive"
            else:
                slot.range_kind = "unknown"
                model.unresolved_ranges.append((spec.name, slot.name, slot.range))

    return model


# ============================================================================
# Domain packs — the swappable overlay
# ============================================================================
#
# A domain pack is the vocabulary of the SUBJECT MATTER (Payment, PAN, Merchant,
# Chargeback), as opposed to the vocabulary of the artifact (Requirement,
# Goal, Container). It is deliberately NOT a fifth entry in LAYER_ORDER:
#
#   * LAYER_ORDER is the fixed base chain, present for every Initiative. A pack is
#     conditional — an Initiative may select none at all, and that must stay a
#     first-class state rather than a degraded one.
#   * `_load_cached` is keyed by directory alone, so a pack baked into it could not
#     be swapped per Initiative without restarting the process.
#   * tests/test_ontology.py asserts exactly four base layers. A pack is an
#     addition to the *working vocabulary*, not a change to the base ontology.
#
# So a pack is loaded separately, cached by its own path, and *overlaid* on a base
# model by the callers that need both (the extraction prompt, the coverage view).
# Nothing here mutates the base model: `load_ontology` hands out a cached instance
# that other callers share, so the pack keeps its own dictionaries and resolution
# goes through `OntologyModel.resolve()`.


@dataclass
class DomainPack:
    """One domain ontology, loaded and integrity-checked against the base layers.

    `classes` and `enums` hold only what THIS pack declares. Inherited base
    classes are reachable through the base model the pack was validated against,
    not copied in — copying would make the pack look like it declares
    `DomainConcept` itself and would double-count it in any census.
    """

    spec: str                      # pack reference: filename stem or relative path
    path: str = ""
    layer: str = DOMAIN_LAYER
    schema_id: str = ""
    title: str = ""
    version: str = ""
    description: str = ""
    prefix: str = ""
    imports: List[str] = field(default_factory=list)
    external_imports: List[str] = field(default_factory=list)
    classes: Dict[str, ClassSpec] = field(default_factory=dict)
    enums: Dict[str, EnumSpec] = field(default_factory=dict)
    subsets: Dict[str, SubsetSpec] = field(default_factory=dict)
    # Free-text guidance the pack contributes to extraction prompts. Kept in the
    # pack rather than in the agent's prompt string for the same reason the
    # vocabulary is: a domain example hard-coded in `agents/` makes the tool
    # domain-shaped while appearing generic, which is exactly how the payment
    # worked example ended up in a base prompt.
    annotations: Dict[str, str] = field(default_factory=dict)
    # Populated by `validate_pack`: unresolved references found INSIDE the pack.
    unresolved_parents: List[Tuple[str, str]] = field(default_factory=list)
    unresolved_ranges: List[Tuple[str, str, str]] = field(default_factory=list)

    @property
    def label(self) -> str:
        return LAYER_LABELS.get(self.layer, self.layer)

    @property
    def class_count(self) -> int:
        return len(self.classes)

    @property
    def enum_count(self) -> int:
        return len(self.enums)

    @property
    def concrete_classes(self) -> List[str]:
        return sorted(
            name for name, spec in self.classes.items() if not spec.abstract and not spec.mixin
        )

    @property
    def is_usable(self) -> bool:
        return not (self.unresolved_parents or self.unresolved_ranges)

    def get(self, name: str) -> Optional[ClassSpec]:
        return self.classes.get(name)

    def is_class(self, name: str) -> bool:
        return name in self.classes

    def to_dict(self) -> Dict[str, Any]:
        return {
            "spec": self.spec,
            "path": self.path,
            "layer": self.layer,
            "layer_label": self.label,
            "schema_id": self.schema_id,
            "title": self.title,
            "version": self.version,
            "description": self.description,
            "prefix": self.prefix,
            "imports": list(self.imports),
            "external_imports": list(self.external_imports),
            "class_count": self.class_count,
            "enum_count": self.enum_count,
            "subset_count": len(self.subsets),
            "concrete_classes": self.concrete_classes,
            # Full specs, not just names: the coverage view needs each class's
            # subsets and description to render a useful census row, and making it
            # re-load the pack for that would be a second source of truth.
            "classes": {
                name: spec.to_dict() for name, spec in sorted(self.classes.items())
            },
            "enums": {name: spec.to_dict() for name, spec in sorted(self.enums.items())},
            "subsets": {
                name: {"name": subset.name, "description": subset.description}
                for name, subset in sorted(self.subsets.items())
            },
            "is_usable": self.is_usable,
            "annotation_keys": sorted(self.annotations),
            "unresolved_parents": list(self.unresolved_parents),
            "unresolved_ranges": list(self.unresolved_ranges),
        }


def domain_pack_dir(root: str | Path = "ontology") -> Path:
    return Path(root) / DOMAIN_PACK_DIRNAME


def discover_domain_packs(root: str | Path = "ontology") -> List[Dict[str, Any]]:
    """List the packs available to select, without loading them.

    Cheap on purpose: this backs a picker, and a picker must not fail because one
    unrelated pack is broken. A malformed pack is reported as `loadable: False`
    with its error, so the UI can offer the rest of the list and say why one is
    unavailable — rather than the whole selector collapsing.
    """
    directory = domain_pack_dir(root)
    if not directory.is_dir():
        return []

    found: List[Dict[str, Any]] = []
    for path in sorted(directory.glob("*.yaml")):
        if path.name.startswith("."):
            continue
        entry: Dict[str, Any] = {
            "spec": path.stem,
            "filename": path.name,
            "path": str(path),
            "title": path.stem.replace("_", " ").title(),
            "version": "",
            "description": "",
            "class_count": 0,
            "loadable": True,
            "error": "",
        }
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            entry["title"] = str(document.get("title") or document.get("name") or entry["title"])
            entry["version"] = str(document.get("version") or "")
            entry["description"] = str(document.get("description") or "").strip()
            entry["class_count"] = len(document.get("classes") or {})
        except Exception as exc:                                     # noqa: BLE001
            entry["loadable"] = False
            entry["error"] = f"{type(exc).__name__}: {exc}"
        found.append(entry)
    return found


def resolve_domain_pack_path(spec: str, root: str | Path = "ontology") -> Optional[Path]:
    """Turn a pack reference into a path. `None` means "no pack", not "not found".

    Accepts what a human, a form, or recorded provenance would actually carry: a
    bare stem (`payment_processing`), a filename, the `domains/`-relative name, a
    full relative path, or a versioned id (`payment_processing@0.1.0`) as written
    into assertion provenance. An empty or explicitly-generic spec resolves to
    `None`, which callers must treat as the supported "no domain pack" state.
    """
    spec = (spec or "").strip()
    if not spec or spec.lower() in {"none", "generic", "null", "-"}:
        return None

    # Provenance records `stem@version` so a vocabulary change is distinguishable
    # from a content change. Resolving the id has to tolerate its own format, or
    # reading the pack back out of a graph fails on a string this module wrote.
    requested_version = ""
    if "@" in spec:
        stem_part, _, requested_version = spec.rpartition("@")
        spec = stem_part or spec

    root = Path(root)
    directory = domain_pack_dir(root)

    def first_existing(candidates) -> Optional[Path]:
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    hit: Optional[Path] = None
    # A path the caller already worked out. Resolved relative to the process cwd
    # first, since that is what a CLI user means by `./ontology/domains/x.yaml`.
    if "/" in spec or spec.endswith(".yaml"):
        raw = Path(spec)
        hit = first_existing([raw, root / spec, root / raw.name, directory / raw.name])
        stem = raw.stem
    else:
        stem = spec.lstrip("./")
        hit = first_existing([directory / f"{stem}.yaml", root / f"{stem}.yaml"])

    if hit is None:
        # A spec that names something is a missing pack, not an absent one.
        # Returning None here would silently run extraction as "generic" and
        # quietly discard the vocabulary the caller asked for — the exact class of
        # inert-layer bug this whole mechanism exists to remove.
        raise OntologyError(f"domain pack not found: {spec!r} (looked in {directory!s})")

    if requested_version:
        declared = _declared_version(hit)
        if declared and declared != requested_version:
            # Not fatal: the file was legitimately edited or re-released since the
            # fact was recorded. But a graph re-grounded in a different revision of
            # the vocabulary than it was built under is worth saying out loud.
            warnings.warn(
                f"domain pack {stem!r} is at version {declared}, but {requested_version} "
                f"was requested; the vocabulary may have changed since this was recorded",
                stacklevel=2,
            )
    return hit


def _declared_version(path: Path) -> str:
    """Read only the `version:` of a schema."""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return ""
    return str(document.get("version") or "")


def load_domain_pack(
    spec: str | Path,
    root: str | Path = "ontology",
) -> Optional[DomainPack]:
    """Load one domain pack and check that everything it references resolves.

    Returns None for an explicit "no pack" spec — see `resolve_domain_pack_path`.

    Validation is the point of this function, not a side effect. A pack is a
    vocabulary handed straight to a model, so a class that inherits from nothing
    or a slot range that points at nothing does not degrade gracefully: it produces
    a prompt that is confidently wrong. Every failure here is loud and names the
    pack.
    """
    resolved = spec if isinstance(spec, Path) else resolve_domain_pack_path(spec, root)
    if resolved is None:
        return None
    return _load_domain_pack_cached(str(Path(resolved).resolve()), str(Path(root).resolve()))


@lru_cache(maxsize=16)
def _load_domain_pack_cached(resolved_path: str, resolved_root: str) -> DomainPack:
    path = Path(resolved_path)
    if not path.is_file():
        raise OntologyError(f"domain pack not found: {resolved_path}")

    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise OntologyError(f"{path} is not valid YAML: {exc}") from exc

    if not isinstance(document, dict):
        raise OntologyError(f"{path} is not a LinkML schema (top level is not a mapping)")

    pack = DomainPack(
        spec=path.stem,
        path=str(path),
        schema_id=str(document.get("id") or ""),
        title=str(document.get("title") or document.get("name") or path.stem),
        version=str(document.get("version") or ""),
        description=str(document.get("description") or "").strip(),
        prefix=str(document.get("default_prefix") or ""),
    )

    # `annotations:` carries prose the pack wants in the prompt — a worked example,
    # domain extraction guidance. Same extension point LinkML uses, read here
    # because the prompt builder is ours, not LinkML's.
    raw_annotations = document.get("annotations") or {}
    if isinstance(raw_annotations, dict):
        for key, value in raw_annotations.items():
            if value is None:
                continue
            text = value.get("value") if isinstance(value, dict) else value
            if isinstance(text, str) and text.strip():
                pack.annotations[str(key)] = text.strip()

    if not pack.schema_id:
        raise OntologyError(f"domain pack {path} declares no `id`")
    # Guard against schema-id drift: a pack copied from another is the normal way
    # one gets written, and a leftover `id` makes two different vocabularies claim
    # the same identity in RDF and in the provenance record.
    if pack.schema_id.rstrip("/").rsplit("/", 1)[-1] != path.stem.replace("_", "-"):
        raise OntologyError(
            f"domain pack {path} has id {pack.schema_id!r}, which does not end in "
            f"{path.stem.replace('_', '-')!r} — the id must be unique to this pack"
        )
    if not pack.version:
        # Not pedantry: the pack id is recorded in assertion provenance, and a pack
        # without a version cannot be told apart from an edited one. "Which
        # vocabulary was in force when this was extracted?" has to stay answerable.
        raise OntologyError(f"domain pack {path} declares no `version` (required for provenance)")

    for class_name, definition in (document.get("classes") or {}).items():
        definition = definition if isinstance(definition, dict) else {}
        pack.classes[class_name] = ClassSpec(
            name=class_name,
            layer=DOMAIN_LAYER,
            description=str(definition.get("description") or "").strip(),
            is_a=(str(definition["is_a"]) if definition.get("is_a") else None),
            mixins=_as_list(definition.get("mixins")),
            abstract=bool(definition.get("abstract")),
            mixin=bool(definition.get("mixin")),
            attributes=_parse_slots(definition.get("attributes")),
            subsets=_as_list(definition.get("in_subset")),
            origin=str(path),
        )

    for enum_name, definition in (document.get("enums") or {}).items():
        spec_obj = _parse_enum(enum_name, definition, DOMAIN_LAYER)
        spec_obj.origin = str(path)
        pack.enums[enum_name] = spec_obj

    for subset_name, definition in (document.get("subsets") or {}).items():
        definition = definition if isinstance(definition, dict) else {}
        pack.subsets[subset_name] = SubsetSpec(
            name=subset_name,
            layer=DOMAIN_LAYER,
            description=str(definition.get("description") or "").strip(),
        )

    if not pack.classes:
        raise OntologyError(f"domain pack {path} declares no classes")

    base = load_ontology(resolved_root)
    _resolve_pack_imports(pack, base, path, _as_list(document.get("imports")))
    _validate_pack(pack, base, path)
    return pack


def _resolve_pack_imports(
    pack: DomainPack, base: OntologyModel, path: Path, declared: List[str]
) -> None:
    """Classify the pack's imports, refusing to silently drop one.

    This is where the earlier `_collect_ontology_names` behaviour was actively
    harmful: it resolved `imports:` relative to the *importing file's* directory
    and, when a file was not there, skipped it with a bare `continue`. A pack at
    `ontology/domains/` importing `requirements_base` would therefore inherit
    nothing at all — while every test still passed, because the base layers
    loaded fine on their own.
    """
    known_layers = {layer.key for layer in base.layers}
    stems = {key: Path(filename).stem for key, filename, _role in LAYER_ORDER}

    for imported in declared:
        if imported in EXTERNAL_IMPORTS or imported.startswith("linkml:"):
            pack.external_imports.append(imported)
            continue

        # Accept both `requirements_base` and `../requirements_base`.
        normalized = str(imported).replace("\\", "/").lstrip("./")
        stem = Path(normalized).name
        if stem.endswith(".yaml"):
            stem = stem[: -len(".yaml")]

        layer_key = next((k for k, s in stems.items() if s == stem), None)
        if layer_key is None:
            raise OntologyError(
                f"domain pack {path} imports {imported!r}, which is neither a base "
                f"ontology layer nor a linkml module (known layers: "
                f"{', '.join(sorted(known_layers))})"
            )
        pack.imports.append(layer_key)


def _validate_pack(pack: DomainPack, base: OntologyModel, path: Path) -> None:
    """Resolve the pack against itself plus the base ontology.

    Ranges may point at a base class (the pack's whole reason for importing one)
    or at a pack-local class. Anything else is a hard failure — see
    `OntologyIntegrityError`.
    """
    def exists(name: str) -> bool:
        return name in pack.classes or base.is_class(name)

    for spec in pack.classes.values():
        if spec.is_a and not exists(spec.is_a):
            pack.unresolved_parents.append((spec.name, spec.is_a))
        for mixin in spec.mixins:
            if not exists(mixin):
                pack.unresolved_parents.append((spec.name, mixin))

        for slot in spec.attributes:
            if slot.range in pack.classes:
                slot.range_kind = "class"
                slot.range_layer = DOMAIN_LAYER
            elif slot.range in base.classes:
                slot.range_kind = "class"
                slot.range_layer = base.classes[slot.range].layer
            elif slot.range in pack.enums:
                slot.range_kind = "enum"
                slot.range_layer = DOMAIN_LAYER
            elif slot.range in base.enums:
                slot.range_kind = "enum"
                slot.range_layer = base.enums[slot.range].layer
            elif slot.range in PRIMITIVE_RANGES:
                slot.range_kind = "primitive"
            else:
                slot.range_kind = "unknown"
                pack.unresolved_ranges.append((spec.name, slot.name, slot.range))

    if not pack.is_usable:
        raise OntologyIntegrityError(
            f"domain pack {path} has unresolved references — "
            f"unresolved supertypes: {pack.unresolved_parents}; "
            f"unresolved slot ranges: {pack.unresolved_ranges}"
        )

    # A pack that specialises nothing is not a pack. Without this check a second
    # base ontology dropped into `domains/` would load happily and shadow nothing,
    # and the coverage census would count artifact classes as subject matter.
    if not pack.imports:
        raise OntologyIntegrityError(
            f"domain pack {path} imports no base ontology layer; a pack must extend "
            f"the base vocabulary rather than restate it"
        )

    if DOMAIN_PACK_ROOT_CLASS not in base.classes:
        raise OntologyIntegrityError(
            f"base ontology does not define {DOMAIN_PACK_ROOT_CLASS}, so pack "
            f"{path} cannot be an overlay"
        )

    specialises_root = any(
        spec.is_a == DOMAIN_PACK_ROOT_CLASS
        or DOMAIN_PACK_ROOT_CLASS in (spec.mixins or [])
        or DOMAIN_PACK_ROOT_CLASS in _ancestor_names(spec, pack, base)
        for spec in pack.classes.values()
    )
    if not specialises_root:
        raise OntologyIntegrityError(
            f"domain pack {path} declares no subclass of {DOMAIN_PACK_ROOT_CLASS}; "
            f"its classes would not be reachable as domain concepts"
        )


def _ancestor_names(spec: ClassSpec, pack: DomainPack, base: OntologyModel) -> List[str]:
    """Walk up through pack-local classes into the base taxonomy.

    Bounded by the number of classes so a malformed cycle cannot hang the loader —
    this runs during validation, which is exactly when the schema is untrustworthy.
    """
    seen: List[str] = []
    queue = [spec.is_a] if spec.is_a else []
    guard = len(pack.classes) + len(base.classes) + 1
    while queue and guard > 0:
        guard -= 1
        current = queue.pop(0)
        if not current or current in seen:
            continue
        seen.append(current)
        local = pack.classes.get(current)
        if local is not None:
            if local.is_a:
                queue.append(local.is_a)
            queue.extend(local.mixins)
        else:
            base_spec = base.classes.get(current)
            if base_spec is not None:
                if base_spec.is_a:
                    queue.append(base_spec.is_a)
                queue.extend(base_spec.mixins)
    return seen


def load_domain_packs(specs, root: str | Path = "ontology") -> List[DomainPack]:
    """Load several packs, preserving order and skipping explicit "no pack" specs."""
    loaded: List[DomainPack] = []
    for spec in specs or []:
        pack = load_domain_pack(spec, root)
        if pack is not None:
            loaded.append(pack)
    return loaded


def domain_pack_payload(packs: List[DomainPack]) -> Dict[str, Any]:
    """Packs as JSON, for the API and the reference view."""
    return {
        "active": [pack.to_dict() for pack in packs],
        "count": len(packs),
        "classes": sorted({name for pack in packs for name in pack.classes}),
        "enums": sorted({name for pack in packs for name in pack.enums}),
    }


def pack_for_graph(graph, root: str | Path = "ontology") -> Optional[DomainPack]:
    """Which pack was in force when this graph was built, if any.

    Read back out of assertion provenance rather than from configuration, because
    those diverge the moment an Initiative is re-extracted under a different pack:
    the record must say which vocabulary produced a fact, not which one is
    selected now. Ties are impossible in practice (one pack per run), and the
    first is taken so the answer is stable rather than arbitrary.
    """
    if graph is None:
        return None
    for assertion in graph.active():
        spec = getattr(assertion.provenance, "domain_pack", "")
        if spec:
            try:
                return load_domain_pack(spec, root)
            except OntologyError:
                # A pack can be renamed or removed after the fact. The graph stays
                # readable; it just cannot be re-grounded in a vocabulary that is
                # no longer present.
                return None
    return None


# ============================================================================
# ISO 25010 quality model — validation and catalogue
# ============================================================================


def quality_characteristics(model: OntologyModel) -> List[str]:
    """Every characteristic value the enum declares, in schema order.

    Ten values: the nine ISO/IEC 25010:2023 characteristics plus
    REGULATORY_COMPLIANCE, which is enterprise governance rather than product
    quality. Use `quality_iso_characteristics()` when only the standard's nine
    should be considered — the distinction is why `QualityConcernClass` exists.
    """
    enum = model.enums.get(QUALITY_CHARACTERISTIC_ENUM)
    return list(enum.values) if enum else []


def quality_iso_characteristics() -> List[str]:
    """The nine ISO/IEC 25010:2023 characteristics, from the taxonomy table.

    Read from the table rather than filtered out of the enum, so the standard's
    own content has exactly one definition.
    """
    return [characteristic for characteristic, _subs in ISO_25010_2023]


def quality_non_iso_characteristics(model: OntologyModel) -> List[str]:
    """Enum values that are not ISO 25010 characteristics."""
    return [c for c in quality_characteristics(model) if c in NON_ISO_QUALITY_CONCERNS]


def quality_subcharacteristics(model: OntologyModel, characteristic: str = "") -> List[str]:
    """Sub-characteristics, optionally restricted to one characteristic."""
    if characteristic:
        return list(dict(ISO_25010_2023).get(characteristic, ()))
    enum = model.enums.get(QUALITY_SUBCHARACTERISTIC_ENUM)
    return list(enum.values) if enum else []


def quality_model_findings(model: OntologyModel) -> List[str]:
    """Where the ISO 25010 table and the schema disagree.

    Returned rather than raised, and surfaced on the reference view, because the
    consequence of drift is silent mis-grouping: the page would show Scalability
    under the wrong characteristic and a reader would have no reason to doubt it.
    """
    findings: List[str] = []

    characteristics = model.enums.get(QUALITY_CHARACTERISTIC_ENUM)
    subs_enum = model.enums.get(QUALITY_SUBCHARACTERISTIC_ENUM)
    if characteristics is None:
        findings.append(f"{QUALITY_CHARACTERISTIC_ENUM} is not declared")
    if subs_enum is None:
        findings.append(f"{QUALITY_SUBCHARACTERISTIC_ENUM} is not declared")
    if findings:
        return findings

    declared_chars = set(characteristics.values)
    table_chars = {c for c, _subs in ISO_25010_2023}
    for missing in sorted(table_chars - declared_chars):
        findings.append(f"{missing} is in the ISO 25010 table but not in the enum")
    for extra in sorted(declared_chars - table_chars):
        if extra not in NON_ISO_QUALITY_CONCERNS:
            findings.append(
                f"{extra} is in the enum but neither in the ISO 25010 table nor "
                f"declared in NON_ISO_QUALITY_CONCERNS"
            )

    declared_subs = set(subs_enum.values)
    table_subs = set(SUBCHARACTERISTIC_PARENT)
    for missing in sorted(table_subs - declared_subs):
        findings.append(f"sub-characteristic {missing} is in the table but not in the enum")
    for extra in sorted(declared_subs - table_subs):
        findings.append(f"sub-characteristic {extra} is in the enum but claimed by no characteristic")

    return findings


def quality_attribute_catalog(model: OntologyModel) -> List[Dict[str, Any]]:
    """The ISO 25010 taxonomy as data, for prompts and views.

    One entry per characteristic, each carrying its sub-characteristics. This is
    what lets extraction classify at the level the standard actually classifies
    at, instead of collapsing "within 500ms" and "1000 TPS with horizontal
    scaling" into one PERFORMANCE_EFFICIENCY bucket.
    """
    catalog: List[Dict[str, Any]] = []
    for characteristic, subs in ISO_25010_2023:
        catalog.append(
            {
                "characteristic": characteristic,
                "concern_class": "ISO_25010_2023",
                "subcharacteristics": list(subs),
            }
        )
    for characteristic, concern in NON_ISO_QUALITY_CONCERNS.items():
        catalog.append(
            {
                "characteristic": characteristic,
                "concern_class": concern,
                "subcharacteristics": [],
            }
        )
    return catalog


def quality_attribute_payload(model: OntologyModel) -> Dict[str, Any]:
    """The quality model as JSON — for the API and the reference view."""
    return {
        "catalog": quality_attribute_catalog(model),
        "characteristics": quality_characteristics(model),
        "subcharacteristics": quality_subcharacteristics(model),
        "subcharacteristic_count": len(quality_subcharacteristics(model)),
        "non_iso_concerns": dict(NON_ISO_QUALITY_CONCERNS),
        "attribute_class_declared": QUALITY_ATTRIBUTE_CLASS in model.classes,
        "findings": quality_model_findings(model),
    }


# ============================================================================
# Predicate vocabulary — the relationship names the graph actually routes on
# ============================================================================
#
# WHY THIS EXISTS. The predicate of a triple is free text on the extraction schema
# (`ExtractedTriple.predicate` is a plain `str`), and nothing ever told the model
# which relationship names the ontology declares. Measured on live runs: 15 of 16
# and 16 of 17 predicates were invented, and only 2 of 16 were routed to
# reconciliation — the rest became local edges that no downstream consumer reads.
#
# The names existed all along as relationship slots; they were simply never sent.
# `_format_ontology_context` had always injected CLASS and ENUM names, and the
# predicate axis was the one nobody wired up.
#
# COMPILED FROM THE ONTOLOGY, not hand-maintained, for the same reason the rest of
# this module is: a hand-written list drifts from the schema it describes. The
# prompt's own examples already demonstrate that — they say `traces_to_goal` while
# the schema declares `traces_to_goals`.
#
# WHAT QUALIFIES. A class-ranged slot is a candidate, but most are not edges: slots
# like `assumptions`, `applications`, `activities` and `stakeholders` are RECORD
# FIELDS that hold lists on one node, not links between nodes. Injecting all 128
# would cost ~1,100 tokens and teach the model to emit `--assumptions-->`, which is
# both noise and wrong. So candidates are filtered to names that read as
# relationships, which is a heuristic — and stated as one, because a slot named
# unusually will be missed and that should be visible rather than silent.

# Slot names that read as relationships between nodes.
_PREDICATE_SHAPE = re.compile(
    r"^(traces_to|binds_to|depends_on|conflicts_with|refines|derives_from|"
    r"governed_by|satisfies_|implements_|realizes_|addresses_|supports_|owns_|"
    r"consumes|produces|comprises|scopes|extends_|is_part_of|performs_|serves_|"
    r"delivers_|held_by|mandated_by|adopted_by|applies_to|runs_on|hosted_on|"
    r"assigned_|excluded_|included_|originates_|pursues_|exposes|provided_by|"
    r"part_of|supersedes|belongs_to|prescribed_by|defined_by)"
)

# Slots that point at infrastructure-of-the-record rather than meaning.
_PREDICATE_EXCLUDE = frozenset({"provenance", "external_references"})

# The layers whose predicates are offered to the extraction agents.
PREDICATE_LAYERS = ("requirements", "architecture")


def relationship_predicates(
    model: OntologyModel, layers: Optional[Any] = None
) -> Dict[str, List[str]]:
    """Declared relationship predicates and the classes they may point at.

    The target kinds matter as much as the names: a predicate whose range is known
    is checkable, and the reconciler already uses exactly this pairing to refuse a
    link to the wrong kind of thing.

    `layers` restricts the vocabulary to the layers an agent may actually speak.
    The filter is on the layer that DECLARES the slot, not on its range: a
    predicate belongs to the profile whose schema defines it, and that is what
    stops the requirements profile being taught the architecture join (YB-030).
    """
    allowed = frozenset(layers) if layers else None
    found: Dict[str, set] = {}
    for spec in model.classes.values():
        if spec.layer not in PREDICATE_LAYERS:
            continue
        if allowed is not None and spec.layer not in allowed:
            continue
        for slot in spec.attributes:
            if slot.range_kind != "class" or slot.name in _PREDICATE_EXCLUDE:
                continue
            if not _PREDICATE_SHAPE.match(slot.name):
                continue
            found.setdefault(slot.name, set()).add(slot.range)
    return {name: sorted(targets) for name, targets in sorted(found.items())}


def entry_layer_key(model: OntologyModel, entry: Any) -> str:
    """The layer key an agent's entry schema names, or "" when it names none.

    `ontology_path` is how an agent says which layer it speaks; this turns that
    filename back into the loader's own key so the two cannot drift apart in a
    second lookup table.
    """
    if not entry:
        return ""
    stem = Path(str(entry)).stem
    for layer in model.layers:
        if Path(layer.filename).stem == stem:
            return layer.key
    return ""


def visible_layer_keys(model: OntologyModel, entry: Any) -> FrozenSet[str]:
    """The layer keys reachable from an entry schema by following `imports:`.

    WHAT THIS FIXES. `_predicate_vocabulary_context` loaded the ontology ROOT, so
    every profile was handed the same vocabulary — including the architecture
    layer's, whatever layer it declared for itself. That is how the requirements
    profile came to emit `implements_requirement`, the join only an architecture
    element can make, and report eleven of twelve requirements realized on a graph
    with no architecture in it at all (YB-030).

    A schema that does not import the architecture layer is not shown the
    architecture layer's predicates. `architecture_base` imports the other three,
    so the architecture profiles see everything and are unchanged; the
    requirements profile sees its own layer and the two beneath it.

    Empty means "no entry schema named", which callers read as unscoped rather
    than as "no vocabulary".
    """
    start = entry_layer_key(model, entry)
    if not start:
        return frozenset()
    seen: set = set()
    stack = [start]
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        layer = model.layer(key)
        if layer is not None:
            stack.extend(layer.imports)
    return frozenset(seen)


def predicate_vocabulary_findings(model: OntologyModel) -> List[str]:
    """Names the shape filter drops, so the heuristic's blind spots are visible.

    Returned rather than raised, like the quality-model findings: a missing
    predicate degrades the vocabulary silently, and silence is what made this
    whole axis invisible for so long.
    """
    findings: List[str] = []
    for spec in model.classes.values():
        if spec.layer not in PREDICATE_LAYERS:
            continue
        for slot in spec.attributes:
            if slot.range_kind != "class" or slot.name in _PREDICATE_EXCLUDE:
                continue
            if not _PREDICATE_SHAPE.match(slot.name):
                findings.append(f"{spec.name}.{slot.name} -> {slot.range}")
    return sorted(set(findings))


def predicate_vocabulary_payload(model: OntologyModel) -> Dict[str, Any]:
    """The predicate vocabulary as JSON, for the API and the reference view."""
    vocab = relationship_predicates(model)
    return {
        "predicates": vocab,
        "count": len(vocab),
        "target_kinds": {name: targets for name, targets in vocab.items()},
        "excluded_as_field_like": predicate_vocabulary_findings(model),
    }


# The predicates the graph ROUTES on: reconciliation matches these into the other
# graph, so their names are the ones that must be spelled exactly right. The rest
# of the vocabulary is still offered, but only these get spelled out with their
# targets in the prompt — they are where a mis-spelling costs a lost traceability
# edge rather than an unused local one.
#
# Kept here as an explicit set rather than imported from
# `core.knowledge.model.CROSS_GRAPH_PREDICATES`, because `core/ontology.py` must
# stay importable without the knowledge layer. A test asserts the two agree, so
# the duplication cannot drift silently.
CORE_ROUTED_PREDICATES = frozenset({
    "implements_requirements",
    "satisfies_quality_attributes",
    "realizes_attribute",
    "satisfies_attributes",
    "traces_to_goals",
    "traces_to_capabilities",
    "traces_to_processes",
    "governed_by_rules",
    "supports_capabilities",
    "addresses_goals",
    "delivers_initiatives",
    "mandated_by",
})


# Singular forms the routing tables already accept, named explicitly so the prompt
# can say so. The schema declares `traces_to_goals`; the model reliably produces
# `traces_to_goal`, and reconciliation has always accepted it. Saying which aliases
# exist is more honest than claiming a general singular/plural rule — there is no
# such rule, and implying one would invite spellings nothing recognises.
#
# THE TABLE WAS INCOMPLETE, AND THAT WAS THE BUG (YB-031). `ROUTING_ALIASES` was
# only ever read to DESCRIBE aliases in the prompt; nothing resolved through it at
# ingest. So a declared name with no entry here was taught to the model, written
# into the graph as a local edge, and made invisible to reconciliation, the map and
# the census — while looking perfectly present. Three relationships had no entry at
# all, and they are the ones that matter most: the join itself
# (`implements_requirements`), and two of the four traceability axes
# (`addresses_goals`, `delivers_initiatives`). Measured on the merged working set:
# 1 `implements_requirements` and 4 `satisfies_quality_attributes` had gone that way.
ROUTING_ALIASES: Dict[str, Tuple[str, ...]] = {
    "implements_requirements": ("implements_requirement",),
    "traces_to_goals": ("traces_to_goal",),
    "traces_to_capabilities": ("traces_to_capability",),
    "traces_to_processes": ("traces_to_process",),
    "supports_capabilities": ("supports_capability",),
    "addresses_goals": ("addresses_goal",),
    "delivers_initiatives": ("delivers_initiative",),
    "satisfies_quality_attributes": ("satisfies_quality_attribute",),
    "realizes_attribute": ("realizes_quality_attribute",),
}


# Routing entries that exist only to absorb extractor drift: names the graph
# matches on that correspond to NO declared slot. They are not offered to the
# model — you do not teach a spelling you would rather it stopped using — but they
# must be acknowledged, because a reader comparing `CROSS_GRAPH_PREDICATES` with
# the vocabulary would otherwise conclude the vocabulary is incomplete. It is not:
# these are aliases with no schema definition behind them, which is its own small
# finding about how the routing tables were built.
ABSORBED_DRIFT_PREDICATES = {
    "implements_functional_requirement": "implements_requirements",
    "implements_non_functional_requirement": "implements_requirements",
    "supports_business_capability": "supports_capabilities",
}

# The names the router actually routes. Duplicated from
# `core.knowledge.model.CROSS_GRAPH_PREDICATES` for the same import reason as
# `CORE_ROUTED_PREDICATES`; `tests/test_ontology.py` proves the two agree.
_ROUTED_PREDICATES = frozenset({
    "implements_requirement",
    "implements_functional_requirement",
    "implements_non_functional_requirement",
    "satisfies_quality_attribute",
    "realizes_quality_attribute",
    "traces_to_goal",
    "traces_to_capability",
    "traces_to_process",
    "supports_capability",
    "supports_business_capability",
    "addresses_goal",
    "delivers_initiative",
    "governed_by_rule",
    "governed_by_rules",
    "mandated_by",
})


# Taught predicates that are deliberately LOCAL — the graph writes them as an edge
# to a node in the SAME graph, never as a cross-graph reference. Listed so a test can
# tell "intentionally local" apart from "silently unrouted", which is the whole
# distinction YB-031 turns on: the defect was never that a predicate was local, it
# was that nobody could tell which ones were meant to be.
LOCAL_PREDICATES = frozenset({
    # An element or pattern declaring the quality attribute it delivers. Ingest
    # materialises the attribute as a node and writes `satisfies_attribute`; the
    # plural here is the schema slot name the model is shown.
    "satisfies_attributes",
})


def canonical_predicate(name: str) -> str:
    """The one name this relationship is written under, whatever the model wrote.

    A predicate the prompt teaches must route, and one relationship must not exist
    in the graph under two names — both were false before this (YB-031). The
    model was handed the schema's plural spellings and the router knew the
    singular, so an edge could be written, drawn by the map, and invisible to
    every consumer that routes on the cross-graph set.

    TWO RULES, AND THE SECOND IS THE IMPORTANT ONE:

    1. An alias is followed to its routed target.
    2. **A predicate the router already routes is never rewritten.** The alias
       table is not a normalisation of the schema — it rescues spellings that do
       not route. `implements_functional_requirement` is routed with the target
       `FunctionalRequirement`, and rewriting it to the general
       `implements_requirement` would lose that precision for no gain. Only an
       unrouted name moves.

    Returns the input unchanged when no rule applies, so a local predicate
    (`part_of`, `uses_technology`) is untouched — this canonicalises the
    cross-graph boundary, not the whole vocabulary.
    """
    key = (name or "").strip()
    if not key or key in _ROUTED_PREDICATES:
        return key
    for alias in ROUTING_ALIASES.get(key, ()):
        if alias in _ROUTED_PREDICATES:
            return alias
    return key
