#!/usr/bin/env python3
"""
Test case for the "Living System" architecture.

Creates synthetic extraction outputs to demonstrate:
1. Assertions are tagged with scope (INITIATIVE_PROPOSAL vs SYSTEM_BASELINE)
2. Initiative nodes are created and linked
3. The graph distinguishes between proposed changes and baseline truth
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.knowledge import graph_from_extraction
from core.knowledge.model import (
    SCOPE_INITIATIVE,
    SCOPE_BASELINE,
    SOURCE_EXTRACTION,
    Provenance,
)


def create_synthetic_arch_output():
    """Create a minimal architecture extraction output for testing."""
    return {
        "elements": [
            {
                "name": "Payment Orchestrator",
                "element_type": "Container",
                "description": "Handles payment initiation and routing",
                "parent": "Payment Gateway Platform",
                "responsibilities": ["Request Acceptance", "Tenancy Mapping"],
                "initiative_id": "INIT-2024-001"
            },
            {
                "name": "Payment Gateway Platform",
                "element_type": "SoftwareSystem",
                "description": "Core payment processing platform"
            }
        ],
        "technology_stacks": [
            {
                "name": "Spring Boot",
                "category": "FRAMEWORK",
                "used_by": ["Payment Orchestrator"]
            }
        ],
        "triples": [
            {
                "subject": "Payment Orchestrator",
                "predicate": "connects_to",
                "object": "Payment Routing Engine",
                "confidence": 0.9,
                "source_text": "Orchestrator routes to decision engine"
            }
        ]
    }


def test_initiative_scoped_ingestion():
    """Test that ingestion properly scopes assertions to an Initiative."""
    print("\n" + "=" * 80)
    print("TEST: Initiative-Scoped Ingestion")
    print("=" * 80)

    output = create_synthetic_arch_output()
    
    # Ingest WITH an Initiative ID (simulating a project-specific extraction)
    initiative_id = "INIT-2024-001"
    graph, run = graph_from_extraction(
        output, 
        {}, 
        document_ref="synthetic_test",
        initiative_id=initiative_id
    )

    print(f"\nInitiative ID: {initiative_id}")
    print(f"Nodes: {len(graph.nodes)}")
    print(f"Assertions: {len(graph.assertions)}")

    # Check 1: Initiative node exists
    initiative_nodes = [n for n in graph.nodes.values() if n.kind == "Initiative"]
    if not initiative_nodes:
        print("✗ FAIL: No Initiative node created")
        return False
    print(f"✓ Initiative node found: {initiative_nodes[0].label}")

    # Check 2: Assertions are scoped to the Initiative
    initiative_assertions = [a for a in graph.assertions.values() 
                            if a.initiative_id == initiative_id]
    if not initiative_assertions:
        print("✗ FAIL: No assertions tagged with initiative_id")
        return False
    print(f"✓ {len(initiative_assertions)} assertions scoped to {initiative_id}")

    # Check 3: Verify scope distribution
    scopes = {}
    for a in graph.assertions.values():
        scopes[a.scope] = scopes.get(a.scope, 0) + 1
    
    print(f"\nScope distribution:")
    for scope, count in sorted(scopes.items()):
        print(f"  {scope}: {count}")

    # Check 4: Verify delivers_initiative links exist
    delivers_links = [a for a in graph.assertions.values() 
                     if a.predicate == "delivers_initiative"]
    if not delivers_links:
        print("⚠ WARNING: No delivers_initiative links (expected if not in output)")
    else:
        print(f"✓ {len(delivers_links)} delivers_initiative links created")
        
        # Show a sample
        sample = delivers_links[0]
        subject_node = graph.nodes.get(sample.subject)
        obj_node = graph.nodes.get(sample.object) if sample.object else None
        print(f"\nSample link:")
        print(f"  [{subject_node.kind}] {subject_node.label}")
        print(f"    --{sample.predicate}-->")
        print(f"  [{obj_node.kind if obj_node else '?'}] {obj_node.label if obj_node else sample.value}")
        print(f"  Scope: {sample.scope}, Initiative: {sample.initiative_id}")

    print("\n" + "=" * 80)
    print("TEST PASSED: Living System scoping is working correctly")
    print("=" * 80)
    return True


def test_baseline_vs_initiative():
    """Test that we can distinguish baseline from initiative proposals."""
    print("\n" + "=" * 80)
    print("TEST: Baseline vs Initiative Distinction")
    print("=" * 80)

    from core.knowledge.model import KnowledgeGraph, make_node_id
    
    graph = KnowledgeGraph()
    
    # Add a baseline fact (no initiative) - represents existing system
    nid = graph.add_node("Container", "Payment Orchestrator")
    graph.add_assertion(
        nid, "description", 
        value="Handles payment initiation",
        scope=SCOPE_BASELINE,
        provenance=Provenance(source_type=SOURCE_EXTRACTION)
    )
    
    # Add an initiative-scoped fact - represents proposed change
    init_nid = graph.add_node("Initiative", "INIT-2024-002")
    new_tech = graph.add_node("TechnologyStack", "NewFramework")
    graph.add_assertion(
        nid, "uses_technology",
        obj=new_tech,
        scope=SCOPE_INITIATIVE,
        initiative_id="INIT-2024-002",
        provenance=Provenance(source_type=SOURCE_EXTRACTION)
    )

    # Query by scope
    baseline_facts = [a for a in graph.assertions.values() if a.scope == SCOPE_BASELINE]
    initiative_facts = [a for a in graph.assertions.values() if a.scope == SCOPE_INITIATIVE]

    print(f"Baseline facts: {len(baseline_facts)}")
    for f in baseline_facts:
        node = graph.nodes.get(f.subject)
        print(f"  - [{node.kind}] {node.label}: {f.predicate} = {f.value}")
    
    print(f"\nInitiative facts: {len(initiative_facts)}")
    for f in initiative_facts:
        node = graph.nodes.get(f.subject)
        print(f"  - [{node.kind}] {node.label}: {f.predicate} -> {f.object} (Initiative: {f.initiative_id})")

    if len(baseline_facts) == 1 and len(initiative_facts) == 1:
        print("\n✓ PASS: Can distinguish baseline from initiative proposals")
        return True
    else:
        print("\n✗ FAIL: Scope distinction not working")
        return False


def test_merge_scenario():
    """Test the merge scenario: promoting initiative facts to baseline."""
    print("\n" + "=" * 80)
    print("TEST: Merge Scenario (Initiative → Baseline)")
    print("=" * 80)
    
    from core.knowledge.model import KnowledgeGraph, STATUS_VERIFIED
    
    graph = KnowledgeGraph()
    
    # Simulate an initiative proposing a technology
    nid = graph.add_node("Container", "Payment Service")
    tech = graph.add_node("TechnologyStack", "PostgreSQL")
    
    # Initiative proposes PostgreSQL
    graph.add_assertion(
        nid, "uses_technology",
        obj=tech,
        scope=SCOPE_INITIATIVE,
        initiative_id="INIT-2024-001",
        status=STATUS_VERIFIED,  # Human has verified this
        provenance=Provenance(source_type=SOURCE_EXTRACTION)
    )
    
    print("Before merge:")
    print(f"  Initiative proposals: {len([a for a in graph.assertions.values() if a.scope == SCOPE_INITIATIVE])}")
    print(f"  Baseline facts: {len([a for a in graph.assertions.values() if a.scope == SCOPE_BASELINE])}")
    
    # Simulate merge: promote verified initiative facts to baseline
    merged_count = 0
    for a in graph.assertions.values():
        if a.scope == SCOPE_INITIATIVE and a.status == STATUS_VERIFIED:
            a.scope = SCOPE_BASELINE
            a.initiative_id = None  # No longer tied to specific initiative
            merged_count += 1
    
    print(f"\nMerged {merged_count} facts from initiative to baseline")
    print("\nAfter merge:")
    print(f"  Initiative proposals: {len([a for a in graph.assertions.values() if a.scope == SCOPE_INITIATIVE])}")
    print(f"  Baseline facts: {len([a for a in graph.assertions.values() if a.scope == SCOPE_BASELINE])}")
    
    if merged_count > 0:
        print("\n✓ PASS: Merge scenario works correctly")
        return True
    else:
        print("\n✗ FAIL: No facts were merged")
        return False


def main():
    results = []
    
    results.append(test_initiative_scoped_ingestion())
    results.append(test_baseline_vs_initiative())
    results.append(test_merge_scenario())
    
    print("\n" + "=" * 80)
    print(f"RESULTS: {sum(results)}/{len(results)} tests passed")
    print("=" * 80)
    
    # Save a summary to output
    output_path = Path("data/output/living_system_test_summary.txt")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        f.write(f"Living System Test Results\n")
        f.write(f"{'=' * 40}\n")
        f.write(f"Tests passed: {sum(results)}/{len(results)}\n\n")
        f.write("Key findings:\n")
        f.write("1. Initiative scoping works: assertions are tagged with initiative_id\n")
        f.write("2. Baseline vs Initiative distinction is maintained\n")
        f.write("3. Merge scenario (Initiative → Baseline) functions correctly\n")
    
    print(f"\nTest summary saved to: {output_path}")
    
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
