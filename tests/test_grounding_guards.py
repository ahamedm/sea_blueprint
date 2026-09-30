"""
Grounding guards — is this fact in the document, and is this value in the vocabulary?

WHY THIS FILE EXISTS. The review queue on the live scope is 486 assertions, and most of
it is not judgement: a classification the ontology already enumerates, or words taken
from the document. Two guards and one projection came out of that measurement:

  * `check_names_are_anchored` — §3.6 of the reliability brainstorm, made deterministic.
    A declared name must appear in the document that is supposed to declare it, because
    an invented `External Services` looks exactly like a declared element.
  * `check_quotations_are_grounded` — the tolerant half, for DESCRIPTIONS.
  * `project_review_buckets` — what the queue is made of, so the page stops reading
    "486 unverified" as 486 judgements.

THE TWO HALVES NEED DIFFERENT RULES, and that is measured rather than preferred. Against
`test_data/arch/payment_platform_arch.md` and the saved architecture output
(2026-09-30): **29 of 30 element names appear literally**, so a strict containment rule
discriminates; **0 of 30 descriptions** do, because a description is a summary, so a
strict rule on it would fire on every one and the report would be worthless. At a floor of
20% of a description's long words the same 30 score min 0.60, mean 0.89, none flagged.

So the tests below check BOTH directions: that each rule catches what it is for, and that
it does not fire on a correct graph. A validator that reports a correct graph is worse
than none — a review queue nobody trusts is how a real defect gets waved through.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.architecture_extraction.passes import (
    DesignTechniqueRecord,
    ElementRecord,
    TechnologyStackRecord,
)
from agents.extraction import (
    TECHNIQUE_FIELD_ENUMS,
    TECHNOLOGY_FIELD_ENUMS,
    check_enum_membership,
    check_names_are_anchored,
    check_quotations_are_grounded,
    check_schema_consistency,
    ontology_enum,
)
from app.projections import project_review_buckets

REPO = Path(__file__).resolve().parents[1]
REAL_DOC = REPO / "test_data" / "arch" / "payment_platform_arch.md"


def _elements(*records):
    return [dict(r) for r in records]


# ============================================================================
# 1. Names are anchored strictly
# ============================================================================


def test_a_name_the_document_never_uses_is_flagged():
    """The defect §3.6 is for: a category word emitted as if it were an element."""
    doc = "The Payment Orchestrator calls PostgreSQL and Valkey."

    flags = check_names_are_anchored(
        _elements({"name": "Payment Orchestrator"}, {"name": "External Services"}), doc)

    assert [f.subject for f in flags] == ["External Services"]
    assert flags[0].kind == "unanchored_name"


def test_anchoring_ignores_case_and_line_breaks():
    """A name read out of a document carries the document's formatting."""
    doc = "The PAN-Card\n  Encryption Service runs in the CDE."

    assert check_names_are_anchored(
        _elements({"name": "pan-card encryption service"}), doc) == []
    # The same name with the newline it had in the source.
    assert check_names_are_anchored(
        _elements({"name": "PAN-Card\nEncryption Service"}), doc) == []


def test_no_source_means_no_opinion():
    """Nothing to anchor against is not evidence of invention.

    The same posture as `check_element_types` with no ontology and
    `check_connection_endpoints` with no declared elements: an empty basis would flag
    every name, which measures the run rather than the graph.
    """
    assert check_names_are_anchored(_elements({"name": "Anything"}), "") == []


# ============================================================================
# 2. Descriptions are grounded TOLERANTLY — the rule is a floor, not a quotation
# ============================================================================


def test_a_description_summarised_from_the_document_is_not_flagged():
    """The false-positive direction, and the reason this rule is not containment.

    A description is a summary. If this test fails, the guard has become a rule that
    fires on every correct extraction — measured at 30 of 30 descriptions when it was
    written as containment.
    """
    doc = ("The Payment Orchestrator accepts and validates payment requests, applies "
           "routing criteria, and forwards the authorisation to the payment gateway.")

    assert check_quotations_are_grounded(_elements({
        "name": "Payment Orchestrator",
        "description": "Accepts payment requests, applies routing criteria and forwards "
                       "authorisation to the gateway.",
    }), doc) == []


def test_a_description_written_from_nothing_is_flagged():
    doc = "The Payment Orchestrator routes authorisations to the gateway."

    flags = check_quotations_are_grounded(_elements({
        "name": "Settlement Ledger",
        "description": "Reconciles interbank clearing positions overnight.",
    }), doc)

    assert [f.subject for f in flags] == ["Settlement Ledger"]
    assert flags[0].kind == "ungrounded_description"


def test_a_description_too_short_to_judge_is_left_alone():
    """No words long enough to compare is not a finding — it is an absence of evidence."""
    assert check_quotations_are_grounded(
        _elements({"name": "X", "description": "n/a"}), "some document text") == []


# ============================================================================
# 3. The same rules against the real document
# ============================================================================


@pytest.mark.skipif(not REAL_DOC.exists(), reason="tracked fixture absent")
def test_the_strict_name_rule_discriminates_on_a_real_document():
    """The calibration that justifies the strict rule, on the tracked fixture.

    Names taken from the document must pass, and a name that reads like the category
    words the prompt forbids must not. If a future edit makes this rule fire on the
    document's own names, the measured 29-of-30 becomes noise and the guard is dead.
    """
    doc = REAL_DOC.read_text(encoding="utf-8")

    anchored = [{"name": n} for n in
                ("Payment Orchestrator", "Payment Gateway Platform", "PostgreSQL",
                 "Valkey", "OpenShift")]
    assert check_names_are_anchored(anchored, doc) == []

    assert [f.subject for f in check_names_are_anchored(
        [{"name": "Critical Components"}, {"name": "Core Services"}], doc)]


@pytest.mark.skipif(not REAL_DOC.exists(), reason="tracked fixture absent")
def test_the_tolerant_description_rule_does_not_fire_on_a_real_document():
    """Built from the document's own vocabulary, as a real description is."""
    doc = REAL_DOC.read_text(encoding="utf-8")

    grounded = [{
        "name": "Payment Orchestrator",
        "description": "Accepts and validates payment requests and routes the "
                       "authorisation to the payment gateway.",
    }]
    assert check_quotations_are_grounded(grounded, doc) == []


# ============================================================================
# 4. The two fields that were unconstrained
# ============================================================================


def test_the_iso_25010_pair_is_now_checked_on_an_element():
    """`quality_category` and `subcharacteristic` are plain `str` on `ElementRecord`.

    Unlike the `Literal`s of the same name on `DesignTechniqueRecord`, nothing
    constrained them at the decoder and nothing checked them — which is 57 of the
    undecided assertions on the live scope, every one carrying a value the ontology
    enumerates anyway.
    """
    assert ontology_enum("QualityAttributeCategory"), "ontology lost the vocabulary"
    assert ontology_enum("QualitySubcharacteristic")

    bad = check_enum_membership(_elements(
        {"name": "Legacy", "quality_category": "SPEEDINESS"},
        {"name": "Fine", "quality_category": "PERFORMANCE_EFFICIENCY",
         "subcharacteristic": "TIME_BEHAVIOUR"},
    ))
    assert [f.subject for f in bad] == ["Legacy"]
    assert "not in QualityAttributeCategory" in bad[0].reasons[0]


def test_the_technique_and_technology_vocabularies_are_in_sync():
    """Four `Literal`s nothing compared to the ontology.

    They match today, so this is green on arrival — it exists so that an ontology edit
    which widens or renames a value cannot leave the schema asserting the old list.
    """
    assert check_schema_consistency(DesignTechniqueRecord, TECHNIQUE_FIELD_ENUMS) == []
    assert check_schema_consistency(TechnologyStackRecord, TECHNOLOGY_FIELD_ENUMS) == []


def test_that_drift_check_has_teeth():
    from typing import Literal

    from pydantic import BaseModel

    class Drifted(BaseModel):
        technique_category: Literal["", "STRUCTURAL", "MADE_UP"] = ""

    flags = check_schema_consistency(Drifted, TECHNIQUE_FIELD_ENUMS)
    reasons = " ".join(r for f in flags for r in f.reasons)

    assert "MADE_UP" in reasons          # the schema invented a value
    assert "INTEGRATION" in reasons      # and dropped ontology ones


# ============================================================================
# 5. What the queue is made of
# ============================================================================


class _A:
    """A minimal active assertion — the projection reads only these fields."""

    def __init__(self, predicate, object=None, status="UNVERIFIED"):
        self.predicate = predicate
        self.object = object
        self.status = status
        self.is_active = True


class _G:
    def __init__(self, assertions):
        self._a = assertions

    def active(self):
        return iter(self._a)


def test_the_queue_separates_judgement_from_classification():
    graph = _G([
        _A("element_type"),            # closed vocabulary
        _A("quality_category"),        # closed vocabulary (newly checked)
        _A("description"),             # the document's own words
        _A("implements_requirement", object="req:1"),   # relational — a judgement
        _A("mechanism"),               # prose — a judgement
        _A("element_type", status="VERIFIED"),          # already reviewed
    ])

    b = project_review_buckets(graph)

    assert b["outstanding"] == 5
    assert b["enum_valued"] == 2
    assert b["quotation"] == 1
    assert b["relational"] == 1
    assert b["other"] == 1
    assert b["needs_judgement"] == 2, "only the relational and the prose need a person"


def test_predicates_with_no_named_guard_are_not_counted_as_decidable():
    """The overclaim this list was corrected for, pinned so it cannot return.

    `pattern` on `EngineeringConventionRecord` is a regex the convention matches names
    against, and `requirement_type` is a free string written at ingest with nothing
    checking it. Both LOOK enum-shaped. Counting either would make the number the review
    page states a lie — and a wrong number on the page is worse than no number, because
    it tells a reviewer to skip work that only they can do.
    """
    graph = _G([_A("pattern"), _A("requirement_type")])

    b = project_review_buckets(graph)

    assert b["enum_valued"] == 0, "these are not decidable by any guard"
    assert b["needs_judgement"] == 2
