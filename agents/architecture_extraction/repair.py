"""
Post-merge structural repair.

TWO DEFECTS, TWO REPAIRS
------------------------
Both are things a per-chunk pass cannot get right, and both are measured on
`test_data/arch/payment_platform_arch.md`.

1. **Containment.** Each pass sees ONE chunk. An element introduced in one chunk
   whose container is named in another cannot be related by the model: the
   container is simply not in the prompt. So `parent` comes back empty and C4 loses
   the hierarchy — four elements were unplaced, and `inv_containment_present`
   failed on all four while every other structural invariant passed. One of them,
   `Settlement and Reconciliation Container`, is named in the document only as
   "their own container"; the system it belongs to is three chunks earlier.
   `repair_containment` attaches it to the system under design.

2. **A style recorded as an element.** The model sometimes records the style twice
   — correctly in `architecture_styles`, and again as a Container that then
   collects the platform's technology, technique and quality edges. `Microservices`
   held 20 subject edges and 2 object edges. `merge_style_elements` removes the
   node and re-points its edges at the system under design, keeping the facts.

BOTH ARE DETERMINISTIC, BOUNDED AND RECORDED
--------------------------------------------
Every change emits a `Flag`, so a reviewer sees exactly what moved and why.
Nothing is silently rewritten, and the only edge ever dropped is a merged style
element's own `part_of`, which has no meaning once the node is gone.

WHEN THEY DECLINE
-----------------
With several plausible systems the graph offers no unambiguous anchor, so the
element is left as it is and the ordinary finding stands. Guessing which system a
floating container belongs to is not a repair, it is a new invention — and the
audit would then treat it as fact.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

from ..extraction.validators import Flag, ontology_enum
from ..knowledge_extraction.agent import ExtractedTriple

# Element types that must sit inside something. Mirrors `validators._CONTAINED_TYPES`
# deliberately: this module repairs exactly the defect that check reports.
CONTAINED_TYPES = frozenset({"Container", "DataStore", "Component", "CodeElement"})

# SoftwareSystem classes that name a domain-owned system. An enterprise platform
# (OpenShift, Splunk) is never the anchor for a business container, which is why
# `ENTERPRISE_TECHNOLOGY_PLATFORM` is absent here.
_DOMAIN_SYSTEM_CLASSES = frozenset({"BUSINESS_TECHNOLOGY_PLATFORM", "BUSINESS_APPLICATION"})

# An inferred parent is not a document claim, so it is recorded well below the
# confidence the extractor assigns to what the document actually says. The finding
# is the primary signal; this keeps triage honest if anyone sorts by confidence.
_INFERRED_CONFIDENCE = 0.5


def system_under_design(elements: Sequence[Dict[str, Any]]) -> str:
    """The anchor for an unplaced contained element, or "" when ambiguous.

    One domain-owned SoftwareSystem is the common case and is unambiguous. Failing
    that, one non-platform SoftwareSystem is also unambiguous. An enterprise
    technology platform is never the anchor: OpenShift is not where a business
    container lives, and inferring it would place the element in the wrong system.
    Anything else returns "" so the caller declines rather than guesses.
    """
    systems = [e for e in elements if e.get("element_type") == "SoftwareSystem"]

    domain = [e for e in systems if e.get("system_class") in _DOMAIN_SYSTEM_CLASSES]
    if len(domain) == 1:
        return str(domain[0].get("name") or "")

    ordinary = [e for e in systems
                if e.get("system_class") != "ENTERPRISE_TECHNOLOGY_PLATFORM"]
    if len(ordinary) == 1:
        return str(ordinary[0].get("name") or "")

    return ""


def _has_part_of(triples: Sequence[Any], child: str, parent: str) -> bool:
    for t in triples:
        if (t.predicate == "part_of"
                and str(t.subject).strip() == child
                and str(getattr(t, "object", "") or "").strip() == parent):
            return True
    return False


def repair_containment(
    elements: List[Dict[str, Any]],
    triples: List[Any],
) -> Tuple[List[Dict[str, Any]], List[Any], List[Flag]]:
    """Attach unplaced contained elements to the system under design.

    Returns `(elements, triples, flags)`. Both inputs are treated as read-only and
    the returned lists are new, so a caller cannot be surprised by a side effect it
    did not ask for. `triples` gains one `part_of` edge per repaired element, so the
    declared parent and the edge agree — which is what `check_containment` requires
    of a declaration it accepts.
    """
    elements = [dict(e) for e in elements]
    triples = list(triples)

    anchor = system_under_design(elements)
    if not anchor:
        return elements, triples, []

    flags: List[Flag] = []
    for element in elements:
        if element.get("element_type") not in CONTAINED_TYPES:
            continue
        if str(element.get("parent") or "").strip():
            continue

        name = str(element.get("name") or "")
        if not name or name.strip() == anchor.strip():
            continue

        element["parent"] = anchor
        if not _has_part_of(triples, name, anchor):
            triples.append(ExtractedTriple(
                subject=name, predicate="part_of", object=anchor,
                confidence=_INFERRED_CONFIDENCE,
                source_text="",
            ))
        flags.append(Flag(
            "containment_repaired", name,
            [f"no parent stated in the document; attached to the system under "
             f"design {anchor!r} rather than left unplaceable"],
            "part_of", anchor,
        ))

    return elements, triples, flags


def style_as_element(elements: Sequence[Dict[str, Any]]) -> List[Flag]:
    """Flag an architectural STYLE that was emitted as an element.

    `ArchitectureStyleName` is the ontology's vocabulary for styles, so an element
    whose name denotes one of its values is the coarse shape of the design filed as
    a structural node — the class of leak the harness already guards for techniques
    and technologies.

    FLAGGED, not removed. `merge_style_elements` handles the case where the graph
    has an unambiguous anchor to fold the element into; this catches what is left,
    including the case where it had to decline.
    """
    flags: List[Flag] = []
    for element in elements:
        name = str(element.get("name") or "")
        if not name:
            continue
        hit = _style_hit(name)
        if hit:
            flags.append(Flag(
                "style_as_element", name,
                [f"{name!r} denotes the {hit} architecture style, which belongs in "
                 f"`architecture_styles`, not in `elements`"],
            ))
    return flags


# Predicates whose OBJECT names a vocabulary value — a style, technology,
# technique, attribute or cross-graph reference — rather than a structural node.
# A style element's name is one of those values, so an object naming it must
# survive the merge instead of being re-pointed at the system. `follows_style` is
# the live case: `Microservices --follows_style--> Microservices` means "the
# platform follows this style", and re-pointing the object would assert that the
# platform follows itself.
_VOCABULARY_OBJECT_PREDICATES = frozenset({
    "follows_style", "conforms_to", "uses_technology", "applies_technique",
    "applies_pattern", "realizes_attribute", "realizes_quality_attribute",
    "realizes_quality_attributes", "satisfies_attribute",
    "satisfies_quality_attribute", "satisfies_quality_attributes",
    "mandated_by", "delivers_initiative", "authorised_by_initiative",
    "governed_by_rule", "governed_by_rules",
})


def merge_style_elements(
    elements: List[Dict[str, Any]],
    styles: List[Dict[str, Any]],
    triples: List[Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Any], List[Flag]]:
    """Fold a style emitted as an element into the system under design.

    The model sometimes records the style twice — once correctly in
    `architecture_styles`, and once as a Container that then collects the
    platform's technology, technique and quality edges. Measured on the payment
    architecture: `Microservices` held 20 subject edges and 2 object edges. It is
    not a deployable thing, but those edges are real facts about the system.

    So the element is REMOVED and its edges are RE-POINTED at the system under
    design, which is where they belonged. Nothing is discarded except the style
    element's own `part_of` edge, which has no meaning once the node is gone.
    Every merge is recorded as a finding.

    Declines without an unambiguous anchor, exactly like `repair_containment`.
    """
    elements = [dict(e) for e in elements]
    styles = [dict(s) for s in styles]
    triples = list(triples)

    anchor = system_under_design(elements)
    if not anchor:
        return elements, styles, triples, []

    doomed = {}
    for element in elements:
        name = str(element.get("name") or "")
        if not name or name.strip() == anchor.strip():
            continue
        if _style_hit(name):
            doomed[name] = _style_hit(name)
    if not doomed:
        return elements, styles, triples, []

    flags: List[Flag] = []
    known = {str(s.get("name") or "") for s in styles}
    for name, hit in doomed.items():
        moved = sum(1 for t in triples
                    if t.subject == name or getattr(t, "object", None) == name)
        if name not in known:
            styles.append({"name": name, "style": hit, "adopted_by": [anchor]})
            known.add(name)
        flags.append(Flag(
            "style_element_merged", name,
            [f"{name!r} is the {hit} architecture style, not an element — its "
             f"{moved} reference(s) were re-pointed at the system under design "
             f"{anchor!r}"],
            "follows_style", hit,
        ))

    out: List[Any] = []
    seen = set()
    for t in triples:
        subject, obj = t.subject, getattr(t, "object", None)
        changed = False

        if subject in doomed:
            if t.predicate == "part_of" and obj == anchor:
                # The style element's own containment: its parent is the anchor, so
                # merging would produce `PGP --part_of--> PGP`, which is not a fact.
                continue
            subject, changed = anchor, True

        if obj in doomed and t.predicate not in _VOCABULARY_OBJECT_PREDICATES:
            obj, changed = anchor, True

        if changed:
            t = t.model_copy(update={"subject": subject, "object": obj})

        key = (t.subject, t.predicate, t.object)
        if key in seen:
            continue
        seen.add(key)
        out.append(t)

    # A child of a merged element is a child of the thing it was merged into.
    for element in elements:
        if element.get("parent") in doomed:
            element["parent"] = anchor

    elements = [e for e in elements if str(e.get("name") or "") not in doomed]
    return elements, styles, out, flags


def _style_hit(name: str) -> str:
    """The ontology style a name denotes, or "".

    Matches the whole normalised name ("Microservices") or a single token of a
    longer one ("Stateless Modular Microservices"), because sources state styles
    both ways. Reads `ArchitectureStyleName` from the ontology rather than a
    hardcoded list, for the reason `validators` gives: a copied vocabulary
    diverges silently.
    """
    styles = ontology_enum("ArchitectureStyleName")
    if not styles:
        return ""
    normalised = _normalise(name)
    if normalised in styles:
        return normalised
    for token in normalised.split("_"):
        if token in styles:
            return token
    return ""


def _normalise(name: str) -> str:
    import re

    return re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")
