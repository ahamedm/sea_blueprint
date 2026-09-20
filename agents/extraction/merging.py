"""
Merging extraction results across chunks and passes.

Chunking deliberately overlaps so no fact is lost at a boundary, which means the
same fact WILL arrive two or three times. Passes likewise produce overlapping
views (a `part_of` triple and an element's `parent` field say the same thing).

So merging is not a formality — it is where duplicates are collapsed and partial
observations are combined. The rules are deliberately conservative:

  - never drop content because one observation of it was thin
  - prefer a non-empty scalar over an empty one
  - union list-valued fields rather than picking a winner
  - keep the highest confidence, and the first non-empty source text

The failure mode to avoid is the interesting one: a fact seen clearly in chunk 3
and half-seen in chunk 5 must survive as the clear observation, not be averaged
into mush or overwritten by the weaker one.
"""

from typing import Any, Callable, Dict, Iterable, List, Sequence, Type


# ----------------------------------------------------------------------------
# Triples
# ----------------------------------------------------------------------------

def _norm(value: str) -> str:
    return " ".join((value or "").lower().split())


def triple_key(t) -> tuple:
    """Identity of a triple: subject + predicate + object, case/space-insensitive."""
    subject = t.get("subject") if isinstance(t, dict) else t.subject
    predicate = t.get("predicate") if isinstance(t, dict) else t.predicate
    obj = t.get("object") if isinstance(t, dict) else t.object
    return (_norm(subject), _norm(predicate), _norm(obj))


def merge_triples(groups: Sequence[Sequence[Any]]) -> List[Dict[str, Any]]:
    """Deduplicate triples across chunks/passes, combining their attributes.

    For a repeated triple, keeps the maximum confidence and the longest source
    text — the fuller observation is the more useful one, and a chunk boundary
    often truncates whichever copy happened to land there.
    """
    merged: Dict[tuple, Dict[str, Any]] = {}

    for group in groups:
        for t in group or []:
            as_dict = t if isinstance(t, dict) else (
                t.model_dump() if hasattr(t, "model_dump") else dict(t)
            )
            key = triple_key(as_dict)
            existing = merged.get(key)

            if existing is None:
                merged[key] = dict(as_dict)
                continue

            # confidence: keep the higher
            new_conf = as_dict.get("confidence") or 0.0
            old_conf = existing.get("confidence") or 0.0
            if new_conf > old_conf:
                existing["confidence"] = new_conf

            # source_text: keep the longer (less truncated)
            new_src = as_dict.get("source_text") or ""
            if len(new_src) > len(existing.get("source_text") or ""):
                existing["source_text"] = new_src

            # scalars: fill blanks, never overwrite a value with a value
            for fieldname in ("ontology_class", "requirement_type"):
                if not existing.get(fieldname) and as_dict.get(fieldname):
                    existing[fieldname] = as_dict[fieldname]

    return list(merged.values())


# ----------------------------------------------------------------------------
# Records (elements, connections, technology stacks, styles)
# ----------------------------------------------------------------------------

def _as_dict(record: Any) -> Dict[str, Any]:
    if isinstance(record, dict):
        return dict(record)
    if hasattr(record, "model_dump"):
        return record.model_dump()
    return dict(record)


def _combine(existing: Dict[str, Any], incoming: Dict[str, Any]) -> None:
    """Fold `incoming` into `existing` in place, conservatively."""
    for key, new_value in incoming.items():
        old_value = existing.get(key)

        # list-valued: union, order-preserving
        if isinstance(new_value, list) and isinstance(old_value, list):
            seen = {repr(v).lower() for v in old_value}
            for item in new_value:
                if repr(item).lower() not in seen:
                    old_value.append(item)
                    seen.add(repr(item).lower())
            continue

        # scalars: only fill a blank, never overwrite
        if (old_value is None or old_value == "") and new_value not in (None, "", []):
            existing[key] = new_value


def merge_records(
    groups: Sequence[Sequence[Any]],
    key_fn: Callable[[Dict[str, Any]], Any],
    prefer: Callable[[Dict[str, Any]], float] | None = None,
) -> List[Dict[str, Any]]:
    """Deduplicate records across chunks/passes, combining their fields.

    Args:
        groups: per-chunk/per-pass lists of records (dicts or pydantic models).
        key_fn: identity of a record, e.g. lambda r: r["name"].lower().
        prefer: optional score deciding which observation is the base. Defaults
            to "first seen wins", which is fine when chunks arrive in document
            order and earlier mentions are usually the fuller ones.
    """
    merged: Dict[Any, Dict[str, Any]] = {}
    scores: Dict[Any, float] = {}

    for group in groups:
        for record in group or []:
            as_dict = _as_dict(record)
            key = key_fn(as_dict)
            if key in (None, ""):
                continue

            if key not in merged:
                merged[key] = as_dict
                scores[key] = prefer(as_dict) if prefer else 0.0
                continue

            # A better-scoring observation becomes the base, the old one folds in.
            if prefer:
                score = prefer(as_dict)
                if score > scores[key]:
                    incoming, base = merged[key], as_dict
                    merged[key] = base
                    scores[key] = score
                    _combine(merged[key], incoming)
                    continue

            _combine(merged[key], as_dict)

    return list(merged.values())


# ----------------------------------------------------------------------------
# Convenience keys
# ----------------------------------------------------------------------------

def element_key(record: Dict[str, Any]) -> str:
    return _norm(record.get("name") or "")


def connection_key(record: Dict[str, Any]) -> tuple:
    """A connection is identified by its endpoints and protocol.

    Two services may legitimately connect over both REST and gRPC; allowing
    protocol in the key keeps those distinct instead of collapsing them.
    """
    return (
        _norm(record.get("source") or ""),
        _norm(record.get("target") or ""),
        _norm(record.get("protocol") or ""),
    )


def named_key(record: Dict[str, Any]) -> str:
    return _norm(record.get("name") or "")


def completeness(record: Dict[str, Any]) -> float:
    """Score by how many fields carry values — used to pick the fuller of two
    observations of the same element."""
    score = 0.0
    for value in record.values():
        if isinstance(value, str) and value.strip():
            score += 1.0
        elif isinstance(value, list) and value:
            score += 1.0 + 0.1 * len(value)
        elif value not in (None, "", [], {}):
            score += 1.0
    return score
