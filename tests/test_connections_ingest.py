"""
Connections reach the graph (YB-051).

WHAT THIS PINS, AND WHY IT NEEDED ITS OWN FILE. The architecture profile emits
`output["connections"]` and ingestion read eleven other keys and not that one, so the
whole `connections` pass was paid for per chunk and discarded. Nothing failed, nothing
warned, and no test noticed — because no test asserted that a connection reached the
graph. These do.

The model was never the problem: the ontology already declares `Connection` with
`source`, `target`, `protocol`, `style` and `via_interface`, and calls it "the arrow in
a C4 diagram". It is reified as a *node* rather than a bare edge because those
attributes have nowhere to live on `(subject, predicate, object|value)`.
"""

from __future__ import annotations

from core.knowledge import graph_from_extraction


def _ingest(output):
    graph, run = graph_from_extraction(
        output,
        metadata={"run_id": "run_connections_test", "document_type": "architecture"},
        document_ref="arch.md",
        document_text="the document body",
    )
    return graph, run


def _connections(graph):
    return [n for n in graph.nodes.values() if n.kind == "Connection"]


def _facts(graph, subject, predicate):
    return [a for a in graph.active()
            if a.subject == subject and a.predicate == predicate]


def test_a_connection_becomes_a_node_joined_to_the_elements_it_names(architecture_output):
    output = {
        **architecture_output,
        "connections": [
            {"source": "Payment Orchestrator", "target": "Transaction Store",
             "description": "persists", "protocol": "JDBC", "style": "SHARED_DATABASE"},
        ],
    }

    graph, _run = _ingest(output)

    connections = _connections(graph)
    assert len(connections) == 1, [n.label for n in connections]
    connection = connections[0]
    assert "Payment Orchestrator" in connection.label
    assert "Transaction Store" in connection.label

    # The endpoints are the SAME nodes the structure pass created — resolving, not
    # duplicating. A second "Payment Orchestrator" would silently split the graph.
    orchestrator = next(n for n in graph.nodes.values()
                        if n.label == "Payment Orchestrator")
    store = next(n for n in graph.nodes.values() if n.label == "Transaction Store")
    assert _facts(graph, connection.id, "source")[0].object == orchestrator.id
    assert _facts(graph, connection.id, "target")[0].object == store.id
    assert len([n for n in graph.nodes.values() if n.label == "Payment Orchestrator"]) == 1


def test_the_protocol_and_style_travel_with_the_connection(architecture_output):
    """The reason it is a node: `protocol` and `style` are what a C4 arrow is labelled
    with, and an assertion has no room for an attribute of an edge."""
    output = {
        **architecture_output,
        "connections": [
            {"source": "Payment Orchestrator", "target": "Transaction Store",
             "description": "persists", "protocol": "JDBC", "style": "SHARED_DATABASE"},
        ],
    }

    graph, _run = _ingest(output)
    connection = _connections(graph)[0]

    assert _facts(graph, connection.id, "protocol")[0].value == "JDBC"
    assert _facts(graph, connection.id, "style")[0].value == "SHARED_DATABASE"
    assert _facts(graph, connection.id, "description_text")[0].value == "persists"


def test_a_connection_is_attributed_to_the_pass_that_produced_it(architecture_output):
    graph, _run = _ingest(architecture_output)
    connection = _connections(graph)[0]

    assert _facts(graph, connection.id, "source")[0].provenance.pass_name == "connections"


def test_an_endpoint_that_declares_nothing_is_a_placeholder_not_a_drop(architecture_output):
    """Every other undeclared referent becomes a placeholder so the gap is visible.
    A connection is not special: dropping it would hide a relationship the extractor
    actually saw."""
    output = {
        **architecture_output,
        "connections": [
            {"source": "Payment Orchestrator", "target": "Nonexistent Broker"},
        ],
    }

    graph, _run = _ingest(output)

    connection = _connections(graph)[0]
    target = _facts(graph, connection.id, "target")[0].object
    assert target in graph.nodes
    assert graph.nodes[target].label == "Nonexistent Broker"


def test_a_connection_missing_an_endpoint_is_skipped(architecture_output):
    """A record with no source or no target is not a relationship, and inventing one
    end of it would be worse than omitting it."""
    output = {
        **architecture_output,
        "connections": [
            {"source": "", "target": "Transaction Store"},
            {"source": "Payment Orchestrator", "target": ""},
        ],
    }

    graph, _run = _ingest(output)

    assert _connections(graph) == []


def test_many_connections_keep_their_own_labels(architecture_output):
    """Two calls between the same pair of elements are two arrows with different
    labels, and collapsing them would lose one."""
    output = {
        **architecture_output,
        "connections": [
            {"source": "Payment Orchestrator", "target": "Transaction Store",
             "protocol": "JDBC"},
            {"source": "Payment Orchestrator", "target": "Payment Gateway Platform",
             "protocol": "REST"},
        ],
    }

    graph, _run = _ingest(output)

    labels = sorted(n.label for n in _connections(graph))
    assert labels == [
        "Payment Orchestrator → Payment Gateway Platform",
        "Payment Orchestrator → Transaction Store",
    ]
    assert len(_connections(graph)) == 2


def test_the_fixture_graph_gains_one_arrow_from_the_shared_fixture(architecture_output):
    """The fixture carries one connection and no protocol. It must land as one node —
    this is the assertion that would have failed before the fix."""
    graph, _run = _ingest(architecture_output)

    assert len(_connections(graph)) == 1
    assert _facts(graph, _connections(graph)[0].id, "description_text")[0].value == "persists"
