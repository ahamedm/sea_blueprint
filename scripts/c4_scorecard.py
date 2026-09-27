"""C4 readiness scorecard — measure what the graph can and cannot specify.

WHY THIS EXISTS
---------------
"There is no C4 view" was answered by building one (`app/viewpoints/c4.py`). What is
left is not a missing feature but a quality question: *how good is the C4 model this
graph can produce?* That question was previously answered by looking at the page and
forming an impression, which is exactly the kind of judgement that cannot be improved
on, because a change and its effect are not comparable.

This prints the numbers the view already computes internally — level census,
arrow count, containment violations, stated-vs-inferred levels, extraction defects —
plus the per-pass outcomes of the runs that produced the graph. Same graph, same
numbers, so a prompt change or an ingest guard can be judged by what it moved.

It reports and does not judge: a low score is not an error, it is a measurement. The
one interpretation it offers is `READY`, and that is defined explicitly at the bottom
of the report rather than left to the reader.

Usage
-----
    uv run scripts/c4_scorecard.py                        # default scope
    uv run scripts/c4_scorecard.py --scope acme_pillar_01
    uv run scripts/c4_scorecard.py --json                 # for a before/after diff
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.viewpoints import c4  # noqa: E402
from core.workspace import WorkspaceError, load_workspace  # noqa: E402

#: Readiness thresholds, stated as data because "READY" that lives in an `if` is a
#: claim nobody can audit. Each is a floor the graph must clear, with the reason.
READY_RULES = (
    ("levels_stated", "every structural element states its C4 level", 1.0),
    ("nesting", "every element sits inside the level above it", 1.0),
    ("reflexive", "no containment is self-referential", 1.0),
    ("acyclic", "containment is a tree", 1.0),
    ("arrows", "at least one relationship is drawn at container level", None),
    ("runs_complete", "every run behind the graph is COMPLETE", 1.0),
)


def _workspace_root(explicit: str) -> str:
    import os

    return explicit or os.getenv("SEA_DATA_DIR", "data")


def _graph(root: str, scope_id: str):
    workspace = load_workspace(root)
    return workspace, workspace.open_store(scope_id or None).load_working().graph


def _element_report(model: dict) -> dict:
    elements = model["elements"]
    by_kind = Counter(e["kind"] for e in elements)
    stated = sum(1 for e in elements if e["level_source"] == "c4_level")
    with_tech = sum(1 for e in elements if e["technology"])
    no_parent = [e["label"] for e in elements if not e["parent_label"]]
    return {
        "total": len(elements),
        "by_level": {level: len(model["by_level"][level]) for level in c4.LEVELS},
        "by_kind": dict(by_kind.most_common()),
        "level_stated": stated,
        "level_inferred": len(elements) - stated,
        "levels_stated_ratio": (stated / len(elements)) if elements else 1.0,
        "with_technology": with_tech,
        "no_parent": no_parent,
    }


def _relationship_report(model: dict) -> dict:
    by_via = Counter(r["via"] for r in model["relationships"])
    protocols = Counter(r["protocol"] for r in model["relationships"] if r["protocol"])
    return {
        "total": len(model["relationships"]),
        "by_source": dict(by_via),
        "protocols": dict(protocols.most_common()),
        "labelled": sum(1 for r in model["relationships"]
                        if r["label"] and r["label"] != "connects"),
    }


def _run_report(graph) -> dict:
    runs = getattr(graph, "runs", {}) or {}
    passes = Counter()
    failures = []
    for run_id, run in runs.items():
        for record in getattr(run, "passes", []) or []:
            name = getattr(record, "pass_name", "?")
            outcome = getattr(record, "outcome", "?")
            passes[f"{name}:{outcome}"] += 1
            if outcome == "failed":
                failures.append({
                    "run": run_id,
                    "pass": name,
                    "chunk": getattr(record, "chunk_label", ""),
                    "elapsed": getattr(record, "elapsed", None),
                    "error": str(getattr(record, "error", "") or "")[:160],
                })
    complete = [r for r in runs.values() if getattr(r, "completeness", "") == "COMPLETE"]
    return {
        "runs": len(runs),
        "complete": len(complete),
        "completeness_ratio": (len(complete) / len(runs)) if runs else 1.0,
        "pass_outcomes": dict(passes.most_common()),
        "failures": failures,
        "documents": sorted({getattr(r, "document_ref", "") for r in runs.values()} - {""}),
    }


def _defect_report(model: dict) -> dict:
    gaps = Counter(g["kind"] for g in model["gaps"])
    duplicates = next((g for g in model["gaps"] if g["kind"] == "duplicate-of-concept"), None)
    return {
        "gaps_by_kind": dict(gaps.most_common()),
        "duplicates": int(str(duplicates["label"]).split()[0]) if duplicates else 0,
        "excluded_non_c4": model["counts"]["excluded_count"],
        "excluded_kinds": model["counts"]["excluded_kinds"],
    }


def scorecard(graph) -> dict:
    """Every number the report shows, as data — the JSON and the text agree."""
    model = c4.c4_model(graph)
    elements = _element_report(model)
    relationships = _relationship_report(model)
    runs = _run_report(graph)
    defects = _defect_report(model)
    checks = {c["name"]: c for c in model["checks"]}

    verdict = []
    for name, description, floor in READY_RULES:
        if name == "arrows":
            held = relationships["total"] > 0
            value = relationships["total"]
        elif name == "levels_stated":
            held = elements["levels_stated_ratio"] >= (floor or 0)
            value = round(elements["levels_stated_ratio"], 3)
        elif name == "runs_complete":
            held = runs["completeness_ratio"] >= (floor or 0)
            value = round(runs["completeness_ratio"], 3)
        else:
            check = checks.get(name, {"holds": None, "violations": 0, "total": 0})
            held = bool(check["holds"])
            value = check["violations"]
        verdict.append({"rule": name, "claim": description, "held": held, "value": value})

    drawn, drawn_relationships, rolled, undrawable = c4.roll_up(model, "container")
    return {
        "system": (model["system"] or {}).get("label", ""),
        "elements": elements,
        "relationships": relationships,
        "runs": runs,
        "defects": defects,
        "checks": [{"name": c["name"], "title": c["title"], "holds": c["holds"],
                    "violations": c["violations"], "total": c["total"],
                    "examples": c["examples"]} for c in model["checks"]],
        "container_diagram": {
            "boxes": len(drawn),
            "arrows": len(drawn_relationships),
            "rolled_up": rolled,
            "undrawable": undrawable,
        },
        "verdict": verdict,
        "ready": all(v["held"] for v in verdict),
    }


def render(report: dict) -> str:
    out: list[str] = []
    add = out.append
    add("=" * 78)
    add(f"C4 READINESS — system: {report['system'] or '[none identified]'}")
    add("=" * 78)

    elements = report["elements"]
    add(f"\nELEMENTS  {elements['total']}")
    add("  by level: " + " · ".join(f"{k} {v}" for k, v in elements["by_level"].items()))
    add(f"  level stated by the extraction: {elements['level_stated']}"
        f"   inferred from node kind: {elements['level_inferred']}")
    add("  by kind: " + " · ".join(f"{k} {v}" for k, v in list(elements["by_kind"].items())[:10]))
    if elements["no_parent"]:
        # Informational, not a defect: the system under design and every external
        # party are SUPPOSED to be top-level. Only a container/component/code with no
        # parent is a hole, and that is the `nesting` check's job below.
        add(f"  top-level (no boundary, expected for the system and external parties): "
            f"{len(elements['no_parent'])} — {', '.join(elements['no_parent'][:6])}")

    relationships = report["relationships"]
    add(f"\nRELATIONSHIPS  {relationships['total']}   (the C4 arrows)")
    if relationships["total"]:
        add(f"  from Connection nodes: {relationships['by_source'].get('connection', 0)}"
            f"   from bare edges: {relationships['by_source'].get('edge', 0)}")
        add(f"  labelled: {relationships['labelled']}   protocols: "
            + (", ".join(f"{k} {v}" for k, v in relationships["protocols"].items()) or "none"))
    else:
        add("  NONE — a box chart. The C4 view will draw no arrow at all.")

    diagram = report["container_diagram"]
    add(f"\nCONTAINER DIAGRAM  {diagram['boxes']} box(es), {diagram['arrows']} arrow(s)")
    add(f"  rolled up from a lower level: {diagram['rolled_up']}")
    if diagram["undrawable"]:
        add(f"  cannot be drawn at this level: {len(diagram['undrawable'])}"
            f" — {', '.join(diagram['undrawable'][:4])}")

    runs = report["runs"]
    add(f"\nRUNS  {runs['runs']}   COMPLETE {runs['complete']}")
    if runs["documents"]:
        add("  documents: " + ", ".join(runs["documents"]))
    if runs["pass_outcomes"]:
        add("  pass outcomes: " + " · ".join(f"{k} {v}" for k, v in runs["pass_outcomes"].items()))
    for failure in runs["failures"]:
        add(f"  FAILED {failure['pass']} ({failure['chunk']}, {failure['elapsed']}s): "
            f"{failure['error']}")

    defects = report["defects"]
    add(f"\nDEFECTS")
    add(f"  non-C4 concepts excluded by design: {defects['excluded_non_c4']}")
    add(f"  extracted twice (element + concept): {defects['duplicates']}")
    for kind, count in defects["gaps_by_kind"].items():
        add(f"  {kind}: {count}")

    add("\nCHECKS")
    for check in report["checks"]:
        mark = "PASS" if check["holds"] else "FAIL"
        add(f"  [{mark}] {check['title']}  ({check['violations']}/{check['total']})")
        if not check["holds"] and check["examples"]:
            add(f"         e.g. {', '.join(check['examples'][:3])}")

    add("\nVERDICT")
    for item in report["verdict"]:
        add(f"  [{'x' if item['held'] else ' '}] {item['claim']}  (value={item['value']})")
    add(f"\n  READY: {'yes' if report['ready'] else 'NO'}")
    add("=" * 78)
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure C4 readiness of a scope's graph")
    parser.add_argument("--root", default="", help="workspace root (default $SEA_DATA_DIR)")
    parser.add_argument("--scope", default="", help="scope id (default: the only scope)")
    parser.add_argument("--json", action="store_true", help="emit the numbers as JSON")
    args = parser.parse_args(argv)

    try:
        _workspace, graph = _graph(_workspace_root(args.root), args.scope)
    except WorkspaceError as exc:
        print(f"workspace error: {exc}", file=sys.stderr)
        return 2

    report = scorecard(graph)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=False, default=str))
    else:
        print(render(report))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
