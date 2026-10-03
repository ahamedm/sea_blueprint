"""
Routing a question onto a registry entry, with no model.

This is the wedge the design argues for first: if a bounded keyword map over a declared
vocabulary cannot route the questions people actually ask, a model will not rescue it,
and finding that out costs a day rather than a feature. It is also the classifier's
FALLBACK — a front door whose availability depends on an LLM being up is worse than no
front door, so a router miss or a classifier failure lands here.

SCORING IS DELIBERATELY PARSIMONIOUS
------------------------------------
A keyword hit is a hit: longest keyword first, ties broken by entry order. No stemming,
no fuzzy matching, and no threshold tuning, because a router that half-matches produces
confident wrong routes and the honest outcome (`no_named_question`) is one of the states
this platform renders. Where it misses, the miss is recorded — that log is the evidence
that a question deserves promotion to a registry entry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from core.questions import QuestionEntry, QuestionRegistry

_WORD_RE = re.compile(r"[a-z0-9]+")

#: Words too common to route on. Kept short on purpose: an aggressive stop list would
#: drop the nouns that carry the question ("which", "what" and "does" go; "decision",
#: "gap" and "principle" stay).
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "did", "do", "does", "for",
    "from", "has", "have", "how", "i", "in", "is", "it", "its", "me", "of", "on", "or",
    "our", "please", "show", "so", "tell", "that", "the", "their", "them", "then",
    "there", "these", "they", "this", "to", "us", "was", "we", "were", "what", "when",
    "where", "which", "who", "why", "will", "with", "you", "your",
})


@dataclass(frozen=True)
class Route:
    """A decision, its reason, and what to try instead when it declines."""

    entry: Optional[QuestionEntry]
    score: int
    matched: Tuple[str, ...] = ()
    nearest: Tuple[dict, ...] = ()

    @property
    def routed(self) -> bool:
        return self.entry is not None


def tokens(text: str) -> Tuple[str, ...]:
    return tuple(w for w in _WORD_RE.findall((text or "").lower()) if w not in _STOPWORDS)


def _score(
    entry: QuestionEntry,
    asked: Tuple[str, ...],
    raw_words: frozenset,
) -> Tuple[int, Tuple[str, ...]]:
    """Longest-keyword-first, with multi-word keywords matched by ALL their words.

    A phrase is matched order-independently and across intervening words, because
    requiring contiguity loses the question it was written for: "no architectural
    answer" does not contain "no answer", so a phrase rule based on substring
    containment routed it nowhere. All-words-present is the rule that actually matches
    how people ask — and it keeps the weight, so "what breaks if I retire this" still
    prefers "what breaks" over "retire".

    Phrases match against the UNFILTERED words: stop words are what carry them.
    """
    hits: List[str] = []
    score = 0
    asked_set = set(asked)
    for keyword in entry.keywords:
        key = keyword.lower().strip()
        if not key:
            continue
        if " " in key or "-" in key:
            parts = set(_WORD_RE.findall(key))
            if parts and parts <= raw_words:
                hits.append(key)
                score += len(parts) + 2
            continue
        if key in asked_set:
            hits.append(key)
            score += 1
    return score, tuple(hits)


def route(registry: QuestionRegistry, question: str, *, limit: int = 3) -> Route:
    """The entry that answers `question`, or the nearest few if none does."""
    asked = tokens(question)
    raw_words = frozenset(_WORD_RE.findall((question or "").lower()))
    scored = []
    for order, entry in enumerate(registry.entries):
        score, hits = _score(entry, asked, raw_words)
        if score:
            scored.append((score, -order, entry, hits))
    if not scored:
        return Route(entry=None, score=0, nearest=_nearest(registry, asked, limit))
    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    best = scored[0]
    return Route(entry=best[2], score=best[0], matched=best[3])


def _nearest(registry: QuestionRegistry, asked: Tuple[str, ...], limit: int) -> Tuple[dict, ...]:
    """On a miss, what the vocabulary DOES cover — so the refusal is useful.

    Scored by vocabulary overlap rather than by keywords, so this still says something
    when the question shares no keyword with anything: the words it does share are the
    ones that might be renamed.
    """
    asked_set = set(asked)
    ranked = []
    for order, entry in enumerate(registry.entries):
        words = set(tokens(entry.question)) | {w for k in entry.keywords for w in tokens(k)}
        overlap = len(asked_set & words)
        ranked.append((overlap, -order, entry))
    ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return tuple(
        {"id": e.id, "question": e.question, "overlap": overlap}
        for overlap, _, e in ranked[:limit]
    )
