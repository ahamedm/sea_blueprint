"""
The architecture pattern catalogue, and the canonical name for a pattern.

WHY A CATALOGUE AND NOT A PARAGRAPH IN A PROMPT
-----------------------------------------------
The PRD's capability D1 asks the Design Assistant to map requirements "against a
library of known design patterns". A library that exists only as prompt text is
not one: it cannot be validated against the ontology, an auditor cannot query it,
and it makes every pass prompt longer for content exactly one pass needs.

So the library is a file (`ontology/catalogues/architecture_patterns.yaml`,
externalised per AGENTS.md), this module reads and validates it, and a pattern the
model named is resolved onto a catalogue entry **deterministically** — the same
posture as `core.quality.canonical_quality_concern`. The prompt carries names and
categories only; mechanism, trade-offs and quality links come from here. That is
what keeps this from worsening YB-007 (prompt scaffolding 2.5:1 over the
document): the catalogue's substance never enters a prompt at all.

WHY IT IS NOT IN THE ONTOLOGY
-----------------------------
`architecture_base.yaml` declares the CLASS (`ArchitecturePattern`) and the
vocabularies it ranges over (`PatternCategory`, `ArchitectureStyleName`). This
file holds INSTANCES of that class. The two have different edit cadences — adding
a pattern is routine, changing a class is a schema version — and keeping them
apart leaves `load_ontology`'s fixed four-file chain and the domain-pack
discovery untouched.

WHAT IT DOES AND DOES NOT CLAIM
-------------------------------
`canonical_pattern` matches a NAME. It does not judge whether the design actually
implements the pattern: "the published pattern is present" is the auditor's
question (`ontology/README.md`), and answering it means checking the technique and
connection facts the design also asserted. A resolved name is a resolved
reference, not a verdict.

A name the catalogue does not know is reported UNRESOLVED rather than folded onto
a near neighbour. A pattern an organisation invented is a legitimate thing to
propose, and silently renaming it to the closest published pattern would destroy
exactly the information a reviewer needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from core.ontology import OntologyModel, enum_name

# Where the catalogue lives. A path rather than an inline default so a deployment
# can point at its own library without editing code — the same reason every other
# configuration in this repo is externalised.
DEFAULT_CATALOGUE_PATH = "ontology/catalogues/architecture_patterns.yaml"

# The ontology vocabularies a catalogue entry is checked against.
CATEGORY_ENUM = "PatternCategory"
QUALITY_ENUM = "QualitySubcharacteristic"
STYLE_ENUM = "ArchitectureStyleName"


@dataclass(frozen=True)
class PatternEntry:
    """One named, published solution."""

    name: str
    category: str = ""
    mechanism: str = ""
    trade_offs: Tuple[str, ...] = ()
    quality_attributes: Tuple[str, ...] = ()
    styles: Tuple[str, ...] = ()
    aliases: Tuple[str, ...] = ()
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "mechanism": self.mechanism,
            "trade_offs": list(self.trade_offs),
            "quality_attributes": list(self.quality_attributes),
            "styles": list(self.styles),
            "aliases": list(self.aliases),
            "description": self.description,
        }


@dataclass(frozen=True)
class PatternCatalogue:
    """Every pattern the Design Assistant may choose from."""

    entries: Tuple[PatternEntry, ...] = ()
    version: str = ""
    description: str = ""
    path: str = ""
    # Findings from load time, surfaced rather than raised — a catalogue that
    # quietly stops matching its own taxonomy is worse than one with a bad entry.
    findings: Tuple[str, ...] = ()

    @property
    def names(self) -> List[str]:
        return [entry.name for entry in self.entries]

    @property
    def by_key(self) -> Dict[str, PatternEntry]:
        """`CIRCUIT_BREAKER` -> entry, plus every alias's key."""
        index: Dict[str, PatternEntry] = {}
        for entry in self.entries:
            index.setdefault(enum_name(entry.name), entry)
            for alias in entry.aliases:
                index.setdefault(enum_name(alias), entry)
        return index

    def get(self, name: str) -> Optional[PatternEntry]:
        return self.by_key.get(enum_name(name))

    def categories(self) -> List[str]:
        return sorted({entry.category for entry in self.entries if entry.category})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "description": self.description,
            "path": self.path,
            "count": len(self.entries),
            "categories": self.categories(),
            "findings": list(self.findings),
            "patterns": [entry.to_dict() for entry in self.entries],
        }


@dataclass(frozen=True)
class PatternResolution:
    """What a proposed pattern name turned out to be.

    `resolved` is False only when nothing in the catalogue matched, so "the
    catalogue could not place this" is a state a caller can count rather than
    infer from an empty field.
    """

    name: str
    entry: Optional[PatternEntry] = None
    resolved: bool = False
    method: str = ""

    @property
    def category(self) -> str:
        return self.entry.category if self.entry else ""

    @property
    def mechanism(self) -> str:
        return self.entry.mechanism if self.entry else ""

    @property
    def trade_offs(self) -> Tuple[str, ...]:
        return self.entry.trade_offs if self.entry else ()

    @property
    def quality_attributes(self) -> Tuple[str, ...]:
        return self.entry.quality_attributes if self.entry else ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "resolved": self.resolved,
            "method": self.method,
            "matched": self.entry.name if self.entry else "",
            "category": self.category,
            "mechanism": self.mechanism,
            "trade_offs": list(self.trade_offs),
            "quality_attributes": list(self.quality_attributes),
        }


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()]


def _entry_from(raw: Dict[str, Any]) -> Optional[PatternEntry]:
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    return PatternEntry(
        name=name,
        category=str(raw.get("category") or "").strip().upper(),
        mechanism=str(raw.get("mechanism") or "").strip(),
        trade_offs=tuple(_as_list(raw.get("trade_offs"))),
        quality_attributes=tuple(a.upper() for a in _as_list(raw.get("quality_attributes"))),
        styles=tuple(s.upper() for s in _as_list(raw.get("styles"))),
        aliases=tuple(_as_list(raw.get("aliases"))),
        description=str(raw.get("description") or "").strip(),
    )


def load_pattern_catalogue(path: str | Path = DEFAULT_CATALOGUE_PATH) -> PatternCatalogue:
    """Read the catalogue. A missing file is a supported state, not a crash.

    Returns an empty catalogue rather than raising: an agent that cannot find a
    pattern library should still be able to propose a design, and reporting zero
    patterns is more honest than refusing to run. The caller decides whether an
    empty catalogue is fatal.
    """
    catalogue_path = Path(path)
    if not catalogue_path.is_file():
        return PatternCatalogue(path=str(catalogue_path), findings=[f"catalogue not found: {path}"])

    try:
        document = yaml.safe_load(catalogue_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return PatternCatalogue(path=str(catalogue_path), findings=[f"catalogue is not valid YAML: {exc}"])

    if not isinstance(document, dict):
        return PatternCatalogue(
            path=str(catalogue_path), findings=["catalogue root is not a mapping"]
        )

    entries: List[PatternEntry] = []
    seen: Dict[str, str] = {}
    findings: List[str] = []
    for raw in document.get("patterns") or []:
        if not isinstance(raw, dict):
            findings.append(f"skipped a non-mapping pattern entry: {raw!r}")
            continue
        entry = _entry_from(raw)
        if entry is None:
            findings.append("skipped a pattern entry with no name")
            continue
        key = enum_name(entry.name)
        if key in seen:
            findings.append(f"duplicate pattern name {entry.name!r} (also {seen[key]!r})")
            continue
        seen[key] = entry.name
        entries.append(entry)

    return PatternCatalogue(
        entries=tuple(entries),
        version=str(document.get("version") or ""),
        description=str(document.get("description") or "").strip(),
        path=str(catalogue_path),
        findings=tuple(findings),
    )


# Tokens that say nothing about WHICH pattern is meant. Stripped before matching,
# so "Bulkhead pattern" resolves without a fuzzy containment rule — and a fuzzy
# rule is not an option here, because containment cannot tell "Monolith" from
# "Modular Monolith", which are two different published patterns with opposite
# trade-offs. A short form that is not a name + noise is DECLARED as an alias.
_NOISE_TOKENS = frozenset({"PATTERN", "PATTERNS", "APPROACH", "STYLE"})


def canonical_pattern(name: str, catalogue: PatternCatalogue) -> PatternResolution:
    """Resolve a proposed pattern name onto a catalogue entry.

    Strict on purpose. The order is:

    1. the entry's own enum spelling (`CIRCUIT_BREAKER`, `Circuit Breaker`)
    2. a declared alias (`breaker`, `outbox`)
    3. the same, with noise words removed (`Bulkhead pattern`)
    4. unresolved — kept under its own name, never folded onto a neighbour

    Step 4 is a real outcome, not a failure. A pattern an organisation invented,
    or a variant this catalogue does not carry, is legitimate to propose; renaming
    it to the closest published pattern would destroy the one piece of information
    a reviewer needs to judge it.
    """
    raw = (name or "").strip()
    if not raw or not catalogue.entries:
        return PatternResolution(name=raw, method="unresolved")

    key = enum_name(raw)
    index = catalogue.by_key
    entry = index.get(key)
    if entry is not None:
        method = "name" if key == enum_name(entry.name) else "alias"
        return PatternResolution(name=raw, entry=entry, resolved=True, method=method)

    stripped = "_".join(t for t in key.split("_") if t and t not in _NOISE_TOKENS)
    if stripped and stripped != key:
        entry = index.get(stripped)
        if entry is not None:
            return PatternResolution(
                name=raw, entry=entry, resolved=True, method="normalised"
            )

    return PatternResolution(name=raw, method="unresolved")


def validate_pattern_catalogue(
    catalogue: PatternCatalogue, model: OntologyModel
) -> List[str]:
    """Check every catalogue reference against the ontology.

    Findings, not exceptions — the `validate_quality_model` posture. A catalogue
    entry naming a category the schema does not declare would otherwise reach a
    prompt as a value the model is told to use and the graph cannot hold.
    """
    findings: List[str] = list(catalogue.findings)

    def vocabulary(enum: str) -> set:
        spec = model.enums.get(enum)
        return set(spec.values) if spec else set()

    categories = vocabulary(CATEGORY_ENUM)
    qualities = vocabulary(QUALITY_ENUM)
    styles = vocabulary(STYLE_ENUM)

    for entry in catalogue.entries:
        if not entry.category:
            findings.append(f"{entry.name}: no category")
        elif categories and entry.category not in categories:
            findings.append(f"{entry.name}: unknown category {entry.category!r}")
        if not entry.mechanism:
            # The mechanism is what a quality scenario's response measure gets
            # checked against, so a pattern without one cannot be assessed.
            findings.append(f"{entry.name}: no mechanism")
        for attribute in entry.quality_attributes:
            if qualities and attribute not in qualities:
                findings.append(f"{entry.name}: unknown quality attribute {attribute!r}")
        for style in entry.styles:
            if styles and style not in styles:
                findings.append(f"{entry.name}: unknown architecture style {style!r}")
    return findings


def pattern_prompt_context(catalogue: PatternCatalogue) -> str:
    """The catalogue as the model needs it: names and categories, nothing more.

    Deliberately not the mechanisms or trade-offs. Those are resolved
    deterministically from the name (see the module docstring), and shipping them
    in the prompt would repeat several thousand characters across every design
    pass for information the model is not being asked to produce.
    """
    if not catalogue.entries:
        return ""
    lines = [
        "\n### Architecture pattern catalogue — choose solutions from this list",
        "Name a pattern with its catalogue name where one fits. A pattern the list does "
        "not contain may still be proposed: give it its own name and it is recorded as "
        "unresolved for a reviewer rather than matched to a near neighbour.",
    ]
    for entry in catalogue.entries:
        category = f" ({entry.category})" if entry.category else ""
        lines.append(f"- {entry.name}{category}")
    return "\n".join(lines) + "\n"
