"""
The C4 specification view — emit notation from the architecture graph (YB-025).

WHY THIS IS A VIEWPOINT AND NOT ANOTHER MAP. `/map` is a force layout of the whole
knowledge graph: good for exploring, useless as a *specification*, because a
simulation has no canonical layout — the same graph draws differently between loads,
arrow routing is incidental, and there is no stable artefact a human can diff or
attach to a design document. This module reduces the same graph to a **C4 model** and
then to **text**, deterministically: same graph, same text.

WHY TEXT FIRST. The notation is the deliverable and the diagram is a convenience. Text
is diffable in `/changes/diff`, reviewable, stable across runs, and it lets an external
tool answer "is this graph structurally sane?", which is an independent check on
extraction quality.

WHAT IT REFUSES TO DO. It does not invent structure to make the notation valid. A graph
that is not a complete C4 model produces notation with holes, and every hole is
reported in `gaps`: structural elements with no C4 level, elements with no boundary to
sit in, connections whose endpoints are not in the model, unresolved references, and
the completeness of the runs that produced the graph. A diagram is more persuasive than
a table, so an unstated hole matters more here rather than less.

WHAT ROLLS UP. A diagram is drawn at one level, so a relationship whose endpoints sit
below that level is drawn between their ancestors at the drawn level — and counted —
rather than dropped or pointed at something invisible.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from app.projections import literal_facts, node_records

__all__ = [
    "LEVELS",
    "LEVEL_TITLES",
    "c4_model",
    "to_structurizr",
    "to_c4_plantuml",
    "to_mermaid",
    "to_payload",
    "filename_stem",
    "roll_up",
]

#: Which level a C4 element must be *inside*. This is the one well-formedness rule
#: C4 actually states, and it is the rule a graph drawn from prose gets wrong: a
#: component belongs in a container, not in the system, and Structurizr refuses the
#: nesting outright. Checking it here is what turns "the diagram looks odd" into a
#: count with names attached.
_EXPECTED_PARENT_LEVEL = {
    "container": "context",
    "component": "container",
    "code": "component",
}

#: C4's levels, outermost first. `code` is included because the graph classifies
#: CodeElements; a view that stopped at components would lose 23 of them here.
LEVELS: Tuple[str, ...] = ("context", "container", "component", "code")

LEVEL_TITLES: Dict[str, str] = {
    "context": "System context",
    "container": "Containers",
    "component": "Components",
    "code": "Code",
}

#: The level an element states about itself, as a literal fact. Preferred over the
#: node kind, because it is what the extraction recorded.
_LEVEL_FROM_FACT = {
    "CONTEXT": "context",
    "CONTAINER": "container",
    "COMPONENT": "component",
    "CODE": "code",
}

#: The fallback when nothing was stated. Measured on the live graph: the system under
#: design was classified `Platform` and the storefronts `Application` — so without
#: these the document's own system is reported as unrepresentable and the diagram is
#: drawn around it rather than from it.
_LEVEL_FROM_KIND = {
    "SoftwareSystem": "context",
    "System": "context",
    "Platform": "context",
    "Application": "context",
    "ExternalSystem": "context",
    "Person": "context",
    "Actor": "context",
    "Container": "container",
    "DataStore": "container",
    "Component": "component",
    "CodeElement": "code",
}

#: Kinds that ARE architecture elements, and so must be placed somewhere or reported.
#: Everything else in the graph — requirements, techniques, conventions, styles,
#: patterns — is real knowledge but not a C4 element: counted, not flagged, because a
#: gap list is only useful if everything in it is a gap.
_STRUCTURAL_KINDS = frozenset({
    "SoftwareSystem", "System", "Platform", "Application", "ExternalSystem",
    "Person", "Actor", "Container", "Component", "DataStore", "CodeElement",
    "DeploymentNode",
})

_CONTAINMENT_PREDICATE = "part_of"
_CONNECTION_PREDICATES = ("connects_to",)
_TECHNOLOGY_PREDICATE = "uses_technology"

#: Kinds that can never be an architecture element, so a structural element carrying
#: one of their labels is the same thing extracted twice under two kinds. Narrower
#: than "everything not structural" on purpose: `TechnologyStack` is the clearest
#: counter-example — PostgreSQL is legitimately both a technology and a container, and
#: flagging that would make the report cry wolf.
_NEVER_AN_ELEMENT_KINDS = frozenset({
    "DesignTechnique", "EngineeringConvention", "ArchitectureStyle", "Standard",
    "QualityAttribute", "Concept", "BusinessRule", "BusinessProcess",
})

#: Element→element facts that are real but are not C4 relationships: deployment,
#: technology adoption, requirement linkage. Counted, so "the diagram has no arrow for
#: it" is a stated exclusion rather than an omission.
_NON_C4_RELATIONSHIPS = (
    "hosted_on", "deploys_on", "depends_on_components", "implements_interfaces",
    "has_functional_requirement", "has_non_functional_requirement",
    "satisfies_attribute", "realizes_attribute", "follows_style", "conforms_to",
    "applies_technique", "binds_to_application", "binds_to_platform", "binds_to_system",
    "authorised_by_initiative", "delivers_initiative", "refines", "depends_on",
)


def _slug(text: str, fallback: str = "element") -> str:
    """A deterministic identifier an external notation will accept.

    Lowercase alphanumeric and underscores, not starting with a digit — the shape
    Structurizr and PlantUML both accept. Deliberately NOT a hash: a human reads these
    in the emitted text, and `payment_orchestrator` is reviewable where `e7f3a1` is not.
    """
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", str(text or "").strip().lower()).strip("_")
    if not slug:
        slug = fallback
    if slug[0].isdigit():
        slug = f"n_{slug}"
    return slug


def _identifiers(elements: List[Dict[str, Any]]) -> Dict[str, str]:
    """One unique identifier per element, stable across runs.

    A collision is resolved with a suffix taken from the node id, not from iteration
    order, so the same graph always emits the same identifiers — which is what makes
    the text diffable at all.
    """
    out: Dict[str, str] = {}
    taken: Dict[str, int] = {}
    for element in elements:
        base = _slug(element["label"], "element")
        candidate = base
        if candidate in out.values():
            taken[base] = taken.get(base, 0) + 1
            candidate = f"{base}_{_slug(element['id'][-6:], 'x')}"
        while candidate in out.values():
            taken[base] = taken.get(base, 0) + 1
            candidate = f"{base}_{taken[base]}"
        out[element["id"]] = candidate
    return out


def _ancestors(elements: Dict[str, Dict[str, Any]], node_id: str) -> List[str]:
    """The node and its `part_of` ancestors, cycle-safe."""
    chain, cursor, seen = [], node_id, set()
    while cursor and cursor in elements and cursor not in seen:
        seen.add(cursor)
        chain.append(cursor)
        cursor = elements[cursor]["parent"]
    return chain


def _descendants(elements: Dict[str, Dict[str, Any]], root: str) -> List[str]:
    return [nid for nid in elements if root in _ancestors(elements, nid)[1:]]


def c4_model(graph: Any) -> Dict[str, Any]:
    """Reduce a knowledge graph to a C4 model, with its gaps stated.

    Plain dicts, like every other projection here, so a template or a CLI can render
    it without importing the model layer.
    """
    records = {n["id"]: n for n in node_records(graph)}
    facts = literal_facts(graph, list(records))

    elements: Dict[str, Dict[str, Any]] = {}
    gaps: List[Dict[str, Any]] = []
    excluded: Dict[str, int] = {}
    inferred = 0

    for node_id, record in records.items():
        if record["kind"] not in _STRUCTURAL_KINDS:
            excluded[record["kind"]] = excluded.get(record["kind"], 0) + 1
            continue

        node_facts = facts.get(node_id, {})
        level = _LEVEL_FROM_FACT.get(str(node_facts.get("c4_level") or "").upper())
        source = "c4_level" if level else ""
        if level is None:
            level = _LEVEL_FROM_KIND.get(record["kind"])
            source = "kind" if level else ""
        if level is None:
            # Deliberately has no C4 level: deployment is a different view, not L1-L4
            # (YB-025's open question, answered by reporting rather than guessing).
            gaps.append({
                "kind": "unlevelled",
                "id": node_id,
                "label": record["label"],
                "node_kind": record["kind"],
                "detail": (f"{record['kind']} sits at no C4 level. Deployment and "
                           f"platform constructs are not L1-L4; reporting it beats "
                           f"placing it somewhere it does not belong."),
            })
            continue
        if source == "kind":
            inferred += 1

        elements[node_id] = {
            **record,
            "facts": node_facts,
            "level": level,
            "level_source": source,
            "description": node_facts.get("description", ""),
            "element_type": node_facts.get("element_type", ""),
            "technology": [],
            "parent": None,
        }

    for assertion in graph.active():
        if assertion.predicate == _CONTAINMENT_PREDICATE:
            if assertion.subject in elements and assertion.object in elements:
                elements[assertion.subject]["parent"] = assertion.object
        elif assertion.predicate == _TECHNOLOGY_PREDICATE:
            holder = elements.get(assertion.subject)
            target = records.get(assertion.object or "")
            if holder is not None and target is not None:
                holder["technology"].append(target["label"])
    for element in elements.values():
        element["technology"].sort()

    # ---- the system under design ----
    #
    # The element that contains the most. Requiring a parentless SoftwareSystem looked
    # right and found nothing: systems carry `part_of` links to each other, and "no
    # parent" is a property of a tree, not of a graph. Any structural element that
    # contains others is a candidate, so a document that classifies its system as
    # `Platform` still yields a diagram instead of an empty workspace.
    containers = [e for e in elements.values() if _descendants(elements, e["id"])]
    if not containers:
        containers = [e for e in elements.values()
                      if e["level"] == "context" and e["kind"] != "ExternalSystem"]
    system = max(containers, key=lambda e: (len(_descendants(elements, e["id"])),
                                            e["label"]), default=None)

    # ---- boundaries ----
    for element in sorted(elements.values(), key=lambda e: e["label"]):
        if element["parent"] in elements:
            continue
        if system is not None and element["id"] != system["id"]:
            gaps.append({
                "kind": "unplaced",
                "id": element["id"],
                "label": element["label"],
                "detail": (f"{element['label']} is a {element['level']} with no "
                           f"`part_of` parent, so the diagram has no boundary to draw "
                           f"it inside."),
            })

    # ---- relationships ----
    #
    # A Connection node is the labelled form: it carries protocol and style, which is
    # what a C4 arrow is annotated with. The `connects_to` triple is the unlabelled
    # form, kept as a fallback so a graph written before Connection nodes existed still
    # draws its arrows.
    pairs: Dict[Tuple[str, str], Dict[str, Any]] = {}
    dangling: List[str] = []
    # A `Connection` node is a relationship, not an element: it carries the protocol
    # and style a C4 arrow is annotated with, and it has no level and belongs in no
    # boundary. So it is read from the records rather than from `elements`, and it is
    # never drawn as a box.
    for record in sorted(records.values(), key=lambda r: r["label"]):
        if record["kind"] != "Connection":
            continue
        node_facts = facts.get(record["id"], {})
        endpoints = _connection_endpoints(graph, record["id"], elements)
        if endpoints is None:
            gaps.append({
                "kind": "dangling-connection",
                "id": record["id"],
                "label": record["label"],
                "detail": ("a connection names an endpoint that is not a C4 element, "
                           "so there is nothing to draw it between"),
            })
            dangling.append(record["label"])
            continue
        source_id, target_id = endpoints
        pairs[(source_id, target_id)] = {
            "source": source_id,
            "target": target_id,
            "label": str(node_facts.get("description_text")
                         or node_facts.get("protocol") or "connects"),
            "protocol": node_facts.get("protocol", ""),
            "style": node_facts.get("style", ""),
            "via": "connection",
        }

    for assertion in graph.active():
        if assertion.predicate not in _CONNECTION_PREDICATES:
            continue
        if assertion.subject not in elements or assertion.object not in elements:
            dangling.append(
                f'{_endpoint_label(records, assertion.subject)} → '
                f'{_endpoint_label(records, assertion.object)}'
            )
            continue
        pairs.setdefault((assertion.subject, assertion.object), {
            "source": assertion.subject,
            "target": assertion.object,
            "label": assertion.predicate.replace("_", " "),
            "protocol": "",
            "style": "",
            "via": "edge",
        })

    if inferred:
        gaps.append({
            "kind": "inferred-level",
            "label": f"{inferred} element(s) with no stated C4 level",
            "detail": ("their level was inferred from the node kind. The diagram is "
                       "drawn from that inference, so a wrong classification shows up "
                       "as an element at the wrong level."),
        })

    unresolved = list(graph.unresolved_references())
    if unresolved:
        gaps.append({
            "kind": "unresolved-references",
            "label": f"{len(unresolved)} unresolved reference(s)",
            "detail": ("named in a fact but never declared, so they appear in no "
                       "diagram — the graph's own record of what it could not place"),
        })

    runs = getattr(graph, "runs", {}) or {}
    partial = [r for r in runs.values() if getattr(r, "completeness", "") != "COMPLETE"]
    if runs:
        gaps.append({
            "kind": "run-completeness",
            "label": f"{len(partial)} of {len(runs)} run(s) not COMPLETE",
            "detail": ("a diagram drawn from an incomplete run is incomplete, and says "
                       "so here rather than looking finished"),
        })

    non_c4 = sum(1 for a in graph.active()
                 if a.predicate in _NON_C4_RELATIONSHIPS
                 and a.subject in elements and a.object in elements)

    # The same thing drawn twice: a label that is both a structural element and a
    # non-architectural concept. Only visible once something decides which kinds are
    # elements — which is what this view does, so it reports it here rather than
    # leaving the duplicate to be found by eye at whichever level it landed on.
    concepts_by_label = {
        n["label"].strip().lower(): n["kind"] for n in records.values()
        if n["kind"] in _NEVER_AN_ELEMENT_KINDS
    }
    duplicates = sorted(
        e["label"] for e in elements.values()
        if e["label"].strip().lower() in concepts_by_label
    )
    if duplicates:
        gaps.append({
            "kind": "duplicate-of-concept",
            "label": f"{len(duplicates)} element(s) extracted twice",
            "detail": ("the same label is also a non-architectural concept, so it is "
                       "drawn as an element here and excluded there: "
                       + ", ".join(duplicates[:EXAMPLE_CAP])
                       + (", …" if len(duplicates) > EXAMPLE_CAP else "")),
        })

    ordered = sorted(elements.values(),
                     key=lambda e: (LEVELS.index(e["level"]), e["label"]))
    by_level: Dict[str, List[Dict[str, Any]]] = {level: [] for level in LEVELS}
    for element in ordered:
        by_level[element["level"]].append(element)
    # Resolved once here rather than in the template: a page that walks the element
    # list once per row is quadratic, and a large graph makes that visible.
    for element in ordered:
        parent = elements.get(element["parent"])
        element["parent_label"] = parent["label"] if parent else ""

    relationships = [pairs[pair] for pair in sorted(pairs)]
    checks = _well_formed(elements, relationships, dangling, system)
    return {
        "system": system,
        "elements": ordered,
        "by_level": by_level,
        "relationships": relationships,
        "gaps": gaps,
        "checks": checks,
        "counts": {
            "elements": len(elements),
            "relationships": len(pairs),
            "excluded_non_c4_relationships": non_c4,
            "excluded_kinds": dict(sorted(excluded.items())),
            "excluded_count": sum(excluded.values()),
            "gaps": len(gaps),
            "checks": len(checks),
            "checks_failed": sum(1 for c in checks if not c["holds"]),
        },
        "identities": _identifiers(ordered),
    }


def _connection_endpoints(
    graph: Any, connection_id: str, elements: Dict[str, Dict[str, Any]]
) -> Optional[Tuple[str, str]]:
    source = target = None
    for assertion in graph.active():
        if assertion.subject != connection_id:
            continue
        if assertion.predicate == "source":
            source = assertion.object
        elif assertion.predicate == "target":
            target = assertion.object
    if source in elements and target in elements:
        return source, target
    return None


def _endpoint_label(records: Dict[str, Dict[str, Any]], node_id: Optional[str]) -> str:
    """A readable name for an endpoint, whether or not it resolved to a node.

    An unresolved endpoint is shown as the reference text the graph holds, because
    that text is the only thing that identifies it — printing the raw node id would
    make the report unactionable.
    """
    if not node_id:
        return "(unstated)"
    record = records.get(node_id)
    return record["label"] if record else str(node_id)


def roll_up(
    model: Dict[str, Any], level: str
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int, List[str]]:
    """One level's elements, and the relationships between them.

    A relationship below the drawn level is re-pointed at the ancestors that ARE drawn
    and counted, so the coupling is not lost when a diagram is drawn higher up. One
    whose two ends collapse to the same ancestor is dropped — that is coupling inside a
    single box, which the box's own diagram shows.

    A relationship the level cannot draw *at all* is returned separately rather than
    skipped. Discovering that an arrow had vanished — an external system is a context
    element, so a container diagram silently lost every call to one — is exactly the
    class of bug this view exists to make impossible, and it would have been committed
    silently.

    Returns `(elements, relationships, rolled_up_count, undrawable)`.
    """
    all_elements = {e["id"]: e for e in model["elements"]}
    drawn: Dict[str, Dict[str, Any]] = {e["id"]: e for e in model["by_level"][level]}
    relationships: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    rolled = 0
    undrawable: List[str] = []
    for relationship in model["relationships"]:
        source = _drawable_at(all_elements, relationship["source"], level)
        target = _drawable_at(all_elements, relationship["target"], level)
        if source is None or target is None:
            undrawable.append(
                f'{_label_of(all_elements, relationship["source"])} → '
                f'{_label_of(all_elements, relationship["target"])}'
            )
            continue
        if source == target:
            continue
        if source != relationship["source"] or target != relationship["target"]:
            rolled += 1
        # An external system is drawn at every level, so it joins the drawn set here
        # rather than being filtered out with the rest of its level.
        drawn.setdefault(source, all_elements[source])
        drawn.setdefault(target, all_elements[target])
        relationships.setdefault(
            (source, target, relationship["label"]),
            {**relationship, "source": source, "target": target},
        )
    ordered = sorted(drawn.values(),
                     key=lambda e: (LEVELS.index(e["level"]), e["label"]))
    return (ordered, [relationships[key] for key in sorted(relationships)], rolled,
            undrawable)


def _label_of(elements: Dict[str, Dict[str, Any]], node_id: str) -> str:
    element = elements.get(node_id)
    return element["label"] if element else str(node_id)


def _drawable_at(
    elements: Dict[str, Dict[str, Any]], node_id: str, level: str
) -> Optional[str]:
    """What to draw for `node_id` in a diagram at `level`, if anything.

    Its own ancestor at that level. Failing that, an external system — C4 draws
    external systems in every diagram, because the point of a boundary is what it
    exchanges with the outside. Failing that, nothing, and the caller reports it.
    """
    ancestors = _ancestors(elements, node_id)
    for ancestor in ancestors:
        if elements[ancestor]["level"] == level:
            return ancestor
    for ancestor in ancestors:
        if elements[ancestor]["kind"] == "ExternalSystem":
            return ancestor
    return None


# ============================================================================
# Notation
# ============================================================================


def _roots(model: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Elements nothing claims as a child — cycle-safe, and never empty.

    A root is an element whose `part_of` names nothing in the model. The inverse — the
    elements that are *parents* — is a different set, and conflating the two emitted
    every leaf at the top level and every boundary with no contents.

    `part_of` can contain a cycle, and then no element is a root at all. Something
    still has to be drawn, so the system is the fallback; anything a cycle hid is
    emitted afterwards by the caller's second pass.
    """
    elements = {e["id"]: e for e in model["elements"]}
    roots = [e for e in model["elements"] if e["parent"] not in elements]
    if roots:
        return roots
    system = model["system"]
    return [system] if system is not None else []


def to_structurizr(model: Dict[str, Any]) -> str:
    """Structurizr DSL — the most structured C4 source, and the cleanest to emit.

    Emitted first because it is the notation that can be read back (YB-006/YB-012),
    which is what would make the round-trip possible rather than one-way.

    The header comment carries the failed well-formedness checks. Structurizr refuses
    a nesting that C4 does not allow, and the alternative to warning here is a parse
    error in someone else's tool with no explanation attached.
    """
    ids = model["identities"]
    system = model["system"]
    title = system["label"] if system else "Architecture"
    lines = [f'workspace "{_escape(title)}" {{', "    model {"]
    emitted: set = set()

    for check in model.get("checks", []):
        if check["holds"]:
            continue
        lines.append(f'        // WARNING {check["violations"]} × {check["title"]}')
        if check["examples"]:
            lines.append(f'        //   e.g. {_escape("; ".join(check["examples"]))}')

    def emit(element: Dict[str, Any], indent: str) -> None:
        if element["id"] in emitted:
            return
        emitted.add(element["id"])
        children = sorted((e for e in model["elements"] if e["parent"] == element["id"]),
                          key=lambda e: e["label"])
        keyword = {"context": "softwareSystem", "container": "container",
                   "component": "component", "code": "component"}[element["level"]]
        desc = _escape(element["description"] or element["kind"])
        tech = (f' "{_escape(", ".join(element["technology"]))}"'
                if element["technology"] else "")
        tags = ' { tags "External" }' if element["kind"] == "ExternalSystem" else ""
        head = (f'{indent}{ids[element["id"]]} = {keyword} '
                f'"{_escape(element["label"])}" "{desc}"{tech}{tags}')
        if children:
            lines.append(head + " {")
            for child in children:
                emit(child, indent + "    ")
            lines.append(indent + "}")
        else:
            lines.append(head)

    for element in sorted(_roots(model), key=lambda e: e["label"]):
        emit(element, "        ")
    for element in sorted(model["elements"], key=lambda e: e["label"]):
        if element["id"] not in emitted:
            emit(element, "        ")

    for relationship in sorted(model["relationships"],
                               key=lambda r: (r["source"], r["target"], r["label"])):
        src, dst = ids.get(relationship["source"]), ids.get(relationship["target"])
        if not src or not dst:
            continue
        tech = (f' "{_escape(relationship["protocol"])}"'
                if relationship["protocol"] else "")
        lines.append(f'        {src} -> {dst} "{_escape(relationship["label"])}"{tech}')

    lines += ["    }", "", "    views {"]
    if system is not None:
        root = ids[system["id"]]
        lines += [
            f'        systemContext {root} "SystemContext" {{',
            "            include *", "            autolayout lr", "        }",
            f'        container {root} "Containers" {{',
            "            include *", "            autolayout lr", "        }",
        ]
        for element in model["by_level"]["container"]:
            if not any(e["parent"] == element["id"] for e in model["elements"]):
                continue
            lines += [
                f'        component {ids[element["id"]]} "Components" {{',
                "            include *", "            autolayout lr", "        }",
            ]
    lines += ["    }", "}"]
    return "\n".join(lines) + "\n"


#: C4-PlantUML's macros take (alias, label, technology-or-description).
_PLANTUML_MACRO = {
    "context": "System",
    "container": "Container",
    "component": "Component",
    "code": "Component",
}


def to_c4_plantuml(model: Dict[str, Any]) -> str:
    """C4-PlantUML text, for copy/paste into the tooling most teams already have.

    The `!include` lines are the standard ones and resolve in the renderer, not here:
    this text is an artefact for a person, not something the app renders.
    """
    ids = model["identities"]
    lines = [
        "@startuml",
        "!include <C4/C4_Container>",
        "!include <C4/C4_Component>",
        "",
        f'title {_escape((model["system"] or {}).get("label", "Architecture"))}',
        "",
    ]
    emitted: set = set()

    def emit(element: Dict[str, Any], indent: str) -> None:
        if element["id"] in emitted:
            return
        emitted.add(element["id"])
        children = sorted((e for e in model["elements"] if e["parent"] == element["id"]),
                          key=lambda e: e["label"])
        if element["kind"] == "ExternalSystem":
            macro = "System_Ext"
        elif element["kind"] == "DataStore":
            macro = "ContainerDb"
        else:
            macro = _PLANTUML_MACRO[element["level"]]
        text = _escape(", ".join(element["technology"])
                       or element["description"] or element["kind"])
        lines.append(f'{indent}{macro}({ids[element["id"]]}, '
                     f'"{_escape(element["label"])}", "{text}")')
        if children:
            lines.append(f'{indent}Container_Boundary({ids[element["id"]]}_b, '
                         f'"{_escape(element["label"])}") {{')
            for child in children:
                emit(child, indent + "  ")
            lines.append(f"{indent}}}")

    for element in sorted(_roots(model), key=lambda e: e["label"]):
        emit(element, "")
    for element in sorted(model["elements"], key=lambda e: e["label"]):
        if element["id"] not in emitted:
            emit(element, "")

    lines.append("")
    for relationship in sorted(model["relationships"],
                               key=lambda r: (r["source"], r["target"], r["label"])):
        src, dst = ids.get(relationship["source"]), ids.get(relationship["target"])
        if not src or not dst:
            continue
        tech = (f', "{_escape(relationship["protocol"])}"'
                if relationship["protocol"] else "")
        lines.append(f'Rel({src}, {dst}, "{_escape(relationship["label"])}"{tech})')
    lines.append("@enduml")
    return "\n".join(lines) + "\n"


def _mermaid_label(text: Any) -> str:
    """`_escape`, plus the one character Mermaid reads even inside a quoted label.

    A pipe delimits an edge label, so `-->|"a | b"|` ends the label early and the rest
    of the line becomes syntax. Structurizr and PlantUML have no such character, which
    is why this is not folded into `_escape` and mangling their output too.
    """
    return _escape(text).replace("|", "/")


def to_mermaid(model: Dict[str, Any], level: str = "container") -> str:
    """Mermaid text for one level, which the page renders client-side.

    `flowchart` rather than Mermaid's experimental C4 diagrams: the syntax is stable
    and the output deterministic, while the notation that has to be *correct* is the
    Structurizr DSL above. The system is drawn as a subgraph, because a container
    diagram without its boundary is a list of boxes.
    """
    elements, relationships, rolled, undrawable = roll_up(model, level)
    ids = {e["id"]: _slug(e["label"], "element") for e in elements}
    by_id = {e["id"]: e for e in model["elements"]}
    system = model["system"]

    inside, outside = [], []
    for element in sorted(elements, key=lambda e: e["label"]):
        anchored = system is not None and system["id"] in _ancestors(by_id, element["id"])
        (inside if anchored else outside).append(element)

    lines = ["flowchart TB"]
    if rolled:
        lines.append(f"    %% {rolled} relationship(s) rolled up from a lower level")
    if undrawable:
        lines.append(f"    %% {len(undrawable)} relationship(s) have no endpoint at "
                     f"this level: {'; '.join(undrawable)}")

    def node(element: Dict[str, Any], indent: str) -> None:
        shape = ("[/", "/]") if element["level"] == "code" else ("[", "]")
        lines.append(f'{indent}{ids[element["id"]]}{shape[0]}"'
                     f'{_mermaid_label(element["label"])}"{shape[1]}')

    if inside and system is not None:
        lines.append(f'    subgraph {_slug(system["label"])}_sys["'
                     f'{_mermaid_label(system["label"])}"]')
        lines.append("        direction TB")
        for element in inside:
            node(element, "        ")
        lines.append("    end")
    for element in outside:
        node(element, "    ")

    for relationship in sorted(relationships,
                               key=lambda r: (r["source"], r["target"], r["label"])):
        src, dst = ids.get(relationship["source"]), ids.get(relationship["target"])
        if not src or not dst:
            continue
        lines.append(f'    {src} -->|"{_mermaid_label(relationship["label"])}"| {dst}')
    return "\n".join(lines) + "\n"


def _escape(text: Any) -> str:
    """One line, no quotes or backslashes — the three things that break a notation."""
    return (str(text or "").replace("\\", "/").replace('"', "'")
            .replace("\n", " ").strip())


def filename_stem(text: Any) -> str:
    """A filesystem-safe stem for a downloaded notation file."""
    stem = re.sub(r"[^0-9a-zA-Z]+", "-", str(text or "").strip().lower()).strip("-")
    return stem or "architecture"


def to_payload(model: Dict[str, Any], level: str = "container") -> Dict[str, Any]:
    """The model as JSON, plus the notation for one level.

    Everything the page shows, so the page's own view and an API consumer's view
    cannot drift — and so the checks can be asserted against without parsing HTML.
    """
    if level not in LEVELS:
        level = "container"
    elements, relationships, rolled_up, undrawable = roll_up(model, level)
    return {
        "system": model["system"],
        "counts": model["counts"],
        "level": level,
        "levels": list(LEVELS),
        "elements": [
            {
                "id": e["id"],
                "label": e["label"],
                "kind": e["kind"],
                "level": e["level"],
                "level_source": e["level_source"],
                "description": e["description"],
                "technology": e["technology"],
                "parent": model["identities"].get(e["parent"] or ""),
            }
            for e in model["elements"]
        ],
        "relationships": [
            {
                "source": model["identities"].get(r["source"]),
                "target": model["identities"].get(r["target"]),
                "label": r["label"],
                "protocol": r["protocol"],
                "style": r["style"],
                "via": r["via"],
            }
            for r in model["relationships"]
        ],
        "drawn": {
            "level": level,
            "elements": [model["identities"].get(e["id"]) for e in elements],
            "relationships": [
                {
                    "source": model["identities"].get(r["source"]),
                    "target": model["identities"].get(r["target"]),
                    "label": r["label"],
                }
                for r in relationships
            ],
            "rolled_up": rolled_up,
            "undrawable": undrawable,
        },
        "checks": model["checks"],
        "gaps": model["gaps"],
        "notation": {
            "structurizr": to_structurizr(model),
            "plantuml": to_c4_plantuml(model),
            "mermaid": to_mermaid(model, level),
        },
    }


# ============================================================================
# Well-formedness
# ============================================================================

EXAMPLE_CAP = 5


def _well_formed(
    elements: Dict[str, Dict[str, Any]],
    relationships: List[Dict[str, Any]],
    dangling: List[str],
    system: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """The rules C4 states, checked against the graph rather than assumed.

    This is the half of the view that is not a drawing. A diagram is persuasive, so a
    hole in it is worse than no diagram; these checks are how the hole is given a
    number and a name instead of being left for the reader to notice.

    Each check reports `holds`, the counts it was decided on, and a capped list of
    examples — enough to act on, not a wall of text.
    """
    if not elements:
        # No model, so no rules apply to it. Reporting "the system has no containers"
        # against a requirements-only graph would be a failure invented by the checker
        # rather than found in the graph.
        return []

    checks: List[Dict[str, Any]] = []

    def add(name: str, title: str, failing: List[str], total: int, detail: str) -> None:
        checks.append({
            "name": name,
            "title": title,
            "holds": not failing,
            "violations": len(failing),
            "total": total,
            "examples": failing[:EXAMPLE_CAP],
            "truncated": max(0, len(failing) - EXAMPLE_CAP),
            "detail": detail,
        })

    # 1. Nesting. The one structural rule C4 states outright.
    misnested: List[str] = []
    for element in sorted(elements.values(), key=lambda e: e["label"]):
        expected = _EXPECTED_PARENT_LEVEL.get(element["level"])
        if expected is None:
            continue
        parent = elements.get(element["parent"])
        actual = parent["level"] if parent else "no parent"
        if actual != expected:
            misnested.append(f'{element["label"]} ({element["level"]} inside {actual})')
    add(
        "nesting", "Every element sits inside the level above it", misnested,
        len(elements),
        "A container belongs in a system, a component in a container, code in a "
        "component. Structurizr and C4-PlantUML both refuse the nesting this reports, "
        "so the emitted notation is rejected at exactly these points.",
    )

    # 2. Reflexive containment. `X part_of X` is never true of anything, and the
    #    extraction produced three of them here that review passed as verified. It
    #    gets its own check because the fix is different from a cycle's: the fact is
    #    simply not a fact.
    reflexive = [e["label"] for e in sorted(elements.values(), key=lambda e: e["label"])
                 if e["parent"] == e["id"]]
    add(
        "reflexive", "Nothing is part of itself", reflexive, len(elements),
        "A self-referential `part_of` is an extraction artefact, not a fact. It also "
        "makes its element a containment root, so it silently becomes a boundary in "
        "the notation rather than a child of one.",
    )

    # 3. Cycles. Reported against the cycle's own members, not against everything
    #    downstream of one: a self-loop at the top of the tree makes every descendant
    #    unreachable from a root, and a report that lists all of them buries the three
    #    elements that actually need fixing.
    cyclic: List[str] = []
    for element in sorted(elements.values(), key=lambda e: e["label"]):
        cursor, seen = element["parent"], set()
        while cursor and cursor in elements and cursor not in seen:
            if cursor == element["id"]:
                cyclic.append(element["label"])
                break
            seen.add(cursor)
            cursor = elements[cursor]["parent"]
    add(
        "acyclic", "Containment is a tree, not a cycle", cyclic, len(elements),
        "A cycle in `part_of` makes the diagram unbuildable at that point: the nested "
        "notation has no root to start from, so the elements inside it are drawn "
        "detached or not at all.",
    )

    # 4. Relationships resolve inside the model. Counted while the relationships were
    #    built, because a connection with an unplaced endpoint never becomes a pair at
    #    all — checking the finished list would always pass and say nothing.
    add(
        "connections", "Every connection joins two elements in the model", dangling,
        len(relationships) + len(dangling),
        "A connection with an endpoint that is not a C4 element cannot be drawn. It "
        "is a hole in the diagram, not a relationship, and it is why a picture can "
        "look complete while the graph still holds unresolved links.",
    )

    # 5. The drawn level is populated. A container diagram of a system with no
    #    containers is the emptiest possible answer, and it looks deliberate.
    has_container = any(
        elements.get(e["parent"], {}).get("level") == "context"
        for e in elements.values() if e["level"] == "container"
    )
    add(
        "populated", "The system has at least one container",
        [] if has_container else [(system or {}).get("label", "no system identified")],
        1,
        "Nothing to draw at the container level means the graph models no containers "
        "for this system; the notation is emitted anyway, empty rather than invented.",
    )

    return checks
