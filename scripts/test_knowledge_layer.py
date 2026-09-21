#!/usr/bin/env python3
"""
Tests for the canonical knowledge layer.

Covers the properties the architecture review said were load-bearing:
stable identity, duplicate collapse, the agent-vs-human distinction, supersession,
incompleteness as first-class state, and the two-form RDF output.

Run:
    .venv/bin/python scripts/test_knowledge_layer.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.knowledge import graph_from_extraction, to_rdf, run_query
from agents.knowledge.model import (
    KnowledgeGraph,
    Provenance,
    STATUS_VERIFIED,
    SOURCE_EXTRACTION,
    SOURCE_HUMAN_ARCHITECT,
    RUN_COMPLETE,
    RUN_UNKNOWN,
    RUN_PARTIAL,
    PassRecord,
    make_node_id,
)
from agents.knowledge.rdf import SEA, PROV

RESULTS = []


def check(name, condition, detail=""):
    RESULTS.append((name, bool(condition), detail))


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------

def test_identity_is_stable_and_derived():
    a = make_node_id("Container", "Payment Orchestrator")
    b = make_node_id("Container", "payment  orchestrator")
    c = make_node_id("Container", "Settlement Service")

    check("identity stable across formatting", a == b, f"{a} == {b}")
    check("identity distinct for distinct nodes", a != c)
    check("identity namespaced by kind",
          make_node_id("Container", "X") != make_node_id("DataStore", "X"))

    # The property that matters: re-extraction converges rather than duplicating.
    g1 = KnowledgeGraph(); g2 = KnowledgeGraph()
    g1.add_node("Container", "Payment Orchestrator")
    g2.add_node("Container", "Payment Orchestrator")
    check("re-run converges on same node id", set(g1.nodes) == set(g2.nodes))


# ---------------------------------------------------------------------------
# Duplicate collapse
# ---------------------------------------------------------------------------

def test_duplicates_collapse_keeping_the_best_observation():
    g = KnowledgeGraph()
    g.add_assertion("n:a", "part_of", obj="n:b", confidence=0.6,
                    source_text="short", provenance=Provenance(source_type=SOURCE_EXTRACTION))
    g.add_assertion("n:a", "part_of", obj="n:b", confidence=0.95,
                    source_text="a much longer and fuller source sentence",
                    provenance=Provenance(source_type=SOURCE_EXTRACTION))

    check("duplicate assertions collapse", len(g.assertions) == 1, f"{len(g.assertions)}")
    a = next(iter(g.assertions.values()))
    check("collapse keeps highest confidence", a.confidence == 0.95, f"{a.confidence}")
    check("collapse keeps fuller source text", len(a.source_text) > 20)


# ---------------------------------------------------------------------------
# The correction-merge requirement (TODO 9b)
# ---------------------------------------------------------------------------

def test_human_assertion_survives_agent_reobservation():
    g = KnowledgeGraph()
    human = Provenance(source_type=SOURCE_HUMAN_ARCHITECT, asserted_by="ahmed",
                       correction_note="corrected ownership")
    agent = Provenance(source_type=SOURCE_EXTRACTION, run_id="run_2",
                       model_id="some-model")

    g.add_assertion("n:x", "system_class", value="BUSINESS_APPLICATION",
                    confidence=0.8, provenance=human, status=STATUS_VERIFIED)
    # A later extraction run proposes something different for the same predicate
    # and value... same assertion id, so it folds rather than duplicating.
    g.add_assertion("n:x", "system_class", value="BUSINESS_APPLICATION",
                    confidence=0.99, provenance=agent)

    a = next(iter(g.assertions.values()))
    check("human provenance survives agent re-observation", a.is_human,
          f"source={a.provenance.source_type}")
    check("human verification status preserved",
          a.status == STATUS_VERIFIED, f"status={a.status}")
    check("agent did not create a duplicate", len(g.assertions) == 1)


def test_supersession_is_excluded_from_active():
    g = KnowledgeGraph()
    old = g.add_assertion("n:x", "responsibility", value="legacy wording",
                          provenance=Provenance(source_type=SOURCE_EXTRACTION))
    new = g.add_assertion("n:x", "responsibility", value="corrected wording",
                          provenance=Provenance(source_type=SOURCE_EXTRACTION))
    old.superseded_by = new.id

    active = list(g.active())
    check("superseded assertion excluded from active", len(active) == 1, f"{len(active)}")
    check("superseded assertion retained for lineage", len(g.assertions) == 2)
    check("stats report the superseded count", g.stats()["superseded"] == 1)


# ---------------------------------------------------------------------------
# Incompleteness as first-class state (TODO 9c)
# ---------------------------------------------------------------------------

def test_completeness_distinguishes_unknown_from_complete():
    g = KnowledgeGraph()

    from agents.knowledge.model import ExtractionRun
    no_data = ExtractionRun(id="r1")
    check("no pass records -> UNKNOWN, not FAILED",
          no_data.compute_completeness() == RUN_UNKNOWN,
          no_data.compute_completeness())

    partial = ExtractionRun(id="r2", passes=[
        PassRecord(pass_name="structure", chunk_label="1", outcome="ok"),
        PassRecord(pass_name="connections", chunk_label="1", outcome="failed"),
    ])
    check("a failed pass -> PARTIAL", partial.compute_completeness() == RUN_PARTIAL)

    complete = ExtractionRun(id="r3", passes=[
        PassRecord(pass_name="structure", chunk_label="1", outcome="ok"),
    ])
    check("all passes ok -> COMPLETE", complete.compute_completeness() == RUN_COMPLETE)

    # And the distinction must reach the RDF, or a consumer cannot honour it.
    # compute_completeness() must be called — the field defaults to COMPLETE, so
    # a run constructed but never evaluated would serialise as falsely complete.
    no_data.completeness = no_data.compute_completeness()
    g.runs["r1"] = no_data
    rdf = to_rdf(g)
    vals = {str(o) for _, _, o in rdf.triples((None, SEA.completeness, None))}
    check("completeness reaches the RDF", any("UNKNOWN" in v for v in vals), str(vals))


# ---------------------------------------------------------------------------
# Cross-graph references (TODO item 5 input)
# ---------------------------------------------------------------------------

def test_cross_graph_references_held_not_invented():
    output = {"triples": [
        {"subject": "Payment Orchestrator", "predicate": "implements_requirement",
         "object": "FR-PM-001", "confidence": 0.9},
    ]}
    g, _ = graph_from_extraction(output, {}, document_ref="t")

    check("cross-graph link does NOT invent a local node",
          all("fr_pm_001" not in nid for nid in g.nodes), str(list(g.nodes)))
    check("cross-graph link recorded as a value, not an object",
          all(a.object is None for a in g.active()))
    check("cross-graph link surfaced as unresolved",
          len(g.unresolved_references()) == 1)


# ---------------------------------------------------------------------------
# RDF output
# ---------------------------------------------------------------------------

def test_rdf_emits_both_forms():
    output = {
        "elements": [
            {"name": "Payment Orchestrator", "element_type": "Container",
             "parent": "Payment Gateway Platform"},
            {"name": "Payment Gateway Platform", "element_type": "SoftwareSystem"},
        ],
    }
    g, _ = graph_from_extraction(output, {}, document_ref="t")
    rdf = to_rdf(g)

    plain = list(rdf.triples((None, SEA.part_of, None)))
    check("plain triple emitted for traversal", len(plain) == 1, f"{len(plain)}")

    asserts = list(rdf.subjects(None, SEA.Assertion))
    check("assertion resource emitted for provenance", len(asserts) > 0, f"{len(asserts)}")

    # A query author must be able to filter on provenance without joining.
    unverified = run_query(rdf, "unverified")
    check("provenance queryable via SPARQL", len(unverified) > 0, f"{len(unverified)}")


# ---------------------------------------------------------------------------
# Real fixtures
# ---------------------------------------------------------------------------

def test_real_extraction_output():
    import json
    path = Path("data/output/test_arch.json")
    if not path.exists():
        check("real extraction output available", False, "data/output/test_arch.json absent")
        return

    blob = json.loads(path.read_text())
    output = {k: v for k, v in blob.items()
              if k not in ("metadata", "case", "input_file")}
    g, run = graph_from_extraction(output, blob.get("metadata", {}), document_ref=str(path))

    check("real output ingests without dangling assertions",
          not g.dangling_assertions(),
          f"{len(g.dangling_assertions())} dangling")
    check("real output records a run", len(g.runs) == 1)
    check("real architecture run is COMPLETE",
          run.completeness == RUN_COMPLETE, run.completeness)
    check("cross-graph references found in real output",
          len(g.unresolved_references()) > 0,
          f"{len(g.unresolved_references())}")
    check("containment survived the round trip",
          len(g.find(predicate="part_of")) > 0,
          f"{len(g.find(predicate='part_of'))} part_of")


def test_initiative_scoping():
    """Test that Initiative extraction and scoping works correctly.
    
    Simulates extraction from a document that states 'The initiative is PSYA-I2001'.
    Verifies that the canonical model creates Initiative nodes and scopes assertions.
    """
    from agents.knowledge.model import SCOPE_INITIATIVE
    
    output = {
        "entities": [
            {
                "name": "Payment Gateway Platform",
                "entity_type": "SoftwareSystem",
                "ontology_class": "SoftwareSystem",
                "initiative_refs": ["PSYA-I2001"]
            },
            {
                "name": "Payment Request Validation",
                "entity_type": "FunctionalRequirement",
                "ontology_class": "FunctionalRequirement",
                "requirement_id": "FR-PM-001",
                "initiative_refs": ["PSYA-I2001"]
            }
        ],
        "triples": [
            {
                "subject": "Payment Gateway Platform",
                "predicate": "has_functional_requirement",
                "object": "Payment Request Validation",
                "confidence": 0.95,
                "source_text": "The Payment Gateway Platform shall accept and validate incoming payment requests"
            }
        ]
    }
    
    initiative_id = "PSYA-I2001"
    g, run = graph_from_extraction(
        output,
        metadata={"model_id": "test-model"},
        document_ref="test_data/prd/sample_requirements.md",
        initiative_id=initiative_id
    )
    
    # Check 1: Initiative node was created
    initiative_nodes = [n for n in g.nodes.values() if n.kind == "Initiative"]
    check("initiative node created", len(initiative_nodes) == 1, 
          f"{len(initiative_nodes)} initiative nodes")
    
    # Check 2: Assertions are scoped to the Initiative
    initiative_scoped = [a for a in g.assertions.values() 
                        if a.scope == SCOPE_INITIATIVE and a.initiative_id == initiative_id]
    check("assertions scoped to initiative", len(initiative_scoped) > 0,
          f"{len(initiative_scoped)} assertions scoped to {initiative_id}")


def main():
    test_identity_is_stable_and_derived()
    test_duplicates_collapse_keeping_the_best_observation()
    test_human_assertion_survives_agent_reobservation()
    test_supersession_is_excluded_from_active()
    test_completeness_distinguishes_unknown_from_complete()
    test_cross_graph_references_held_not_invented()
    test_rdf_emits_both_forms()
    test_initiative_scoping()
    test_real_extraction_output()

    print(f"\n{'=' * 68}\nCANONICAL KNOWLEDGE LAYER\n{'=' * 68}")
    passed = 0
    for name, ok, detail in RESULTS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<52} {detail}")
        passed += ok
    print(f"\n  {passed}/{len(RESULTS)} checks passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
