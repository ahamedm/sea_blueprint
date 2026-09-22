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
| `agents.ontology` + `app.ontology_reference` |
  the LinkML schemas (TBox) | what concepts exist, and how do they relate? |

This module is the third: **the schema, not the data**. It parses `ontology/*.yaml`
and knows nothing about extracted graphs, nodes, assertions or confidence. It
imports nothing from `agents.knowledge` and nothing from `app`, so it can be used
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
from typing import Any, Dict, List, Optional, Tuple

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


class OntologyError(Exception):
    """The ontology could not be read."""


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
