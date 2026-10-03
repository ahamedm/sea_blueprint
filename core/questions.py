"""
The named questions this platform can answer deterministically.

WHY A REGISTRY AND NOT A QUERY GENERATOR
----------------------------------------
The tempting shape for "ask the graph" is natural language in, query text out. It is
the wrong one here, for the reason `docs/design/natural-language-enquiry.md` records:
a generated query that is *nearly* right returns a well-formed, confident, wrong
answer, and nothing in the pipeline notices. A registry inverts that. Every answerable
question is declared, named, typed and testable; a model's job shrinks to *selecting*
one and filling its slots, which is checkable; and "what can I ask?" becomes a question
with an answer — enumerate this file.

It also makes the honest outcome cheap. `no_named_question` and `substrate_absent` are
first-class states rather than low-confidence answers, which matters most for the
questions that cannot be answered *yet*: this vocabulary's own governance layer has
classes and no instances, so "which principles does this design violate?" must answer
"nothing is in this scope to check against" and not "no violations".

MIRRORS `core/patterns.py` DELIBERATELY
---------------------------------------
Same shape as the pattern catalogue: a frozen entry, a frozen catalogue, a loader that
degrades to empty with `findings` rather than raising, and a prompt-context renderer.
That is the proven "bounded vocabulary + deterministic resolution" shape in this
codebase, and a second invention would be a second thing to learn.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

# Where the shipped catalogue lives, repo-relative. Exposed so the app, the tests and
# the prompt renderer agree on one path instead of each computing it.
DEFAULT_CATALOGUE_PATH = Path("ontology") / "catalogues" / "questions.yaml"

#: The answer states. `answered` is the only one that carries a result; the other three
#: are different KINDS of silence and must never be rendered alike:
#:
#:   substrate_absent   the graph could answer this and has nothing to answer FROM
#:                      (0 governance nodes, no class hierarchy). The platform is
#:                      silent, and it names what is missing and which item owns it.
#:   no_named_question  nothing in the registry matches. The vocabulary is silent.
#:   out_of_scope       a declared non-goal, so the refusal is deliberate.
STATE_ANSWERED = "answered"
STATE_SUBSTRATE_ABSENT = "substrate_absent"
STATE_NO_NAMED_QUESTION = "no_named_question"
STATE_OUT_OF_SCOPE = "out_of_scope"

ANSWER_STATES = (
    STATE_ANSWERED,
    STATE_SUBSTRATE_ABSENT,
    STATE_NO_NAMED_QUESTION,
    STATE_OUT_OF_SCOPE,
)


@dataclass(frozen=True)
class QuestionEntry:
    """One answerable question, and everything needed to answer and to qualify it.

    `caveats` and `pointer` are part of the entry rather than the engine's business,
    because they are properties of the QUESTION: "which requirements have no answer?"
    always carries the completeness caveat, whoever computes it, and it is always
    answerable at `/gaps`.
    """

    id: str
    intent: str
    question: str
    engine: str
    keywords: Tuple[str, ...] = ()
    params: Tuple[str, ...] = ()
    caveats: Tuple[str, ...] = ()
    pointer: str = ""
    # For `engine: sparql` — a key of `core.knowledge.rdf.QUERIES`, never raw SPARQL.
    # The allowlist is the enforcement: a model selecting an entry cannot introduce
    # query text at all, because there is nowhere to put it.
    query: str = ""
    # Does this question need the ontology's class hierarchy in the RDF projection?
    # A property-path query returns ZERO rows without it, which is indistinguishable
    # from "nothing is missing" — so the engine refuses to answer rather than answer.
    needs_hierarchy: bool = False
    # `include_superseded=False` is part of a query's MEANING: a retired implementer is
    # not an implementer, but the plain triple survives into a lineage graph.
    current_state: bool = True
    # For `engine: declared_absent` — what is missing, and the item that owns closing it.
    # `state` distinguishes the two kinds of absence and is required for those entries:
    #   substrate_absent  the graph owes this answer and has nothing to answer from
    #   out_of_scope      deliberately not built yet
    absent: str = ""
    blocked_by: str = ""
    state: str = ""


@dataclass(frozen=True)
class QuestionRegistry:
    """The catalogue, or an empty one with the reasons it is empty.

    Empty is a supported state, not a crash, for the same reason the pattern catalogue
    says so: an interface with no questions should report that, not refuse to start.
    """

    entries: Tuple[QuestionEntry, ...] = ()
    version: str = ""
    description: str = ""
    path: str = ""
    findings: Tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.entries)

    def by_id(self, question_id: str) -> Optional[QuestionEntry]:
        for entry in self.entries:
            if entry.id == question_id:
                return entry
        return None

    def by_intent(self, intent: str) -> Optional[QuestionEntry]:
        for entry in self.entries:
            if entry.intent == intent:
                return entry
        return None

    def engines(self) -> Tuple[str, ...]:
        return tuple(sorted({e.engine for e in self.entries}))

    def answerable(self) -> Tuple[QuestionEntry, ...]:
        """Entries that can carry a result today — not the declared-absent ones."""
        return tuple(e for e in self.entries if e.engine != "declared_absent")

    def absent(self) -> Tuple[QuestionEntry, ...]:
        return tuple(e for e in self.entries if e.engine == "declared_absent")


def _as_tuple(value: Any) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v).strip() for v in value if str(v).strip())
    return ()


def _entry_from(raw: Dict[str, Any]) -> Optional[QuestionEntry]:
    question_id = str(raw.get("id") or "").strip()
    engine = str(raw.get("engine") or "").strip()
    if not question_id or not engine:
        return None
    return QuestionEntry(
        id=question_id,
        intent=str(raw.get("intent") or question_id).strip(),
        question=str(raw.get("question") or "").strip(),
        engine=engine,
        keywords=_as_tuple(raw.get("keywords")),
        params=_as_tuple(raw.get("params")),
        caveats=_as_tuple(raw.get("caveats")),
        pointer=str(raw.get("pointer") or "").strip(),
        query=str(raw.get("query") or "").strip(),
        needs_hierarchy=bool(raw.get("needs_hierarchy") or False),
        current_state=bool(raw.get("current_state", True)),
        absent=str(raw.get("absent") or "").strip(),
        blocked_by=str(raw.get("blocked_by") or "").strip(),
        state=str(raw.get("state") or "").strip(),
    )


def load_question_registry(
    path: str | Path = DEFAULT_CATALOGUE_PATH,
) -> QuestionRegistry:
    """Read the catalogue. A missing file is a supported state, not a crash."""
    catalogue_path = Path(path)
    if not catalogue_path.is_file():
        return QuestionRegistry(
            path=str(catalogue_path), findings=(f"catalogue not found: {path}",)
        )

    try:
        document = yaml.safe_load(catalogue_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return QuestionRegistry(
            path=str(catalogue_path), findings=(f"catalogue is not valid YAML: {exc}",)
        )

    if not isinstance(document, dict):
        return QuestionRegistry(
            path=str(catalogue_path), findings=("catalogue root is not a mapping",)
        )

    entries: List[QuestionEntry] = []
    seen: Dict[str, str] = {}
    findings: List[str] = []
    for raw in document.get("questions") or []:
        if not isinstance(raw, dict):
            findings.append(f"skipped a non-mapping question entry: {raw!r}")
            continue
        entry = _entry_from(raw)
        if entry is None:
            findings.append("skipped a question entry with no id or engine")
            continue
        if entry.id in seen:
            findings.append(f"duplicate question id {entry.id!r}")
            continue
        seen[entry.id] = entry.engine
        entries.append(entry)

    return QuestionRegistry(
        entries=tuple(entries),
        version=str(document.get("version") or ""),
        description=str(document.get("description") or "").strip(),
        path=str(catalogue_path),
        findings=tuple(findings),
    )


def validate_question_registry(
    registry: QuestionRegistry,
    *,
    known_engines: Tuple[str, ...] = (),
    known_queries: Tuple[str, ...] = (),
) -> Tuple[str, ...]:
    """Findings that make the catalogue wrong rather than merely empty.

    An entry naming an engine or a query that does not exist is the defect this exists
    for: it looks answerable, routes fine, and fails at the last step — which in a
    model-driven loop is where nobody is looking.
    """
    findings: List[str] = list(registry.findings)
    for entry in registry.entries:
        if known_engines and entry.engine not in known_engines:
            findings.append(f"{entry.id}: unknown engine {entry.engine!r}")
        if entry.engine == "sparql":
            if not entry.query:
                findings.append(f"{entry.id}: engine 'sparql' with no query name")
            elif known_queries and entry.query not in known_queries:
                findings.append(
                    f"{entry.id}: query {entry.query!r} is not a registered QUERIES key "
                    f"(raw SPARQL is not accepted)"
                )
        if entry.engine == "declared_absent":
            if not entry.absent:
                findings.append(
                    f"{entry.id}: declared_absent with nothing said about what is missing"
                )
            if entry.state not in (STATE_SUBSTRATE_ABSENT, STATE_OUT_OF_SCOPE):
                findings.append(
                    f"{entry.id}: declared_absent must declare state "
                    f"{STATE_SUBSTRATE_ABSENT!r} or {STATE_OUT_OF_SCOPE!r}, not {entry.state!r}"
                )
        if not entry.pointer:
            findings.append(f"{entry.id}: no pointer, so an answer could not be checked")
    return tuple(findings)


def question_prompt_context(registry: QuestionRegistry) -> str:
    """The catalogue as the classifier sees it: what can be asked, never how to ask it.

    Deliberately no query text and no route. The model chooses an id and fills slots;
    everything else is looked up, so there is nothing here to mis-transcribe.
    """
    lines = ["### Questions this platform can answer", ""]
    for entry in registry.entries:
        params = f" (needs: {', '.join(entry.params)})" if entry.params else ""
        if entry.engine == "declared_absent":
            lines.append(f"- `{entry.id}` — {entry.question} [NOT ANSWERABLE YET: {entry.absent}]")
        else:
            lines.append(f"- `{entry.id}` — {entry.question}{params}")
    lines.append("")
    lines.append(
        "If none of these matches the question, say so by returning an empty intent — "
        "do not translate the question into a query, and do not answer from the graph "
        "yourself."
    )
    return "\n".join(lines)
