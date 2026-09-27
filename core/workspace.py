"""
Workspace — the container that makes a scope addressable.

WHY THIS EXISTS
---------------
Today one process is one store is one graph: `create_app` resolves a single root and
every route reads it (`app/__init__.py:152,166,184`). An architect working on more than
one system has no way to say which one they are looking at, and node identity is the
LABEL (`make_node_id`), so two systems that each contain a "Database" collapse into one
node. This module is the addressing layer that fixes both: a workspace lists scopes, and
a scope resolves to a store.

WHAT A SCOPE IS
---------------
A scope is one system (or product) — the unit of graph, baseline and review. Isolation
is what keeps node identity safe, and the isolation boundary is the scope's store. That
is also why the store backend is chosen **per scope**: a workspace can migrate one
system to SQLite without migrating the rest.

BACKWARD COMPATIBILITY IS A REQUIREMENT
---------------------------------------
Every existing store is a bare directory (`data/sea`, `data/sea-deepseek`). A directory
with no `workspace.yaml` is read as a single-scope workspace whose one scope is the
directory itself, so nothing in `app/`, `scripts/` or the harness has to change to keep
working.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

MANIFEST_NAME = "workspace.yaml"
SCOPES_DIRNAME = "scopes"
DEFAULT_SCOPE_ID = "default"

#: Store backends a manifest may name. `file` is today's layout and stays the default,
#: so a workspace written before the SQL backend existed keeps loading.
BACKEND_FILE = "file"
BACKEND_SQLITE = "sqlite"
BACKENDS = (BACKEND_FILE, BACKEND_SQLITE)

#: Whether a backend can guard a write, declared WITHOUT opening it. A listing
#: endpoint must not create a database as a side effect, and `open_store` costs an
#: engine and a connection pool — so the answer is a property of the backend, not
#: something you ask an instance.
BACKEND_CONCURRENCY_SAFE = {
    BACKEND_FILE: False,     # atomic per file, no lock, no version check
    BACKEND_SQLITE: True,    # version-guarded, `BEGIN IMMEDIATE`
}


def backend_is_concurrency_safe(backend: str) -> bool:
    """False for a backend this build does not know, which is the conservative read:
    assuming a write is guarded when it is not is how lost updates stay hidden."""
    return BACKEND_CONCURRENCY_SAFE.get(backend, False)


class WorkspaceError(Exception):
    """A manifest that cannot be honoured — bad YAML, an unknown backend, no scopes."""


@dataclass
class Scope:
    """One system or product: the unit of graph, baseline and review."""

    scope_id: str
    name: str = ""
    kind: str = "system"          # system | product
    backend: str = BACKEND_FILE
    path: str = ""                # optional override; otherwise derived from the layout
    baseline_ref: str = ""        # the central baseline this scope's work started from

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scope_id": self.scope_id, "name": self.name, "kind": self.kind,
            "backend": self.backend, "path": self.path, "baseline_ref": self.baseline_ref,
        }


@dataclass
class Workspace:
    """A set of scopes an enterprise works on, plus the context they share.

    `brief` is the enterprise one-pager (YB-047): grounding for the agents, not the
    policy register. `ontology_dir` is workspace-level because the vocabulary is
    shared across scopes — the same reason the domain ontology layer is central.
    """

    workspace_id: str
    name: str = ""
    root: Path = field(default_factory=Path)
    scopes: List[Scope] = field(default_factory=list)
    brief: str = ""
    ontology_dir: str = "ontology"

    # -- lookup ------------------------------------------------------------

    def scope(self, scope_id: Optional[str] = None) -> Scope:
        """The named scope, or the only one when a workspace has a single scope.

        Single-scope workspaces are the backward-compatible case, so `scope()` with no
        argument is what the app uses when it has not been given a product to show.
        """
        if scope_id:
            for candidate in self.scopes:
                if candidate.scope_id == scope_id:
                    return candidate
            raise WorkspaceError(
                f"workspace {self.workspace_id!r} has no scope {scope_id!r} "
                f"(known: {[s.scope_id for s in self.scopes]})"
            )
        if len(self.scopes) == 1:
            return self.scopes[0]
        raise WorkspaceError(
            f"workspace {self.workspace_id!r} has {len(self.scopes)} scopes; "
            f"name one of {[s.scope_id for s in self.scopes]}"
        )

    def scope_ids(self) -> List[str]:
        return [s.scope_id for s in self.scopes]

    # -- stores ------------------------------------------------------------

    def paths(self, scope: Scope) -> Dict[str, Path]:
        """Where a scope's data lives, honouring an explicit override in the manifest."""
        if scope.path:
            base = Path(scope.path)
            base = base if base.is_absolute() else self.root / base
        else:
            base = self.root / SCOPES_DIRNAME / scope.scope_id
        if scope.backend == BACKEND_SQLITE:
            return {"db": base.with_suffix(".sqlite")}
        return {"root": base}

    def open_store(self, scope_id: Optional[str] = None):
        """Resolve a scope to a `Store`.

        The backend module is imported HERE rather than at module import: SQLAlchemy is
        an optional extra, and `import core.workspace` must not require it.
        """
        scope = self.scope(scope_id)
        paths = self.paths(scope)

        if scope.backend == BACKEND_SQLITE:
            try:
                from .knowledge.store_sql import SqliteStore
            except ImportError as exc:  # pragma: no cover - depends on the install
                raise WorkspaceError(
                    f"scope {scope.scope_id!r} needs the SQL backend: "
                    f"pip install 'sea-agents[sor-sql]'"
                ) from exc
            return SqliteStore(paths["db"], scope_id=scope.scope_id).ensure()

        from .knowledge import RevisionStore

        return RevisionStore(paths["root"]).ensure()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workspace_id": self.workspace_id, "name": self.name,
            "root": str(self.root), "brief": self.brief,
            "ontology_dir": self.ontology_dir,
            "scopes": [s.to_dict() for s in self.scopes],
        }


# ============================================================================
# Loading
# ============================================================================


def load_workspace(root: str | Path) -> Workspace:
    """Read a workspace, or infer a single-scope one from a bare store directory.

    Inference is the migration path: an existing `data/sea` keeps working untouched,
    and gains a manifest only when it needs a second scope or a SQL backend.
    """
    root = Path(root)
    manifest = root / MANIFEST_NAME

    if not manifest.exists():
        # A bare directory IS a workspace with one scope, whose store is the directory
        # itself — exactly where the current store already lives. `path="."` is what
        # keeps `open_store` pointing at the root rather than a `scopes/` subdirectory
        # that does not exist, which would look like data loss.
        return Workspace(
            workspace_id=root.name or DEFAULT_SCOPE_ID,
            name=root.name or DEFAULT_SCOPE_ID,
            root=root,
            scopes=[Scope(scope_id=DEFAULT_SCOPE_ID, name=root.name or "Default",
                          path=".")],
        )

    try:
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise WorkspaceError(f"{manifest} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkspaceError(f"{manifest} must contain a mapping")

    raw_scopes = data.get("scopes") or []
    if not raw_scopes:
        raise WorkspaceError(
            f"{manifest} declares no scopes; a workspace with no scope has nothing to "
            f"open. Omit the file entirely to get the single-scope default."
        )

    scopes: List[Scope] = []
    for index, raw in enumerate(raw_scopes):
        if isinstance(raw, str):
            raw = {"scope_id": raw}
        if not isinstance(raw, dict) or not raw.get("scope_id"):
            raise WorkspaceError(f"{manifest} scope #{index} needs a `scope_id`")
        backend = str(raw.get("backend") or BACKEND_FILE)
        if backend not in BACKENDS:
            raise WorkspaceError(
                f"{manifest} scope {raw['scope_id']!r} names backend {backend!r}; "
                f"known backends are {list(BACKENDS)}"
            )
        scopes.append(Scope(
            scope_id=str(raw["scope_id"]),
            name=str(raw.get("name") or raw["scope_id"]),
            kind=str(raw.get("kind") or "system"),
            backend=backend,
            path=str(raw.get("path") or ""),
            baseline_ref=str(raw.get("baseline_ref") or ""),
        ))

    duplicates = {s.scope_id for s in scopes if [x.scope_id for x in scopes].count(s.scope_id) > 1}
    if duplicates:
        raise WorkspaceError(f"{manifest} declares duplicate scope ids: {sorted(duplicates)}")

    return Workspace(
        workspace_id=str(data.get("workspace_id") or root.name),
        name=str(data.get("name") or data.get("workspace_id") or root.name),
        root=root,
        scopes=scopes,
        brief=str(data.get("brief") or ""),
        ontology_dir=str(data.get("ontology_dir") or "ontology"),
    )
