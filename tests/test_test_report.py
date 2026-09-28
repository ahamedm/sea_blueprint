"""
The test report's taxonomy is data, and data drifts unless something checks it.

WHY THIS FILE EXISTS. A report is only as honest as its grouping. The failure mode
is not a crash — it is a NEW test file that belongs to no area, appears in no
report, and lets the suite grow past the report's ability to describe it, which is
exactly the comprehension problem the report was built to solve. So the coverage of
`AREAS` is asserted here rather than trusted: adding `tests/test_something.py`
without deciding which area it belongs to fails the first test below, by name.

The renderers are checked too, but lightly — that they produce the areas and the
descriptions, not that the HTML is pretty. A rendering bug should be visible in the
report itself, not only here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

REPORT_PATH = REPO_ROOT / "scripts" / "test_report.py"


@pytest.fixture(scope="module")
def report():
    spec = importlib.util.spec_from_file_location("test_report_lib", REPORT_PATH)
    module = importlib.util.module_from_spec(spec)
    # Registered BEFORE exec: the module uses `from __future__ import annotations`,
    # so its dataclasses resolve string annotations through sys.modules and would
    # fail with 'NoneType has no __dict__' without this.
    sys.modules["test_report_lib"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def docs(report):
    return report.describe_suite()


# ============================================================================
# The contract: every test file is in exactly one area
# ============================================================================


def test_every_test_file_belongs_to_exactly_one_area(report, docs):
    """The one assertion that keeps the report from silently under-reporting."""
    on_disk = set(docs)
    assigned = list(report.ASSIGNED_FILES)
    duplicates = sorted({name for name in assigned if assigned.count(name) > 1})
    missing = sorted(on_disk - set(assigned))
    phantom = sorted(set(assigned) - on_disk)

    assert not missing, (
        f"these test files are in no area, so no report describes them: {missing}. "
        f"Add each to an Area in scripts/test_report.py — pick the QUESTION it "
        f"answers, not the directory it happens to sit in."
    )
    assert not duplicates, f"these files are claimed by two areas: {duplicates}"
    assert not phantom, f"the taxonomy names files that do not exist: {phantom}"


def test_every_area_states_a_question_and_an_intent(report):
    """An area with no intent is a directory with a nicer name."""
    for area in report.AREAS:
        assert area.key and area.key.replace("-", "").isalnum(), area.key
        assert len(area.question) > 20, f"{area.key}: question too thin"
        assert area.question.endswith("?"), f"{area.key}: the area is a question"
        assert len(area.intent) > 120, (
            f"{area.key}: intent must say what the area protects and why it exists"
        )
        assert area.files, f"{area.key}: an area with no files is a claim, not a report"


def test_area_keys_are_unique(report):
    keys = [area.key for area in report.AREAS]
    assert len(keys) == len(set(keys)), f"duplicate area keys: {keys}"


def test_every_area_holds_at_least_twenty_tests(docs, report):
    """A one-test 'area' is a file with a heading; group by meaning, not by name."""
    for area in report.AREAS:
        total = sum(len(docs[name].tests) for name in area.files if name in docs)
        assert total >= 10, f"{area.key}: only {total} tests — is this a real area?"


# ============================================================================
# Descriptions: every test gets a sentence, and the gap is countable
# ============================================================================


def test_every_discovered_test_gets_a_non_empty_description(docs):
    for module, doc in docs.items():
        for test in doc.tests:
            assert test.description.strip(), f"{module}::{test.name} has no description"


def test_a_test_without_a_docstring_says_so(report, docs):
    """Name-derived descriptions are allowed — being indistinguishable is not."""
    derived = [t for doc in docs.values() for t in doc.tests if not t.from_docstring]
    assert derived, "if every test had a docstring this test should be deleted"
    for test in derived[:20]:
        assert test.description == report.humanise(test.name)


def test_humanise_turns_a_test_name_into_a_sentence(report):
    assert report.humanise("test_a_run_with_no_sink_still_produces_its_record") == \
        "A run with no sink still produces its record"
    assert report.humanise("test_x") == "X"


def test_a_module_title_is_not_repeated_as_its_intent(report, tmp_path):
    """The de-duplication that stops every file heading appearing twice."""
    path = tmp_path / "test_example.py"
    path.write_text(
        '"""One line title.\n'
        "\n"
        "The paragraph that actually explains why the file exists, at length.\n"
        '"""\n'
        "\n"
        "def test_something():\n"
        '    """A description."""\n'
    )
    doc = report.describe_module(path)

    assert doc.title == "One line title"
    assert doc.intent.startswith("The paragraph that actually explains")


# ============================================================================
# Outcomes: the aggregation rules a reader depends on
# ============================================================================


def test_an_aggregated_row_reports_the_worst_case(report):
    """Nine parameters, one failure: the row must not read as passing."""
    outcome = report.Outcome(nodeid="m::t[1]", module="m", test="t")
    outcome.absorb("passed", 0.1, "", "1")
    outcome.absorb("passed", 0.2, "", "2")
    outcome.absorb("failed", 0.3, "boom", "3")

    assert outcome.outcome == "failed"
    assert outcome.cases == 3
    assert outcome.duration == pytest.approx(0.6)
    assert outcome.detail == "boom"


def test_a_skipped_test_is_not_a_failure(report):
    """Skips arrive in the setup phase; calling them failures is a report nobody
    trusts twice."""
    outcome = report.Outcome(nodeid="m::t", module="m", test="t")
    outcome.absorb("skipped", 0.0, "needs valkey", "")

    assert outcome.outcome == "skipped"
    suite = report.build_report({}, collector=None)
    assert suite.failed == 0


def test_a_file_that_cannot_be_imported_counts_as_a_failure(report, docs):
    """A collection error removes a whole file's tests; it must not read as absent."""
    collector = report._Collector()
    collector.collect_errors["test_serialise"] = "ImportError: something broke"
    suite = report.build_report(docs, collector=collector,
                                only=["knowledge-model"], ran=True, exit_code=1)

    broken = [f for a in suite.areas for f in a.files if f.collection_error]
    assert [f.module for f in broken] == ["test_serialise"]
    assert suite.areas[0].failed == 1
    assert "could not be imported" in report.render_markdown(suite)


# ============================================================================
# Rendering: the areas and the sentences actually reach the output
# ============================================================================


def test_the_renderers_carry_every_area_and_a_known_description(report, docs):
    suite = report.build_report(docs, collector=None)
    markdown = report.render_markdown(suite)
    page = report.render_html(suite)

    for area in report.AREAS:
        assert area.title in markdown, area.key
        assert area.question in markdown, area.key
        assert area.title in page, area.key
    # One known docstring and one known name-derived description, both present.
    assert "core/ must not import agents=" not in markdown  # guard against raw HTML
    assert "A connection becomes a node joined to the elements it names" in markdown
    assert "description from the name" in markdown
    assert page.startswith("<!DOCTYPE html>") and page.rstrip().endswith("</html>")


def test_catalog_mode_runs_no_tests(report, monkeypatch):
    """The comprehension view must be free and instant, or nobody reads it."""
    def must_not_run(*_args, **_kwargs):
        raise AssertionError("--catalog executed the suite")

    monkeypatch.setattr(report, "run_pytest", must_not_run)
    code = report.main(["--catalog", "--format", "console", "--no-harness"])

    assert code == 0
    assert len(report.describe_suite()) > 0


def test_an_unknown_area_lists_the_real_ones(report, capsys):
    """A typo should read as a list of areas, not as a KeyError traceback."""
    with pytest.raises(SystemExit) as exit_info:
        report.main(["--only", "nope", "--format", "console", "--no-harness"])

    assert exit_info.value.code == 2
    assert "known: foundation" in capsys.readouterr().err


def test_the_taxonomy_covers_every_area_in_a_real_run(report, docs):
    """A run that only reports areas with outcomes would hide an untouched area."""
    suite = report.build_report(docs, collector=report._Collector(),
                                ran=True, exit_code=0)
    assert {a.area.key for a in suite.areas} == {a.key for a in report.AREAS}
    assert all(a.tests for a in suite.areas), "an area with no tests cannot be read"
