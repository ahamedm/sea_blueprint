"""
Filling an entry's slots from the question, using the graph as the vocabulary.

This is the one place a question's words are matched against the GRAPH rather than
against the registry, and it exists because the alternative is asking a user to name an
element in a second box. "What is inside the Payment Gateway Platform?" already contains
the element — the graph knows what a Payment Gateway Platform is, so it can say which.

LONGEST MATCH WINS, and that is not a detail. A scope holding both `Payment Platform`
and `Payment Gateway Platform` would otherwise resolve a question about the latter to
the former, silently, and answer confidently about the wrong system. The same instinct
that refuses to fuzzy-match labels here applies to the registry as a whole: a wrong bind
presented as an answer is the failure mode, so this matches only names the graph holds,
whole, case-insensitively, and prefers the most specific one.
"""

from __future__ import annotations

from typing import Any, Dict, List

from core.questions import QuestionEntry

#: Slot names the registry may declare, mapped to how they are filled. An entry naming a
#: param this module does not know is a finding in the catalogue's tests, not a silent
#: empty answer — an unfilled slot would otherwise read as "nothing matched".
FILLABLE = ("element",)


def _named_in(question: str, graph: Any) -> List[Any]:
    """Every node whose label or id appears in the question, most specific first."""
    low = (question or "").lower()
    found = []
    for node in graph.nodes.values():
        for candidate in (node.label, node.id):
            text = str(candidate or "").strip()
            if len(text) < 3:
                continue  # a two-letter name would match half the language
            if text.lower() in low:
                found.append((len(text), node))
                break
    found.sort(key=lambda pair: pair[0], reverse=True)
    return [node for _, node in found]


def params_from_question(entry: QuestionEntry, question: str, graph: Any) -> Dict[str, str]:
    """The slots this entry declares, filled from the question where the graph allows."""
    wanted = [p for p in entry.params if p in FILLABLE]
    if not wanted:
        return {}
    named = _named_in(question, graph)
    if not named:
        return {}
    out: Dict[str, str] = {}
    for param in wanted:
        if param == "element":
            out[param] = named[0].label or named[0].id
    return out
