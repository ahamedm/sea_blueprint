"""
The extraction harness guards its own classification (the §3.13 lever).

The harness is a measurement, and §1 of the reliability brainstorm records what
happens to an unmeasured measurement: the C4 well-formedness checks found three real
defects and three false positives of their own, and a report that overstates the
damage is worth less than no report. Two things could rot silently:

  1. **An invariant added without a decision.** `inv_*` functions are referenced by
     cases; if a new one is neither a gate nor a budget it could pass by omission.
     The harness aborts on that at startup — this test proves it, and proves the
     committed cases agree with the classification.
  2. **A budget that cannot be compared.** A baseline naming a check the harness no
     longer has would read as "no previous value" and quietly fall back to an
     absolute rule, which is a looser promise than the file appears to make.

And the property the whole design rests on: validation is offline. `--validate-only`
re-checks saved output JSON, so it must never reach an extraction — otherwise
"instant, free, deterministic" is a comment rather than a guarantee.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

HARNESS_PATH = REPO_ROOT / "scripts" / "run_extraction_tests.py"


@pytest.fixture(scope="module")
def harness():
    spec = importlib.util.spec_from_file_location("extraction_harness", HARNESS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_case_references_a_known_invariant(harness):
    assert harness._missing_from_cases(harness.CASES) == []


def test_every_defined_invariant_is_exactly_one_of_gate_or_budget(harness):
    assert harness._classification_problems(harness.CASES) == []


def test_the_committed_baseline_only_names_checks_the_harness_still_has(harness):
    blob = harness.load_baseline(harness.BASELINE_PATH)
    known_cases = {case["name"] for case in harness.CASES}
    known_budgets = set(harness.BUDGETS) | set(harness.C4_RULE_BUDGETS)

    for case_name, values in (blob.get("cases") or {}).items():
        assert case_name in known_cases, f"baseline names a case that is gone: {case_name}"
        for check_name in values:
            assert check_name in known_budgets, (
                f"baseline names {check_name!r} for {case_name!r}, which is no "
                f"longer a budget — the comparison would silently fall back to an "
                f"absolute rule"
            )


def test_a_defect_rule_that_fires_on_a_legitimate_state_is_not_a_gate(harness):
    """The two share rules are budgets, with the reason recorded next to them.

    Gating them would report RUN BROKEN on a run this project has argued is honest
    (YB-053: 25/26 levels stated, one `empty` connections answer counting against
    completeness by design). Pinned so a later reader cannot "tidy" them back into
    gates without meeting the argument.
    """
    assert set(harness.C4_RULE_BUDGETS) == {"c4.levels_stated", "c4.runs_complete"}
    assert all(spec.fallback[0] == "min" for spec in harness.C4_RULE_BUDGETS.values())


def test_validate_only_never_reaches_an_extraction(harness, monkeypatch):
    def must_not_run(*_args, **_kwargs):
        raise AssertionError("--validate-only reached run_case: extraction was attempted")

    monkeypatch.setattr(harness, "run_case", must_not_run)
    monkeypatch.setattr(sys, "argv", ["run_extraction_tests.py", "--validate-only",
                                      "--only", "req_sample"])
    # Exit 0/1/2 all mean the same thing here: it returned without extracting.
    assert harness.main() in (0, 1, 2)


def test_update_baseline_implies_validate_only(harness):
    """Recording numbers must never be the reason a model is called."""
    assert "update_baseline" in HARNESS_PATH.read_text(), "flag renamed; update this test"


# ============================================================================
# The category guards read every ELEMENT POSITION, not just `elements` (ISS-2)
# ============================================================================


def test_a_leak_through_an_attribution_list_is_caught(harness):
    """The guards read `elements` alone, so the same word arriving through `used_by`
    or `applies_to` was unmeasured — and that is where `concept:all_microservices`
    came from, one node per phrasing, on a run reporting COMPLETE.
    """
    out = {
        "elements": [{"name": "Payment Platform"}],
        "design_techniques": [{"name": "Statelessness",
                               "applies_to": ["Payment Platform", "Docker"]}],
    }

    ok, detail = harness.inv_no_tech_leak(None, out)

    assert not ok, detail
    assert "design_techniques.applies_to" in detail


def test_a_leak_through_a_connection_endpoint_is_caught(harness):
    out = {
        "elements": [{"name": "Payment Platform"}],
        "connections": [{"source": "Payment Platform", "target": "Docker"}],
    }

    ok, detail = harness.inv_no_tech_leak(None, out)

    assert not ok and "connections.target" in detail


def test_a_style_named_in_an_attribution_list_is_caught(harness):
    """Reuses the ontology's own `ArchitectureStyleName` vocabulary rather than a
    second copy of the list, so the two cannot drift."""
    out = {
        "elements": [{"name": "Payment Platform"}],
        "architecture_styles": [{"name": "Microservices",
                                 "adopted_by": ["Stateless Modular Microservices"]}],
    }

    ok, detail = harness.inv_no_style_as_element(None, out)

    assert not ok and "Stateless Modular Microservices" in detail


def test_the_category_guards_stay_quiet_on_a_clean_run(harness):
    """A guard that fires on correct output is worse than none — the whole reason
    triple endpoints are excluded is that `uses_technology Docker` is CORRECT."""
    out = {
        "elements": [{"name": "Payment Platform"}],
        "triples": [{"subject": "Payment Platform", "predicate": "uses_technology",
                     "object": "Docker"}],
        "connections": [{"source": "Payment Platform", "target": "Payment Platform"}],
        "design_techniques": [{"name": "Statelessness",
                               "applies_to": ["Payment Platform"]}],
    }

    assert harness.inv_no_tech_leak(None, out)[0]
    assert harness.inv_no_style_as_element(None, out)[0]
    source = HARNESS_PATH.read_text()
    marker = "args.update_baseline:"
    assert marker in source
    assert "args.validate_only = True" in source.split(marker, 1)[1][:400]
