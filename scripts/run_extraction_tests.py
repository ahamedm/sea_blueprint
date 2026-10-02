#!/usr/bin/env python3
"""Extraction test harness.

Runs each extraction agent against a known input, saves the raw output JSON per
case, and checks a battery of `inv_*` invariants against the result. Every
invariant is classified — in exactly one place, below — as a GATE or a BUDGET.

GATES — what the harness guarantees
-----------------------------------
A gate is a structural or contract property that cannot legitimately vary with
sampling: the run succeeded, triples/elements are present, element types are
valid, containers have parents, a declared parent has a matching `part_of` edge,
technologies and architectural styles did not leak in as elements, declared
expectations are present, the schema and the ontology did not drift, identifier
joins survive, and the Design Assistant's promised collections and links arrived.
**Any failed gate means the run is broken**: it prints FAIL and the process exits
non-zero. The classification is self-checked at startup — an `inv_*` that is in
neither GATES nor BUDGETS (or is in both) aborts the harness instead of silently
passing.

BUDGETS — what it measures but deliberately does not gate
---------------------------------------------------------
A budget is a quality count or ratio that legitimately moves between runs
(contract-violation ratio, ontology-class coverage, confidence spread,
responsibilities carried, technologies captured, C4 defect counts). Each budget
declares an allowed band. With a committed baseline
(`scripts/extraction_test_baseline.json`) the current value must stay inside
`baseline ± max(abs_slack, rel_slack * |baseline|)`; outside it the budget prints
WARN with the previous value, the current value and the band. Without a baseline
it falls back to an absolute floor/ceiling, so it still says something on a first
run. A budget never changes the exit code — deliberately: §3.13 of
`docs/design/extraction-reliability-levers.md` records 27-vs-30-element variance
on identical input, so a within-band move is not evidence of anything, and a
budget that could fail the build would be noise wearing a gate's authority.

C4 SCORECARD — a deliberate, external, optional dependency
----------------------------------------------------------
`scripts/c4_scorecard.py` is run as a SUBPROCESS and never imported: this module
must not import `app.viewpoints.c4` or `core.workspace`, so a machine with no
workspace store degrades to a SKIPPED check with the reason instead of an
ImportError or, worse, a silent pass. Its four DEFECT rules (reflexive, nesting,
acyclic, arrows) are folded in as gates; its two SHARE rules (levels stated, runs
complete — see `C4_RULE_BUDGETS`) and its defect counts (duplicated concepts,
non-C4 concepts excluded by design, capability gaps) as budgets. A store is used
when `--scorecard-root` is given, otherwise when a case declares
`"scorecard": {"root", "scope"}`, otherwise when a case names a `store_root`. A
missing store, a scorecard error, a non-JSON result, or a report with no graph is
reported SKIPPED with the reason.

WHAT IT DOES *NOT* GUARANTEE
----------------------------
* **That a passing run is correct.** Gates read structure, categories and named
  expectations. They cannot see a well-formed but wrong fact, an element the
  source never stated, or a relationship drawn to the wrong endpoint.
* **That a passing budget is an improvement.** Budgets are measurements against a
  baseline; they separate signal from noise only as far as the band is honest.
* **Anything about the model call itself** — cost, latency, prompt quality,
  determinism. Only `--validate-only` is deterministic. In `--validate-only` the
  `inv_success` gate is additionally vacuous: it re-checks saved JSON, where
  success is assumed rather than re-derived.
* **The pipeline between extraction and the store**, beyond the seams named by
  the invariants and whatever the scorecard can see of a named store.
* **That the committed baseline is current.** It is a reviewed snapshot.
  `--update-baseline` rewrites it and the diff is the review.

EXIT CODES
----------
    0  every gate passed (budgets may still WARN)
    1  at least one gate FAILED — the run is broken
    2  harness misconfigured, unknown case, or NOTHING CHECKED (every selected
       case had no saved output): a skipped-only run is not a pass

Why this exists (YB-007): every prompt change this session silently broke a
previously-working invariant — tightening the object contract killed traceability
predicates, adding containment resurrected technologies as elements. Catching
those depended on manually diffing runs. This makes it a command instead of a
discipline.

Usage:
    .venv/bin/python scripts/run_extraction_tests.py                    # costs money
    .venv/bin/python scripts/run_extraction_tests.py --only arch
    .venv/bin/python scripts/run_extraction_tests.py --validate-only    # no model call
    .venv/bin/python scripts/run_extraction_tests.py --validate-only \
        --scorecard-root data/sea --scorecard-scope default
    .venv/bin/python scripts/run_extraction_tests.py --update-baseline   # no model call

`--update-baseline` implies `--validate-only`: it re-checks saved output JSON and
never triggers extraction (extraction takes minutes and real money).
"""

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.knowledge_extraction import create_knowledge_extraction_agent
from agents.architecture_extraction import create_architecture_extraction_agent


BASELINE_PATH = Path(__file__).resolve().with_name("extraction_test_baseline.json")
SCORECARD_PATH = Path(__file__).resolve().with_name("c4_scorecard.py")
BASELINE_SCHEMA = 1
SCORECARD_TIMEOUT = 300


# ----------------------------------------------------------------------------
# Invariants
# ----------------------------------------------------------------------------
# Each returns (passed, detail). Detail is shown whether it passes or fails —
# a test that only reports failures hides how close a passing case was.
#
# An invariant's own (passed, detail) is the ABSOLUTE observation. For gates that
# result is the verdict. For budgets the verdict comes from the tolerance layer
# below (baseline band, or the absolute rule when no baseline exists) and the
# invariant's detail is carried along as context.

def _ok(cond: bool, detail: str) -> Tuple[bool, str]:
    return bool(cond), detail


def inv_success(res, out):
    return _ok(res.success, f"success={res.success} errors={res.errors or '[]'}")


def inv_triples_present(res, out):
    n = len(out.get("triples", []))
    return _ok(n > 0, f"triples={n}")


def inv_contract_ratio(res, out):
    """Clause-shaped nodes must stay a minority. Items 1 + 7."""
    n = len(out.get("triples", []))
    if n == 0:
        return _ok(False, "no triples")
    v = len(out.get("contract_violations", []))
    pct = 100 * v / n
    return _ok(pct < 20, f"contract violations {v}/{n} ({pct:.0f}%) < 20%")


def inv_ontology_class_coverage(res, out):
    t = out.get("triples", [])
    if not t:
        return _ok(False, "no triples")
    filled = sum(1 for x in t if x.get("ontology_class"))
    pct = 100 * filled / len(t)
    return _ok(pct >= 80, f"ontology_class on {filled}/{len(t)} ({pct:.0f}%) >= 80%")


def inv_confidence_varies(res, out):
    """A flat 1.0 defeats the human-review threshold. Item 1."""
    t = out.get("triples", [])
    if not t:
        return _ok(False, "no triples")
    distinct = len({round(x.get("confidence", 0), 2) for x in t})
    return _ok(distinct >= 2, f"{distinct} distinct confidence values >= 2")


# ---- requirements-side ----


def inv_requirement_ids(res, out):
    """Identifier preservation — the join key for ARC-G. Item 5."""
    ents = out.get("entities", [])
    ids = [e.get("requirement_id") for e in ents if e.get("requirement_id")]
    return _ok(len(ids) >= 1, f"{len(ids)} requirement ids captured")


def inv_traceability_predicates(res, out):
    """Item 1 regression: traceability predicates must survive object-contract tuning."""
    preds = {t["predicate"] for t in out.get("triples", [])}
    hits = {p for p in preds if p.startswith("traces_to") or p.startswith("binds_to")}
    return _ok(len(hits) >= 1, f"traceability predicates present: {sorted(hits) or 'NONE'}")


# ---- architecture-side ----


def inv_elements_present(res, out):
    n = len(out.get("elements", []))
    return _ok(n > 0, f"elements={n}")


def _element_reference_occurrences(out):
    """Every place a pass names an ELEMENT, and which list it named it in.

    ISS-2: the guards below read `elements` alone, so the same word arriving through
    `connections` or an attribution list (`used_by`, `adopted_by`, `applies_to`) was
    unmeasured — and the attribution lists are exactly where `concept:all_microservices`
    came from, one node per phrasing, on a run reporting COMPLETE.

    Triples are deliberately NOT scanned. A triple such as `X uses_technology Docker`
    legitimately names a technology, so running a rule about ELEMENTS over triple
    endpoints would flag correct output — `_TECH_LEAK` contains `docker`, `grpc` and
    `aes-256` precisely because those are used, not run. The rule is "a name in an
    element position", and these are the element positions.
    """
    for element in out.get("elements") or []:
        name = str(element.get("name") or "").strip()
        if name:
            yield "elements", name
    for connection in out.get("connections") or []:
        for field in ("source", "target"):
            value = str(connection.get(field) or "").strip()
            if value:
                yield f"connections.{field}", value
    for collection, field in (("technology_stacks", "used_by"),
                              ("architecture_styles", "adopted_by"),
                              ("design_techniques", "applies_to"),
                              ("engineering_conventions", "applies_to")):
        for record in out.get(collection) or []:
            for value in record.get(field) or []:
                text = str(value).strip()
                if text:
                    yield f"{collection}.{field}", text


# Things that are USED, never RUN. These must never appear as elements.
# Deliberately excludes PostgreSQL/Valkey (a datastore engine named as the
# running store IS an element) and monitoring servers (they run too). The
# distinction is run-vs-used, not "sounds technical".
_TECH_LEAK = {
    "spring boot", "spring boot / java 21", "java 21", "docker", "tls 1.2+",
    "alpinejs", "rbac", "stateless modular microservices", "active-active",
    "rest api", "grpc", "aes-256",
    # The Settlement Job Orchestrator embeds a scheduler and runs a batch
    # pattern. Both are USED, not RUN, and must not surface as elements —
    # `Quartz Job Scheduler` in particular reads like a component name.
    "quartz", "quartz job scheduler", "quartz scheduler",
}

# Names that belong to a non-element CLASS and must never become an element.
# Kept separate from `_TECH_LEAK` because the failure is different in kind: a
# technique emitted as an element does not merely mislabel a thing, it discards
# the `realizes_quality_attribute` edge that is the reason the class exists.
# A convention emitted as an element discards its checkable `pattern`.
_TECHNIQUE_OR_CONVENTION_LEAK = {
    "stateless services", "stateless service", "redundancy / replicas",
    "redundancy", "replicas", "health-checked removal from rotation",
    "bounded batch processing with checkpoints", "idempotent job execution",
    "asynchronous offload of payment state transitions",
    "container naming", "service naming", "environment naming",
    "semantic versioning", "package structure",
}


def inv_no_tech_leak(res, out):
    """Item 7 regression: things that are USED must not become elements.

    ISS-2: every element POSITION, not just `elements` — a leak through an
    attribution list becomes a node of its own just as surely.
    """
    leaked = [(where, name) for where, name in _element_reference_occurrences(out)
              if name.strip().lower() in _TECH_LEAK]
    return _ok(not leaked,
               f"{len(leaked)} used-not-run items named in an element position: "
               f"{leaked or 'none'}")


def inv_no_style_as_element(res, out):
    """An architectural style is not an element — it is an ArchitectureStyle.

    Reads `ArchitectureStyleName` from the ontology rather than a hardcoded list,
    the same way the agent's own validator does. A style emitted as a Container
    collects technology and technique edges that describe no deployable thing,
    and it is the one containment defect the parent-repair does NOT fix: the
    element gets placed, but it should not have been an element at all.

    ISS-2: the rule is fed the names from every element position, so a style
    arriving through `applies_to` is caught by the same vocabulary check rather
    than by a second copy of it.
    """
    from agents.architecture_extraction.repair import style_as_element

    flagged = [f.subject for f in style_as_element(out.get("elements") or [])]
    elsewhere = [{"name": name} for where, name in _element_reference_occurrences(out)
                 if where != "elements"]
    flagged += [f.subject for f in style_as_element(elsewhere)]
    return _ok(not flagged, f"styles in an element position: {sorted(set(flagged)) or 'none'}")


def inv_software_systems_classified(res, out):
    """Several SoftwareSystems is correct; UNCLASSIFIED ones are the finding.

    Earlier this asserted a single SoftwareSystem, which was wrong: an
    architecture legitimately contains the system under design plus the
    enterprise technology platforms it depends on (OpenShift, Splunk, Grafana).
    The real invariant is that each one is classified, since system_class is
    what lets the auditor exclude cross-cutting platforms from business
    capability-coverage questions.
    """
    ss = [e for e in out.get("elements", []) if e.get("element_type") == "SoftwareSystem"]
    if not ss:
        return _ok(True, "no SoftwareSystem elements")
    unclassified = [e["name"] for e in ss if not e.get("system_class")]
    return _ok(not unclassified,
                f"{len(ss)} SoftwareSystems, {len(unclassified)} unclassified"
                + (f": {unclassified}" if unclassified else " — all classified"))


def inv_schema_ontology_consistency(res, out):
    """Schema constraints and validator vocabularies must not drift.

    Both read from the ontology; this catches the case where one was edited and
    the other was not — validators then check rules the schema no longer states.

    The CONNECTION vocabularies are checked here too. `IntegrationStyle` and
    `IntegrationProtocol` are `Literal`s hand-copied from ontology enums that no
    Python read at all, so they were the pair most able to drift unnoticed: editing
    either enum would have left the pass schema asserting the old vocabulary, with
    no failing test and no run reporting anything.
    """
    from agents.extraction import (
        CONNECTION_FIELD_ENUMS,
        TECHNIQUE_FIELD_ENUMS,
        TECHNOLOGY_FIELD_ENUMS,
        check_schema_consistency,
    )
    from agents.architecture_extraction.passes import (
        ConnectionRecord,
        DesignTechniqueRecord,
        ElementRecord,
        TechnologyStackRecord,
    )
    flags = check_schema_consistency(ElementRecord)
    flags += check_schema_consistency(ConnectionRecord, CONNECTION_FIELD_ENUMS)
    # The technique and technology vocabularies. These four are `Literal`s, so the
    # decoder constrains them and nothing was comparing them to the ontology — which
    # is exactly the pair of conditions under which a schema silently outlives the
    # vocabulary it was copied from.
    flags += check_schema_consistency(DesignTechniqueRecord, TECHNIQUE_FIELD_ENUMS)
    flags += check_schema_consistency(TechnologyStackRecord, TECHNOLOGY_FIELD_ENUMS)
    return _ok(not flags,
               f"{len(flags)} schema/ontology drift findings"
               + (f": {[f.subject for f in flags]}" if flags else " — in sync"))


def inv_expected_present(res, out):
    """Completeness: assert content that MUST be present.

    Every other check validates what survived. This one catches what went
    missing — the failure mode where a schema change silently drops a whole
    category and the output still looks well-formed, so correctness checks pass
    over a diminished graph (observed: 5 SoftwareSystems down to 1, reported PASS).
    """
    expected = res.expected if hasattr(res, "expected") else []
    if not expected:
        return _ok(True, "(no expectations declared)")
    have = {str(e.get("name") or "").strip().lower()
            for e in out.get("elements", []) if isinstance(e, dict)}
    missing = [n for n in expected if n.lower() not in have]
    return _ok(not missing,
               f"{len(expected)-len(missing)}/{len(expected)} expected present"
               + (f" — MISSING {missing}" if missing else ""))


def inv_responsibilities_populated(res, out):
    """Responsibilities are the semantic content of the graph."""
    els = [e for e in out.get("elements", []) if isinstance(e, dict)]
    if not els:
        return _ok(False, "no elements")
    with_resp = [e["name"] for e in els if e.get("responsibilities")]
    return _ok(len(with_resp) >= 1,
               f"{len(with_resp)}/{len(els)} elements carry responsibilities")


def inv_technology_captured(res, out):
    n = len(out.get("technology_stacks", []))
    return _ok(n > 0, f"technology_stacks captured: {n}")


def inv_datastores_typed(res, out):
    """A named running store must be a DataStore, not a generic container."""
    known = {"postgresql", "valkey", "redis", "mysql", "mongodb", "kafka"}
    wrong = [e["name"] for e in out.get("elements", [])
             if e["name"].strip().lower() in known and e.get("element_type") != "DataStore"]
    return _ok(not wrong, f"stores not typed DataStore: {wrong or 'none'}")


def inv_containment_present(res, out):
    """A container with no parent is a node the graph cannot place. Item 7."""
    CONTAINED = {"Container", "DataStore", "Component", "CodeElement"}
    els = [e for e in out.get("elements", []) if e.get("element_type")]
    if not els:
        return _ok(False, "no typed elements")
    orphans = [e["name"] for e in els
               if e.get("element_type") in CONTAINED and not e.get("parent")]
    return _ok(not orphans, f"{len(orphans)} orphaned containers: {orphans or 'none'}")


def inv_part_of_edges(res, out):
    """Declared parents must also exist as `part_of` triples."""
    parts = {t["subject"] for t in out.get("triples", []) if t["predicate"] == "part_of"}
    need = {e["name"] for e in out.get("elements", [])
            if e.get("element_type") in {"Container", "DataStore", "Component", "CodeElement"}
            and e.get("parent")}
    missing = sorted(need - parts)
    return _ok(not missing, f"part_of edges: {len(parts)} for {len(need)} contained elements"
                            + (f" — missing {missing}" if missing else ""))


def inv_valid_element_types(res, out):
    VALID = {"SoftwareSystem", "ExternalSystem", "Person", "Container",
             "DataStore", "Component", "CodeElement", "DeploymentNode"}
    bad = [e["name"] for e in out.get("elements", [])
           if e.get("element_type") and e["element_type"] not in VALID]
    return _ok(not bad, f"invalid element_type: {bad or 'none'}")


def inv_deployment_nodes_unlevelled(res, out):
    """Deployment nodes have no C4 level; forcing one puts them at CONTEXT."""
    bad = [e["name"] for e in out.get("elements", [])
           if e.get("element_type") == "DeploymentNode" and e.get("c4_level")]
    return _ok(not bad, f"DeploymentNodes with a C4 level: {bad or 'none'}")


def inv_tech_construct_populated(res, out):
    n = len(out.get("technology_stacks", []))
    return _ok(n > 0, f"technology_stacks={n}")


def inv_responsibility_present(res, out):
    """Responsibility is the semantic content of the graph."""
    held = [e["name"] for e in out.get("elements", []) if e.get("responsibilities")]
    return _ok(len(held) >= 1, f"{len(held)} elements carry responsibilities")


def inv_techniques_populated(res, out):
    """Design techniques must be captured as techniques, not as elements.

    `Stateless Services` and `Redundancy / Replicas` read like component names,
    so this is the same class of regression as the technology leak — and the
    guard matters more here, because a technique recorded as an element loses the
    `realizes_quality_attributes` edge that is the entire point of the class.

    The technique list itself is *expected* to contain these names; only their
    appearance as ELEMENTS is the defect.

    A GATE, not a budget: its failure modes are a leak-as-element or an empty
    collection, both structural. How MANY techniques a run captures is a count
    that legitimately moves, but that number is not what this returns.
    """
    techniques = out.get("design_techniques", [])
    if not techniques:
        return _ok(False, "no design_techniques captured (the fixture names several)")
    as_elements = [e["name"] for e in out.get("elements", [])
                   if e["name"].strip().lower() in _TECHNIQUE_OR_CONVENTION_LEAK]
    linked = [t for t in techniques
              if t.get("realizes_quality_attributes") or t.get("quality_category")]
    return _ok(
        not as_elements and len(linked) >= 1,
        f"{len(techniques)} techniques, {len(linked)} aimed at a quality attribute"
        + (f" — LEAKED AS ELEMENTS: {as_elements}" if as_elements else ""),
    )


def inv_conventions_populated(res, out):
    """A convention with no checkable `pattern` is prose, not a convention.

    A GATE for the same reason as `inv_techniques_populated`: an empty list, a
    NAMING convention without a pattern, or a convention leaked as an element are
    all structural defects, not sampling noise.
    """
    conventions = out.get("engineering_conventions", [])
    if not conventions:
        return _ok(False, "no engineering_conventions captured (the fixture names several)")
    as_elements = [e["name"] for e in out.get("elements", [])
                   if e["name"].strip().lower() in _TECHNIQUE_OR_CONVENTION_LEAK]
    named = [c for c in conventions if c.get("convention_type") == "NAMING"]
    with_pattern = [c for c in named if c.get("pattern")]
    return _ok(
        len(with_pattern) >= 1 and not as_elements,
        f"{len(conventions)} conventions, {len(named)} naming, "
        f"{len(with_pattern)} with a checkable pattern"
        + (f" — LEAKED AS ELEMENTS: {as_elements}" if as_elements else ""),
    )


# ---- design-side ----
#
# The Design Assistant reads a GRAPH, not a file, so its invariants read the
# output collections directly and its case names a store rather than an input.
# They check the properties the profile exists to produce, not that it ran:
# every promised collection arrived, a pattern resolved against the catalogue,
# every scenario is measurable, and every element is either grounded or REPORTED
# as ungrounded. That last one is the difference between a design and a guess.


def inv_design_collections(res, out):
    wanted = ("elements", "connections", "design_techniques",
              "architecture_patterns", "quality_scenarios", "references")
    missing = [k for k in wanted if not out.get(k)]
    detail = (f"missing: {missing}" if missing else
              ", ".join(f"{len(out[k])} {k}" for k in wanted))
    return _ok(not missing, detail)


def inv_design_patterns_resolved(res, out):
    """A pattern the catalogue does not know is reported, but one must resolve."""
    resolutions = out.get("pattern_resolutions") or []
    resolved = [r for r in resolutions if r.get("resolved")]
    names = [r.get("name") for r in resolutions]
    return _ok(bool(resolved),
               f"{len(resolved)}/{len(resolutions)} resolved from the catalogue: {names}")


def inv_design_scenarios_measurable(res, out):
    """A scenario without a number does not make the requirement falsifiable."""
    scenarios = out.get("quality_scenarios") or []
    vague = [s.get("name") for s in scenarios
             if not any(ch.isdigit() for ch in str(s.get("response_measure") or ""))]
    return _ok(not vague,
               f"unmeasurable: {vague}" if vague
               else f"{len(scenarios)} scenario(s), all with a numeric measure")


def inv_design_techniques_linked(res, out):
    """A technique aimed at no quality attribute answers nothing."""
    techniques = out.get("design_techniques") or []
    unlinked = [t.get("name") for t in techniques
                if not (t.get("realizes_quality_attributes") or t.get("quality_category")
                        or t.get("subcharacteristic"))]
    return _ok(not unlinked,
               f"aimed at nothing: {unlinked}" if unlinked
               else f"{len(techniques)} technique(s), all linked to a quality concern")


def inv_design_elements_grounded_or_reported(res, out):
    """An invented element is only acceptable if it is NAMED as ungrounded."""
    grounded = {str(r.get("element") or "").strip().lower()
                for r in out.get("references") or []}
    ungrounded = [e.get("name") for e in out.get("elements") or []
                  if str(e.get("name") or "").strip().lower() not in grounded]
    reported = {f.get("subject") for f in out.get("findings") or []
                if f.get("kind") == "ungrounded"}
    unreported = [name for name in ungrounded if name not in reported]
    return _ok(not unreported,
               f"ungrounded AND unreported: {unreported}" if unreported
               else f"{len(ungrounded)} ungrounded, every one reported as a finding")


# ----------------------------------------------------------------------------
# Gate / budget classification
# ----------------------------------------------------------------------------
# This is the single place an invariant is classified. `_classification_problems`
# refuses to run when an `inv_*` is in neither set or in both, so adding an
# invariant without deciding its class is a hard error rather than a default.
#
# GATE      — structural / contract; failure means the run is broken (exit != 0).
# BUDGET    — a count or ratio that varies with sampling; compared to a band and
#             reported WARN, never FAIL.

GATES = frozenset({
    # run-level contract
    "inv_success",
    "inv_triples_present",
    "inv_elements_present",
    # typed structure and the containment tree
    "inv_valid_element_types",
    "inv_containment_present",
    "inv_part_of_edges",
    "inv_datastores_typed",
    "inv_deployment_nodes_unlevelled",
    "inv_software_systems_classified",
    # ontology / schema contract
    "inv_schema_ontology_consistency",
    # category discipline: USED things must not become elements
    "inv_no_tech_leak",
    "inv_no_style_as_element",
    "inv_techniques_populated",
    "inv_conventions_populated",
    # completeness / joins
    "inv_expected_present",
    "inv_requirement_ids",
    "inv_traceability_predicates",
    # design-side contract
    "inv_design_collections",
    "inv_design_patterns_resolved",
    "inv_design_scenarios_measurable",
    "inv_design_techniques_linked",
    "inv_design_elements_grounded_or_reported",
})


@dataclass(frozen=True)
class Budget:
    """A quality count whose exact value is expected to move between runs.

    `measure` pulls the number out of a saved output (or a scorecard report).
    The allowed band is `baseline ± max(abs_slack, rel_slack * |baseline|)`;
    without a baseline the `fallback` absolute rule applies instead —
    `("min", x)` means "at least x", `("max", x)` means "at most x".
    """

    measure: Callable[[Dict[str, Any]], Optional[float]]
    rel_slack: float
    abs_slack: float
    fallback: Tuple[str, float]
    unit: str = ""
    note: str = ""


def _m_contract_pct(out: Dict[str, Any]) -> Optional[float]:
    n = len(out.get("triples") or [])
    if n == 0:
        return None
    return 100.0 * len(out.get("contract_violations") or []) / n


def _m_ontology_coverage_pct(out: Dict[str, Any]) -> Optional[float]:
    triples = out.get("triples") or []
    if not triples:
        return None
    return 100.0 * sum(1 for t in triples if t.get("ontology_class")) / len(triples)


def _m_confidence_distinct(out: Dict[str, Any]) -> Optional[float]:
    triples = out.get("triples") or []
    if not triples:
        return None
    return float(len({round(t.get("confidence", 0), 2) for t in triples}))


def _m_responsibilities_pct(out: Dict[str, Any]) -> Optional[float]:
    elements = [e for e in out.get("elements") or [] if isinstance(e, dict)]
    if not elements:
        return None
    return 100.0 * sum(1 for e in elements if e.get("responsibilities")) / len(elements)


def _m_technology_stacks(out: Dict[str, Any]) -> float:
    return float(len(out.get("technology_stacks") or []))


BUDGETS: Dict[str, Budget] = {
    "inv_contract_ratio": Budget(
        _m_contract_pct, rel_slack=1.0, abs_slack=2.0,
        fallback=("max", 20.0), unit="%",
        note="clause-shaped nodes must stay a minority"),
    "inv_ontology_class_coverage": Budget(
        _m_ontology_coverage_pct, rel_slack=0.10, abs_slack=5.0,
        fallback=("min", 80.0), unit="%",
        note="share of triples carrying an ontology_class"),
    "inv_confidence_varies": Budget(
        _m_confidence_distinct, rel_slack=0.5, abs_slack=1.0,
        fallback=("min", 2.0), unit="",
        note="distinct confidence values; a flat 1.0 defeats review triage"),
    "inv_responsibilities_populated": Budget(
        _m_responsibilities_pct, rel_slack=0.25, abs_slack=10.0,
        fallback=("min", 1.0), unit="%",
        note="share of elements carrying responsibilities"),
    "inv_responsibility_present": Budget(
        _m_responsibilities_pct, rel_slack=0.25, abs_slack=10.0,
        fallback=("min", 1.0), unit="%",
        note="same measure as inv_responsibilities_populated"),
    "inv_technology_captured": Budget(
        _m_technology_stacks, rel_slack=0.25, abs_slack=3.0,
        fallback=("min", 1.0), unit="",
        note="technology_stacks captured"),
    "inv_tech_construct_populated": Budget(
        _m_technology_stacks, rel_slack=0.25, abs_slack=3.0,
        fallback=("min", 1.0), unit="",
        note="same measure as inv_technology_captured"),
}


# Scorecard defect counts, folded in under the synthetic case name `c4`. A broken
# containment tree is not noise, so the four DEFECT rules are gates; the two SHARE
# rules and the defect COUNTS vary with graph size and extraction, so they are
# budgets.
C4_RULE_BUDGETS: Dict[str, Budget] = {
    "c4.levels_stated": Budget(
        lambda _report: None, rel_slack=0.02, abs_slack=0.05,
        fallback=("min", 0.90), unit="",
        note="share of structural elements stating their C4 level"),
    "c4.runs_complete": Budget(
        lambda _report: None, rel_slack=0.25, abs_slack=0.25,
        fallback=("min", 0.50), unit="",
        note="share of runs behind the graph that are COMPLETE"),
}
"""The two readiness rules that are measurements, not verdicts.

WHY NOT GATES. YB-053 records this exact test case at 25/26 levels stated (one
element's level is inferred and said to be inferred — "not a defect") and a PARTIAL
architecture run (one `empty` connections answer, which ADR-0013 deliberately counts
against completeness so a miss cannot hide). Gating either one means the harness
reports RUN BROKEN on a run this project has already argued is honest — and a gate
that fires on a legitimate state is how a harness teaches its reader to ignore
gates. The floors below are smoke alarms, not acceptance criteria.
"""


def _m_c4_duplicates(report: Dict[str, Any]) -> float:
    return float((report.get("defects") or {}).get("duplicates", 0))


def _m_c4_excluded(report: Dict[str, Any]) -> float:
    return float((report.get("defects") or {}).get("excluded_non_c4", 0))


def _m_c4_gaps(report: Dict[str, Any]) -> float:
    return float(sum(((report.get("defects") or {}).get("gaps_by_kind") or {}).values()))


C4_BUDGETS: Dict[str, Budget] = {
    "c4.duplicates": Budget(
        _m_c4_duplicates, rel_slack=0.5, abs_slack=1.0,
        fallback=("max", 5.0), unit="",
        note="concepts extracted twice (element + concept)"),
    "c4.excluded_non_c4": Budget(
        _m_c4_excluded, rel_slack=0.5, abs_slack=5.0,
        fallback=("max", 200.0), unit="",
        note="non-C4 concepts excluded by design"),
    "c4.gaps": Budget(
        _m_c4_gaps, rel_slack=0.5, abs_slack=3.0,
        fallback=("max", 30.0), unit="",
        note="total readiness gaps of any kind"),
}


def _defined_invariants() -> set:
    return {name for name, obj in globals().items()
            if name.startswith("inv_") and callable(obj)}


def _classification_problems(cases: List[Dict[str, Any]]) -> List[str]:
    """Return the reasons the classification is incomplete (empty == good)."""
    defined = _defined_invariants()
    both = sorted(set(GATES) & set(BUDGETS))
    unclassified = sorted(defined - set(GATES) - set(BUDGETS))
    unknown_gate = sorted(set(GATES) - defined)
    unknown_budget = sorted(set(BUDGETS) - defined)
    problems = []
    if both:
        problems.append(f"classified as BOTH gate and budget: {both}")
    if unclassified:
        problems.append(f"defined but unclassified: {unclassified}")
    if unknown_gate:
        problems.append(f"gate name is not a defined invariant: {unknown_gate}")
    if unknown_budget:
        problems.append(f"budget name is not a defined invariant: {unknown_budget}")
    problems.extend(_missing_from_cases(cases))
    return problems


def _missing_from_cases(cases: List[Dict[str, Any]]) -> List[str]:
    defined = _defined_invariants()
    referenced = {inv.__name__ for case in cases for inv in case["invariants"]}
    unknown = sorted(referenced - defined)
    return [f"case references an unknown invariant: {unknown}"] if unknown else []


# ----------------------------------------------------------------------------
# Checks, tolerances and the baseline
# ----------------------------------------------------------------------------


class Check(NamedTuple):
    """One reported check. `status` is PASS, FAIL (gates) or WARN (budgets)."""

    name: str
    kind: str                      # "gate" | "budget"
    status: str                    # "PASS" | "FAIL" | "WARN"
    detail: str
    value: Optional[float] = None
    baseline: Optional[float] = None


def _fmt(value: Optional[float], unit: str) -> str:
    if value is None:
        return "n/a"
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{text or '0'}{unit}"


def _baseline_value(baseline: Dict[str, Any], case_name: str, check_name: str) -> Optional[float]:
    cases = (baseline or {}).get("cases") or {}
    value = (cases.get(case_name) or {}).get(check_name)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _band_check(name: str, spec: Budget, value: Optional[float],
                previous: Optional[float], context: str = "") -> Check:
    """Compare one budget value to its baseline band, or its absolute fallback."""
    tail = f" — {context}" if context else ""
    if value is None:
        return Check(name, "budget", "WARN", f"not measurable from this output{tail}")

    if previous is not None:
        slack = max(spec.abs_slack, spec.rel_slack * abs(previous))
        low, high = previous - slack, previous + slack
        within = low <= value <= high
        detail = (
            f"{_fmt(value, spec.unit)} "
            f"{'within' if within else 'OUTSIDE'} band "
            f"{_fmt(low, spec.unit)}–{_fmt(high, spec.unit)} "
            f"(previous {_fmt(previous, spec.unit)}, tolerance ±{_fmt(slack, spec.unit)})"
        )
        return Check(name, "budget", "PASS" if within else "WARN",
                     detail + tail, value, previous)

    direction, threshold = spec.fallback
    within = value <= threshold if direction == "max" else value >= threshold
    symbol = "<=" if direction == "max" else ">="
    detail = (f"{_fmt(value, spec.unit)} {symbol} {_fmt(threshold, spec.unit)} "
              f"(no baseline: absolute rule)")
    return Check(name, "budget", "PASS" if within else "WARN", detail + tail, value, None)


def _budget_check(case_name: str, inv: Callable, res, out: Dict[str, Any],
                  baseline: Dict[str, Any]) -> Check:
    """Tolerance-governed check for a budget invariant.

    The invariant still runs — its absolute detail is carried as context and an
    exception is surfaced rather than swallowed — but its pass/fail is NOT the
    verdict: the band (or the absolute fallback) is.
    """
    name = inv.__name__
    spec = BUDGETS[name]
    try:
        _passed, context = inv(res, out)
    except Exception as exc:                                   # noqa: BLE001
        context = f"invariant raised {type(exc).__name__}: {exc}"
    try:
        value = spec.measure(out)
    except Exception as exc:                                   # noqa: BLE001
        value = None
        context = f"{context}; measure raised {type(exc).__name__}: {exc}"
    return _band_check(name, spec, value,
                       _baseline_value(baseline, case_name, name), context)


def _checks(case: Dict[str, Any], result, out: Dict[str, Any],
            baseline: Dict[str, Any]) -> List[Check]:
    """Run a case's invariants and classify each result as gate or budget.

    `AgentResult` is a pydantic model, so a lightweight proxy carries the
    case-level expectations the completeness invariants need rather than trying to
    bolt an attribute onto it.
    """

    class _Res:
        def __init__(self, real, expected):
            self._real = real
            self.expected = expected

        def __getattr__(self, item):
            return getattr(self._real, item)

    result_view = _Res(result, case.get("expected", []))
    checks: List[Check] = []
    for inv in case["invariants"]:
        name = inv.__name__
        if name in GATES:
            try:
                passed, detail = inv(result_view, out)
            except Exception as exc:                           # noqa: BLE001
                passed, detail = False, f"raised {type(exc).__name__}: {exc}"
            checks.append(Check(name, "gate", "PASS" if passed else "FAIL", detail))
        else:
            checks.append(_budget_check(case["name"], inv, result_view, out, baseline))
    return checks


def load_baseline(path: Path) -> Dict[str, Any]:
    """Read the committed baseline. A missing/corrupt file degrades to absolute rules."""
    if not path.exists():
        return {"schema_version": BASELINE_SCHEMA, "cases": {}}
    try:
        blob = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  baseline unreadable ({exc}) — budgets use their absolute rules",
              file=sys.stderr)
        return {"schema_version": BASELINE_SCHEMA, "cases": {}}
    if not isinstance(blob, dict):
        print("  baseline is not a JSON object — budgets use their absolute rules",
              file=sys.stderr)
        return {"schema_version": BASELINE_SCHEMA, "cases": {}}
    blob.setdefault("cases", {})
    return blob


def write_baseline(path: Path, results: List[Dict[str, Any]]) -> Dict[str, int]:
    """Merge the current budget numbers into the baseline file.

    Merges rather than replaces: `--only <case> --update-baseline` must not erase
    the other cases' numbers. Never runs extraction — only budget values observed
    on outputs already on disk are written.
    """
    blob = load_baseline(path)
    blob["schema_version"] = BASELINE_SCHEMA
    blob["generated_by"] = (
        "scripts/run_extraction_tests.py --update-baseline "
        "(validate-only; no model call)"
    )
    blob["note"] = (
        "Quality counts measured from saved output JSON. Each budget compares "
        "against these values ± its tolerance; regenerate deliberately and review "
        "the diff, because accepting a moved baseline also accepts the change."
    )
    cases = blob.setdefault("cases", {})
    written: Dict[str, int] = {}
    for result in results:
        values = {c.name: round(float(c.value), 4)
                  for c in result["checks"]
                  if c.kind == "budget" and c.value is not None}
        if values:
            cases[result["case"]] = values
            written[result["case"]] = len(values)
    path.write_text(json.dumps(blob, indent=2, sort_keys=True) + "\n")
    return written


# ----------------------------------------------------------------------------
# Cases
# ----------------------------------------------------------------------------

CASES: List[Dict[str, Any]] = [
    {
        "name": "req_sample",
        "agent": "knowledge_extraction",
        "input": "test_data/prd/sample_requirements.md",
        "output": "data/output/test_req_sample.json",
        # Payment Processing is the MVP domain, so these cases select its pack.
        # Another domain adds a case with its own pack; nothing else changes.
        "domain_pack": "payment_processing",
        "invariants": [inv_success, inv_triples_present, inv_contract_ratio,
                       inv_ontology_class_coverage, inv_confidence_varies],
    },
    {
        "name": "req_prd",
        "agent": "knowledge_extraction",
        "input": "test_data/prd/payment_platform_brief.md",
        "output": "data/output/test_req_prd.json",
        "domain_pack": "payment_processing",
        "invariants": [inv_success, inv_triples_present, inv_contract_ratio,
                       inv_ontology_class_coverage, inv_confidence_varies,
                       inv_requirement_ids, inv_traceability_predicates],
    },
    {
        "name": "arch",
        "agent": "architecture_extraction",
        "input": "test_data/arch/payment_platform_arch.md",
        "output": "data/output/test_arch.json",
        "domain_pack": "payment_processing",
        "expected": [
            "Payment Orchestrator", "Payment Routing Decision Engine",
            "PAN-Card Encryption Service", "Storefront Management Service",
            "Payment UI Service", "PostgreSQL", "Valkey",
            "Mastercard", "Elavon", "CCnet",
            # Added with the batch/scheduling container. Its jobs are Components
            # inside it, so the container must survive as a Container element and
            # Quartz must stay a technology (see `_TECH_LEAK`).
            "Settlement Job Orchestrator",
        ],
        "invariants": [inv_success, inv_triples_present, inv_elements_present,
                       inv_no_tech_leak, inv_no_style_as_element,
                       inv_containment_present, inv_part_of_edges,
                       inv_valid_element_types, inv_deployment_nodes_unlevelled,
                       inv_responsibilities_populated, inv_software_systems_classified,
                       inv_datastores_typed, inv_technology_captured,
                       inv_techniques_populated, inv_conventions_populated,
                       inv_schema_ontology_consistency, inv_expected_present],
    },
    {
        "name": "design",
        "agent": "design_assistant",
        # No `input`: this profile reads REQ-G and the baseline ARC-G out of the
        # store. `store_root` is the input, and the run writes a proposal only —
        # it never touches the working set.
        #
        # A case may also declare a `"scorecard": {"root", "scope"}` pointing at a
        # WORKSPACE store (not this RevisionStore root) when C4 readiness should be
        # folded in; otherwise `store_root` is used and the scorecard reports
        # SKIPPED with the reason when no workspace is there.
        "store_root": "data/sea",
        "output": "data/output/test_design.json",
        "domain_pack": "payment_processing",
        "invariants": [inv_success, inv_design_collections,
                       inv_design_patterns_resolved, inv_design_scenarios_measurable,
                       inv_design_techniques_linked,
                       inv_design_elements_grounded_or_reported],
    },
]


# ----------------------------------------------------------------------------
# Case execution
# ----------------------------------------------------------------------------


def _run_design_case(case: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Any]:
    """The Design Assistant's case: a GRAPH input, so a different flow.

    Kept separate rather than threaded through `run_case`, which reads a document
    from `case["input"]`. This profile has no document — its input is REQ-G and the
    baseline ARC-G out of the store — and pretending otherwise would be the same
    category error as giving it a `--input` file in the CLI.
    """
    from agents.design_assistant import create_design_assistant_agent
    from core.knowledge import RevisionStore

    store = RevisionStore(case.get("store_root", "data/sea")).ensure()
    snapshot = store.load_working()
    if not snapshot.graph.nodes:
        raise RuntimeError(f"no working set at {case.get('store_root')} — ingest first")

    baselines = store.baselines()
    baseline_graph = store.load_revision(baselines[0].id).graph if baselines else None
    base_ref = baselines[0].id if baselines else ""

    agent = create_design_assistant_agent()
    pack_spec = case.get("domain_pack") or os.getenv("SEA_DOMAIN_PACK", "")
    if pack_spec:
        agent.use_domain_pack(pack_spec)
    if agent.active_domain_pack_id():
        print(f"  domain pack: {agent.active_domain_pack_id()}")
    print(f"  graph : {case.get('store_root')} "
          f"({len(snapshot.graph.nodes)} nodes, baseline {base_ref or 'none'})")

    t0 = time.time()
    result = agent.run({
        "graph": snapshot.graph,
        "baseline": baseline_graph,
        "base_ref": base_ref,
        "initiative_id": snapshot.meta.get("initiative_id", ""),
        "domain_pack": agent.active_domain_pack_id(),
    })
    elapsed = time.time() - t0

    out = result.output or {}
    Path(case["output"]).parent.mkdir(parents=True, exist_ok=True)
    Path(case["output"]).write_text(json.dumps(
        {"case": case["name"], "input_file": case.get("store_root", ""),
         "metadata": result.metadata, **out}, indent=2))

    return {
        "case": case["name"], "agent": case["agent"],
        "input": case.get("store_root", ""), "output": case["output"],
        "elapsed": elapsed,
        "path": (result.metadata or {}).get("extraction_path"),
        "checks": _checks(case, result, out, baseline),
    }


def run_case(case: Dict[str, Any], baseline: Dict[str, Any]) -> Dict[str, Any]:
    if case["agent"] == "design_assistant":
        return _run_design_case(case, baseline)

    agent = (create_knowledge_extraction_agent()
             if case["agent"] == "knowledge_extraction"
             else create_architecture_extraction_agent())

    # Ground the run in a domain vocabulary when one is named. Selected BEFORE
    # `run`, not passed through `input_data`, because the pack is compiled into the
    # system prompt at selection time. The `"domain": "payment_processing"` that
    # used to sit in this dict was read, logged, and never reached a prompt or a
    # schema — the inert-layer defect the domain pack replaces.
    pack_spec = case.get("domain_pack") or os.getenv("SEA_DOMAIN_PACK", "")
    if pack_spec:
        agent.use_domain_pack(pack_spec)
    if agent.active_domain_pack_id():
        print(f"  domain pack: {agent.active_domain_pack_id()}")

    document = Path(case["input"]).read_text()

    t0 = time.time()
    result = agent.run({
        "document": document,
        "document_type": "requirements" if case["agent"] == "knowledge_extraction" else "architecture",
        "domain_pack": agent.active_domain_pack_id(),
    })
    elapsed = time.time() - t0

    out = result.output or {}
    Path(case["output"]).write_text(json.dumps(
        {"case": case["name"], "input_file": case["input"],
         "metadata": result.metadata, **out}, indent=2))

    return {
        "case": case["name"], "agent": case["agent"],
        "input": case["input"], "output": case["output"],
        "elapsed": elapsed, "path": result.metadata.get("extraction_path"),
        "checks": _checks(case, result, out, baseline),
    }


def validate_saved(case: Dict[str, Any], baseline: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Re-check the last saved output for a case, without re-running extraction."""
    path = Path(case["output"])
    if not path.exists():
        return None
    blob = json.loads(path.read_text())

    class _Res:                      # minimal shim; invariants need success/errors/expected
        success = True
        errors: List[str] = []
        metadata = blob.get("metadata", {})
        expected = case.get("expected", [])

    return {"case": case["name"], "agent": case["agent"],
            "input": case.get("input", ""), "output": case["output"], "elapsed": 0.0,
            "path": blob.get("metadata", {}).get("extraction_path"),
            "checks": _checks(case, _Res(), blob, baseline)}


# ----------------------------------------------------------------------------
# C4 scorecard pairing (subprocess — never an import)
# ----------------------------------------------------------------------------


def _scorecard_spec(cases: List[Dict[str, Any]], args: argparse.Namespace) -> Dict[str, str]:
    """Resolve which workspace store, if any, the scorecard should measure."""
    if args.scorecard_root:
        return {"root": args.scorecard_root, "scope": args.scorecard_scope}
    for case in cases:
        spec = case.get("scorecard")
        if spec:
            return {"root": str(spec.get("root", "")), "scope": str(spec.get("scope", ""))}
    for case in cases:
        if case.get("store_root"):
            return {"root": str(case["store_root"]), "scope": str(case.get("scorecard_scope", ""))}
    return {}


def _skipped(case: str, reason: str) -> Dict[str, Any]:
    return {"case": case, "skipped": True, "reason": reason}


def run_scorecard(spec: Dict[str, str], baseline: Dict[str, Any]) -> Dict[str, Any]:
    """Run `scripts/c4_scorecard.py` as a subprocess and fold its report in.

    Returns a result dict (with `case == "c4"`) or a `_skipped(...)` record. Every
    failure path — no store named, store absent, timeout, non-JSON output, an
    empty graph — is a SKIPPED check carrying the reason. It is never a pass.
    """
    root = spec.get("root") or ""
    scope = spec.get("scope") or ""
    label = root or "(default workspace)"
    if scope:
        label += f" scope={scope}"

    if not root:
        return _skipped("c4", "no workspace store named "
                              "(pass --scorecard-root, or give a case `scorecard`/`store_root`)")
    if not Path(root).exists():
        return _skipped("c4", f"no workspace store at {root} (directory does not exist)")
    if not SCORECARD_PATH.exists():
        return _skipped("c4", f"companion tool missing: {SCORECARD_PATH}")

    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, str(SCORECARD_PATH),
             "--root", root, "--scope", scope, "--json"],
            capture_output=True, text=True, timeout=SCORECARD_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return _skipped("c4", f"scorecard timed out after {SCORECARD_TIMEOUT}s on {label}")
    except OSError as exc:
        return _skipped("c4", f"scorecard could not run: {exc}")
    elapsed = time.time() - t0

    # The scorecard exits 1 when the graph is simply not READY; only an unparseable
    # stdout means it did not produce a report at all.
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        lines = [ln for ln in (proc.stderr or proc.stdout or "").strip().splitlines() if ln]
        return _skipped("c4", f"scorecard produced no report on {label}: "
                              f"{lines[-1] if lines else f'exit {proc.returncode}'}")

    if not (report.get("elements") or {}).get("total") and not (report.get("runs") or {}).get("runs"):
        return _skipped("c4", f"scorecard found no graph at {label} (empty working set) "
                              "— readiness of nothing is not a pass")

    checks: List[Check] = []
    for item in report.get("verdict") or []:
        rule = str(item.get("rule") or "")
        value = item.get("value")
        detail = f"{item.get('claim', '')} (value={value})"
        budget = C4_RULE_BUDGETS.get(f"c4.{rule}")
        if budget is not None:
            checks.append(_band_check(
                f"c4.{rule}", budget,
                float(value) if isinstance(value, (int, float)) else None,
                _baseline_value(baseline, "c4", f"c4.{rule}"),
                context=str(item.get("claim") or ""),
            ))
            continue
        checks.append(Check(f"c4.{rule}", "gate",
                            "PASS" if item.get("held") else "FAIL", detail))
    for name, spec_ in C4_BUDGETS.items():
        value = spec_.measure(report)
        checks.append(_band_check(name, spec_, value,
                                  _baseline_value(baseline, "c4", name)))

    return {
        "case": "c4", "agent": "c4_scorecard", "input": label,
        "output": "scorecard report (in-memory)", "elapsed": elapsed, "path": "",
        "ready": bool(report.get("ready")), "checks": checks,
    }


# ----------------------------------------------------------------------------
# Reporting
# ----------------------------------------------------------------------------


def _print_checks(checks: List[Check]) -> None:
    for check in checks:
        print(f"    [{check.status}] {check.name:<40} {check.detail}")


def print_summary(results: List[Dict[str, Any]], skipped: List[Dict[str, Any]]) -> None:
    print(f"\n{'=' * 72}\nSUMMARY\n{'=' * 72}")

    for result in results:
        gates = [c for c in result["checks"] if c.kind == "gate"]
        budgets = [c for c in result["checks"] if c.kind == "budget"]
        gate_pass = sum(1 for c in gates if c.status == "PASS")
        budget_in = sum(1 for c in budgets if c.status == "PASS")
        print(f"  {result['case']:<14} gates {gate_pass}/{len(gates)}   "
              f"budgets {budget_in}/{len(budgets)} in band   "
              f"{result['elapsed']:.0f}s   {result['path']}")

    gates = [c for r in results for c in r["checks"] if c.kind == "gate"]
    gate_fail = [c for c in gates if c.status == "FAIL"]
    budgets = [c for r in results for c in r["checks"] if c.kind == "budget"]
    budget_in = [c for c in budgets if c.status == "PASS"]
    budget_out = [c for c in budgets if c.status == "WARN"]

    if gates:
        print(f"\n  GATES  {len(gates) - len(gate_fail)}/{len(gates)} passed"
              + ("  — RUN BROKEN" if gate_fail else "  — structural contract holds"))
    else:
        print("\n  GATES  none evaluated")
    for check in gate_fail:
        print(f"    FAIL {check.name:<40} {check.detail}")

    print(f"\n  BUDGETS within tolerance   {len(budget_in)}/{len(budgets)}")
    print(f"  BUDGETS outside tolerance  {len(budget_out)}/{len(budgets)}")
    for check in budget_out:
        print(f"    WARN {check.name:<40} {check.detail}")

    print(f"\n  SKIPPED  {len(skipped)}")
    for item in skipped:
        print(f"    SKIP {item['case']:<40} {item['reason']}")

    if not gates:
        # Nothing was evaluated (every selected case reported "no saved output").
        # That is not a pass: it is the silent-green failure this harness exists
        # to prevent, so the caller gets a non-zero exit (2) and this wording.
        verdict = ("NOTHING CHECKED — no gate was evaluated; a skipped-only run "
                   "is not a pass")
    elif gate_fail:
        verdict = f"FAIL — {len(gate_fail)} gate(s) broken"
    elif budget_out:
        verdict = (f"PASS with warnings — gates hold; "
                   f"{len(budget_out)} budget(s) outside tolerance")
    else:
        verdict = "PASS — gates hold; every budget within tolerance"
    print(f"\n  VERDICT: {verdict}")


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Run extraction agents and check gates/budgets against a baseline.")
    ap.add_argument("--only", help="run one case by name")
    ap.add_argument("--validate-only", action="store_true",
                    help="re-check saved outputs without re-running extraction "
                         "(validation is instant; extraction takes minutes)")
    ap.add_argument("--update-baseline", action="store_true",
                    help="write the current budget numbers into the baseline file "
                         "(implies --validate-only; never calls a model)")
    ap.add_argument("--baseline", default=str(BASELINE_PATH),
                    help=f"baseline JSON path (default: {BASELINE_PATH.name})")
    ap.add_argument("--scorecard-root", default="",
                    help="workspace root for scripts/c4_scorecard.py "
                         "(default: a case's `scorecard`/`store_root`)")
    ap.add_argument("--scorecard-scope", default="",
                    help="scope id for scripts/c4_scorecard.py")
    args = ap.parse_args()

    if args.update_baseline:
        # Never spend a model call to record numbers: the baseline is measured
        # from output JSON that extraction has already paid for.
        args.validate_only = True

    cases = [c for c in CASES if not args.only or c["name"] == args.only]
    if args.only and not cases:
        print(f"unknown case {args.only!r}; known: {[c['name'] for c in CASES]}",
              file=sys.stderr)
        return 2

    problems = _classification_problems(CASES)
    if problems:
        print("HARNESS MISCONFIGURED — every invariant must be exactly one of "
              "gate/budget:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    unexercised = sorted(_defined_invariants()
                         - {inv.__name__ for c in CASES for inv in c["invariants"]})
    if unexercised:
        print(f"note: invariants classified but used by no case: {unexercised}")

    baseline = load_baseline(Path(args.baseline))
    baseline_note = ("baseline loaded" if (baseline.get("cases"))
                     else "no baseline yet — budgets use absolute rules")
    print(f"baseline: {args.baseline} ({baseline_note})")

    results: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for case in cases:
        print(f"\n{'=' * 72}\nCASE {case['name']}  ({case['agent']})\n{'=' * 72}")
        if case.get("input"):
            print(f"  input : {case['input']}")
        if args.validate_only:
            result = validate_saved(case, baseline)
            if result is None:
                reason = f"no saved output at {case['output']}"
                print(f"  (no saved output — run without --validate-only first: "
                      f"{case['output']})")
                skipped.append({"case": case["name"], "reason": reason})
                continue
        else:
            result = run_case(case, baseline)
        results.append(result)
        print(f"  output: {result['output']}")
        print(f"  path  : {result['path']}   elapsed: {result['elapsed']:.0f}s")
        _print_checks(result["checks"])

    # ---- C4 scorecard: one workspace-level measurement, folded into the report ----
    print(f"\n{'=' * 72}\nC4 SCORECARD  (companion: scripts/c4_scorecard.py)\n{'=' * 72}")
    scorecard = run_scorecard(_scorecard_spec(cases, args), baseline)
    if scorecard.get("skipped"):
        print(f"  [SKIP] {scorecard['reason']}")
        skipped.append({"case": "c4", "reason": scorecard["reason"]})
    else:
        results.append(scorecard)
        print(f"  workspace: {scorecard['input']}   elapsed: {scorecard['elapsed']:.1f}s   "
              f"READY: {'yes' if scorecard['ready'] else 'NO'}")
        _print_checks(scorecard["checks"])

    if args.update_baseline:
        written = write_baseline(Path(args.baseline), results)
        detail = ", ".join(f"{case}={n}" for case, n in sorted(written.items()))
        print(f"\nbaseline written: {args.baseline}"
              + (f"  ({detail})" if detail else "  (no budget values observed)"))

    print_summary(results, skipped)

    failed_gates = [c for r in results for c in r["checks"]
                    if c.kind == "gate" and c.status == "FAIL"]
    evaluated_gates = [c for r in results for c in r["checks"] if c.kind == "gate"]
    if not evaluated_gates:
        return 2          # nothing was checked — not a pass (see print_summary)
    return 1 if failed_gates else 0


if __name__ == "__main__":
    sys.exit(main())
