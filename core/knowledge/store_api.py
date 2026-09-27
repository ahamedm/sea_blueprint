"""
The store contract — one interface, replaceable backends.

WHY THIS EXISTS
---------------
Today the system of record is JSON files under one directory (`RevisionStore`). That
is single-writer by construction: `_write_atomic` is atomic *per file*, with no lock,
no version check, and a read-modify-write index — and the server serves requests
concurrently, because `app.run` sets `threaded=True` by default. Two reviewers acting
on one scope can lose a decision **today** (`docs/design/system-of-record.md` §2).

The store decision is therefore not "which database", it is "what is the contract a
database has to satisfy". This module names that contract so a second backend can be
checked against it rather than assumed equivalent.

TWO THINGS A BACKEND MUST GET RIGHT
-----------------------------------
1. **Round-trip fidelity.** A graph saved and loaded must be the same graph: status,
   provenance, `superseded_by` and scope all survive. `serialise.py` owns that and its
   `SCHEMA_VERSION` is the boundary; a backend must not reach around it.
2. **Guarded writes, where the backend can.** `save_working(..., expected_version=N)`
   must either apply or raise `StoreConflict` — never silently overwrite. A backend
   that cannot do this atomically declares `concurrency_safe = False` and REJECTS a
   guarded call, so misuse fails loudly instead of losing data quietly.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from .errors import StoreConflict, StoreError
from .model import GraphDelta, KnowledgeGraph, new_revision_id
from .review import ReviewLog
from .store import Revision, Snapshot

__all__ = [
    "Store", "StoreError", "StoreConflict", "new_revision_id",
]


@runtime_checkable
class Store(Protocol):
    """What every system of record must provide.

    Deliberately narrow and whole-graph: the access pattern is "load one scope's graph
    and compute in Python", so the contract moves snapshots and deltas, not rows. That
    is also what keeps a file backend and a relational one interchangeable.
    """

    #: True when the backend can apply a guarded write atomically. False means a
    #: caller must not rely on `expected_version`, and a guarded call will be refused.
    concurrency_safe: bool

    def ensure(self) -> "Store": ...

    # -- working set -------------------------------------------------------

    def has_working(self) -> bool: ...

    def load_working(self) -> Snapshot: ...

    def save_working(
        self,
        graph: KnowledgeGraph,
        log: Optional[ReviewLog] = None,
        meta: Optional[Dict[str, Any]] = None,
        expected_version: Optional[int] = None,
    ) -> Snapshot: ...

    def discard_working(self) -> None: ...

    # -- revisions ---------------------------------------------------------

    def list_revisions(self) -> List[Revision]: ...

    def get_revision(self, revision_id: str) -> Optional[Revision]: ...

    def baselines(self) -> List[Revision]: ...

    def latest(self) -> Optional[Revision]: ...

    def load_revision(self, revision_id: str) -> Snapshot: ...

    def commit(
        self,
        graph: KnowledgeGraph,
        log: Optional[ReviewLog] = None,
        label: str = "",
        actor: str = "",
        note: str = "",
        initiative_id: str = "",
    ) -> Revision: ...

    def freeze(
        self,
        revision_id: Optional[str] = None,
        label: str = "",
        actor: str = "",
        allow_unverified: bool = False,
    ) -> Revision: ...

    # -- comparison --------------------------------------------------------

    def diff(self, from_id: str, to_id: str) -> GraphDelta: ...

    def diff_against_revision(self, graph: KnowledgeGraph, revision_id: str) -> GraphDelta: ...

    def diff_against_working(self, graph: KnowledgeGraph) -> GraphDelta: ...
