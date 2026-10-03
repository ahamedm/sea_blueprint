"""
What an answer IS — the contract between an engine and everything that renders one.

The point of this module is that a result and a *claim* are different objects. An
answer carries the caveats that qualify it, the assumptions it was computed under, and
the route where a reader can check it. Those travel WITH the result rather than being
the renderer's business, because the failure this design exists to prevent is a fluent
answer that has quietly dropped its own qualifications.

The three silences are distinct on purpose:

    substrate_absent   the graph owes this answer and has nothing to answer FROM.
                       Zero governance nodes; a missing class hierarchy. The platform
                       is silent, and it says what is missing and who owns it.
    no_named_question  nothing in the registry matches. The vocabulary is silent.
    out_of_scope       deliberately not built. The refusal is a decision, not a gap.

Rendering any of them as an empty result is the vacuous-answer failure: "no principles
violated" and "no principles in the graph to check against" look identical if the shape
does not distinguish them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Tuple

from core.questions import (
    STATE_ANSWERED,
    STATE_NO_NAMED_QUESTION,
    STATE_OUT_OF_SCOPE,
    STATE_SUBSTRATE_ABSENT,
    QuestionEntry,
)

#: Rows returned by a SPARQL-backed answer before it is truncated. A cap is part of the
#: read-only contract — an unbounded query is a denial of service waiting for a reason.
DEFAULT_ROW_LIMIT = 200


@dataclass(frozen=True)
class AnswerContext:
    """Everything an engine may read. Passing the store explicitly keeps engines honest
    about what they depend on, and lets a test supply a graph and nothing else.

    `ref` is the revision the answer was computed against, and it is not decoration: an
    impact or gap answer that cannot name its revision is vacuous, because the graph it
    describes is not the graph the reader has.
    """

    graph: Any
    log: Any = None
    scope_id: str = ""
    ref: str = "working"
    store: Any = None
    ontology_dir: str = "ontology"
    initiative_id: str = ""


@dataclass(frozen=True)
class AnswerShaped:
    """A result, or one of the three ways of not having one."""

    state: str
    result: Any = None
    caveats: Tuple[str, ...] = ()
    assumptions: Tuple[str, ...] = ()
    #: For the absent states: what is missing, in the graph's own terms.
    detail: str = ""
    #: The item that owns closing an absence, when one does.
    blocked_by: str = ""
    #: Which engine, query or projection produced this — the audit trail for an answer.
    source: str = ""
    #: True when `result` was truncated by `DEFAULT_ROW_LIMIT`.
    truncated: bool = False

    @property
    def answered(self) -> bool:
        return self.state == STATE_ANSWERED


def absent(state: str, detail: str, *, blocked_by: str = "", source: str = "") -> AnswerShaped:
    """A silence, named. `state` must be one of the two absence states."""
    if state not in (STATE_SUBSTRATE_ABSENT, STATE_OUT_OF_SCOPE):
        raise ValueError(f"{state!r} is not an absence state")
    return AnswerShaped(state=state, detail=detail, blocked_by=blocked_by, source=source)


def compose(entry: QuestionEntry, shaped: AnswerShaped) -> dict:
    """The entry's own qualifications, attached to whatever an engine returned.

    Caveats and the pointer belong to the QUESTION, so they are merged here rather than
    trusted to each engine: "which requirements have no answer?" carries the
    completeness caveat whoever computes it, and it is always checkable at `/gaps`.
    """
    caveats = tuple(dict.fromkeys((*entry.caveats, *shaped.caveats)))
    # entry caveats first: they qualify the QUESTION, and an engine cannot drop them.
    assumptions = tuple(dict.fromkeys((*shaped.assumptions,)))
    return {
        "question_id": entry.id,
        "intent": entry.intent,
        "question": entry.question,
        "state": shaped.state,
        "result": shaped.result,
        "caveats": caveats,
        "assumptions": assumptions,
        "pointer": entry.pointer,
        "detail": shaped.detail,
        "blocked_by": shaped.blocked_by or entry.blocked_by,
        "source": shaped.source,
        "truncated": shaped.truncated,
    }


def unmatched(question: str, nearest: Tuple[dict, ...] = ()) -> dict:
    """The honest outcome when nothing matches: the vocabulary is silent, and here is
    what it does cover. Never a low-confidence guess — a wrong answer that looks
    answered is the failure this whole design exists to prevent."""
    return {
        "question_id": "",
        "intent": "",
        "question": question,
        "state": STATE_NO_NAMED_QUESTION,
        "result": None,
        "caveats": (),
        "assumptions": (),
        "pointer": "",
        "detail": (
            "No question in the registry matches this. It is logged so the gap "
            "can be measured."
        ),
        "blocked_by": "",
        "source": "router",
        "truncated": False,
        "nearest": list(nearest),
    }
