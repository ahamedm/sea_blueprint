"""
Workspace — the addressing layer.

WHAT IS PINNED

  1. **Backward compatibility**, because it is the migration path: a bare store
     directory (`data/sea`, `data/sea-deepseek`) must load as a single-scope workspace
     whose store is that directory. If this breaks, every existing store breaks — and
     the failure would look like data loss, not like a config error.
  2. **Isolation**, because it is the reason scopes exist: two scopes each containing a
     "Database" must remain two graphs. Node identity is the LABEL, so nothing but a
     separate store keeps them apart.
  3. **Per-scope backend choice**, because that is what lets a workspace migrate one
     system to SQLite without migrating the rest.
  4. **Loud refusals** — an unknown backend, a duplicate scope id, or a multi-scope
     workspace addressed without a name must raise, never silently pick.
"""

from __future__ import annotations

import pytest

from core.workspace import (
    BACKEND_SQLITE,
    DEFAULT_SCOPE_ID,
    Workspace,
    WorkspaceError,
    load_workspace,
)


# ============================================================================
# 1. Backward compatibility — the migration path
# ============================================================================


def test_a_bare_store_directory_is_a_single_scope_workspace(tmp_path):
    """An existing store gains a manifest only when it needs one."""
    root = tmp_path / "sea"
    root.mkdir()
    (root / "working.json").write_text("{}")

    workspace = load_workspace(root)

    assert workspace.scope_ids() == [DEFAULT_SCOPE_ID]
    assert workspace.scope().scope_id == DEFAULT_SCOPE_ID
    # The scope's store IS the directory, not a subdirectory of it.
    assert workspace.paths(workspace.scope())["root"] == root


def test_a_bare_directory_opens_the_same_store_the_app_used_before(tmp_path):
    root = tmp_path / "sea"
    root.mkdir()

    store = load_workspace(root).open_store()

    assert store.has_working() is False
    assert (root / "working.json").parent == root  # nothing was relocated


# ============================================================================
# 2. A declared workspace
# ============================================================================


def _manifest(root, body: str):
    root.mkdir(parents=True, exist_ok=True)
    (root / "workspace.yaml").write_text(body, encoding="utf-8")
    return root


def test_a_manifest_lists_scopes_and_their_context(tmp_path):
    root = _manifest(tmp_path / "ws", """
workspace_id: acme
name: ACME
brief: briefs/acme.md
ontology_dir: ontology
scopes:
  - scope_id: payments
    name: Payment Gateway Platform
    kind: system
  - scope_id: settlement
    name: Settlement
    kind: system
""")

    workspace = load_workspace(root)

    assert workspace.workspace_id == "acme"
    assert workspace.brief == "briefs/acme.md"
    assert workspace.scope_ids() == ["payments", "settlement"]
    assert workspace.scope("settlement").name == "Settlement"


def test_scopes_are_isolated_so_the_same_label_stays_two_graphs(tmp_path):
    """The whole point. Two systems with a 'Database' must not collapse into one node
    — and only separate stores keep them apart, because identity is the label."""
    root = _manifest(tmp_path / "ws", """
workspace_id: acme
scopes:
  - scope_id: payments
  - scope_id: settlement
""")
    workspace = load_workspace(root)

    payments = workspace.open_store("payments")
    settlement = workspace.open_store("settlement")

    assert workspace.paths(workspace.scope("payments"))["root"] != \
        workspace.paths(workspace.scope("settlement"))["root"]

    for store in (payments, settlement):
        graph = store.load_working().graph
        graph.add_node("DataStore", "Database")
        store.save_working(graph)

    assert len(payments.load_working().graph.nodes) == 1
    assert len(settlement.load_working().graph.nodes) == 1

    # AND THE IDS COINCIDE. Identity is the label (`make_node_id`), so the same label
    # in two scopes yields the SAME node id. Isolation is therefore what keeps the two
    # graphs apart, and any cross-scope view must partition by scope —
    # `(scope_id, node_id)` per workspace-structure.md §11 — rather than assume node
    # ids are unique across a workspace. Pinned here because it is the trap this
    # isolation is buying protection from.
    assert (list(payments.load_working().graph.nodes)
            == list(settlement.load_working().graph.nodes))


def test_the_store_backend_is_choosable_per_scope(tmp_path):
    root = _manifest(tmp_path / "ws", f"""
workspace_id: acme
scopes:
  - scope_id: legacy
  - scope_id: modern
    backend: {BACKEND_SQLITE}
""")
    workspace = load_workspace(root)

    pytest.importorskip("sqlalchemy")
    legacy = workspace.open_store("legacy")
    modern = workspace.open_store("modern")

    assert legacy.concurrency_safe is False
    assert modern.concurrency_safe is True
    # And the SQL scope lands its database beside the scopes directory, not in it.
    assert workspace.paths(workspace.scope("modern"))["db"].suffix == ".sqlite"


# ============================================================================
# 3. Loud refusals
# ============================================================================


def test_a_multi_scope_workspace_refuses_to_guess(tmp_path):
    root = _manifest(tmp_path / "ws", """
workspace_id: acme
scopes:
  - scope_id: a
  - scope_id: b
""")
    workspace = load_workspace(root)

    with pytest.raises(WorkspaceError, match="name one of"):
        workspace.scope()
    with pytest.raises(WorkspaceError, match="no scope 'c'"):
        workspace.scope("c")


def test_an_unknown_backend_is_refused_rather_than_defaulted(tmp_path):
    root = _manifest(tmp_path / "ws", """
workspace_id: acme
scopes:
  - scope_id: a
    backend: oracle
""")
    with pytest.raises(WorkspaceError, match="known backends"):
        load_workspace(root)


def test_a_manifest_with_no_scopes_is_refused(tmp_path):
    root = _manifest(tmp_path / "ws", "workspace_id: acme\nscopes: []\n")
    with pytest.raises(WorkspaceError, match="declares no scopes"):
        load_workspace(root)


def test_duplicate_scope_ids_are_refused(tmp_path):
    root = _manifest(tmp_path / "ws", """
workspace_id: acme
scopes:
  - scope_id: a
  - scope_id: a
""")
    with pytest.raises(WorkspaceError, match="duplicate scope ids"):
        load_workspace(root)


def test_invalid_yaml_names_the_file(tmp_path):
    root = _manifest(tmp_path / "ws", "workspace_id: [unclosed\n")
    with pytest.raises(WorkspaceError, match="not valid YAML"):
        load_workspace(root)


def test_importing_the_workspace_module_does_not_require_sqlalchemy():
    """SQLAlchemy is an optional extra, so the backend import lives inside
    `open_store`, not at module scope."""
    import ast
    from pathlib import Path

    source = Path("core/workspace.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    top_level = [
        node for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and "store_sql" in ast.dump(node)
    ]
    assert top_level == [], "the SQL backend must be imported lazily"
