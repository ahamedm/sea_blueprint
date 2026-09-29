#!/usr/bin/env python3
"""Test reporting — the suite as areas that answer questions, not 1000 node ids.

WHY THIS EXISTS
---------------
The suite passed a thousand tests and a reader could no longer say what it
*protected*. `pytest -q` prints a progress bar and failures; it has no place to put
"this file exists because two runs of one document disagreed", so the reason a test
exists lives only in the head of whoever wrote it. Volume made that unreadable long
before it made it slow.

WHAT A REPORT IS HERE
---------------------
Three ideas, in order of importance:

1. **An area is a question, not a directory.** `AREAS` below maps every test file to
   exactly one logical area, and each area carries the QUESTION it answers and the
   INTENT behind it. The mapping is data, and it is self-checked: a new test file
   that is not assigned fails `tests/test_test_report.py` instead of quietly
   appearing nowhere. That check is what stops this report drifting from the suite.
2. **Every test gets a sentence.** A test's own docstring is its description; where
   there is none, the name is humanised and the entry is MARKED as name-derived, so
   the gap is visible and countable rather than papered over.
3. **Two layers, one view.** The fast suite (pytest) and the extraction harness
   (gates and toleranced budgets over saved or live model runs) are the two halves
   of the same question. The harness half is attached as a catalogue — running it
   live costs real money, so the report never does that implicitly.

Rendering is deliberately dependency-free and three-way: console for the working
loop, Markdown for a diffable record and for reading, HTML for actually seeing the
shape of the suite. Nothing is fetched from a network, so the HTML opens offline.

Usage:
    .venv/bin/python scripts/test_report.py                 # run the suite, write reports/
    .venv/bin/python scripts/test_report.py --catalog       # describe it, run nothing
    .venv/bin/python scripts/test_report.py --only ingest-seams -k connections
    .venv/bin/python scripts/test_report.py --failures-only --format console
    .venv/bin/python scripts/test_report.py --format md --out /tmp
"""

from __future__ import annotations

import argparse
import ast
import html
import importlib.util
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"
DEFAULT_OUT = REPO_ROOT / "reports"

# ============================================================================
# The taxonomy — an area is a QUESTION the suite answers
# ============================================================================


@dataclass(frozen=True)
class Area:
    """One logical area of the suite: what it answers, and which files belong to it.

    `files` is exhaustive on purpose. The self-check in `tests/test_test_report.py`
    fails when a test file is missing from every area or named by two, so adding
    `tests/test_something_new.py` forces a decision about where it belongs instead
    of letting the report silently under-report the suite.
    """

    key: str
    title: str
    question: str
    intent: str
    files: Tuple[str, ...]


AREAS: Tuple[Area, ...] = (
    Area(
        key="foundation",
        title="Platform foundations",
        question="Is the ground the rest of the suite stands on still flat?",
        intent=(
            "The rules that hold the codebase together rather than any one feature: "
            "core/ must not import agents/ or app/, a workspace has an address and a "
            "guarded save, agent configuration resolves to the endpoint and model it "
            "claims, and the project's own records (TODO entries, decisions) stay "
            "valid. Failures here are not feature bugs — they are the reasons other "
            "failures would be untrustworthy."
        ),
        files=(
            "test_layering",
            "test_workspace",
            "test_agent_config",
            "test_todo",
            "test_test_report",
        ),
    ),
    Area(
        key="ontology",
        title="Ontology and vocabularies",
        question="Is the vocabulary extraction is held to well-formed and complete?",
        intent=(
            "The classes, enums, quality model and catalogues that define what MAY be "
            "said. Extraction can only be as good as this layer: a missing range or an "
            "enum the model cannot satisfy shows up later as a fact that classifies as "
            "nothing. Includes the domain-pack overlay, because a vocabulary that loads "
            "for one Initiative and not another is a silent scope change."
        ),
        files=(
            "test_ontology",
            "test_ontology_reference",
            "test_domain_pack",
            "test_quality_model",
            "test_pattern_catalogue",
        ),
    ),
    Area(
        key="knowledge-model",
        title="The canonical graph",
        question="Does a fact survive being keyed, written, saved and loaded?",
        intent=(
            "The knowledge layer's own contract: content-addressed identity, typed "
            "identifiers that are safe to join on, a JSON round trip that loses "
            "nothing review depends on, and a store that separates the working set "
            "from immutable revisions and a frozen baseline. A bug here is worse than "
            "a bad extraction — it corrupts corrected facts too."
        ),
        files=(
            "test_identity",
            "test_serialise",
            "test_store",
            "test_store_contract",
        ),
    ),
    Area(
        key="ingest-seams",
        title="Ingest and the seams",
        question="Is anything the model produced dropped, mangled or impossible once stored?",
        intent=(
            "Where extraction output becomes knowledge, and where every defect of the "
            "form 'the model did it right and we lost it' has lived: connections never "
            "read, findings discarded, a representation mismatch across a seam that "
            "threw away a paid-for run, a reflexive fact that acquired human authority. "
            "The tests here are deliberately boring and structural, because this is the "
            "layer that must be boring for anything above it to be true."
        ),
        files=(
            "test_connections_ingest",
            "test_output_consumption",
            "test_post_extraction_failure",
            "test_irreflexive_guard",
            "test_completeness_reporting",
            "test_requirements_chunking",
            "test_merge",
            "test_design_ingest",
        ),
    ),
    Area(
        key="extraction",
        title="Extraction agents and run reliability",
        question="Do the passes produce well-shaped output, on budget, on the configured endpoint?",
        intent=(
            "The agents themselves: pass schemas and cross-chunk repair, deterministic "
            "classification, the bounds that stop a model looping, the endpoint contract "
            "that decides which key and flags a call carries, prompt budget, token and "
            "cost accounting, and the harness that gates all of it. The expensive, "
            "stochastic layer — so the tests here assert shape and bounds, and leave "
            "quality to the harness's toleranced budgets."
        ),
        files=(
            "test_containment_repair",
            "test_aspect_guards",
            "test_quality_classifier",
            "test_generation_bounds",
            "test_hosted_endpoint",
            "test_prompt_budget",
            "test_semantic_accuracy",
            "test_extraction_harness",
            "test_usage_accounting",
            "test_design_agent",
            "test_design_digest",
        ),
    ),
    Area(
        key="review",
        title="Review gate and audit trail",
        question="Can a human vouch for, correct or remove a fact — and can the graph prove it?",
        intent=(
            "The half of the product that makes the graph trustworthy: verify, dispute, "
            "correct, retire, and the decision log that answers 'why does the graph look "
            "like this?'. Also the reconciliation that binds a reference to a node, and "
            "the reports (realization, quality census) that decide what a gap IS. A "
            "review gate that accepts something impossible is the failure this area "
            "exists to prevent."
        ),
        files=(
            "test_review",
            "test_review_bulk",
            "test_review_exclusions",
            "test_retire",
            "test_reconcile",
            "test_reconcile_cli",
            "test_realization",
            "test_quality_census",
        ),
    ),
    Area(
        key="views",
        title="Views and projections",
        question="Does the graph render as an artefact without inventing or hiding structure?",
        intent=(
            "What an architect actually reads: the C4 specification view and its "
            "notation, the merged map's lenses, and the pure projection functions both "
            "draw from. A diagram is persuasive, so the tests here are as concerned "
            "with what a view REFUSES to draw (unplaced, inferred, unmapped) as with "
            "what it shows."
        ),
        files=(
            "test_projections",
            "test_c4_view",
            "test_viewpoint_map",
        ),
    ),
    Area(
        key="app",
        title="The web app",
        question="Does the UI behave over HTTP the way the domain layer promises?",
        intent=(
            "Routes, templates and the small amount of glue that turns domain "
            "operations into pages: ingest and its guards, the review queue's layout, "
            "the Design Assistant's pages, and workspace-addressed requests. These run "
            "against the real Flask app with a fake extractor, so a break here is a "
            "break a user would meet."
        ),
        files=(
            "test_app",
            "test_app_workspace",
            "test_design_app",
            "test_review_ui",
        ),
    ),
    Area(
        key="runs",
        title="Background runs and progress",
        question="Can work outlive the request that started it, and say how it is going?",
        intent=(
            "The asynchronous increment end to end: the job store's compare-and-set, the "
            "versioned run journal and its transport, the progress seam from agent to "
            "sink to SSE, the read side through the app, and the wiring that correlates a "
            "live stream with the run record it eventually becomes. A run nobody is "
            "waiting for is the hardest thing here to keep honest."
        ),
        files=(
            "test_async_runs",
            "test_jobs",
            "test_run_journal",
            "test_run_progress",
            "test_run_progress_routes",
            "test_run_progress_wiring",
            "test_app_run_events",
            "test_sse",
        ),
    ),
)

AREA_BY_KEY = {area.key: area for area in AREAS}
ASSIGNED_FILES = tuple(name for area in AREAS for name in area.files)


# ============================================================================
# Descriptions — derived from the source, never invented
# ============================================================================


@dataclass
class TestDoc:
    """One test function as the source describes it."""

    name: str
    description: str
    from_docstring: bool
    body: str = ""            # the remainder of the docstring, for a detail view


@dataclass
class ModuleDoc:
    """One test module: its file-level intent and the tests it holds."""

    module: str               # dotted-ish name, e.g. "test_ingest_seams"
    path: Path
    title: str
    intent: str
    tests: List[TestDoc] = field(default_factory=list)


def _paragraph(text: str) -> str:
    """The first paragraph of a docstring, whitespace collapsed.

    First paragraph rather than first line: this repo's docstrings start with a
    short claim and then explain it, and both are worth showing — the claim as the
    description, the rest as the detail a reader can open.
    """
    if not text:
        return ""
    block = text.strip().split("\n\n", 1)[0]
    return " ".join(block.split())


def _remainder(text: str) -> str:
    if not text:
        return ""
    parts = text.strip().split("\n\n", 1)
    return _paragraph(parts[1]) if len(parts) > 1 else ""


def humanise(name: str) -> str:
    """A readable sentence from a test name, for tests that carry no docstring.

    Mechanical on purpose. The point is not that a good description can be derived
    from a name — it is that the absence of one is visible and countable, so the
    suite can be improved rather than merely reported on.
    """
    text = name
    for prefix in ("test_", "async_test_"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    text = text.replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else name


def _same_sentence(a: str, b: str) -> bool:
    """Whether two strings say the same thing, ignoring punctuation and case."""
    keep = lambda s: "".join(ch for ch in s.lower() if ch.isalnum())  # noqa: E731
    return bool(keep(a)) and keep(a) == keep(b)


def _module_title_and_intent(doc: str, fallback: str) -> Tuple[str, str]:
    """The heading and the explanatory line for one test module.

    De-duplicated on purpose: most of this repo's module docstrings open with a
    one-line title in their own paragraph, and printing that same sentence under a
    heading that already says it is noise. When that happens the NEXT paragraph is
    the intent, which is the sentence that actually adds something.
    """
    if not doc.strip():
        return fallback, ""
    stripped = doc.strip()
    title = stripped.splitlines()[0].strip().rstrip(".")
    paragraphs = stripped.split("\n\n")
    head = _paragraph(paragraphs[0])
    if len(paragraphs) > 1 and _same_sentence(head, title):
        return title, _paragraph(paragraphs[1])
    return title, head


def describe_module(path: Path) -> ModuleDoc:
    """Read one test module's docstring and every test function's description.

    Source, not the collected pytest objects: this has to work in `--catalog` mode,
    where nothing is imported, and it must stay correct for a file that fails to
    import (a broken test file should still appear in the report, described, and
    marked as an error).
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    module_doc = ast.get_docstring(tree) or ""
    title, intent = _module_title_and_intent(
        module_doc, fallback=path.stem.replace("_", " ").title())
    doc = ModuleDoc(module=path.stem, path=path, title=title, intent=intent)
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        raw = ast.get_docstring(node) or ""
        doc.tests.append(TestDoc(
            name=node.name,
            description=_paragraph(raw) or humanise(node.name),
            from_docstring=bool(_paragraph(raw)),
            body=_remainder(raw),
        ))
    return doc


def describe_suite(directory: Path = TESTS_DIR) -> Dict[str, ModuleDoc]:
    """Every test module that exists, keyed by module name.

    The directory is the source of truth, not `AREAS`: a file that is on disk and
    not in the taxonomy must be *visible* to the self-check, which is the whole
    mechanism that keeps the grouping honest.
    """
    return {path.stem: describe_module(path)
            for path in sorted(directory.glob("test_*.py"))}


# ============================================================================
# The fast layer — run pytest and collect outcomes
# ============================================================================


@dataclass
class Outcome:
    """One test FUNCTION, aggregated over its parametrizations.

    A report is for comprehension, so `test_x[1]` … `test_x[9]` is one row that says
    how many cases it has and how many failed, not nine rows that bury the file's
    shape. The worst outcome across the cases is the row's outcome: a suite where one
    parameter fails must not read as passing.
    """

    nodeid: str
    module: str
    test: str
    outcome: str = "passed"       # passed | failed | error | skipped | xfailed | xpassed
    duration: float = 0.0
    detail: str = ""              # failure/skip text of the worst case, truncated
    cases: int = 0
    params: str = ""

    _RANK = ("passed", "skipped", "xfailed", "xpassed", "failed", "error")

    def absorb(self, outcome: str, duration: float, detail: str, param: str) -> None:
        self.cases += 1
        self.duration = round(self.duration + duration, 4)
        if self._RANK.index(outcome) >= self._RANK.index(self.outcome):
            if outcome != self.outcome or not self.detail:
                self.detail = detail or self.detail
            self.outcome = outcome
        if param:
            seen = self.params.split(", ") if self.params else []
            if param not in seen and len(seen) < 3:
                seen.append(param)
                self.params = ", ".join(seen)


class _Collector:
    """Minimal pytest plugin: one aggregated row per test function.

    Keyed by `module::test`, which is what the taxonomy and the source descriptions
    are keyed by. A bare test name would collide across files — the same mistake the
    report exists to make impossible to miss.
    """

    def __init__(self) -> None:
        self.rows: Dict[str, Outcome] = {}
        self.reruns: Dict[str, int] = {}
        # A module that fails to IMPORT never produces a runtest report, so without
        # this it would be absent from the report entirely — the one failure shape
        # that must never be silent, because it takes a whole file's tests with it.
        self.collect_errors: Dict[str, str] = {}
        self.terminal_output: str = ""

    def pytest_collectreport(self, report) -> None:
        if report.failed:
            module = Path(str(report.nodeid).split("::")[0]).stem
            self.collect_errors[module] = (
                getattr(report, "longreprtext", "") or str(report.longrepr or "")
            )[:2000]

    def pytest_runtest_logreport(self, report) -> None:
        if report.when not in ("setup", "call", "teardown"):
            return
        significant = report.when == "call" or (
            report.when in ("setup", "teardown") and report.outcome != "passed"
        )
        if not significant:
            return
        module_part, _, rest = report.nodeid.partition("::")
        name, _, params = rest.partition("[")
        key = f"{Path(module_part).stem}::{name}"

        outcome = report.outcome
        if report.when != "call" and outcome == "failed":
            # A failure outside the call phase is an ERROR (setup/teardown broke),
            # but a SKIP arrives in the setup phase too and is not a failure. The
            # distinction matters: a report that calls its skips "failed" is a report
            # nobody trusts twice.
            outcome = "error"
        if getattr(report, "wasxfail", None):
            outcome = "xfailed" if outcome == "skipped" else "xpassed"

        detail = ""
        if outcome in ("failed", "error"):
            detail = (getattr(report, "longreprtext", "") or str(report.longrepr or ""))[:4000]
        elif outcome in ("skipped", "xfailed"):
            detail = str(report.longrepr or "")[:300]

        row = self.rows.get(key)
        if row is None:
            row = Outcome(nodeid=report.nodeid, module=Path(module_part).stem, test=name)
            self.rows[key] = row
        elif row.outcome == "failed" and outcome == "passed":
            self.reruns[key] = self.reruns.get(key, 0) + 1
        row.absorb(outcome, float(report.duration or 0.0), detail, params.rstrip("]"))


def run_pytest(
    targets: Sequence[str],
    extra_args: Sequence[str] = (),
    collector: Optional[_Collector] = None,
) -> Tuple[int, _Collector, float]:
    """Run pytest in-process and return (exit code, collector, seconds).

    In-process rather than a subprocess so the outcomes, durations and failure text
    are objects instead of parsed stdout. `-o addopts=` clears the project's `-v`
    because this run is for the report; the report does its own printing.

    pytest's OWN terminal output is captured rather than shown: this run exists to
    feed a report, and two summaries interleaved is how a reader stops trusting
    either. The captured text is kept on the collector for the case where the run
    produced no rows at all — the one time there is nothing else to show.
    """
    import contextlib
    import io

    import pytest

    collector = collector or _Collector()
    args = ["-o", "addopts=", "-q", "--no-header", "-p", "no:cacheprovider",
            "--tb=line", *extra_args, *targets]
    buffer = io.StringIO()
    started = time.time()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        code = pytest.main(args, plugins=[collector])
    collector.terminal_output = buffer.getvalue()
    return int(code), collector, round(time.time() - started, 2)


# ============================================================================
# The slow layer — the extraction harness, attached as a catalogue
# ============================================================================


@dataclass
class HarnessCase:
    name: str
    agent: str
    invariants: int
    gates: int
    budgets: int


@dataclass
class HarnessLayer:
    """The extraction harness's own classification, read rather than re-run.

    Running it live costs model calls (minutes and money), so the report refuses to
    do that implicitly. What it can show for free is the shape: which cases exist,
    how many of their invariants are gates versus toleranced budgets, and whether a
    committed baseline exists to compare against.
    """

    cases: List[HarnessCase]
    baseline_cases: List[str]
    baseline_path: str
    note: str = ""


def harness_layer(path: Optional[Path] = None) -> Optional[HarnessLayer]:
    """Import the harness's tables. Returns None if it cannot be read."""
    path = path or (REPO_ROOT / "scripts" / "run_extraction_tests.py")
    if not path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("_harness_for_report", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cases = []
        for case in module.CASES:
            names = [inv.__name__ for inv in case["invariants"]]
            cases.append(HarnessCase(
                name=case["name"],
                agent=case["agent"],
                invariants=len(names),
                gates=sum(1 for n in names if n in module.GATES),
                budgets=sum(1 for n in names if n in module.BUDGETS),
            ))
        baseline = module.load_baseline(module.BASELINE_PATH)
        return HarnessLayer(
            cases=cases,
            baseline_cases=sorted((baseline.get("cases") or {}).keys()),
            baseline_path=str(module.BASELINE_PATH.relative_to(REPO_ROOT)),
        )
    except Exception as exc:                                     # noqa: BLE001
        return HarnessLayer(cases=[], baseline_cases=[],
                            baseline_path="", note=f"harness not readable: {exc}")


# ============================================================================
# The report model
# ============================================================================


@dataclass
class TestEntry:
    name: str
    display: str
    description: str
    from_docstring: bool
    detail_doc: str = ""
    outcome: Optional[str] = None
    duration: float = 0.0
    failure: str = ""
    params: str = ""
    cases: int = 0


@dataclass
class FileEntry:
    module: str
    title: str
    intent: str
    tests: List[TestEntry] = field(default_factory=list)
    collection_error: str = ""

    @property
    def passed(self) -> int:
        return sum(1 for t in self.tests if t.outcome == "passed")

    @property
    def failed(self) -> int:
        # A file that could not be imported counts as one failure, because it is:
        # every test it holds is unknown rather than passing.
        return (1 if self.collection_error else 0) + sum(
            1 for t in self.tests if t.outcome in ("failed", "error"))

    @property
    def skipped(self) -> int:
        return sum(1 for t in self.tests if t.outcome in ("skipped", "xfailed"))

    @property
    def duration(self) -> float:
        return round(sum(t.duration for t in self.tests), 2)


@dataclass
class AreaEntry:
    area: Area
    files: List[FileEntry] = field(default_factory=list)

    @property
    def tests(self) -> List[TestEntry]:
        return [t for f in self.files for t in f.tests]

    @property
    def passed(self) -> int:
        return sum(f.passed for f in self.files)

    @property
    def failed(self) -> int:
        return sum(f.failed for f in self.files)

    @property
    def skipped(self) -> int:
        return sum(f.skipped for f in self.files)

    @property
    def duration(self) -> float:
        return round(sum(f.duration for f in self.files), 2)

    @property
    def described(self) -> int:
        return sum(1 for t in self.tests if t.from_docstring)


@dataclass
class SuiteReport:
    areas: List[AreaEntry]
    ran: bool
    exit_code: int
    duration: float
    generated_at: str
    harness: Optional[HarnessLayer] = None
    note: str = ""

    @property
    def tests(self) -> List[TestEntry]:
        return [t for a in self.areas for t in a.tests]

    @property
    def passed(self) -> int:
        return sum(a.passed for a in self.areas)

    @property
    def failed(self) -> int:
        return sum(a.failed for a in self.areas)

    @property
    def skipped(self) -> int:
        return sum(a.skipped for a in self.areas)

    @property
    def name_derived(self) -> int:
        return sum(1 for t in self.tests if not t.from_docstring and t.outcome is not None)

    @property
    def failures(self) -> List[TestEntry]:
        return [t for t in self.tests if t.outcome in ("failed", "error")]

    @property
    def collection_errors(self) -> List[FileEntry]:
        """Files that failed to import — the failures no per-test hook can see."""
        return [f for a in self.areas for f in a.files if f.collection_error]

    def failed_area(self, area: AreaEntry) -> bool:
        """Whether an area should start expanded: the ones that need attention do."""
        return area.failed > 0


def build_report(
    docs: Dict[str, ModuleDoc],
    collector: Optional[_Collector] = None,
    only: Sequence[str] = (),
    ran: bool = False,
    exit_code: int = 0,
    duration: float = 0.0,
    harness: Optional[HarnessLayer] = None,
    note: str = "",
) -> SuiteReport:
    """Join the taxonomy (questions, files) with descriptions and outcomes.

    Every area is always rendered, even when nothing ran and even when a file is
    absent from disk — a report that hides an area because its tests did not execute
    would answer "what does this suite protect" with "whatever happened to run".
    """
    outcomes: Dict[str, Outcome] = dict(collector.rows) if collector else {}
    areas: List[AreaEntry] = []
    selected = [a for a in AREAS if not only or a.key in only]
    for area in selected:
        entry = AreaEntry(area=area)
        for module_name in area.files:
            doc = docs.get(module_name)
            if doc is None:
                entry.files.append(FileEntry(
                    module=module_name, title=module_name,
                    intent="(file not found on disk — the taxonomy names it)",
                    tests=[],
                ))
                continue
            file_entry = FileEntry(module=module_name, title=doc.title, intent=doc.intent)
            if collector is not None:
                file_entry.collection_error = collector.collect_errors.get(module_name, "")
            for test in doc.tests:
                outcome = outcomes.get(f"{module_name}::{test.name}")
                file_entry.tests.append(TestEntry(
                    name=test.name,
                    display=humanise(test.name),
                    description=test.description,
                    from_docstring=test.from_docstring,
                    detail_doc=test.body,
                    outcome=(outcome.outcome if outcome else None),
                    duration=(outcome.duration if outcome else 0.0),
                    failure=(outcome.detail if outcome else ""),
                    params=(outcome.params if outcome else ""),
                    cases=(outcome.cases if outcome else 0),
                ))
            entry.files.append(file_entry)
        areas.append(entry)
    return SuiteReport(
        areas=areas,
        ran=ran,
        exit_code=exit_code,
        duration=duration,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        harness=harness,
        note=note,
    )


# ============================================================================
# Rendering — console
# ============================================================================


def _bar(passed: int, failed: int, skipped: int, width: int = 24) -> str:
    total = max(1, passed + failed + skipped)
    ok = round(width * passed / total)
    bad = round(width * failed / total)
    rest = width - ok - bad
    return "[" + "#" * ok + "!" * bad + "." * rest + "]"


def render_console(report: SuiteReport, failures_only: bool = False,
                   slowest: int = 5) -> str:
    out: List[str] = []
    mode = "ran the suite" if report.ran else "catalogue only (nothing executed)"
    out.append("=" * 78)
    out.append(f"TEST REPORT — {mode}")
    out.append("=" * 78)
    if report.ran:
        out.append(f"  {len(report.tests)} tests in {report.duration:.2f}s   "
                   f"passed {report.passed}   failed {report.failed}   "
                   f"skipped {report.skipped}   exit {report.exit_code}")
    else:
        out.append(f"  {len(report.tests)} tests across "
                   f"{sum(len(a.files) for a in report.areas)} files")
    out.append(f"  descriptions from a docstring: "
               f"{sum(a.described for a in report.areas)}/{len(report.tests)}"
               + (f"   {report.name_derived} name-derived" if report.name_derived else ""))
    out.append("")

    for area in report.areas:
        head = f"  {area.area.title}  [{area.area.key}]"
        if report.ran:
            head += (f"   {len(area.tests)} tests   "
                     f"{_bar(area.passed, area.failed, area.skipped)}   "
                     f"{area.duration:.1f}s")
        else:
            head += f"   {len(area.tests)} tests in {len(area.files)} files"
        out.append(head)
        out.append(f"      Q: {area.area.question}")
        if failures_only:
            for test in area.tests:
                if test.outcome in ("failed", "error"):
                    out.append(f"      FAIL {test.name} — {test.description}")
        out.append("")

    if report.collection_errors:
        out.append("-" * 78)
        out.append(f"COLLECTION ERRORS ({len(report.collection_errors)}) "
                   f"— a whole file's tests are unknown, not passing")
        out.append("-" * 78)
        for file_entry in report.collection_errors:
            out.append(f"  tests/{file_entry.module}.py")
            first = next((ln for ln in file_entry.collection_error.splitlines()
                          if ln.strip()), "")
            if first:
                out.append(f"    {first.strip()[:150]}")
        out.append("")

    if report.failures:
        out.append("-" * 78)
        out.append(f"FAILURES ({len(report.failures)})")
        out.append("-" * 78)
        for test in report.failures:
            out.append(f"  {test.name}")
            out.append(f"    {test.description}")
            first = next((ln for ln in test.failure.splitlines()
                          if ln.strip() and not ln.strip().startswith("_")), "")
            if first:
                out.append(f"    {first.strip()[:150]}")
        out.append("")

    if report.ran and slowest:
        ranked = sorted(report.tests, key=lambda t: -t.duration)[:slowest]
        out.append(f"SLOWEST {slowest}")
        for test in ranked:
            out.append(f"  {test.duration:7.3f}s  {test.name}")
        out.append("")

    if report.harness is not None:
        layer = report.harness
        out.append("-" * 78)
        out.append("EXTRACTION HARNESS (the expensive layer — catalogue only here)")
        out.append("-" * 78)
        if layer.note:
            out.append(f"  {layer.note}")
        for case in layer.cases:
            out.append(f"  case {case.name:<12} {case.agent:<22} "
                       f"{case.invariants:>2} invariants  "
                       f"{case.gates:>2} gates  {case.budgets:>2} budgets")
        out.append(f"  baseline: {layer.baseline_path} "
                   f"(cases: {', '.join(layer.baseline_cases) or 'none'})")
        out.append("  run it: .venv/bin/python scripts/run_extraction_tests.py "
                   "--validate-only   # free, offline")
        out.append("")

    out.append(f"generated {report.generated_at}")
    return "\n".join(out)


# ============================================================================
# Rendering — Markdown
# ============================================================================


def _md_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _status(entry: TestEntry) -> str:
    if entry.outcome is None:
        return "—"
    return {"passed": "ok", "failed": "FAIL", "error": "ERROR",
            "skipped": "skip", "xfailed": "xfail", "xpassed": "XPASS"}.get(
        entry.outcome, entry.outcome)


def render_markdown(report: SuiteReport) -> str:
    out: List[str] = []
    out.append("# Test report — what the suite protects")
    out.append("")
    out.append(f"Generated {report.generated_at}"
               + (f" · {report.duration:.2f}s · exit {report.exit_code}"
                  if report.ran else " · catalogue only (nothing executed)"))
    out.append("")
    if not report.ran:
        out.append("> This is the catalogue: it reads the test sources and prints what "
                   "each area, file and test is FOR, without running anything. "
                   "Run without `--catalog` to add outcomes.")
        out.append("")
    out.append(f"**{len(report.tests)} tests** across "
               f"**{sum(len(a.files) for a in report.areas)} files** in "
               f"**{len(report.areas)} areas**."
               + (f" Passed **{report.passed}**, failed **{report.failed}**, "
                  f"skipped **{report.skipped}**." if report.ran else ""))
    out.append("")
    out.append("| Area | The question it answers | Files | Tests | Described | Passed | Failed | Skipped | Time |")
    out.append("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for area in report.areas:
        out.append(
            f"| [{area.area.title}](#{area.area.key}) | {_md_cell(area.area.question)} "
            f"| {len(area.files)} | {len(area.tests)} | {area.described}/{len(area.tests)} "
            f"| {area.passed} | {area.failed} "
            f"| {area.skipped} | {area.duration:.1f}s |"
        )
    out.append("")
    out.append("*Described* is how many tests carry their own docstring; the rest show "
               "a description derived from the test name, which is a gap worth closing "
               "in the area that matters most to you.")
    out.append("")

    for area in report.areas:
        out.append(f"<a id=\"{area.area.key}\"></a>")
        out.append(f"## {area.area.title}")
        out.append("")
        out.append(f"**{area.area.question}**")
        out.append("")
        out.append(area.area.intent)
        out.append("")
        for file_entry in area.files:
            out.append(f"### `tests/{file_entry.module}.py` — {file_entry.title}")
            out.append("")
            if file_entry.intent:
                out.append(file_entry.intent)
                out.append("")
            if file_entry.collection_error:
                out.append("> **This file could not be imported** — none of its tests "
                           "ran. Every test below is *unknown*, not passing.")
                out.append("")
                out.append("```")
                out.append(file_entry.collection_error.strip()[:2000])
                out.append("```")
                out.append("")
            out.append("| Test | What it checks | Result | Time |")
            out.append("|---|---|---|---:|")
            for test in file_entry.tests:
                name = test.name + (f"[{test.params}]" if test.params else "")
                if test.cases > 1:
                    name += f" ({test.cases} cases)"
                note = test.description
                if not test.from_docstring:
                    note += " *(description from the name — no docstring yet)*"
                out.append(f"| `{name}` | {_md_cell(note)} | {_status(test)} "
                           f"| {test.duration:.3f}s |")
            out.append("")

    if report.failures:
        out.append("## Failures")
        out.append("")
        for test in report.failures:
            out.append(f"### `{test.name}`")
            out.append("")
            out.append(test.description)
            out.append("")
            if test.failure:
                out.append("```")
                out.append(test.failure.strip()[:2500])
                out.append("```")
                out.append("")

    if report.harness is not None:
        layer = report.harness
        out.append("## The extraction harness — the expensive layer")
        out.append("")
        out.append("Live extraction is minutes and real money per case, so this report "
                   "catalogs the harness instead of running it. Gates fail the run; "
                   "budgets are toleranced against a committed baseline and only warn.")
        out.append("")
        if layer.note:
            out.append(f"> {layer.note}")
            out.append("")
        out.append("| Case | Agent | Invariants | Gates | Budgets |")
        out.append("|---|---|---:|---:|---:|")
        for case in layer.cases:
            out.append(f"| `{case.name}` | {case.agent} | {case.invariants} "
                       f"| {case.gates} | {case.budgets} |")
        out.append("")
        out.append(f"Baseline: `{layer.baseline_path}` — cases "
                   f"{', '.join(layer.baseline_cases) or 'none'}. Regenerate with "
                   "`--update-baseline` (offline, no model call).")
        out.append("")

    out.append("---")
    out.append("")
    out.append("Regenerate with `.venv/bin/python scripts/test_report.py`; describe it "
               "without running anything with `--catalog`. The area taxonomy lives in "
               "`scripts/test_report.py` and is self-checked by "
               "`tests/test_test_report.py`.")
    return "\n".join(out)


# ============================================================================
# Rendering — HTML
# ============================================================================

_HTML_CSS = """
:root { --bg:#0f1115; --panel:#171a21; --ink:#e8eaf0; --dim:#98a0b3; --line:#262b36;
        --ok:#3fb950; --bad:#f85149; --warn:#d29922; --skip:#8b949e; --accent:#58a6ff; }
@media (prefers-color-scheme: light) {
  :root { --bg:#f6f7f9; --panel:#fff; --ink:#1b1f27; --dim:#5a6273; --line:#e3e6ec;
          --ok:#1a7f37; --bad:#cf222e; --warn:#9a6700; --skip:#6e7781; --accent:#0969da; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
       font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
header { padding:28px 32px 12px; }
h1 { margin:0 0 6px; font-size:24px; }
.sub { color:var(--dim); font-size:13px; }
.wrap { padding:0 32px 64px; max-width:1180px; }
.cards { display:flex; flex-wrap:wrap; gap:10px; margin:18px 0 8px; }
.card { background:var(--panel); border:1px solid var(--line); border-radius:10px;
        padding:12px 16px; min-width:112px; }
.card b { display:block; font-size:22px; }
.card span { color:var(--dim); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
.toolbar { display:flex; gap:10px; align-items:center; margin:16px 0; flex-wrap:wrap; }
input[type=search] { flex:1; min-width:220px; padding:9px 12px; border-radius:8px;
        border:1px solid var(--line); background:var(--panel); color:var(--ink); }
button { padding:9px 14px; border-radius:8px; border:1px solid var(--line);
        background:var(--panel); color:var(--ink); cursor:pointer; }
details.area { background:var(--panel); border:1px solid var(--line); border-radius:12px;
        margin:14px 0; overflow:hidden; }
details.area > summary { cursor:pointer; padding:14px 18px; list-style:none;
        display:flex; gap:12px; align-items:baseline; flex-wrap:wrap; }
details.area > summary::-webkit-details-marker { display:none; }
details.area > summary .title { font-weight:650; font-size:16px; }
details.area > summary .q { color:var(--dim); font-style:italic; }
details.area > summary .counts { margin-left:auto; color:var(--dim); font-size:13px;
        white-space:nowrap; }
.areabody { padding:0 18px 18px; border-top:1px solid var(--line); }
.intent { color:var(--dim); margin:14px 0 18px; max-width:88ch; }
.file { margin:18px 0; }
.file h3 { font-size:13px; margin:0 0 4px; font-family:ui-monospace, SFMono-Regular, Menlo, monospace; }
.file .fi { color:var(--dim); font-size:13px; margin:0 0 10px; max-width:88ch; }
table { width:100%; border-collapse:collapse; font-size:13.5px; }
th, td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line);
        vertical-align:top; }
th { color:var(--dim); font-weight:600; font-size:12px; text-transform:uppercase;
     letter-spacing:.04em; }
td.t { font-family:ui-monospace, SFMono-Regular, Menlo, monospace; white-space:nowrap; }
td.d { color:var(--ink); }
td.d .nd { color:var(--warn); font-size:11px; border:1px solid var(--line);
        border-radius:6px; padding:1px 5px; margin-left:6px; white-space:nowrap; }
td.n { text-align:right; color:var(--dim); white-space:nowrap; }
.pill { font-size:11px; padding:2px 7px; border-radius:999px; border:1px solid var(--line); }
.pill.passed { color:var(--ok); } .pill.failed, .pill.error { color:var(--bad); font-weight:700; }
.pill.skipped, .pill.xfailed { color:var(--skip); } .pill.none { color:var(--dim); }
pre { background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:10px 12px;
      overflow:auto; font-size:12px; }
details.body > summary { cursor:pointer; color:var(--dim); font-size:12px; }
.failrow td { background:rgba(248,81,73,.07); }
footer { color:var(--dim); font-size:12px; padding:0 32px 40px; }
"""

_HTML_JS = """
function flt(q) {
  q = (q || '').toLowerCase();
  document.querySelectorAll('details.area').forEach(function (a) {
    var shown = 0;
    a.querySelectorAll('tr.testrow').forEach(function (r) {
      var hit = !q || r.dataset.text.indexOf(q) >= 0;
      r.style.display = hit ? '' : 'none';
      if (hit) shown++;
    });
    a.querySelectorAll('.file').forEach(function (f) {
      var any = Array.prototype.some.call(f.querySelectorAll('tr.testrow'),
        function (r) { return r.style.display !== 'none'; });
      f.style.display = any ? '' : 'none';
    });
    if (q) { a.open = shown > 0; }
  });
}
"""


def _pill(outcome: Optional[str]) -> str:
    if outcome is None:
        return '<span class="pill none">not run</span>'
    return f'<span class="pill {outcome}">{html.escape(outcome)}</span>'


_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")


def _prose(text: str) -> str:
    """Escape prose, then honour the two inline conventions this repo writes in.

    The docstrings are Markdown by habit — `` `part_of` `` and occasional `**bold**`.
    Escaping alone would show the backticks; rendering them is what makes the report
    read like the source it describes instead of like a raw dump of it.
    """
    escaped = html.escape(text)
    escaped = _INLINE_CODE.sub(r"<code>\1</code>", escaped)
    return _BOLD.sub(r"<strong>\1</strong>", escaped)


def render_html(report: SuiteReport) -> str:
    parts: List[str] = []
    add = parts.append
    add("<!DOCTYPE html>")
    add('<html lang="en"><head><meta charset="utf-8">')
    add('<meta name="viewport" content="width=device-width, initial-scale=1">')
    add("<title>Test report — what the suite protects</title>")
    add(f"<style>{_HTML_CSS}</style>")
    add("</head><body>")
    add("<header><h1>Test report — what the suite protects</h1>")
    mode = (f"ran the suite in {report.duration:.2f}s · exit {report.exit_code}"
            if report.ran else "catalogue only — nothing was executed")
    add(f'<div class="sub">Generated {html.escape(report.generated_at)} · '
        f"{html.escape(mode)}</div></header>")
    add('<div class="wrap">')
    add('<div class="cards">')
    for label, value in (
        ("tests", len(report.tests)),
        ("files", sum(len(a.files) for a in report.areas)),
        ("areas", len(report.areas)),
        ("passed", report.passed if report.ran else "—"),
        ("failed", report.failed if report.ran else "—"),
        ("skipped", report.skipped if report.ran else "—"),
        ("name-derived", sum(1 for a in report.areas for t in a.tests
                             if not t.from_docstring)),
    ):
        add(f'<div class="card"><b>{value}</b><span>{label}</span></div>')
    add("</div>")
    add('<p class="intent">Each area below is a <b>question the suite answers</b>, not a '
        "directory — the files under it were grouped by what they protect. A test's "
        "description is its own docstring where it has one; where it does not, the name "
        "is humanised and tagged <b>from the name</b>, so the gap is visible rather than "
        "hidden. Filter with the box, or open an area to read it.</p>")

    add('<div class="toolbar">'
        '<input type="search" placeholder="Filter tests and descriptions…" '
        'oninput="flt(this.value)">'
        '<button onclick="document.querySelectorAll(\'details.area\').forEach('
        'a=>a.open=true)">Expand all</button>'
        '<button onclick="document.querySelectorAll(\'details.area\').forEach('
        'a=>a.open=false)">Collapse all</button></div>')

    for area in report.areas:
        open_attr = " open" if report.failed_area(area) else ""
        counts = (f"{len(area.tests)} tests · {area.passed} passed · "
                  f"{area.failed} failed · {area.duration:.1f}s"
                  if report.ran else f"{len(area.tests)} tests · {len(area.files)} files")
        counts += f" · {area.described}/{len(area.tests)} described"
        add(f'<details class="area"{open_attr}><summary>'
            f'<span class="title">{html.escape(area.area.title)}</span>'
            f'<span class="q">{html.escape(area.area.question)}</span>'
            f'<span class="counts">{html.escape(counts)}</span></summary>')
        add('<div class="areabody">')
        add(f'<p class="intent">{_prose(area.area.intent)}</p>')
        for file_entry in area.files:
            described = sum(1 for t in file_entry.tests if t.from_docstring)
            add('<div class="file">')
            add(f"<h3>tests/{html.escape(file_entry.module)}.py — "
                f"{html.escape(file_entry.title)}</h3>")
            if file_entry.intent:
                add(f'<p class="fi">{_prose(file_entry.intent)}</p>')
            if file_entry.collection_error:
                add('<p class="fi" style="color:var(--bad)"><b>This file could not be '
                    'imported</b> — none of its tests ran, so every row below is '
                    'unknown rather than passing.</p>')
                add(f"<pre>{html.escape(file_entry.collection_error[:2000])}</pre>")
            add("<table><thead><tr><th>Test</th><th>What it checks</th>"
                "<th>Result</th><th>Time</th></tr></thead><tbody>")
            for test in file_entry.tests:
                search = f"{test.name} {test.description} {file_entry.module}".lower()
                fail = ' class="failrow"' if test.outcome in ("failed", "error") else ""
                name = test.name + (f"[{test.params}]" if test.params else "")
                note = _prose(test.description)
                if not test.from_docstring:
                    note += '<span class="nd">from the name</span>'
                if test.cases > 1:
                    note += f'<span class="nd">{test.cases} cases</span>'
                if test.detail_doc:
                    note += (f'<details class="body"><summary>more</summary>'
                             f"{html.escape(test.detail_doc)}</details>")
                detail = ""
                if test.failure:
                    detail = f'<pre>{html.escape(test.failure[:2500])}</pre>'
                add(f'<tr class="testrow"{fail} data-text="{html.escape(search)}">'
                    f'<td class="t">{html.escape(name)}</td>'
                    f"<td class=\"d\">{note}{detail}</td>"
                    f"<td>{_pill(test.outcome)}</td>"
                    f'<td class="n">{test.duration:.3f}s</td></tr>')
            add("</tbody></table>")
            add("</div>")
        add("</div></details>")

    if report.harness is not None:
        layer = report.harness
        add('<details class="area" open><summary>'
            '<span class="title">Extraction harness — the expensive layer</span>'
            '<span class="q">Do live model runs still satisfy the gates, within '
            'toleranced budgets?</span>'
            '<span class="counts">catalogued, not run</span></summary>'
            '<div class="areabody">')
        add('<p class="intent">Live extraction costs minutes and real money per case, '
            'so this report never runs it implicitly. Gates fail the run; budgets are '
            'compared against a committed baseline and only warn.</p>')
        if layer.note:
            add(f'<p class="fi">{_prose(layer.note)}</p>')
        add("<table><thead><tr><th>Case</th><th>Agent</th><th>Invariants</th>"
            "<th>Gates</th><th>Budgets</th></tr></thead><tbody>")
        for case in layer.cases:
            add(f"<tr><td class=\"t\">{html.escape(case.name)}</td>"
                f"<td>{html.escape(case.agent)}</td><td class=\"n\">{case.invariants}</td>"
                f"<td class=\"n\">{case.gates}</td><td class=\"n\">{case.budgets}</td></tr>")
        add("</tbody></table>")
        add(f'<p class="fi">Baseline: {html.escape(layer.baseline_path)} — cases '
            f'{html.escape(", ".join(layer.baseline_cases) or "none")}. Offline run: '
            "<code>.venv/bin/python scripts/run_extraction_tests.py --validate-only</code>"
            "</p></div></details>")

    add("</div>")
    add(f"<footer>Regenerate with <code>.venv/bin/python scripts/test_report.py"
        f"</code> · describe without running with <code>--catalog</code> · the area "
        f"taxonomy lives in <code>scripts/test_report.py</code> and is self-checked by "
        f"<code>tests/test_test_report.py</code>.</footer>")
    add(f"<script>{_HTML_JS}</script>")
    add("</body></html>")
    return "\n".join(parts)


# ============================================================================
# CLI
# ============================================================================


def _select_targets(only: Sequence[str]) -> List[str]:
    if not only:
        return ["tests"]
    files: List[str] = []
    for key in only:
        area = AREA_BY_KEY.get(key)
        if area is None:
            raise SystemExit(f"unknown area {key!r}; known: {', '.join(AREA_BY_KEY)}")
        files.extend(f"tests/{name}.py" for name in area.files)
    return files


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report the test suite as areas, with intent and outcomes.")
    parser.add_argument("--catalog", action="store_true",
                        help="describe the suite from source and run nothing")
    parser.add_argument("--only", action="append", default=[], metavar="AREA",
                        help=f"limit to one area (repeatable): {', '.join(AREA_BY_KEY)}")
    parser.add_argument("-k", dest="keyword", default="",
                        help="pytest -k expression, passed through")
    parser.add_argument("--failures-only", action="store_true",
                        help="console output: show only failing tests per area")
    parser.add_argument("--slowest", type=int, default=5,
                        help="console: how many slowest tests to list (0 to skip)")
    parser.add_argument("--format", default="all",
                        choices=["all", "console", "md", "html"],
                        help="which renderings to produce (default: all)")
    parser.add_argument("--out", default=str(DEFAULT_OUT),
                        help="directory for report files (default: reports/)")
    parser.add_argument("--no-harness", action="store_true",
                        help="omit the extraction-harness catalogue")
    parser.add_argument("--quiet", action="store_true",
                        help="write files, print only the final summary line")
    args = parser.parse_args(argv)

    unknown = [key for key in args.only if key not in AREA_BY_KEY]
    if unknown:
        # Validated here, before anything touches AREA_BY_KEY: an unknown area is a
        # typo, and a typo should read as a list of the real names, not a KeyError.
        parser.error(f"unknown area(s) {', '.join(unknown)}; "
                     f"known: {', '.join(AREA_BY_KEY)}")

    docs = describe_suite()
    unassigned = sorted(set(docs) - set(ASSIGNED_FILES))
    if unassigned:
        print(f"warning: {len(unassigned)} test file(s) are in no area and are not "
              f"reported: {', '.join(unassigned)}", file=sys.stderr)

    target_docs = docs
    if args.only:
        wanted = {name for key in args.only for name in AREA_BY_KEY[key].files}
        target_docs = {k: v for k, v in docs.items() if k in wanted}

    collector: Optional[_Collector] = None
    code, duration, note = 0, 0.0, ""
    if not args.catalog:
        extra = ["-k", args.keyword] if args.keyword else []
        try:
            code, collector, duration = run_pytest(_select_targets(args.only), extra)
        except Exception as exc:                                 # noqa: BLE001
            # A report is still worth printing when the run cannot start: the
            # catalogue is exactly what a reader needs in that moment.
            note = f"the suite could not run: {type(exc).__name__}: {exc}"
            code, collector = 3, None
        if collector is not None and not collector.rows and not collector.collect_errors:
            # Nothing ran and nothing failed to import: pytest said why, and that
            # reason is the only content the run produced. Keep it.
            tail = [ln for ln in collector.terminal_output.strip().splitlines() if ln.strip()]
            note = "the run collected no tests" + (f": {tail[-1][:300]}" if tail else "")

    harness = None if args.no_harness else harness_layer()
    report = build_report(
        target_docs, collector=collector, only=args.only,
        ran=collector is not None, exit_code=code, duration=duration,
        harness=harness, note=note,
    )

    out_dir = Path(args.out)
    written: List[Path] = []
    if args.format in ("all", "console") and not args.quiet:
        print(render_console(report, failures_only=args.failures_only,
                             slowest=args.slowest))
    if args.format in ("all", "md"):
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "test-report.md"
        path.write_text(render_markdown(report))
        written.append(path)
    if args.format in ("all", "html"):
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "test-report.html"
        path.write_text(render_html(report))
        written.append(path)

    if args.quiet or args.format != "console":
        state = "ran" if report.ran else "catalogued"
        print(f"{state} {len(report.tests)} tests in {len(report.areas)} areas "
              f"({report.passed} passed, {report.failed} failed, "
              f"{report.skipped} skipped)"
              + (f" — {', '.join(str(p) for p in written)}" if written else ""))
    if report.note:
        print(f"note: {report.note}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
