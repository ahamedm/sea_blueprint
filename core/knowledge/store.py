"""
Revision store — the change-management spine.

WORKING SET vs REVISION
-----------------------
The **working set** is the graph under active review. It is autosaved on every
mutation so nothing is lost, but it is not a revision and nothing should diff
against it.

A **revision** is a deliberate, immutable snapshot. A **baseline** is a revision
that has been frozen and is therefore the thing later work is compared to.

Conflating the two is what makes "verified" a flag on a mutable graph rather than
a state — `docs/user-journey.md` gap 3. Keeping them separate means the review
gate can be a continuous activity while the baseline stays a stable reference.

Layout on disk::

    <root>/
      working.json            graph + review log + meta, autosaved
      index.json              revision metadata, newest last
      revisions/<rev_id>.json immutable snapshot
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import StoreConflict
from .model import GraphDelta, KnowledgeGraph, compute_graph_delta, new_revision_id, utc_now
from .review import ReviewLog, ReviewProgress, review_progress
from .serialise import graph_from_dict, graph_to_dict


class BaselineNotReady(Exception):  # noqa: N818 - reads better than BaselineNotReadyError
    """Raised when a revision is asked to become the baseline while the graph
    still contains unverified or disputed assertions.

    Not a bug and not a warning: `docs/user-journey.md` §4 holds that step 5
    (audit) over unverified knowledge reports extraction artifacts as
    architecture gaps. Freezing that as the baseline makes the falsehood
    permanent, so the gate refuses unless a human explicitly overrides.
    """

    def __init__(self, progress: ReviewProgress, revision_id: str = ""):
        self.progress = progress
        self.revision_id = revision_id
        super().__init__(
            f"{progress.outstanding} of {progress.total} assertions are outstanding "
            f"({progress.unverified} unverified, {progress.disputed} disputed)"
        )


# ============================================================================
# Records
# ============================================================================


@dataclass
class Revision:
    """Metadata for one immutable snapshot. The graph itself lives beside it."""

    id: str
    label: str
    created_at: str
    parent_id: str = ""
    kind: str = "draft"  # draft | baseline
    actor: str = ""
    frozen_at: str = ""
    frozen_by: str = ""
    note: str = ""
    initiative_id: str = ""
    documents: List[str] = field(default_factory=list)
    completeness: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    review_summary: Dict[str, int] = field(default_factory=dict)
    progress: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_baseline(self) -> bool:
        return self.kind == "baseline"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "created_at": self.created_at,
            "parent_id": self.parent_id,
            "kind": self.kind,
            "actor": self.actor,
            "frozen_at": self.frozen_at,
            "frozen_by": self.frozen_by,
            "note": self.note,
            "initiative_id": self.initiative_id,
            "documents": list(self.documents),
            "completeness": list(self.completeness),
            "stats": self.stats,
            "review_summary": self.review_summary,
            "progress": self.progress,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Revision":
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


@dataclass
class Snapshot:
    """A graph together with the decisions that produced it."""

    graph: KnowledgeGraph
    log: ReviewLog = field(default_factory=ReviewLog)
    meta: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# Store
# ============================================================================


def _new_revision_id() -> str:
    """Delegates to the shared minter so both backends produce interchangeable ids."""
    return new_revision_id()


class RevisionStore:
    """Filesystem-backed working set and revision history.

    SINGLE-WRITER. Atomic per file, with no lock and no version check across files,
    so it cannot honour a guarded write. `concurrency_safe` says so, and a caller that
    passes `expected_version` is refused rather than quietly unprotected — the server
    runs threaded, and a silent lost update is exactly what the SQL backend exists to
    remove. See `docs/design/workspace-and-sor-enablers.md`.
    """

    WORKING_FILE = "working.json"
    INDEX_FILE = "index.json"
    concurrency_safe = False

    def __init__(self, root: str | os.PathLike):
        self.root = Path(root)
        self.revisions_dir = self.root / "revisions"

    # -- lifecycle ---------------------------------------------------------

    def ensure(self) -> "RevisionStore":
        self.root.mkdir(parents=True, exist_ok=True)
        self.revisions_dir.mkdir(parents=True, exist_ok=True)
        return self

    def _write_atomic(self, path: Path, text: str) -> None:
        self.ensure()
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)  # atomic: a crash never leaves a half-written graph

    def _read_json(self, path: Path) -> Optional[Dict[str, Any]]:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    # -- working set -------------------------------------------------------

    def has_working(self) -> bool:
        return (self.root / self.WORKING_FILE).exists()

    def load_working(self) -> Snapshot:
        data = self._read_json(self.root / self.WORKING_FILE)
        if not data:
            return Snapshot(graph=KnowledgeGraph())
        return Snapshot(
            graph=graph_from_dict(data.get("graph")),
            log=ReviewLog.from_dict(data.get("review_log")),
            meta=data.get("meta") or {},
        )

    def save_working(
        self,
        graph: KnowledgeGraph,
        log: Optional[ReviewLog] = None,
        meta: Optional[Dict[str, Any]] = None,
        expected_version: Optional[int] = None,
    ) -> Snapshot:
        """Write the working set.

        `expected_version` is refused here rather than ignored: this backend has no
        way to check it atomically, and accepting the argument while not enforcing it
        would be worse than not offering it.
        """
        if expected_version is not None:
            raise StoreConflict(
                f"the file backend cannot guard writes (expected_version="
                f"{expected_version}); use a concurrency-safe backend such as "
                f"core.knowledge.store_sql.SqliteStore",
                expected=expected_version,
            )
        log = log or ReviewLog()
        meta = meta or {}
        meta = {**meta, "saved_at": utc_now()}
        payload = {
            "graph": graph_to_dict(graph),
            "review_log": log.to_dict(),
            "meta": meta,
        }
        self._write_atomic(self.root / self.WORKING_FILE, json.dumps(payload, indent=2))
        return Snapshot(graph=graph, log=log, meta=meta)

    def discard_working(self) -> None:
        path = self.root / self.WORKING_FILE
        if path.exists():
            path.unlink()

    # -- revisions ---------------------------------------------------------

    def list_revisions(self) -> List[Revision]:
        """Newest first — the order every UI wants to show.

        Ordered by *insertion*, not by `created_at`. Timestamps have second
        precision, so two commits in the same second (which is normal when a
        script commits after each ingest) would otherwise sort arbitrarily and
        `latest()` would pick the wrong parent.
        """
        return list(reversed(self._read_index()))

    def _read_index(self) -> List[Revision]:
        """File order: oldest first, append-only."""
        data = self._read_json(self.root / self.INDEX_FILE) or {}
        return [Revision.from_dict(r) for r in data.get("revisions", [])]

    def get_revision(self, revision_id: str) -> Optional[Revision]:
        for r in self.list_revisions():
            if r.id == revision_id:
                return r
        return None

    def baselines(self) -> List[Revision]:
        return [r for r in self.list_revisions() if r.is_baseline]

    def latest(self) -> Optional[Revision]:
        revisions = self.list_revisions()
        return revisions[0] if revisions else None

    def load_revision(self, revision_id: str) -> Snapshot:
        data = self._read_json(self.revisions_dir / f"{revision_id}.json")
        if not data:
            raise KeyError(f"no such revision: {revision_id}")
        return Snapshot(
            graph=graph_from_dict(data.get("graph")),
            log=ReviewLog.from_dict(data.get("review_log")),
            meta=data.get("meta") or {},
        )

    def commit(
        self,
        graph: KnowledgeGraph,
        log: Optional[ReviewLog] = None,
        label: str = "",
        actor: str = "",
        note: str = "",
        initiative_id: str = "",
    ) -> Revision:
        """Snapshot the current graph as an immutable revision."""
        log = log or ReviewLog()
        parent = self.latest()
        revision_id = _new_revision_id()

        graph = _stamp(graph, revision_id, parent.id if parent else "")

        progress = review_progress(graph)
        revision = Revision(
            id=revision_id,
            label=label or f"Revision {revision_id[-4:]}",
            created_at=utc_now(),
            parent_id=parent.id if parent else "",
            kind="draft",
            actor=actor,
            note=note,
            initiative_id=initiative_id,
            documents=sorted({r.document_ref for r in graph.runs.values() if r.document_ref}),
            completeness=sorted({r.completeness for r in graph.runs.values()}),
            stats=graph.stats(),
            review_summary=log.summary(),
            progress=progress.to_dict(),
        )

        payload = {
            "revision": revision.to_dict(),
            "graph": graph_to_dict(graph),
            "review_log": log.to_dict(),
            "meta": {"committed_at": revision.created_at, "actor": actor},
        }
        self._write_atomic(
            self.revisions_dir / f"{revision_id}.json", json.dumps(payload, indent=2)
        )
        self._append_index(revision)
        return revision

    def freeze(
        self,
        revision_id: Optional[str] = None,
        label: str = "",
        actor: str = "",
        allow_unverified: bool = False,
    ) -> Revision:
        """Mark a revision as the baseline that later work is compared against."""
        revisions = self.list_revisions()
        if not revisions:
            raise KeyError("no revisions to freeze")
        target = None
        if revision_id:
            target = self.get_revision(revision_id)
            if target is None:
                raise KeyError(f"no such revision: {revision_id}")
        else:
            target = revisions[0]

        snapshot = self.load_revision(target.id)
        progress = review_progress(snapshot.graph)
        if not progress.is_auditable and not allow_unverified:
            raise BaselineNotReady(progress, target.id)

        updated = Revision.from_dict(target.to_dict())
        updated.kind = "baseline"
        updated.frozen_at = utc_now()
        updated.frozen_by = actor
        if label:
            updated.label = label

        data = self._read_json(self.revisions_dir / f"{target.id}.json") or {}
        data["revision"] = updated.to_dict()
        self._write_atomic(self.revisions_dir / f"{target.id}.json", json.dumps(data, indent=2))
        self._rewrite_index([updated if r.id == target.id else r for r in self.list_revisions()])
        return updated

    def diff(self, from_id: str, to_id: str) -> GraphDelta:
        """Structural difference between two revisions."""
        return compute_graph_delta(
            self.load_revision(from_id).graph, self.load_revision(to_id).graph
        )

    def diff_against_revision(self, graph: KnowledgeGraph, revision_id: str) -> GraphDelta:
        """Diff an in-memory graph (e.g. a fresh extraction merge) against a revision.

        This is the diff-and-review primitive: re-extraction is shown as a change
        set rather than silently replacing what a human already reviewed.
        """
        return compute_graph_delta(self.load_revision(revision_id).graph, graph)

    def diff_against_working(self, graph: KnowledgeGraph) -> GraphDelta:
        return compute_graph_delta(self.load_working().graph, graph)

    # -- internals ---------------------------------------------------------

    def _append_index(self, revision: Revision) -> None:
        revisions = [r.to_dict() for r in self._read_index()]
        revisions.append(revision.to_dict())
        self._write_atomic(
            self.root / self.INDEX_FILE, json.dumps({"revisions": revisions}, indent=2)
        )

    def _rewrite_index(self, revisions: List[Revision]) -> None:
        """`revisions` arrives newest-first (as `list_revisions` returns it);
        the file is kept oldest-first so it stays append-ordered."""
        ordered = [r.to_dict() for r in reversed(list(revisions))]
        self._write_atomic(
            self.root / self.INDEX_FILE, json.dumps({"revisions": ordered}, indent=2)
        )


def _stamp(graph: KnowledgeGraph, version_id: str, parent_id: str) -> KnowledgeGraph:
    """Stamp identity onto a graph being snapshotted.

    Mutating the caller's graph would make the in-memory working set disagree
    with what was written, so the stamp is applied to a copy.
    """
    import copy

    stamped = copy.deepcopy(graph)
    stamped.version_id = version_id
    stamped.parent_version_id = parent_id
    return stamped
