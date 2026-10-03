"""
The engines: one function per `engine` name a registry entry may use.

Every one of them is read-only by construction. They call projections and allowlisted
queries, and nothing here touches the store's write path — which is the whole reason the
answer can be trusted: an agent that can write is an agent whose answer is a proposal.

TWO GUARDS THAT EXIST BECAUSE THE FAILURE IS SILENT
---------------------------------------------------
1. `run_named_query` accepts a `QUERIES` KEY and never query text. `run_query` in
   `core.knowledge.rdf` falls back to treating its argument as raw SPARQL, so the
   allowlist has to be enforced here, at the boundary — this is the function that stops
   a model introducing query text, and it is why the registry has nowhere to put one.
2. A query that needs the class hierarchy REFUSES to answer when the hierarchy is
   absent. `to_rdf` degrades to no hierarchy if the ontology cannot be read, and a
   property-path query then returns zero rows — measured, against one row with the
   ontology present. Zero rows means "nothing is missing", so answering with it would
   turn a load failure into a clean bill of health.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from rdflib import RDFS

from app.projections import (
    project_delta,
    project_gap_report,
    project_quality_report,
    project_review_summary,
)
from core.knowledge.rdf import QUERIES, run_query, to_rdf
from core.knowledge.realization import realization_report
from core.qna.answers import (
    DEFAULT_ROW_LIMIT,
    AnswerContext,
    AnswerShaped,
    absent,
    compose,
)
from core.questions import (
    STATE_ANSWERED,
    STATE_SUBSTRATE_ABSENT,
    QuestionEntry,
    QuestionRegistry,
)

#: A single answer, as an engine returns it.
Engine = Callable[[AnswerContext, Dict[str, Any], QuestionEntry], AnswerShaped]


def run_named_query(
    graph,
    name: str,
    limit: int = DEFAULT_ROW_LIMIT,
    *,
    current_state: bool = True,
    ontology_dir: str = "ontology",
) -> List[Dict[str, Any]]:
    """Run one of the NAMED reference queries, and only a named one.

    Raises `KeyError` for anything else — a raw SPARQL string included. That is the
    point: the allowlist is what makes an LLM-facing query surface safe, and it is
    enforced here rather than trusted to the caller.

    `current_state` is not a preference. The plain triple form is emitted regardless of
    an assertion's status, so a RETIRED implementer survives into a lineage graph and an
    "active" question answered against one is wrong.
    """
    if name not in QUERIES:
        raise KeyError(
            f"{name!r} is not a registered query. Registered: {sorted(QUERIES)}. "
            f"Raw SPARQL is not accepted here."
        )
    rdf = to_rdf(graph, include_superseded=not current_state, ontology_dir=ontology_dir)
    return run_query(rdf, name)[:limit]


def _hierarchy_triples(rdf) -> int:
    return len(list(rdf.triples((None, RDFS.subClassOf, None))))


def _revision_note(ctx: AnswerContext) -> str:
    return f"computed against revision {ctx.ref!r} of scope {ctx.scope_id!r}"


# ---------------------------------------------------------------------------
# The answer-shaped projections
# ---------------------------------------------------------------------------

def _realization(ctx: AnswerContext, params: Dict[str, Any], entry: QuestionEntry) -> AnswerShaped:
    report = realization_report(ctx.graph)
    summary = report.get("summary", {})
    coverage = summary.get("coverage", {})
    caveats: List[str] = []
    # The completeness gate belongs to the answer, not to the reader's memory.
    if coverage.get("unresolved"):
        caveats.append(
            f"{coverage['unresolved']} requirement(s) carry a claim that is not yet "
            f"BOUND; they are unresolved, not unanswered."
        )
    return AnswerShaped(
        state=STATE_ANSWERED,
        result={
            "summary": summary,
            "unrealized": report.get("unrealized", []),
        },
        caveats=tuple(caveats),
        assumptions=(_revision_note(ctx),),
        source="realization_report",
    )


def _gap_report(ctx: AnswerContext, params: Dict[str, Any], entry: QuestionEntry) -> AnswerShaped:
    report = project_gap_report(ctx.graph)
    caveats: List[str] = []
    if not report.get("is_auditable", False):
        caveats.append(
            "This graph is not auditable: absence of a fact is NOT evidence of its "
            "absence."
        )
    return AnswerShaped(
        state=STATE_ANSWERED,
        result=report,
        caveats=tuple(caveats),
        assumptions=(_revision_note(ctx),),
        source="project_gap_report",
    )


def _quality_report(
    ctx: AnswerContext, params: Dict[str, Any], entry: QuestionEntry
) -> AnswerShaped:
    report = project_quality_report(ctx.graph)
    return AnswerShaped(
        state=STATE_ANSWERED,
        result=report,
        assumptions=(_revision_note(ctx),),
        source="project_quality_report",
    )


def _review_queue(ctx: AnswerContext, params: Dict[str, Any], entry: QuestionEntry) -> AnswerShaped:
    if ctx.log is None:
        return absent(
            STATE_SUBSTRATE_ABSENT,
            "No review log is in this context, so the queue cannot report what has been decided.",
            source="project_review_summary",
        )
    summary = project_review_summary(ctx.graph, ctx.log)
    progress = summary.get("progress", {})
    caveats: List[str] = []
    if progress.get("needs_judgement") is not None:
        caveats.append(
            f"{progress.get('outstanding', 0)} outstanding assertion(s) carry "
            f"{progress['needs_judgement']} decision(s): the rest are decidable by a validator."
        )
    return AnswerShaped(
        state=STATE_ANSWERED,
        result=summary,
        caveats=tuple(caveats),
        assumptions=(_revision_note(ctx),),
        source="project_review_summary",
    )


def _containment(ctx: AnswerContext, params: Dict[str, Any], entry: QuestionEntry) -> AnswerShaped:
    wanted = str(params.get("element") or "").strip()
    if not wanted:
        return absent(
            STATE_SUBSTRATE_ABSENT,
            "This question needs an element name (`params: [element]`) and none was supplied.",
            source="containment",
        )
    key = wanted.lower()
    match = None
    for node in ctx.graph.nodes.values():
        if node.label.strip().lower() == key or node.id.lower() == key:
            match = node
            break
    if match is None:
        return AnswerShaped(
            state=STATE_ANSWERED,
            result={"element": wanted, "children": [], "parents": [], "found": False},
            caveats=("No node carries that name or id, so nothing is contained by it.",),
            assumptions=(_revision_note(ctx),),
            source="containment",
        )
    children, parents = [], []
    for assertion in ctx.graph.active():
        if assertion.predicate != "part_of" or not assertion.object:
            continue
        if assertion.object == match.id:
            children.append(assertion.subject)
        elif assertion.subject == match.id:
            parents.append(assertion.object)
    label = {n.id: n.label for n in ctx.graph.nodes.values()}
    return AnswerShaped(
        state=STATE_ANSWERED,
        result={
            "element": match.label,
            "found": True,
            "children": [{"id": c, "label": label.get(c, c)} for c in sorted(children)],
            "parents": [{"id": p, "label": label.get(p, p)} for p in sorted(parents)],
        },
        caveats=(
            "Containment is a property on the element, not a drawn edge: only DECLARED "
            "parents appear.",
        ),
        assumptions=(_revision_note(ctx),),
        source="part_of",
    )


def _delta(ctx: AnswerContext, params: Dict[str, Any], entry: QuestionEntry) -> AnswerShaped:
    """What changed between a baseline and now. Needs the store; says so without one."""
    if ctx.store is None:
        return absent(
            STATE_SUBSTRATE_ABSENT,
            "No store is in this context, so two revisions cannot be compared.",
            source="project_delta",
        )
    from core.knowledge.model import KnowledgeGraph
    from core.knowledge.serialise import compute_graph_delta

    old_id = str(params.get("from") or "").strip()
    new_id = str(params.get("to") or "working").strip()
    try:
        old = ctx.store.load_revision(old_id) if old_id else None
        new = ctx.store.load_working() if new_id == "working" else ctx.store.load_revision(new_id)
    except KeyError as exc:
        return absent(
            STATE_SUBSTRATE_ABSENT,
            f"That revision does not exist: {exc}",
            source="project_delta",
        )
    old_graph = old.graph if old is not None else KnowledgeGraph()
    delta = compute_graph_delta(old_graph, new.graph)
    view = project_delta(delta, old_graph, new.graph)
    caveats = ()
    if not old_id:
        caveats = (
            "There is no frozen revision, so this compares against an EMPTY graph rather "
            "than against a baseline — every fact reads as added.",
        )
    return AnswerShaped(
        state=STATE_ANSWERED,
        result=view,
        caveats=caveats,
        assumptions=(f"from {old_id or '(empty)'} to {new_id}", _revision_note(ctx)),
        source="project_delta",
    )


def _sparql(ctx: AnswerContext, params: Dict[str, Any], entry: QuestionEntry) -> AnswerShaped:
    """One of the named queries, with both guards applied."""
    if entry.query not in QUERIES:
        return absent(
            STATE_SUBSTRATE_ABSENT,
            f"{entry.query!r} is not a registered query; raw SPARQL is not accepted.",
            source="run_named_query",
        )
    rdf = to_rdf(
        ctx.graph,
        include_superseded=not entry.current_state,
        ontology_dir=ctx.ontology_dir,
    )
    if entry.needs_hierarchy and _hierarchy_triples(rdf) == 0:
        return absent(
            STATE_SUBSTRATE_ABSENT,
            "This question needs the ontology's class hierarchy in the RDF projection and "
            "it is absent, so a subclass-aware query would return nothing for a reason that "
            "is not about the graph. Refusing to answer rather than reporting an empty result.",
            source="run_named_query",
        )
    rows = run_query(rdf, entry.query)
    truncated = len(rows) > DEFAULT_ROW_LIMIT
    return AnswerShaped(
        state=STATE_ANSWERED,
        result=rows[:DEFAULT_ROW_LIMIT],
        assumptions=(
            _revision_note(ctx),
            "graph form: current state" if entry.current_state else "graph form: with lineage",
        ),
        source=f"QUERIES[{entry.query!r}]",
        truncated=truncated,
    )


def _declared_absent(
    ctx: AnswerContext, params: Dict[str, Any], entry: QuestionEntry
) -> AnswerShaped:
    """A question the registry knows is not answerable yet, said out loud.

    Declared rather than omitted so the reader learns WHY — "no governance instruments
    are in this scope" is a different answer from "nothing matched your question", and
    unlike a missing entry this one names the item that would close it.
    """
    return absent(entry.state, entry.absent, blocked_by=entry.blocked_by, source="declared_absent")


#: The engine names a registry entry may use. `validate_question_registry` checks the
#: shipped catalogue against these, so a typo is a finding rather than a runtime 500.
ENGINES: Dict[str, Engine] = {
    "realization": _realization,
    "gap_report": _gap_report,
    "quality_report": _quality_report,
    "review_queue": _review_queue,
    "containment": _containment,
    "delta": _delta,
    "sparql": _sparql,
    "declared_absent": _declared_absent,
}


def answer(
    registry: QuestionRegistry,
    entry: QuestionEntry,
    ctx: AnswerContext,
    params: Optional[Dict[str, Any]] = None,
) -> dict:
    """Run one entry's engine and attach the entry's own qualifications."""
    engine = ENGINES.get(entry.engine)
    if engine is None:
        shaped = absent(
            STATE_SUBSTRATE_ABSENT,
            f"engine {entry.engine!r} is not implemented",
            source="dispatcher",
        )
    else:
        try:
            shaped = engine(ctx, dict(params or {}), entry)
        except Exception as exc:  # noqa: BLE001 — an engine must not take the page down
            shaped = AnswerShaped(
                state=STATE_SUBSTRATE_ABSENT,
                detail=f"the {entry.engine!r} engine failed: {type(exc).__name__}: {exc}",
                source=entry.engine,
            )
    return compose(entry, shaped)
