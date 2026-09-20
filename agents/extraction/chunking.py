"""
Document chunking for extraction.

Real requirement and architecture documents are LARGE and verbose — tens of
thousands of characters, often more. The small samples in `test_data/` exist to
shape the ontology and sanity-check the pipeline; they are not representative of
what the extractor will meet in production.

So the extractor cannot assume a document fits one call. It must:

  1. split the document into chunks that fit a per-call budget
  2. respect document structure where possible (split on headings, not mid-sentence)
  3. overlap chunks, so content spanning a boundary is captured by at least one
  4. carry a heading breadcrumb, so a chunk knows where it sits in the document

This module does (1)-(4). Chunk counting and merging live in `merging.py`.
"""

from dataclasses import dataclass, field
from typing import List, Optional
import re


@dataclass
class Chunk:
    """One unit of document handed to a single extraction call."""

    index: int
    total: int
    text: str
    heading_path: str = ""          # breadcrumb, e.g. "3. Scope Definition > 3.1 In Scope"
    char_start: int = 0
    char_end: int = 0

    @property
    def label(self) -> str:
        return f"chunk {self.index + 1}/{self.total}"

    def header_note(self) -> str:
        """Context line prepended to a prompt, so the model knows where it is.

        Without this, a chunk lifted from the middle of a document loses the
        heading that gave it meaning — and headings frequently carry the subject
        ("Security Requirements"), which changes how the body should be read.
        """
        if not self.heading_path:
            return ""
        return (
            f"## Document position\n"
            f"This is {self.label} of a larger document.\n"
            f"Section: {self.heading_path}\n"
            f"(Content may continue beyond this chunk; extract only what is here.)\n\n"
        )


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


@dataclass
class _Section:
    level: int
    title: str
    start: int
    body_start: int
    end: int = 0
    parent_path: str = ""


def _split_sections(text: str) -> List[_Section]:
    """Split on markdown headings, recording each section's breadcrumb."""
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [_Section(level=0, title="", start=0, body_start=0, end=len(text))]

    sections: List[_Section] = []

    # Preamble before the first heading is its own section.
    if matches[0].start() > 0:
        sections.append(_Section(level=0, title="", start=0, body_start=0,
                                 end=matches[0].start()))

    for i, m in enumerate(matches):
        level = len(m.group(1))
        title = m.group(2).strip()
        body_start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append(_Section(level=level, title=title, start=m.start(),
                                 body_start=body_start, end=end))
    return sections


def _breadcrumbs(sections: List[_Section]) -> None:
    """Assign a heading path to each section, based on heading nesting."""
    stack: List[tuple] = []          # (level, title)
    for s in sections:
        if s.level == 0 or not s.title:
            continue
        while stack and stack[-1][0] >= s.level:
            stack.pop()
        stack.append((s.level, s.title))
        s.parent_path = " > ".join(t for _, t in stack)


def chunk_document(
    text: str,
    max_chars: int = 7000,
    overlap_chars: int = 500,
    min_chars: int = 800,
) -> List[Chunk]:
    """Split a document into overlapping, structure-aware chunks.

    Args:
        text: the full document.
        max_chars: soft ceiling per chunk. Chosen so a chunk plus a pass-specific
            prompt fits comfortably, leaving room for the response.
        overlap_chars: how much adjacent chunks share, so a fact spanning a
            boundary is not lost by both.
        min_chars: sections smaller than this are merged into the next section
            rather than producing a chunk too small to be worth a call.

    Returns:
        Chunks in document order. A document that fits returns exactly one chunk,
        so callers need no special case for the small path.
    """
    if len(text) <= max_chars:
        return [Chunk(index=0, total=1, text=text,
                      char_start=0, char_end=len(text))]

    sections = _split_sections(text)
    _breadcrumbs(sections)

    # Group sections into chunks under the budget, keeping crumbs attached.
    groups: List[tuple] = []          # (start, end, heading_path)
    cur_start: Optional[int] = None
    cur_end = 0
    cur_path = ""

    def flush():
        nonlocal cur_start, cur_end, cur_path
        if cur_start is not None and cur_end > cur_start:
            groups.append((cur_start, cur_end, cur_path))
        cur_start, cur_end, cur_path = None, 0, ""

    for s in sections:
        if s.end <= s.start:
            continue
        span = s.end - s.start

        # A single oversized section gets split by hard character windows,
        # starting a fresh group for each window.
        if span > max_chars:
            flush()
            pos = s.start
            while pos < s.end:
                stop = min(pos + max_chars, s.end)
                groups.append((pos, stop, s.parent_path or s.title))
                pos = stop - overlap_chars if stop < s.end else s.end
            continue

        if cur_start is None:
            cur_start, cur_end, cur_path = s.start, s.end, s.parent_path or s.title
        elif (s.end - cur_start) <= max_chars:
            cur_end = s.end                      # still fits, extend
            if not cur_path:
                cur_path = s.parent_path or s.title
        else:
            # Would overflow — close the current group, start a new one.
            # Only merge tiny tails forward, never let a group go under min_chars
            # if it can absorb the section.
            if (cur_end - cur_start) < min_chars:
                cur_end = s.end
                flush()
                continue
            flush()
            cur_start, cur_end, cur_path = s.start, s.end, s.parent_path or s.title

    flush()

    # Materialise chunks with overlap applied between neighbours.
    chunks: List[Chunk] = []
    for i, (start, end, path) in enumerate(groups):
        lo = max(0, start - (overlap_chars if i > 0 else 0))
        hi = min(len(text), end)
        body = text[lo:hi]
        if not body.strip():
            continue
        chunks.append(Chunk(index=len(chunks), total=0, text=body,
                            heading_path=path, char_start=lo, char_end=hi))

    if not chunks:                       # defensive: never return nothing
        return [Chunk(index=0, total=1, text=text, char_start=0, char_end=len(text))]

    total = len(chunks)
    for c in chunks:
        c.total = total
    return chunks


def summarise_chunks(chunks: List[Chunk]) -> str:
    """One-line description of a chunking outcome, for logs."""
    if len(chunks) == 1:
        return f"1 chunk ({len(chunks[0].text):,} chars) — fits a single call"
    sizes = [len(c.text) for c in chunks]
    return (f"{len(chunks)} chunks, {min(sizes):,}-{max(sizes):,} chars each, "
            f"{sum(sizes):,} chars total (overlap included)")
