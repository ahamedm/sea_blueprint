"""
Deterministic ingest of the recorded Architecture Decisions (ADRs).

`docs/decisions/ADR-*.md` are human-written records of what was decided and why.
They are the source of truth for `ArchitectureDecision` nodes — an LLM must not
re-derive what a human already recorded. This module parses them and reuses the
same `architecture_decisions` ingest path the Design Assistant's proposals use,
so the two origins cannot drift.

The parse is deliberately conservative: frontmatter is authoritative, and the
body contributes only a short context excerpt. `consequences`,
`alternatives_considered`, `affects_elements` and `supersedes` are NOT inferred
from prose — those richer links come from the Design Assistant's decisions pass,
which is a proposal, not an extraction.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from .ingest import graph_from_extraction, merge_graphs
from .model import SOURCE_HUMAN_ARCHITECT

_FM_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)

# Leading characters that mark structure rather than prose in an ADR body.
_MARKDOWN_MARKERS = frozenset("#>-*|`")

# Where the recorded decisions live, repo-relative. Exposed so the digest and the
# design validator agree on one directory instead of each computing a path.
DEFAULT_DECISIONS_DIR = Path(__file__).resolve().parents[2] / "docs" / "decisions"


def load_adr_records(decisions_dir: str | Path) -> List[Dict[str, Any]]:
    """Parse `docs/decisions/ADR-*.md` into ArchitectureDecision-shaped records.

    Frontmatter is authoritative; the body contributes only a truncated context
    excerpt. A file with no parseable frontmatter is skipped rather than guessed.
    """
    import yaml

    records: List[Dict[str, Any]] = []
    root = Path(decisions_dir)
    if not root.is_dir():
        return records
    for path in sorted(root.glob("ADR-*.md")):
        text = path.read_text()
        match = _FM_RE.match(text)
        if not match:
            continue
        try:
            meta = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            continue
        if not isinstance(meta, dict):
            continue
        title = str(meta.get("title") or "").strip()
        if not title:
            continue
        records.append({
            "id": str(meta.get("id") or "").strip(),
            "title": title,
            "status": str(meta.get("status") or "").strip(),
            "decided_date": str(meta.get("date") or "").strip(),
            "decision": title,
            "context": _first_prose(text[match.end():]),
        })
    return records


def _first_prose(body: str, limit: int = 300) -> str:
    """The first plain paragraph of an ADR body, for the `context` literal.

    Headers, blockquotes, list items and table rows are skipped — they are
    structure, not the forces at play.
    """
    for line in body.splitlines():
        line = line.strip()
        if not line or line[0] in _MARKDOWN_MARKERS:
            continue
        return line[:limit]
    return ""


def ingest_decisions(graph, records, *, document_ref: str = "decisions:ADR"):
    """Write ADR decisions into `graph`, returning the merged graph.

    Reuses `graph_from_extraction` + `merge_graphs` so ADR ingest and the Design
    Assistant's decisions pass share one ingest path. Decisions are stamped
    HUMAN_ARCHITECT — they are a record, not a proposal.
    """
    sub, _run = graph_from_extraction(
        {"architecture_decisions": list(records)},
        {"model_id": "adr-ingest", "model_calls": 0,
         "document_type": "architecture"},
        document_ref=document_ref,
        document_text="ADR records",
        source_type=SOURCE_HUMAN_ARCHITECT,
    )
    return merge_graphs(graph, sub)
