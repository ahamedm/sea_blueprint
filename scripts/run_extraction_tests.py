#!/usr/bin/env python3
"""
Extraction test harness  [DRAFT]

Runs each extraction agent against a known input, writes a named output file,
and asserts a battery of invariants against the result.

Why this exists (YB-007): every prompt change this session silently broke
a previously-working invariant — tightening the object contract killed
traceability predicates, adding containment resurrected technologies as
elements. Catching those depended on manually diffing runs. This makes it a
command instead of a discipline.

Usage:
    .venv/bin/python scripts/run_extraction_tests.py
    .venv/bin/python scripts/run_extraction_tests.py --only arch
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.knowledge_extraction import create_knowledge_extraction_agent
from agents.architecture_extraction import create_architecture_extraction_agent


# ----------------------------------------------------------------------------
# Invariants
# ----------------------------------------------------------------------------
# Each returns (passed, detail). Detail is shown whether it passes or fails —
# a test that only reports failures hides how close a passing case was.

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
    """Item 7 regression: things that are USED must not become elements."""
    leaked = [e["name"] for e in out.get("elements", [])
              if e["name"].strip().lower() in _TECH_LEAK]
    return _ok(not leaked, f"{len(leaked)} used-not-run items became elements: {leaked or 'none'}")


def inv_no_style_as_element(res, out):
    """An architectural style is not an element — it is an ArchitectureStyle.

    Reads `ArchitectureStyleName` from the ontology rather than a hardcoded list,
    the same way the agent's own validator does. A style emitted as a Container
    collects technology and technique edges that describe no deployable thing,
    and it is the one containment defect the parent-repair does NOT fix: the
    element gets placed, but it should not have been an element at all.
    """
    from agents.architecture_extraction.repair import style_as_element

    flagged = [f.subject for f in style_as_element(out.get("elements") or [])]
    return _ok(not flagged, f"styles emitted as elements: {flagged or 'none'}")


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
    """
    from agents.extraction import check_schema_consistency
    from agents.architecture_extraction.passes import ElementRecord
    flags = check_schema_consistency(ElementRecord)
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
    """A convention with no checkable `pattern` is prose, not a convention."""
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
        "store_root": "data/sea",
        "output": "data/output/test_design.json",
        "domain_pack": "payment_processing",
        "invariants": [inv_success, inv_design_collections,
                       inv_design_patterns_resolved, inv_design_scenarios_measurable,
                       inv_design_techniques_linked,
                       inv_design_elements_grounded_or_reported],
    },
]


def _checks(case: Dict[str, Any], result, out: Dict[str, Any]) -> List[tuple]:
    """Run a case's invariants and return (name, passed, detail) triples.

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
    checks = []
    for inv in case["invariants"]:
        try:
            passed, detail = inv(result_view, out)
        except Exception as e:                                  # noqa: BLE001
            passed, detail = False, f"raised {type(e).__name__}: {e}"
        checks.append((inv.__name__, passed, detail))
    return checks


def _run_design_case(case: Dict[str, Any]) -> Dict[str, Any]:
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
    baseline = store.load_revision(baselines[0].id).graph if baselines else None
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
        "baseline": baseline,
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
        "checks": _checks(case, result, out),
    }


def run_case(case: Dict[str, Any]) -> Dict[str, Any]:
    if case["agent"] == "design_assistant":
        return _run_design_case(case)

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
        "checks": _checks(case, result, out),
    }


def validate_saved(case: Dict[str, Any]) -> Dict[str, Any]:
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

    checks = []
    for inv in case["invariants"]:
        try:
            passed, detail = inv(_Res(), blob)
        except Exception as e:                                  # noqa: BLE001
            passed, detail = False, f"raised {type(e).__name__}: {e}"
        checks.append((inv.__name__, passed, detail))

    return {"case": case["name"], "agent": case["agent"],
            "input": case.get("input", ""), "output": case["output"], "elapsed": 0.0,
            "path": blob.get("metadata", {}).get("extraction_path"),
            "checks": checks}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="run one case by name")
    ap.add_argument("--validate-only", action="store_true",
                    help="re-check saved outputs without re-running extraction "
                         "(validation is instant; extraction takes minutes)")
    args = ap.parse_args()

    cases = [c for c in CASES if not args.only or c["name"] == args.only]
    results = []
    for case in cases:
        print(f"\n{'=' * 72}\nCASE {case['name']}  ({case['agent']})\n{'=' * 72}")
        if case.get("input"):
            print(f"  input : {case['input']}")
        if args.validate_only:
            r = validate_saved(case)
            if r is None:
                print("  (no saved output — run without --validate-only first)")
                continue
        else:
            r = run_case(case)
        results.append(r)
        print(f"  output: {r['output']}")
        print(f"  path  : {r['path']}   elapsed: {r['elapsed']:.0f}s")
        for name, passed, detail in r["checks"]:
            print(f"    [{'PASS' if passed else 'FAIL'}] {name:<32} {detail}")

    print(f"\n{'=' * 72}\nSUMMARY\n{'=' * 72}")
    total = passed_n = 0
    for r in results:
        p = sum(1 for _, ok, _ in r["checks"] if ok)
        t = len(r["checks"])
        total += t
        passed_n += p
        print(f"  {r['case']:<14} {p}/{t} invariants   {r['elapsed']:.0f}s   {r['path']}")
    print(f"\n  TOTAL: {passed_n}/{total} invariants passed")
    return 0 if passed_n == total else 1


if __name__ == "__main__":
    sys.exit(main())
