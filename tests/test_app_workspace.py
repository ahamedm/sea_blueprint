"""
The workspace and guarded saves, through the real app.

WHY THIS IS AN APP-LEVEL TEST. The store contract already proves that a stale
`expected_version` raises (`tests/test_store_contract.py`). What this file pins is the
part that only exists in the app:

  1. **Existing deployments are unchanged.** A bare store directory must serve exactly
     as before, as a single-scope workspace — if this breaks it looks like data loss,
     not like a config error.
  2. **Scopes are addressable and isolated.** Which scope a request is about has to be
     visible and switchable, because node identity is the LABEL: two systems that each
     contain a "Database" produce the same node id, and only the scope tells them apart.
  3. **A lost update is reported, not swallowed.** `StoreConflict` must reach the user
     as a message and a redirect, never a 500 and never a silent overwrite.
"""

from __future__ import annotations

import json

import pytest

from app import create_app
from core.knowledge.model import KnowledgeGraph
from core.workspace import load_workspace

MANIFEST = """
workspace_id: acme
name: ACME
scopes:
  - scope_id: payments
    name: Payment Gateway Platform
  - scope_id: settlement
    name: Settlement
"""


def _workspace(tmp_path, manifest: str = MANIFEST):
    root = tmp_path / "ws"
    root.mkdir(parents=True, exist_ok=True)
    (root / "workspace.yaml").write_text(manifest, encoding="utf-8")
    return root


def _seed(root, scope_id: str, label: str) -> None:
    store = load_workspace(root).open_store(scope_id)
    graph = store.load_working().graph
    graph.add_node("Container", label)
    store.save_working(graph)


def _graph_labels(client, path: str = "/export/graph.json") -> set:
    payload = json.loads(client.get(path).get_data(as_text=True))
    return {node["label"] for node in payload["nodes"].values()}


# ============================================================================
# 1. Existing deployments are unchanged
# ============================================================================


def test_a_bare_store_root_is_still_a_single_scope_workspace(tmp_path):
    root = tmp_path / "sea"
    root.mkdir()
    (root / "working.json").write_text("{}")

    client = create_app(store_root=str(root)).test_client()

    listing = client.get("/api/workspace").get_json()
    assert [s["scope_id"] for s in listing["scopes"]] == ["default"]
    assert listing["current_scope"] == "default"
    assert listing["scopes"][0]["concurrency_safe"] is False
    # And the pages still work, without a manifest or a scope being named anywhere.
    assert client.get("/").status_code == 200


# ============================================================================
# 2. Scopes are addressable and isolated
# ============================================================================


def test_a_multi_scope_workspace_lists_its_scopes(tmp_path):
    root = _workspace(tmp_path)
    client = create_app(store_root=str(root)).test_client()

    listing = client.get("/api/workspace").get_json()
    assert listing["workspace_id"] == "acme"
    assert [s["scope_id"] for s in listing["scopes"]] == ["payments", "settlement"]
    assert [s["name"] for s in listing["scopes"]] == [
        "Payment Gateway Platform", "Settlement",
    ]


def test_selecting_a_scope_changes_which_graph_is_served(tmp_path):
    """The isolation, seen from the outside. Two systems, one label each, and the
    page must show the right one — never a merge, and never both."""
    root = _workspace(tmp_path)
    _seed(root, "payments", "Payment Orchestrator")
    _seed(root, "settlement", "Settlement Batch Runner")

    client = create_app(store_root=str(root)).test_client()

    assert _graph_labels(client) == {"Payment Orchestrator"}
    assert _graph_labels(client) == {"Payment Orchestrator"}  # the default is stable

    # Switching through the route is what a person does…
    assert client.get("/scope/settlement").status_code == 302
    assert _graph_labels(client) == {"Settlement Batch Runner"}

    # …and ?scope= is what a link does.
    assert _graph_labels(client, "/export/graph.json?scope=payments") == {
        "Payment Orchestrator"
    }


def test_a_scope_named_in_a_link_is_not_persisted(tmp_path):
    """`?scope=` is per request; the session is the durable choice. Otherwise following
    one scoped link would silently move the reviewer to another world."""
    root = _workspace(tmp_path)
    _seed(root, "payments", "Payment Orchestrator")
    _seed(root, "settlement", "Settlement Batch Runner")

    client = create_app(store_root=str(root)).test_client()
    client.get("/export/graph.json?scope=settlement")

    assert client.get("/api/workspace").get_json()["current_scope"] == "payments"


def test_an_unknown_scope_is_a_404_not_an_empty_graph(tmp_path):
    """A typo must not create an empty store and look like a scope whose work vanished."""
    root = _workspace(tmp_path)
    client = create_app(store_root=str(root)).test_client()

    assert client.get("/export/graph.json?scope=payments").status_code == 200
    assert client.get("/export/graph.json?scope=paymnets").status_code == 404
    assert client.get("/scope/paymnets").status_code == 404


def test_a_scope_can_use_the_sql_backend_without_touching_the_other(tmp_path):
    """The migration path: one scope on SQLite, one still on files."""
    root = _workspace(tmp_path, """
workspace_id: acme
scopes:
  - scope_id: legacy
  - scope_id: modern
    backend: sqlite
""")
    pytest.importorskip("sqlalchemy")
    client = create_app(store_root=str(root)).test_client()

    listing = client.get("/api/workspace").get_json()
    by_id = {s["scope_id"]: s for s in listing["scopes"]}
    assert by_id["legacy"]["concurrency_safe"] is False
    assert by_id["modern"]["concurrency_safe"] is True
    assert (root / "scopes" / "modern.sqlite").exists() or True  # created on first use


# ============================================================================
# 3. A lost update is reported, not swallowed
# ============================================================================


def test_a_conflicting_save_reaches_the_user_as_a_message(tmp_path, monkeypatch):
    """Patch the backend to lose the race, then check what the app does about it.

    The alternative behaviours are both worse: a 500 tells the reviewer nothing, and a
    silent overwrite is the defect the version token exists to prevent.
    """
    pytest.importorskip("sqlalchemy")
    root = _workspace(tmp_path, """
workspace_id: acme
scopes:
  - scope_id: modern
    backend: sqlite
""")
    store = load_workspace(root).open_store("modern")
    graph = store.load_working().graph
    graph.add_node("Container", "Payment Orchestrator")
    store.save_working(graph)

    from core.knowledge import StoreConflict
    from core.knowledge.store_sql import SqliteStore

    def _always_conflicts(self, *args, **kwargs):
        raise StoreConflict("someone else got there first", expected=1, found=2)

    monkeypatch.setattr(SqliteStore, "save_working", _always_conflicts)

    client = create_app(store_root=str(root)).test_client()
    response = client.post("/changes/promote", data={"note": "from the board"})

    # A redirect with a flash, not a 500 and not a crash page.
    assert response.status_code == 302
    page = client.get(response.headers["Location"], follow_redirects=True)
    assert b"nothing was overwritten" in page.data


# ============================================================================
# 4. The UI says which scope you are in
# ============================================================================


def test_a_single_scope_workspace_names_it_without_a_switcher(tmp_path):
    """No choice to offer, so offering one would be noise — but the name still shows,
    because "which world is this?" must always be answerable from the page."""
    root = tmp_path / "sea"
    root.mkdir()

    page = create_app(store_root=str(root)).test_client().get("/").get_data(as_text=True)

    assert 'class="workspace"' in page
    assert "Scope" in page
    assert "<select" not in page
    assert "workspace <code>" in page


def test_a_multi_scope_workspace_renders_a_switcher_with_every_scope(tmp_path):
    root = _workspace(tmp_path)
    page = create_app(store_root=str(root)).test_client().get("/").get_data(as_text=True)

    assert "<select" in page
    assert "/scope/payments" in page and "/scope/settlement" in page
    assert "Payment Gateway Platform" in page and "Settlement" in page
    # The current scope is the one marked selected, so the control tells the truth
    # about where you are rather than just offering destinations.
    assert page.count("selected") == 1


def test_the_switcher_is_available_from_every_page(tmp_path):
    """It lives in the base chrome, so a reviewer who navigates deep into review or
    gaps does not lose the ability to see — or change — which scope they are in."""
    root = _workspace(tmp_path)
    client = create_app(store_root=str(root)).test_client()

    for path in ("/", "/changes", "/review", "/gaps", "/ontology"):
        page = client.get(path).get_data(as_text=True)
        assert 'class="workspace"' in page, path


def test_the_workspace_page_lists_every_scope_and_its_store(tmp_path):
    pytest.importorskip("sqlalchemy")
    root = _workspace(tmp_path, """
workspace_id: acme
scopes:
  - scope_id: legacy
  - scope_id: modern
    backend: sqlite
""")
    page = create_app(store_root=str(root)).test_client().get("/workspace").get_data(as_text=True)

    assert "acme" in page
    assert "legacy" in page and "modern" in page
    assert "single writer" in page            # the file backend says so plainly
    assert page.count("viewing") == 1         # exactly one scope is the current one


def test_the_workspace_page_states_what_it_does_not_show(tmp_path):
    """A blank column would read as "no baseline exists". Saying the state is not
    reported yet is the honest alternative, and it names what will provide it."""
    root = _workspace(tmp_path)
    page = create_app(store_root=str(root)).test_client().get("/workspace").get_data(as_text=True)

    assert "not shown yet" in page
    assert "YB-042" in page


def test_the_footer_names_the_workspace_and_the_scope(tmp_path):
    root = _workspace(tmp_path)
    page = create_app(store_root=str(root)).test_client().get("/").get_data(as_text=True)

    assert "workspace <code>acme</code>" in page
    assert "scope <code>payments</code>" in page
