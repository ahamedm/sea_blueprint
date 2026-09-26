"""
The headless reconciliation CLI.

Reconciliation is the one judgement the platform must not make on its own, so the
CLI's contract is deliberately narrow: it proposes, it reports what it declined,
and it writes nothing unless `--apply` says so. These tests pin that contract
against a real working set on disk rather than against the script's internals —
the failure they guard against is a "dry run" that quietly saves, or an `--apply`
that binds a reference the threshold should have refused.

The graph is built end to end through `graph_from_extraction` and `merge_graphs`,
so the references under test are the ones ingest actually leaves open: a
verbatim-named target is already a link by the time merge finishes, and only a
paraphrase or a citation with nothing behind it is offered for binding.

No model and no network are involved.
"""

from __future__ import annotations

import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from core.knowledge import RevisionStore, graph_from_extraction, merge_graphs
from core.knowledge.model import STATUS_SUPERSEDED, STATUS_VERIFIED
from tests.conftest import REPO_ROOT

SCRIPT = REPO_ROOT / "scripts" / "reconcile.py"


def _load_cli():
    """Load `scripts/reconcile.py` by path — `scripts/` is not an installed package."""
    spec = importlib.util.spec_from_file_location("sea_reconcile_cli", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


# ============================================================================
# Fixture: three open references, one of each outcome
# ============================================================================


def _merged_graph():
    """A REQ document plus an ARC document that cites it three ways.

    Deliberately one reference of each kind the report has to distinguish:

      * a paraphrase that clears the threshold (`Payment Acceptance Service` ->
        `Payment Acceptance`, 0.92 as containment) — the one an `--apply` binds;
      * a weak overlap that does not (`Payment Handling`, 0.45) — declined, and
        it must stay open;
      * a goal reference with no `BusinessGoal` in the graph at all — no
        candidate of the expected kind, so bulk may not invent one.
    """
    requirements = {
        "entities": [
            {
                "name": "Payment Acceptance",
                "ontology_class": "FunctionalRequirement",
                "requirement_id": "FR-PM-001",
            },
            {
                "name": "Settlement Initialization",
                "ontology_class": "FunctionalRequirement",
                "requirement_id": "FR-PM-002",
            },
        ],
        "triples": [],
    }
    architecture = {
        "elements": [
            {"name": "Payment Orchestrator", "element_type": "Container"},
            {"name": "Settlement Worker", "element_type": "Container"},
        ],
        "triples": [
            {
                "subject": "Payment Orchestrator",
                "predicate": "implements_requirement",
                "object": "Payment Acceptance Service",
                "confidence": 0.6,
            },
            {
                "subject": "Settlement Worker",
                "predicate": "implements_requirement",
                "object": "Payment Handling",
                "confidence": 0.5,
            },
            {
                "subject": "Payment Orchestrator",
                "predicate": "traces_to_goal",
                "object": "Delight Customers",
                "confidence": 0.4,
            },
        ],
    }
    req_graph, _ = graph_from_extraction(
        requirements,
        {"document_type": "requirements", "model_id": "test"},
        document_ref="prd.md",
    )
    arch_graph, _ = graph_from_extraction(
        architecture,
        {"document_type": "architecture", "model_id": "test"},
        document_ref="arch.md",
    )
    return merge_graphs(req_graph, arch_graph)


@pytest.fixture
def seeded_store(tmp_path):
    """A working set on disk holding the fixture graph, as a real run would leave it."""
    root = tmp_path / "sea"
    RevisionStore(root).ensure().save_working(_merged_graph())
    return root


def _load(root: Path):
    return RevisionStore(root).ensure().load_working()


def _fingerprint(root: Path):
    """Every file under the store, by content. A dry run must not change one byte."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


# ============================================================================
# Dry run is the default
# ============================================================================


def test_a_dry_run_proposes_but_writes_nothing(seeded_store, capsys):
    before = _fingerprint(seeded_store)

    assert cli.main(["--store-root", str(seeded_store)]) == 0
    output = capsys.readouterr().out

    assert _fingerprint(seeded_store) == before
    assert "DRY RUN" in output
    assert "nothing was written" in output
    # The proposals are still shown — a dry run reports a hypothetical, it does
    # not refuse to do the work.
    assert "Payment Acceptance Service" in output
    assert "bulk resolve (threshold 0.75)" in output

    graph = _load(seeded_store).graph
    assert {a.value for a in graph.unresolved_references()} == {
        "Payment Acceptance Service",
        "Payment Handling",
        "Delight Customers",
    }


# ============================================================================
# Apply
# ============================================================================


def test_apply_binds_the_reference_above_threshold_and_persists(seeded_store, capsys):
    assert cli.main(["--store-root", str(seeded_store), "--apply"]) == 0
    output = capsys.readouterr().out

    snapshot = _load(seeded_store)
    graph = snapshot.graph
    bound = [
        a for a in graph.active() if a.predicate == "implements_requirement" and a.object
    ]
    assert len(bound) == 1
    link = bound[0]
    assert link.object == "functionalrequirement:payment_acceptance"
    assert link.status == STATUS_VERIFIED
    assert link.is_human
    assert link.provenance.asserted_by == "architect"

    # The literal the document actually carried is kept, superseded, for lineage.
    literal = next(
        a for a in graph.assertions.values() if a.value == "Payment Acceptance Service"
    )
    assert literal.status == STATUS_SUPERSEDED
    assert literal.superseded_by == link.id

    # Exactly one decision reached the audit log, so the pass is not silently
    # re-recording the same binding or recording the declined ones.
    assert len(snapshot.log.entries) == 1
    assert snapshot.log.entries[0].assertion_id == literal.id

    assert "BOUND" in output
    assert "WROTE 1 binding(s)" in output
    # Coverage BEFORE and AFTER are both reported, and they differ.
    assert "COVERAGE BEFORE" in output
    assert "COVERAGE AFTER" in output
    assert "realized 0" in output
    assert "realized 1" in output


def test_a_below_threshold_reference_is_reported_and_left_open(seeded_store, capsys):
    assert cli.main(["--store-root", str(seeded_store), "--apply"]) == 0
    output = capsys.readouterr().out

    graph = _load(seeded_store).graph
    weak = next(a for a in graph.unresolved_references() if a.value == "Payment Handling")
    assert weak.object is None
    assert not weak.superseded_by
    assert weak in graph.unresolved_references()

    assert "below threshold 1" in output
    assert "Payment Handling" in output
    assert "0.45" in output
    assert "DECLINED" in output


def test_a_no_candidate_reference_is_reported_and_left_open(seeded_store, capsys):
    assert cli.main(["--store-root", str(seeded_store), "--apply"]) == 0
    output = capsys.readouterr().out

    graph = _load(seeded_store).graph
    goal = next(a for a in graph.unresolved_references() if a.value == "Delight Customers")
    assert goal.object is None

    assert "no candidate    1" in output or "no candidate 1" in output
    assert "Delight Customers" in output
    assert "BusinessGoal" in output


# ============================================================================
# Threshold, actor, limit
# ============================================================================


def test_a_stricter_threshold_declines_everything_and_writes_nothing(seeded_store, capsys):
    """The route's rule, kept: persist only when something was resolved.

    At threshold 1.0 even the 0.92 containment match is declined, so `--apply`
    must leave the store byte-identical rather than rewrite it for no change.
    """
    before = _fingerprint(seeded_store)

    assert cli.main(["--store-root", str(seeded_store), "--apply", "--threshold", "1.0"]) == 0
    output = capsys.readouterr().out

    assert _fingerprint(seeded_store) == before
    assert "Nothing cleared the threshold" in output
    assert len(_load(seeded_store).log.entries) == 0


def test_the_actor_defaults_to_the_reviewer_environment(seeded_store, monkeypatch):
    monkeypatch.setenv("SEA_REVIEWER", "headless-bot")

    assert cli.main(["--store-root", str(seeded_store), "--apply"]) == 0

    graph = _load(seeded_store).graph
    link = next(
        a for a in graph.active() if a.predicate == "implements_requirement" and a.object
    )
    assert link.provenance.asserted_by == "headless-bot"


def test_limit_caps_how_many_proposals_are_printed(seeded_store, capsys):
    assert cli.main(["--store-root", str(seeded_store), "--limit", "1"]) == 0
    output = capsys.readouterr().out

    assert "PROPOSALS — 3 open reference(s)" in output
    assert "2 more proposal(s) not shown (--limit 1)" in output


# ============================================================================
# The web layer stays out of the reconciliation path
# ============================================================================


def test_the_cli_does_not_import_the_web_layer():
    """A headless pass must not need Flask, the agent SDK, or the app package.

    Checked in a fresh interpreter rather than in-process: pytest has already
    imported `app` for other test modules, so `sys.modules` here proves nothing.
    """
    program = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location("
        f"'sea_reconcile_cli', {str(SCRIPT)!r})\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "print(','.join(m for m in ('flask', 'strands', 'app') if m in sys.modules))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", f"reconciliation pulled in: {result.stdout.strip()}"
