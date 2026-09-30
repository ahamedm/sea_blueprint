"""
Graph -> prompt digest.

WHAT PROBLEM THIS SOLVES
------------------------
Every extraction agent reads a DOCUMENT. The Design Assistant has none: its input
is REQ-G and, where one exists, the baseline ARC-G. This module is the answer to
"what is the document when there is no document" — a deterministic text rendering
of the two graphs, wrapped by the agent in a single `Chunk` so the existing pass
harness, merge and validators all apply unchanged.

DETERMINISTIC ON PURPOSE
------------------------
Same graph in, same text out. No timestamps, no run ids, no dictionary order. That
matters for three reasons:

- the prompt is reproducible, so two runs of one input differ only by the model;
- `_run_id`/`document_hash` stay meaningful, because they hash this text;
- a diff between two digests is a diff between two knowledge states, not noise.

DEGRADATION IS REPORTED, NEVER SILENT
-------------------------------------
A requirement set larger than the budget cannot be trimmed quietly: a design that
never saw half the requirements is not a smaller design, it is a wrong one. So the
renderer drops content in a fixed, documented order — responsibilities, then
descriptions, then non-NFR detail — and records every drop in `caveats`, which the
agent puts in the prompt and the page shows to the reviewer. A hard truncation is
the last resort and says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .ingest import completeness_note
from .model import KnowledgeGraph, REQUIREMENT_KINDS
from .quality import quality_report
from .realization import realization_report

# Kind -> the section it belongs to when rendering architecture. Ordered the way a
# designer reads it: the system, what it is made of, what it talks to, then the
# cross-cutting decisions.
_ARCHITECTURE_SECTIONS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("Systems", ("SoftwareSystem", "ExternalSystem", "Person")),
    ("Containers", ("Container", "DataStore")),
    ("Components", ("Component", "CodeElement")),
    ("Deployment", ("DeploymentNode",)),
    ("Design techniques", ("DesignTechnique",)),
    ("Architecture patterns", ("ArchitecturePattern",)),
    ("Architecture styles", ("ArchitectureStyle",)),
)

_ARCHITECTURE_KINDS = frozenset(
    kind for _title, kinds in _ARCHITECTURE_SECTIONS for kind in kinds
)

# Cross-graph predicates worth showing: they are the links a designer must not
# recreate. A pattern/technique link is listed on the node itself.
_LINK_PREDICATES = (
    "implements_requirement",
    "traces_to_goal",
    "traces_to_capabilities",
    "supports_capabilities",
    "satisfies_quality_attribute",
    "delivers_initiative",
    "authorised_by_initiative",
)


@dataclass
class DigestSection:
    """One rendered section, with what it cost and what was left out."""

    title: str
    text: str = ""
    counts: Dict[str, int] = field(default_factory=dict)
    caveats: List[str] = field(default_factory=list)


@dataclass
class DesignInput:
    """The two graphs as one prompt-ready document."""

    text: str
    base_ref: str = "working"
    counts: Dict[str, int] = field(default_factory=dict)
    caveats: List[str] = field(default_factory=list)

    @property
    def truncated(self) -> bool:
        return bool(self.caveats)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "base_ref": self.base_ref,
            "counts": dict(self.counts),
            "caveats": list(self.caveats),
            "chars": len(self.text),
            "text": self.text,
        }


# ============================================================================
# Reading the graph
# ============================================================================


def _literal_facts(graph: KnowledgeGraph) -> Dict[str, Dict[str, str]]:
    """subject -> {predicate: value} for assertions whose object is a value."""
    out: Dict[str, Dict[str, str]] = {}
    for a in graph.active():
        if a.object is None and a.value is not None:
            out.setdefault(a.subject, {})[a.predicate] = str(a.value)
    return out


def _target_label(graph: KnowledgeGraph, a) -> str:
    """What the assertion points at, as a reader wants to see it.

    `Assertion.target` returns the node ID when the object is a node, so printing
    it directly leaks `qualityattribute:time_behaviour` into a prompt. Resolving to
    the label is the difference between a document and a database dump.
    """
    if a.object is not None:
        node = graph.nodes.get(a.object)
        return node.label if node else a.object
    return a.value or ""


def _outgoing(graph: KnowledgeGraph) -> Dict[str, List[Tuple[str, str]]]:
    """subject -> [(predicate, target label)] for every active assertion."""
    out: Dict[str, List[Tuple[str, str]]] = {}
    for a in graph.active():
        target = _target_label(graph, a)
        if target:
            out.setdefault(a.subject, []).append((a.predicate, target))
    return out


def _source_texts(graph: KnowledgeGraph) -> Dict[str, str]:
    """subject -> the document's own words for it, where an assertion quoted any.

    `source_text` is a field on the assertion rather than a literal fact, so it
    does not appear in `_literal_facts`. Nothing populates it for a requirement
    today — the entity loop writes no source text — so this returns an empty map on
    every current graph and the renderer simply omits the line. Kept because the
    extraction profiles DO quote triples, and the moment a requirements run starts
    carrying the statement through, the design should see it.
    """
    out: Dict[str, str] = {}
    for a in graph.active():
        if a.source_text and a.subject not in out:
            out[a.subject] = a.source_text
    return out


def _sorted_nodes(graph: KnowledgeGraph, kinds: Optional[Iterable[str]] = None):
    wanted = None if kinds is None else set(kinds)
    return sorted(
        (n for n in graph.nodes.values() if wanted is None or n.kind in wanted),
        key=lambda n: (n.kind, n.label.lower(), n.id),
    )


def _refs(node) -> str:
    refs = [r for r in (node.external_refs or []) if r]
    return f" [{' '.join(refs)}]" if refs else ""


# ============================================================================
# Requirements
# ============================================================================


def _render_requirements(
    graph: KnowledgeGraph,
    initiative_id: str,
    *,
    terse: bool,
    section: DigestSection,
) -> None:
    facts = _literal_facts(graph)
    out = _outgoing(graph)
    sources = _source_texts(graph)
    lines: List[str] = []

    initiative = next(
        (n for n in graph.nodes.values() if n.kind == "Initiative"), None
    )
    if initiative is not None:
        label = f"{initiative.label}"
        if initiative_id and initiative_id not in label:
            label = f"{label} ({initiative_id})"
        lines.append(f"Initiative: {label}")

    for kind, title in (
        ("BusinessGoal", "Business goals"),
        ("BusinessCapability", "Business capabilities"),
    ):
        nodes = _sorted_nodes(graph, (kind,))
        if nodes:
            lines.append(f"{title}: " + "; ".join(n.label + _refs(n) for n in nodes))

    requirement_nodes = _sorted_nodes(graph, REQUIREMENT_KINDS)
    section.counts["requirements"] = len(requirement_nodes)

    by_kind: Dict[str, List[Any]] = {}
    for node in requirement_nodes:
        by_kind.setdefault(node.kind, []).append(node)

    for kind in sorted(by_kind):
        nodes = by_kind[kind]
        lines.append("")
        lines.append(f"## {kind} ({len(nodes)})")
        for node in nodes:
            facts_for = facts.get(node.id, {})
            lines.append(f"- {node.label}{_refs(node)}")
            if terse:
                continue
            quality = " / ".join(
                v for v in (facts_for.get("quality_category"),
                            facts_for.get("subcharacteristic")) if v
            )
            if quality:
                lines.append(f"    quality: {quality}")
            attributes = [
                target for predicate, target in out.get(node.id, [])
                if predicate in ("realizes_attribute", "realizes_quality_attributes")
            ]
            if attributes:
                lines.append("    attribute: " + ", ".join(sorted(set(attributes))))
            for predicate, target in sorted(out.get(node.id, [])):
                if predicate in ("traces_to_goal", "traces_to_capability",
                                 "traces_to_capabilities", "governed_by_rules"):
                    lines.append(f"    {predicate}: {target}")
            source = sources.get(node.id)
            if source:
                lines.append(f"    source: {source[:200]}")

    section.text = "\n".join(lines).strip()


# ============================================================================
# Architecture
# ============================================================================


def _render_architecture(
    graph: KnowledgeGraph,
    base_ref: str,
    *,
    include_responsibilities: bool,
    include_links: bool,
    section: DigestSection,
) -> None:
    facts = _literal_facts(graph)
    out = _outgoing(graph)
    lines: List[str] = [f"Existing architecture ({base_ref})"]

    for title, kinds in _ARCHITECTURE_SECTIONS:
        nodes = _sorted_nodes(graph, kinds)
        if not nodes:
            continue
        lines.append("")
        lines.append(f"## {title} ({len(nodes)})")
        section.counts[title.lower().replace(" ", "_")] = len(nodes)
        for node in nodes:
            facts_for = facts.get(node.id, {})
            detail = facts_for.get("element_type") or facts_for.get("technique_category") \
                or facts_for.get("pattern_category") or facts_for.get("style") or ""
            suffix = f" ({detail})" if detail and detail != node.kind else ""
            lines.append(f"- {node.label}{_refs(node)}{suffix}")
            if include_responsibilities:
                responsibilities = [
                    a.value for a in graph.active()
                    if a.subject == node.id and a.predicate == "responsibility" and a.value
                ]
                for responsibility in sorted(responsibilities):
                    lines.append(f"    responsibility: {responsibility}")
            mechanism = facts_for.get("mechanism")
            if mechanism:
                lines.append(f"    mechanism: {mechanism[:200]}")
            attributes = sorted({
                target for predicate, target in out.get(node.id, [])
                if predicate == "satisfies_attribute"
            })
            if attributes:
                lines.append("    delivers: " + ", ".join(attributes))

    if include_links:
        # The single most useful thing the design can be told: which requirements
        # the existing architecture already answers, and which it does not. A raw
        # dump of every recorded link costs several times this for less signal —
        # and the unanswered list is the design's actual worklist.
        report = realization_report(graph)
        answered = [r for r in report["requirements"] if r["bound"]]
        unanswered = [r for r in report["requirements"] if not r["realized"]]
        section.counts["requirements_answered"] = len(answered)
        section.counts["requirements_unanswered"] = len(unanswered)
        if answered:
            lines.append("")
            lines.append(
                f"## Requirements the existing architecture already answers ({len(answered)})"
            )
            lines.append("Extend or refine these rather than proposing a duplicate answer.")
            for row in answered:
                sources = ", ".join(sorted({c["source_label"] for c in row["bound"]}))
                lines.append(f"- {row['label']} <- {sources}")
        if unanswered:
            lines.append("")
            lines.append(
                f"## Requirements with NO architectural answer ({len(unanswered)})"
            )
            lines.append("These are the gaps a core design should close first.")
            for row in unanswered:
                lines.append(f"- {row['label']} ({row['kind']})")

    section.text = "\n".join(lines).strip()


# ============================================================================
# Quality
# ============================================================================


def _render_quality(graph: KnowledgeGraph, section: DigestSection) -> None:
    report = quality_report(graph)
    summary = report["summary"]
    if not summary["attributes"]:
        section.text = "No quality attributes are classified in REQ-G yet."
        return

    lines = [
        "## Quality attributes and their coverage",
        "`gap` = a requirement states it and nothing delivers it. `unasked` = the "
        "architecture delivers it and no requirement states it. Both are findings the "
        "design should answer.",
    ]
    section.counts["quality_concerns"] = summary["attributes"]
    for row in report["characteristics"]:
        lines.append(f"### {row['label']}")
        for concern in row["concerns"]:
            lines.append(
                f"- {concern['display']}: {concern['coverage_label']}"
                f" (stated {concern['counts']['stated']},"
                f" delivered {concern['counts']['delivered']})"
            )
    section.text = "\n".join(lines)


# ============================================================================
# Assembly
# ============================================================================


def requirements_digest(
    graph: KnowledgeGraph,
    initiative_id: str = "",
    budget_chars: int = 12000,
) -> DigestSection:
    """REQ-G as text, degraded in a documented order if it exceeds the budget."""
    section = DigestSection(title="REQ-G")
    _render_requirements(graph, initiative_id, terse=False, section=section)
    if len(section.text) <= budget_chars:
        return section

    minimal = DigestSection(title="REQ-G", counts=dict(section.counts))
    _render_requirements(graph, initiative_id, terse=True, section=minimal)
    section.caveats.append(
        "requirement detail (quality classification and traceability) was dropped "
        "to fit the prompt budget"
    )
    section.text = minimal.text
    if len(section.text) <= budget_chars:
        return section

    section.text = section.text[:budget_chars]
    section.caveats.append(
        f"REQ-G was TRUNCATED at {budget_chars} characters — the design did not see "
        f"every requirement it was given"
    )
    return section


def architecture_digest(
    graph: KnowledgeGraph,
    base_ref: str = "working",
    budget_chars: int = 8000,
) -> DigestSection:
    """The existing architecture as text, degraded the same way."""
    section = DigestSection(title="ARC-G")
    _render_architecture(
        graph, base_ref,
        include_responsibilities=True, include_links=True, section=section,
    )
    if len(section.text) <= budget_chars:
        return section

    sparse = DigestSection(title="ARC-G", counts=dict(section.counts))
    _render_architecture(
        graph, base_ref,
        include_responsibilities=False, include_links=True, section=sparse,
    )
    section.caveats.append("element responsibilities were dropped to fit the prompt budget")
    section.text = sparse.text
    if len(section.text) <= budget_chars:
        return section

    terse = DigestSection(title="ARC-G", counts=dict(section.counts))
    _render_architecture(
        graph, base_ref,
        include_responsibilities=False, include_links=False, section=terse,
    )
    section.caveats.append(
        "the already-recorded requirement links were dropped to fit the prompt budget"
    )
    section.text = terse.text
    if len(section.text) <= budget_chars:
        return section

    section.text = section.text[:budget_chars]
    section.caveats.append(
        f"the baseline architecture was TRUNCATED at {budget_chars} characters"
    )
    return section


def _architecture_node_count(graph: KnowledgeGraph) -> int:
    """How many nodes the architecture renderer would actually draw.

    Deliberately the same `_ARCHITECTURE_KINDS` the sections are built from, so
    "this graph declares no architecture" can never disagree with what the model was
    shown. A second notion of "has architecture" would drift from the renderer and
    the caveat below would start lying in whichever direction drifted last.
    """
    return sum(1 for n in graph.nodes.values() if n.kind in _ARCHITECTURE_KINDS)


def design_input(
    graph: KnowledgeGraph,
    baseline: Optional[KnowledgeGraph] = None,
    initiative_id: str = "",
    base_ref: str = "",
    requirements_budget: int = 12000,
    architecture_budget: int = 8000,
) -> DesignInput:
    """REQ-G + the baseline ARC-G, as one document for the Design Assistant.

    `baseline` is the frozen architecture this design should extend. When none is
    given the working set's own architecture is used and the caveat says so — the
    alternative, silently designing against nothing, is how a proposal ends up
    duplicating containers that already exist.
    """
    architecture_source = baseline if baseline is not None else graph
    resolved_ref = base_ref or ("baseline" if baseline is not None else "working (no frozen baseline)")

    requirements = requirements_digest(graph, initiative_id=initiative_id,
                                       budget_chars=requirements_budget)
    architecture = architecture_digest(architecture_source, base_ref=resolved_ref,
                                       budget_chars=architecture_budget)

    caveats = list(requirements.caveats) + list(architecture.caveats)
    if baseline is None:
        caveats.append(
            "no frozen baseline ARC-G exists; the existing architecture shown is the "
            "working set, which may itself be an unreviewed proposal"
        )
    elif _architecture_node_count(architecture_source) == 0:
        # A frozen baseline with no architecture is NOT automatically a mistake:
        # freezing REQ-G before any design exists is the greenfield path, and a
        # proposal that reuses nothing is the correct answer there. What must not
        # pass silently is the other case — the enterprise has architecture in the
        # working set that never reached a baseline, and the design cannot see it.
        # Extending nothing without saying so is how a proposal ends up duplicating
        # containers that already exist, which is the hazard this whole header is
        # for. The design still extends nothing: the baseline is the authority, and
        # quietly substituting the working set would be a different baseline.
        in_working = _architecture_node_count(graph)
        if in_working:
            caveats.append(
                f"the frozen baseline names no architecture, but the working set holds "
                f"{in_working} architecture element(s) that are not in it — the design "
                f"extends an empty baseline and may propose duplicates"
            )
        else:
            caveats.append(
                "the frozen baseline names no architecture, and neither does the "
                "working set — a greenfield design, so a proposal that reuses nothing "
                "is expected"
            )
    # `completeness_note` is a header plus one line per run; flatten it so the
    # prompt header stays one bullet per caveat rather than one multi-line bullet.
    caveats.extend(
        line.strip() for line in completeness_note(graph).splitlines() if line.strip()
    )

    counts = {**requirements.counts, **architecture.counts}
    text = "\n\n".join(
        part for part in (
            "# Input 1 — Requirements (REQ-G)",
            requirements.text,
            "# Input 2 — Existing architecture to extend",
            architecture.text,
            "# Quality coverage",
            _quality_text(graph),
        ) if part
    )

    header = "\n".join(f"> {c}" for c in caveats)
    text = f"{header}\n\n{text}\n" if header else f"{text}\n"

    return DesignInput(
        text=text,
        base_ref=resolved_ref,
        counts=counts,
        caveats=caveats,
    )


def _quality_text(graph: KnowledgeGraph) -> str:
    section = DigestSection(title="quality")
    _render_quality(graph, section)
    return section.text
