#!/usr/bin/env python3
"""
Run one real document through the real extractor and the real ingest route.

WHY THIS EXISTS. Every completeness, cross-graph and quality reading in
`data/output/` is taken from saved fixtures, and those fixtures predate the work
they are quoted for: they carry no `passes` metadata, so they read `UNKNOWN`, and
they contain no `QualityAttribute` nodes, so any census over them is empty by
construction. A number measured against them says what the code does to an old
payload, not what it does to a document.

This runs the thing itself: the configured model, the real profile, the Flask
ingest route, the working set on disk. It then prints the three readings the
saved fixtures cannot give —

  1. extraction completeness   (does the run describe itself?)
  2. the cross-graph join      (which requirements an architecture answers)
  3. quality-attribute coverage (what the quality joining converges on)

Usage:
    .venv/bin/python scripts/run_real_ingest.py test_data/prd/sample_requirements.md
    .venv/bin/python scripts/run_real_ingest.py doc.md --type architecture --dry-run

`--dry-run` extracts and reports without writing to the working set: the only
honest way to look at a run's shape before letting it into the graph.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from core.knowledge import RevisionStore, completeness_note, quality_report, realization_report


def build_app(store_root: str):
    app = create_app(store_root=store_root)
    app.config["TESTING"] = True
    return app


def ingest(app, path: Path, doc_type: str, domain_pack: str, initiative: str):
    """POST the document to the real route and return (status, flashes)."""
    text = path.read_text(encoding="utf-8")
    client = app.test_client()
    response = client.post(
        "/ingest",
        data={
            "document": (io.BytesIO(text.encode("utf-8")), path.name),
            "type": doc_type,
            "domain_pack": domain_pack,
            "initiative_id": initiative,
            "revision_label": f"Real run · {path.name}",
        },
        content_type="multipart/form-data",
    )
    with client.session_transaction() as session:
        flashes = list(session.get("_flashes", []))
    return response, flashes


# ============================================================================
# Readings
# ============================================================================


def read_runs(graph) -> None:
    print("\n" + "=" * 72)
    print("1. EXTRACTION COMPLETENESS — does each run describe itself?")
    print("=" * 72)
    print(completeness_note(graph))
    print()
    for run in sorted(graph.runs.values(), key=lambda r: r.started_at):
        print(f"  {run.id}")
        print(f"    document       {run.document_ref}")
        print(f"    type           {run.document_type}")
        print(f"    model          {run.model_id or '(missing)'}")
        print(f"    completeness   {run.completeness}")
        print(f"    passes         {len(run.passes)}")
        for p in run.passes:
            print(
                f"      - {p.pass_name:14s} outcome={p.outcome:7s} "
                f"path={p.path or '-':12s} triples={p.triples_produced}"
            )


def read_join(graph) -> None:
    print("\n" + "=" * 72)
    print("2. CROSS-GRAPH JOIN — which requirements an architecture answers")
    print("=" * 72)
    report = realization_report(graph)
    for key, value in report["summary"].items():
        print(f"  {key:22s} {json.dumps(value)}")
    print("\n  per-requirement coverage:")
    for record in report["requirements"]:
        print(
            f"    {record['coverage']:11s} {record['kind']:26s} "
            f"{record['label'][:40]:42s} "
            f"bound={len(record['bound'])} unbound={len(record['unbound'])}"
        )


def read_quality(graph) -> None:
    print("\n" + "=" * 72)
    print("3. QUALITY-ATTRIBUTE COVERAGE — the census, grouped by canonical concern")
    print("=" * 72)
    summary = quality_report(graph)["summary"]
    for key in (
        "attributes", "attribute_nodes", "merged_labels", "resolved", "unresolved",
        "characteristics", "stated", "delivered", "unasked", "architecture_gaps",
    ):
        print(f"  {key:22s} {summary[key]}")
    print(f"  {'states':22s} {summary['states']}")
    if summary["unpopulated_states"]:
        print(f"  {'empty states':22s} {', '.join(summary['unpopulated_states'])}")

    print("\n  by characteristic:")
    for row in quality_report(graph)["characteristics"]:
        print(f"    {row['label']:26s} {row['counts']}")
        for concern in row["concerns"]:
            aliases = (
                f"  aliases={concern['aliases']}" if len(concern["aliases"]) > 1 else ""
            )
            print(
                f"      {concern['display'][:26]:28s} {concern['coverage']:18s}"
                f" {concern['counts']}{aliases}"
            )

    print("\n  delivered but no requirement states it (the inverse direction):")
    for concern in quality_report(graph)["unasked"]:
        print(f"    {concern['display']:26s} {concern['category_label']}")
    print("  stated but nothing delivers it:")
    for concern in quality_report(graph)["architecture_gaps"]:
        print(f"    {concern['display']:26s} {concern['category_label']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("document", type=Path)
    parser.add_argument("--type", default="requirements", choices=["requirements", "architecture"])
    parser.add_argument("--domain-pack", default="payment_processing")
    parser.add_argument("--initiative", default="PSYA-I2001")
    parser.add_argument("--store-root", default="data/sea")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and report only; do not merge into the working set.",
    )
    args = parser.parse_args()

    if not args.document.exists():
        print(f"no such document: {args.document}", file=sys.stderr)
        return 2

    store_root = args.store_root
    if args.dry_run:
        # A throwaway store: the real route still runs end to end, the real
        # working set is untouched.
        store_root = "data/scratch/dry-run-sea"

    before = RevisionStore(store_root).ensure().load_working()

    app = build_app(store_root)
    print(f"ingest   {args.document}  type={args.type}  pack={args.domain_pack}")
    response, flashes = ingest(app, args.document, args.type, args.domain_pack, args.initiative)
    print(f"route    HTTP {response.status_code}")
    for category, message in flashes:
        print(f"flash    [{category}] {message}")

    after = RevisionStore(store_root).ensure().load_working()
    graph = after.graph

    print("\n" + "=" * 72)
    print("DELTA")
    print("=" * 72)
    print(f"  nodes       {len(before.graph.nodes)} -> {len(graph.nodes)}")
    print(f"  assertions  {len(before.graph.assertions)} -> {len(graph.assertions)}")
    print(f"  runs        {len(before.graph.runs)} -> {len(graph.runs)}")

    read_runs(graph)
    read_join(graph)
    read_quality(graph)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
