"""
Design-specific validators.

WHAT THESE ARE FOR. The shared validators (`agents/extraction/validators.py`) check
that the graph is well-formed: an element has a C4 type, a triple names a thing, a
value is a member of its enum. They do not and cannot check whether a DESIGN is
answerable to anything — a perfectly well-formed proposal can invent a container
nobody asked for, choose a pattern that does not exist, or write a quality scenario
with no number in it.

Each check below exists because the failure it catches is silent in the output. An
ungrounded element looks like every other element; an unresolved pattern name looks
like a resolved one until somebody tries to look it up; a scenario whose "response
measure" is a sentence looks like coverage.

Findings, never exceptions — the same posture as the shared validators. A design run
that produced something useful and flagged three problems is worth more than one
that refused to run.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from core.ontology import enum_name
from core.patterns import PatternCatalogue, canonical_pattern

from ..extraction.validators import Flag, as_record_dicts

# The five fields that make a scenario falsifiable. `artifact` is deliberately not
# required: which part of the system is affected is often implied by the stimulus,
# and demanding it produces filler rather than precision.
_REQUIRED_SCENARIO_FIELDS = (
    "stimulus_source",
    "stimulus",
    "environment",
    "response",
    "response_measure",
)

# A response measure with no digit cannot be checked. "Fast enough" is not a
# measure, and an unmeasurable scenario is worse than an absent one because it
# reads as coverage.
_DIGITS = set("0123456789")


def check_grounded_elements(
    elements: Sequence[Any],
    references: Sequence[Any],
) -> List[Flag]:
    """Every proposed element should answer something in REQ-G.

    An element that answers nothing may still be legitimate — a gateway, a
    scheduler, an observability sidecar — so this flags rather than fails. What it
    must not do is pass unnoticed: an invented container is indistinguishable from a
    required one in the graph, and only this list separates them.
    """
    grounded = {
        str(r.get("element") or "").strip().lower()
        for r in as_record_dicts(references)
        if str(r.get("element") or "").strip()
    }
    flags: List[Flag] = []
    for element in as_record_dicts(elements):
        name = str(element.get("name") or "").strip()
        if name and name.lower() not in grounded:
            flags.append(
                Flag("ungrounded", name, [
                    "no traceability link to a requirement, goal or capability"
                ])
            )
    return flags


def check_scenario_shape(scenarios: Sequence[Any]) -> List[Flag]:
    """A scenario missing an ATAM field is not yet a scenario."""
    flags: List[Flag] = []
    for scenario in as_record_dicts(scenarios):
        name = str(scenario.get("name") or "").strip()
        missing = [
            field for field in _REQUIRED_SCENARIO_FIELDS
            if not str(scenario.get(field) or "").strip()
        ]
        if missing:
            flags.append(Flag("scenario", name, [f"missing: {', '.join(missing)}"]))
            continue
        measure = str(scenario.get("response_measure") or "")
        if not any(ch in _DIGITS for ch in measure):
            flags.append(
                Flag("scenario", name, [
                    f"response_measure has no number, so it cannot be checked: {measure!r}"
                ])
            )
    return flags


def check_pattern_resolution(
    patterns: Sequence[Any],
    catalogue: PatternCatalogue,
) -> List[Flag]:
    """A pattern name the catalogue does not carry is reported, not guessed at."""
    if not catalogue.entries:
        return []
    flags: List[Flag] = []
    for pattern in as_record_dicts(patterns):
        name = str(pattern.get("name") or "").strip()
        if name and not canonical_pattern(name, catalogue).resolved:
            flags.append(
                Flag("pattern", name, [
                    "not in the architecture pattern catalogue — proposed as an "
                    "unresolved pattern for review"
                ])
            )
    return flags


def check_quality_linkage(
    techniques: Sequence[Any],
    patterns: Sequence[Any],
    catalogue: PatternCatalogue,
) -> List[Flag]:
    """A mechanism aimed at a quality family it cannot deliver.

    Only checked where the claim can be checked: a pattern that resolves to a
    catalogue entry, and names a `quality_category` that entry does not list. A
    technique is not checked here because a technique's category is the model's
    assertion about a mechanism, and the catalogue says nothing about it.
    """
    if not catalogue.entries:
        return []
    flags: List[Flag] = []
    for pattern in as_record_dicts(patterns):
        name = str(pattern.get("name") or "").strip()
        resolution = canonical_pattern(name, catalogue)
        if not resolution.resolved or not resolution.entry:
            continue
        declared = set(resolution.quality_attributes)
        claimed = {
            str(a).strip().upper()
            for a in (pattern.get("satisfies_attributes") or [])
            if str(a).strip()
        }
        if not claimed or not declared:
            continue
        # `satisfies_attributes` names attributes, the catalogue names
        # sub-characteristics; a claim that matches neither exactly nor as a
        # substring of a declared one is worth a look.
        unmatched = sorted(
            claim for claim in claimed
            if not any(claim in d or d in claim for d in declared)
        )
        if unmatched:
            flags.append(
                Flag("quality", name, [
                    f"claims {', '.join(unmatched)} but the catalogue entry lists "
                    f"{', '.join(sorted(declared)) or 'none'}"
                ])
            )
    return flags


def check_techniques_are_mechanisms(
    techniques: Sequence[Any],
    requirement_labels: Sequence[str],
) -> List[Flag]:
    """A technique named after the requirement it answers is not a mechanism.

    Measured on the live model, first real run: the techniques pass returned
    `Role-Based Access Control Enforcement`, `Horizontal Scaling`,
    `TLS 1.2+ Transport Security`, `Payment Gateway Fallback Strategy` — the
    requirements' own names — where the schema asks for the mechanism
    (`Redundancy / Replicas`, `Stateless Services`, `Connection Pooling`).

    Echoing the requirement is not a formatting slip. The technique layer exists to
    say HOW a quality attribute is delivered, and a technique that restates the
    requirement adds a node and no information: the census would then report the
    attribute as "has a realizing technique" on the strength of a renamed
    requirement. That is a false assurance about coverage, which is the one thing
    this layer must not produce.
    """
    wanted = {enum_name(label) for label in requirement_labels if label.strip()}
    if not wanted:
        return []
    flags: List[Flag] = []
    for technique in as_record_dicts(techniques):
        name = str(technique.get("name") or "").strip()
        if name and enum_name(name) in wanted:
            flags.append(
                Flag("mechanism", name, [
                    "named after a requirement; a technique has to name the "
                    "MECHANISM that delivers the attribute, not restate the "
                    "requirement"
                ])
            )
    return flags


def check_techniques_are_linked(techniques: Sequence[Any]) -> List[Flag]:
    """A technique linked to no quality concern answers nothing (YB-038).

    `check_techniques_are_mechanisms` catches the echo — a technique named after a
    requirement. This catches the other half of the same live failure: every
    quality field left empty. A technique with no `realizes_quality_attributes`,
    `quality_category` or `subcharacteristic` is a node with an incoming
    `applies_technique` edge and no outgoing claim, so `core.knowledge.quality`
    reports the attribute as having no mechanism. That reading is the honest one,
    which is exactly why the technique has to be flagged rather than accepted: it
    looks like coverage on the page.

    The two checks together ARE the operational definition of "fixed" — the harness
    invariant `inv_design_techniques_linked` asserts this same condition.
    """
    flags: List[Flag] = []
    for technique in as_record_dicts(techniques):
        name = str(technique.get("name") or "").strip()
        if not name:
            continue
        linked = any(
            technique.get(key)
            for key in ("realizes_quality_attributes", "quality_category",
                        "subcharacteristic")
        )
        if not linked:
            flags.append(
                Flag("unlinked", name, [
                    "names no quality attribute, characteristic or sub-characteristic; "
                    "this layer exists to link the design to the quality it delivers, so "
                    "an unlinked technique is an attribute the design does not answer"
                ])
            )
    return flags


def check_name_collisions(
    elements: Sequence[Any],
    existing_kinds: Dict[str, str],
) -> List[Flag]:
    """A proposed element whose label already belongs to a DIFFERENT kind.

    `ingest._resolve` reuses any node carrying a matching label, whatever its kind.
    A proposed Container named `Capacity` therefore does not create a container — it
    silently folds into the requirement node called `Capacity`, and the graph gains
    a fact about the wrong thing without anything failing. This is a known hazard
    (it was hit while building the quality census), and the design run is exactly
    where it is most likely, because a design names things after the requirements
    it answers.
    """
    flags: List[Flag] = []
    for element in as_record_dicts(elements):
        name = str(element.get("name") or "").strip()
        kind = str(element.get("element_type") or "").strip()
        if not name or not kind:
            continue
        existing = existing_kinds.get(name.lower())
        if existing and existing != kind:
            flags.append(
                Flag("collision", name, [
                    f"a {existing} node already uses this name; proposing it as "
                    f"{kind} would merge into that node rather than create one"
                ])
            )
    return flags
