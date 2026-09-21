#!/usr/bin/env python3
"""
Simple table-view proof of concept for human review of extracted assertions.

Reads an extraction output JSON, ingests it into the canonical model, and
displays assertions in a tabular format for human review.

Usage:
    .venv/bin/python scripts/review_assertions.py data/output/test_arch.json
    .venv/bin/python scripts/review_assertions.py data/output/test_req.json
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.knowledge import graph_from_extraction
from agents.knowledge.model import (
    STATUS_UNVERIFIED,
    STATUS_VERIFIED,
    STATUS_CORRECTED,
    SOURCE_EXTRACTION,
)


def truncate(text: str, max_len: int = 60) -> str:
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def display_table(graph, run):
    """Display assertions in a simple table format."""
    print("\n" + "=" * 120)
    print(f"EXTRACTION REVIEW — {run.document_ref}")
    print(f"Model: {run.model_id} | Run ID: {run.id}")
    print(f"Completeness: {run.completeness} | Passes: {len(run.passes)}")
    print("=" * 120)

    # Summary stats
    stats = graph.stats()
    print(f"\nNodes: {stats['nodes']} | Assertions: {stats['assertions']} | "
          f"Human corrections: {stats['human_assertions']} | "
          f"Dangling: {stats['dangling']} | Unresolved refs: {stats['unresolved_references']}")

    if stats["node_kinds"]:
        print(f"Node kinds: {', '.join(f'{k}({v})' for k, v in stats['node_kinds'].items())}")

    print("\n" + "-" * 120)
    print("ASSERTIONS FOR REVIEW")
    print("-" * 120)

    # Header
    header = (
        f"{'ID':<18} "
        f"{'Subject':<30} "
        f"{'Predicate':<25} "
        f"{'Object/Value':<30} "
        f"{'Conf':<5} "
        f"{'Status':<12} "
        f"{'Source':<18} "
        f"{'Source Text'}"
    )
    print(header)
    print("-" * 120)

    # Sort assertions by subject, then predicate for readability
    sorted_assertions = sorted(
        graph.active(),
        key=lambda a: (a.subject, a.predicate, a.target)
    )

    for a in sorted_assertions:
        node = graph.nodes.get(a.subject)
        subject_label = node.label if node else a.subject
        subject_kind = node.kind if node else "?"

        obj_display = a.object or a.value or ""
        if a.object:
            obj_node = graph.nodes.get(a.object)
            if obj_node:
                obj_display = f"[{obj_node.kind}] {obj_node.label}"

        source_short = a.provenance.source_type.replace("SOURCE_", "")
        if a.provenance.run_id:
            source_short += f" ({a.provenance.run_id[:8]})"

        row = (
            f"{a.id:<18} "
            f"[{subject_kind}] {truncate(subject_label, 27):<27} "
            f"{a.predicate:<25} "
            f"{truncate(obj_display, 28):<28} "
            f"{a.confidence:<5.2f} "
            f"{a.status:<12} "
            f"{source_short:<18} "
            f"{truncate(a.source_text, 40)}"
        )
        print(row)

    print("-" * 120)
    print(f"\nTotal assertions shown: {len(sorted_assertions)}")

    # Show unresolved references separately
    unresolved = graph.unresolved_references()
    if unresolved:
        print("\n" + "=" * 120)
        print("UNRESOLVED CROSS-GRAPH REFERENCES (need reconciliation)")
        print("=" * 120)
        for a in unresolved:
            node = graph.nodes.get(a.subject)
            subject_label = node.label if node else a.subject
            print(f"  [{a.subject}] {a.predicate} → '{a.target}'")
        print(f"\nTotal unresolved: {len(unresolved)}")

    # Show dangling assertions
    dangling = graph.dangling_assertions()
    if dangling:
        print("\n" + "=" * 120)
        print("DANGLING ASSERTIONS (point to non-existent nodes)")
        print("=" * 120)
        for a in dangling:
            print(f"  {a.subject} --{a.predicate}--> {a.object or a.value}")
        print(f"\nTotal dangling: {len(dangling)}")


def interactive_review(graph):
    """Simple interactive loop to verify or correct assertions."""
    print("\n" + "=" * 120)
    print("INTERACTIVE REVIEW MODE")
    print("=" * 120)
    print("Commands:")
    print("  v <assertion_id>  — Mark as VERIFIED")
    print("  c <assertion_id> <new_value> — Mark as CORRECTED with new value")
    print("  s <assertion_id> — Show full details of an assertion")
    print("  q — Quit without saving")
    print("  w — Write corrected graph to JSON")
    print("=" * 120)

    while True:
        try:
            cmd = input("\nreview> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not cmd:
            continue

        parts = cmd.split(None, 2)
        action = parts[0].lower()

        if action == "q":
            print("Exiting without saving.")
            break

        elif action == "w":
            output_path = Path("data/output/reviewed_graph.json")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            # Serialize the graph
            data = {
                "nodes": {nid: n.to_dict() for nid, n in graph.nodes.items()},
                "assertions": {aid: a.to_dict() for aid, a in graph.assertions.items()},
                "runs": {rid: r.to_dict() for rid, r in graph.runs.items()},
            }
            output_path.write_text(json.dumps(data, indent=2))
            print(f"Graph written to {output_path}")

        elif action == "v" and len(parts) >= 2:
            aid = parts[1]
            if aid in graph.assertions:
                graph.assertions[aid].status = STATUS_VERIFIED
                print(f"✓ Assertion {aid} marked as VERIFIED")
            else:
                print(f"✗ Assertion {aid} not found")

        elif action == "c" and len(parts) >= 3:
            aid = parts[1]
            new_value = parts[2]
            if aid in graph.assertions:
                a = graph.assertions[aid]
                a.value = new_value
                a.status = STATUS_CORRECTED
                print(f"✓ Assertion {aid} corrected to: '{new_value}'")
            else:
                print(f"✗ Assertion {aid} not found")

        elif action == "s" and len(parts) >= 2:
            aid = parts[1]
            if aid in graph.assertions:
                a = graph.assertions[aid]
                print(f"\n  ID:           {a.id}")
                print(f"  Subject:      {a.subject}")
                print(f"  Predicate:    {a.predicate}")
                print(f"  Object:       {a.object}")
                print(f"  Value:        {a.value}")
                print(f"  Confidence:   {a.confidence}")
                print(f"  Status:       {a.status}")
                print(f"  Ontology:     {a.ontology_class}")
                print(f"  Source Text:  {a.source_text}")
                print(f"  Provenance:   {a.provenance.to_dict()}")
            else:
                print(f"✗ Assertion {aid} not found")

        else:
            print(f"Unknown command: {cmd}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/review_assertions.py <extraction_output.json>")
        print("\nExample files:")
        print("  data/output/test_arch.json")
        print("  data/output/test_req.json")
        return 1

    input_path = Path(sys.argv[1])
    if not input_path.exists():
        print(f"File not found: {input_path}")
        return 1

    print(f"Loading: {input_path}")
    blob = json.loads(input_path.read_text())

    # Separate metadata from extraction output
    metadata = blob.get("metadata", {})
    output = {k: v for k, v in blob.items()
              if k not in ("metadata", "case", "input_file")}

    # Ingest into canonical model
    graph, run = graph_from_extraction(output, metadata, document_ref=str(input_path))

    # Display table
    display_table(graph, run)

    # Interactive review mode
    interactive_review(graph)

    return 0


if __name__ == "__main__":
    sys.exit(main())
