"""
The package layering.

`core/` is the domain layer: the canonical knowledge model and the LinkML schema
reader. Both the agents and the app read it; it reads neither. That direction is
the whole reason it moved out of `agents/`, so it is worth asserting rather than
assuming — a single convenience import in the wrong direction would quietly
re-fuse the layers.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from tests.conftest import REPO_ROOT

CORE = REPO_ROOT / "core"


def _core_sources():
    return sorted(path for path in CORE.rglob("*.py")
                  if "__pycache__" not in path.parts)


def test_core_has_the_modules_we_think_it_has():
    """A rename that silently half-applied would otherwise pass every other test."""
    assert (CORE / "__init__.py").exists()
    assert (CORE / "ontology.py").exists()
    for module in ("model", "serialise", "ingest", "review", "reconcile", "store", "rdf"):
        assert (CORE / "knowledge" / f"{module}.py").exists(), module


def test_core_does_not_import_the_agents_or_the_app():
    """The one-way rule, checked as source rather than trusted as convention."""
    offenders = []
    for path in _core_sources():
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("from agents") or stripped.startswith("import agents"):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{number} imports agents")
            if stripped.startswith("from app") or stripped.startswith("import app"):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{number} imports app")
    assert offenders == []


def test_agents_and_app_no_longer_reach_into_agents_for_the_domain():
    """`agents.knowledge` and `agents.ontology` must not come back."""
    stale = []
    for path in REPO_ROOT.rglob("*.py"):
        if any(part in {".venv", "__pycache__", ".git"} for part in path.parts):
            continue
        if path.name == Path(__file__).name and path.parent == Path(__file__).parent:
            continue  # this test contains the needles it looks for
        text = path.read_text()
        for needle in ("agents.knowledge.", "agents.knowledge ", "agents.ontology"):
            if needle in text:
                stale.append(f"{path.relative_to(REPO_ROOT)}: {needle.strip()}")
    assert stale == []


def test_the_domain_layer_loads_without_the_agent_or_web_stack():
    """The concrete payoff, asserted rather than hoped for.

    Importing the knowledge model must not drag in the LLM SDK or the web
    framework. If it does, the domain layer is not really independent and the
    Ontology Engineer agent cannot use the schema reader without the agent stack.
    """
    program = (
        "import sys\n"
        "import core.knowledge, core.ontology\n"
        "heavy = [m for m in ('strands', 'flask', 'linkml_runtime') if m in sys.modules]\n"
        "print(','.join(heavy))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", f"core pulled in: {result.stdout.strip()}"


def test_the_schema_reader_is_reachable_from_core_not_agents():
    """It reads the LinkML schemas, so it belongs with the domain, and the
    Ontology Engineer and Domain Context agents will read it from here."""
    assert (CORE / "ontology.py").exists()
    assert not (REPO_ROOT / "agents" / "ontology.py").exists()

    from core.ontology import load_ontology

    assert load_ontology(REPO_ROOT / "ontology").stats()["classes"] == 71


def test_the_canonical_graph_is_reachable_from_core_not_agents():
    assert not (REPO_ROOT / "agents" / "knowledge").exists()

    from core.knowledge import KnowledgeGraph, ReviewLog, graph_from_extraction

    assert KnowledgeGraph and ReviewLog and graph_from_extraction


def test_core_is_registered_for_packaging():
    """A new top-level package that setuptools does not know about installs as a
    source tree and fails for anyone using the package rather than the checkout."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text()
    assert '"core*"' in pyproject or "'core*'" in pyproject
