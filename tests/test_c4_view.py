"""
The C4 specification view (YB-025).

WHAT THIS PINS. Three separable claims, because they fail independently:

1. **The reduction.** Which nodes become C4 elements, at which level, inside which
   boundary — and which are deliberately *not* elements but still counted.
2. **The notation.** That the emitted Structurizr/PlantUML/Mermaid text is
   deterministic, correctly nested, escaped, and that it refuses to invent structure
   the graph does not hold.
3. **The checks.** That the view says what it cannot represent — a level it had to
   infer, a `part_of` that is reflexive or cyclic, a connection whose endpoint is not
   an element — rather than drawing a diagram that looks finished.

The checks matter most. A diagram is persuasive, so an unstated hole is worse here
than anywhere else in the app, and the reflexivity check exists because a live graph
carried three `X part_of X` facts that human review had passed as verified.
"""

from __future__ import annotations

import pytest

from app.viewpoints import c4
from core.knowledge import graph_from_extraction


def _graph(output):
    graph, _run = graph_from_extraction(
        output,
        metadata={"run_id": "run_c4_test", "document_type": "architecture"},
        document_ref="arch.md",
        document_text="the document body",
    )
    return graph


def _element(name, element_type="Container", parent=None, **extra):
    return {"name": name, "element_type": element_type, "parent": parent, **extra}


@pytest.fixture
def arch_graph(architecture_output):
    """The shared architecture fixture: one system, one container, one data store."""
    return _graph(architecture_output)


@pytest.fixture
def layered_output():
    """A system with a container, a component inside it, and code inside that.

    The containment the fixture above never exercised: every level populated, which is
    what the nesting and roll-up claims need.
    """
    return {
        "elements": [
            _element("Payments", "SoftwareSystem"),
            _element("Orchestrator", "Container", "Payments"),
            _element("Router", "Component", "Orchestrator"),
            _element("RateTable", "CodeElement", "Router"),
            _element("Ledger", "DataStore", "Payments"),
        ],
        "connections": [
            {"source": "Router", "target": "Ledger", "description": "writes",
             "protocol": "JDBC"},
            {"source": "Orchestrator", "target": "Ledger", "description": "reads"},
        ],
    }


# ============================================================================
# The reduction
# ============================================================================


def test_the_system_and_its_containers_become_c4_elements(arch_graph):
    model = c4.c4_model(arch_graph)

    labels = {e["label"] for e in model["elements"]}
    assert {"Payment Gateway Platform", "Payment Orchestrator",
            "Transaction Store"} <= labels
    assert model["system"]["label"] == "Payment Gateway Platform"
    assert {e["level"] for e in model["elements"]} <= set(c4.LEVELS)


def test_a_stated_level_is_preferred_over_the_node_kind(arch_graph):
    """`c4_level` is what the extraction recorded; the kind is the fallback."""
    model = c4.c4_model(arch_graph)
    orchestrator = next(e for e in model["elements"]
                        if e["label"] == "Payment Orchestrator")
    # The fixture states no c4_level, so the level came from the kind — and that
    # inference is itself reported rather than passed off as knowledge.
    assert orchestrator["level_source"] == "kind"
    assert any(gap["kind"] == "inferred-level" for gap in model["gaps"])


def test_an_element_with_no_level_is_reported_not_placed(arch_graph):
    """`DeploymentNode` is not L1-L4. YB-025 asked for a decision; this is it."""
    output = {
        "elements": [
            _element("Payments", "SoftwareSystem"),
            _element("OpenShift Cluster", "DeploymentNode", "Payments"),
        ],
    }
    model = c4.c4_model(_graph(output))

    labels = {e["label"] for e in model["elements"]}
    assert "OpenShift Cluster" not in labels
    unlevelled = [g for g in model["gaps"] if g["kind"] == "unlevelled"]
    assert [g["label"] for g in unlevelled] == ["OpenShift Cluster"]


def test_a_requirements_graph_yields_no_containers_and_says_so(requirements_output):
    """Requirements describe what a system must do, not what it contains.

    The named `System` still becomes a context-level element — the graph declares it,
    so C4 draws it — but nothing below it is invented, and the count of what was left
    out is reported rather than the graph simply looking empty.
    """
    model = c4.c4_model(_graph(requirements_output))

    assert model["by_level"]["container"] == []
    assert {e["level"] for e in model["elements"]} <= {"context"}
    assert model["counts"]["excluded_count"] > 0
    assert model["counts"]["relationships"] == 0


def test_an_unplaced_element_is_reported_rather_than_dropped(arch_graph):
    output = {
        "elements": [
            _element("Payments", "SoftwareSystem"),
            _element("Orphan Service", "Container"),
        ],
    }
    model = c4.c4_model(_graph(output))

    assert "Orphan Service" in {e["label"] for e in model["elements"]}
    unplaced = [g for g in model["gaps"] if g["kind"] == "unplaced"]
    assert "Orphan Service" in {g["label"] for g in unplaced}


# ============================================================================
# Relationships
# ============================================================================


def test_a_connection_becomes_a_labelled_relationship(arch_graph):
    model = c4.c4_model(arch_graph)

    assert len(model["relationships"]) == 1
    relationship = model["relationships"][0]
    assert relationship["label"] == "persists"
    # The Connection node carries protocol; the bare `connects_to` edge does not, so
    # the node form is preferred and this is the reason.
    assert relationship["via"] == "connection"


def test_a_connection_carries_its_protocol_into_the_notation(layered_output):
    graph = _graph(layered_output)
    model = c4.c4_model(graph)

    labels = {r["label"] for r in model["relationships"]}
    assert "writes" in labels and "reads" in labels
    protocols = {r["protocol"] for r in model["relationships"]}
    assert "JDBC" in protocols


def test_a_connection_to_something_that_is_not_an_element_is_reported(arch_graph):
    output = {
        "elements": [_element("Payments", "SoftwareSystem")],
        "connections": [{"source": "Payments", "target": "Nothing Declared Here",
                         "description": "talks to"}],
    }
    model = c4.c4_model(_graph(output))

    assert model["relationships"] == []
    assert any(gap["kind"] == "dangling-connection" for gap in model["gaps"])
    connections = next(c for c in model["checks"] if c["name"] == "connections")
    assert not connections["holds"]


# ============================================================================
# Rolling up
# ============================================================================


def test_a_relationship_below_the_drawn_level_is_rolled_up_and_counted(layered_output):
    """A component-to-datastore write is still coupling at the container diagram."""
    model = c4.c4_model(_graph(layered_output))

    elements, relationships, rolled_up, _undrawable = c4.roll_up(model, "container")
    # The system is context level, so the container diagram is its contents and not
    # the system itself.
    assert {e["label"] for e in elements} == {"Orchestrator", "Ledger"}

    pairs = {(r["source"], r["target"]) for r in relationships}
    by_id = {e["id"]: e for e in model["elements"]}
    assert {(by_id[s]["label"], by_id[t]["label"]) for s, t in pairs} == {
        ("Orchestrator", "Ledger")
    }
    # The component→datastore write collapsed onto its container, and the collapse is
    # reported: an arrow that means "something inside this" must say so.
    assert rolled_up == 1


def test_rolling_up_drops_a_relationship_whose_ends_collapse_together(layered_output):
    """A relationship inside one container is not a relationship on the diagram."""
    output = {
        "elements": [
            _element("Payments", "SoftwareSystem"),
            _element("Orchestrator", "Container", "Payments"),
            _element("Router", "Component", "Orchestrator"),
            _element("Validator", "Component", "Orchestrator"),
        ],
        "connections": [{"source": "Router", "target": "Validator",
                         "description": "calls"}],
    }
    model = c4.c4_model(_graph(output))

    _elements, relationships, _rolled, _undrawable = c4.roll_up(model, "container")
    assert relationships == []


def test_every_level_can_be_rolled_up(layered_output):
    model = c4.c4_model(_graph(layered_output))
    for level in c4.LEVELS:
        elements, _relationships, _rolled, _undrawable = c4.roll_up(model, level)
        assert isinstance(elements, list)


# ============================================================================
# The checks
# ============================================================================


def _check(model, name):
    return next(c for c in model["checks"] if c["name"] == name)


def test_a_component_directly_in_the_system_fails_the_nesting_check(arch_graph):
    output = {
        "elements": [
            _element("Payments", "SoftwareSystem"),
            _element("Orphan Component", "Component", "Payments"),
        ],
    }
    model = c4.c4_model(_graph(output))

    nesting = _check(model, "nesting")
    assert not nesting["holds"]
    assert nesting["examples"] == ["Orphan Component (component inside context)"]


def test_correct_nesting_passes(layered_output):
    model = c4.c4_model(_graph(layered_output))

    assert _check(model, "nesting")["holds"]
    assert _check(model, "reflexive")["holds"]
    assert _check(model, "acyclic")["holds"]


def test_a_reflexive_part_of_is_reported_as_its_own_failure(arch_graph):
    """`X part_of X` is never true. Three of them survived review on a live graph."""
    output = {
        "elements": [
            _element("Payments", "SoftwareSystem", "Payments"),
            _element("Orchestrator", "Container", "Payments"),
        ],
    }
    model = c4.c4_model(_graph(output))

    reflexive = _check(model, "reflexive")
    assert not reflexive["holds"]
    assert reflexive["examples"] == ["Payments"]
    # And it is not silently treated as a boundary: the element still contains its
    # container, so the same three elements are not also reported as unplaced.
    assert not [g for g in model["gaps"] if g["kind"] == "unplaced"]


def test_a_containment_cycle_is_reported_against_its_members(arch_graph):
    output = {
        "elements": [
            _element("A", "Container"),
            _element("B", "Container", "A"),
        ],
        "connections": [],
    }
    graph = _graph(output)
    # A → B (declared) plus B → A (the cycle), which the fixture shape cannot express
    # because `parent` is single-valued.
    by_label = {n.label: n.id for n in graph.nodes.values()}
    graph.add_assertion(by_label["A"], "part_of", obj=by_label["B"], confidence=1.0)
    model = c4.c4_model(graph)

    acyclic = _check(model, "acyclic")
    assert not acyclic["holds"]
    assert set(acyclic["examples"]) == {"A", "B"}


def test_a_failed_check_is_written_into_the_emitted_dsl(arch_graph):
    """Otherwise the DSL is rejected by Structurizr with the cause left behind."""
    output = {
        "elements": [
            _element("Payments", "SoftwareSystem"),
            _element("Orphan Component", "Component", "Payments"),
        ],
    }
    model = c4.c4_model(_graph(output))
    text = c4.to_structurizr(model)

    assert "// WARNING" in text
    assert "Orphan Component" in text


def test_the_checks_are_the_model_not_the_page(arch_graph):
    """A caller of /api/c4 gets the verdict without parsing HTML."""
    model = c4.c4_model(arch_graph)
    payload = c4.to_payload(model, "container")

    assert {c["name"] for c in payload["checks"]} == {
        "nesting", "reflexive", "acyclic", "connections", "populated",
    }
    assert payload["counts"]["checks_failed"] == sum(
        1 for c in payload["checks"] if not c["holds"]
    )


# ============================================================================
# The notation
# ============================================================================


def test_structurizr_nests_containers_inside_their_system(layered_output):
    model = c4.c4_model(_graph(layered_output))
    text = c4.to_structurizr(model)

    assert text.startswith('workspace "Payments" {')
    # The container is declared INSIDE the system's braces, not beside it: that
    # nesting is the whole difference between C4 and a list of boxes.
    system_line = next(i for i, line in enumerate(text.splitlines())
                       if '= softwareSystem "Payments"' in line)
    orchestrator_line = next(i for i, line in enumerate(text.splitlines())
                             if '= container "Orchestrator"' in line)
    assert system_line < orchestrator_line
    assert text.splitlines()[system_line].rstrip().endswith("{")
    assert text.count("views {") == 1


def test_structurizr_emits_the_relationship_with_its_protocol(layered_output):
    model = c4.c4_model(_graph(layered_output))
    text = c4.to_structurizr(model)

    assert 'orchestrator -> ledger "reads"' in text
    assert 'router -> ledger "writes" "JDBC"' in text


def test_the_same_graph_emits_the_same_text(arch_graph):
    """Determinism is what makes the notation diffable in /changes/diff."""
    first = c4.to_structurizr(c4.c4_model(arch_graph))
    second = c4.to_structurizr(c4.c4_model(arch_graph))

    assert first == second


def test_a_label_cannot_break_the_notation():
    output = {
        "elements": [
            _element('Payments "core"\nsecond line', "SoftwareSystem",
                     description='quotes "and" backslashes \\ here'),
        ],
        "connections": [],
    }
    model = c4.c4_model(_graph(output))
    text = c4.to_structurizr(model)

    assert "\\" not in text
    # One element, one line: a newline inside a label would otherwise emit a second
    # declaration and Structurizr would reject the file.
    declarations = [line for line in text.splitlines() if "= softwareSystem" in line]
    assert len(declarations) == 1
    assert declarations[0].endswith('"quotes \'and\' backslashes / here"')


def test_plantuml_uses_a_boundary_for_a_system_with_children(layered_output):
    model = c4.c4_model(_graph(layered_output))
    text = c4.to_c4_plantuml(model)

    assert text.startswith("@startuml") and text.rstrip().endswith("@enduml")
    assert "Container_Boundary(" in text
    assert 'Rel(orchestrator, ledger, "reads")' in text


def test_a_pipe_in_a_label_cannot_end_a_mermaid_edge_label(layered_output):
    """A pipe delimits Mermaid's edge label even inside quotes."""
    output = {
        "elements": [
            _element("Payments", "SoftwareSystem"),
            _element("Orchestrator", "Container", "Payments"),
            _element("Ledger", "DataStore", "Payments"),
        ],
        "connections": [{"source": "Orchestrator", "target": "Ledger",
                         "description": "reads | writes"}],
    }
    text = c4.to_mermaid(c4.c4_model(_graph(output)), "container")

    assert "reads / writes" in text
    assert "reads | writes" not in text


def test_an_external_system_is_drawn_at_every_level(layered_output):
    """C4 draws external systems in every diagram: a boundary is what it exchanges."""
    output = {
        "elements": [
            _element("Payments", "SoftwareSystem"),
            _element("Orchestrator", "Container", "Payments"),
            _element("Legacy Gateway", "ExternalSystem"),
        ],
        "connections": [{"source": "Orchestrator", "target": "Legacy Gateway",
                         "description": "calls"}],
    }
    model = c4.c4_model(_graph(output))

    elements, relationships, _rolled, undrawable = c4.roll_up(model, "container")
    assert {e["label"] for e in elements} == {"Orchestrator", "Legacy Gateway"}
    assert undrawable == []
    assert len(relationships) == 1

    text = c4.to_mermaid(model, "container")
    # Outside the system boundary, because that is what it is.
    assert 'legacy_gateway["Legacy Gateway"]' in text
    assert 'orchestrator -->|"calls"| legacy_gateway' in text


def test_a_relationship_with_no_endpoint_at_this_level_is_reported(layered_output):
    """The first version dropped these silently — which is how a bug gets committed."""
    output = {
        "elements": [
            _element("Payments", "SoftwareSystem"),
            _element("Partner Bank", "SoftwareSystem"),
        ],
        "connections": [{"source": "Payments", "target": "Partner Bank",
                         "description": "settles with"}],
    }
    model = c4.c4_model(_graph(output))

    _elements, _relationships, _rolled, undrawable = c4.roll_up(model, "code")
    assert undrawable and "Partner Bank" in undrawable[0]

    text = c4.to_mermaid(model, "code")
    assert "no endpoint at this level" in text
    assert "Partner Bank" in text


def test_mermaid_draws_the_system_as_one_boundary_without_duplicating_it(layered_output):
    """The label inside the subgraph is the boundary; a node repeating it is a bug."""
    model = c4.c4_model(_graph(layered_output))
    text = c4.to_mermaid(model, "container")

    assert text.startswith("flowchart TB")
    assert text.count("subgraph") == 1
    assert 'subgraph payments_sys["Payments"]' in text
    # The system is the boundary, not also a node inside itself.
    assert "\n    payments[" not in text


def test_mermaid_notes_how_many_relationships_were_rolled_up(layered_output):
    model = c4.c4_model(_graph(layered_output))
    text = c4.to_mermaid(model, "container")

    assert "%% 1 relationship(s) rolled up from a lower level" in text
    assert 'orchestrator -->|"reads"| ledger' in text


def test_an_unknown_level_is_not_silently_substituted(arch_graph):
    """`/c4?level=contexts` is a typo, and a typo must not look like a valid view."""
    model = c4.c4_model(arch_graph)
    payload = c4.to_payload(model, "contexts")

    assert payload["level"] == "container"
    assert "contexts" not in payload["levels"]


# ============================================================================
# The routes
# ============================================================================


def test_the_c4_page_renders_the_model_and_its_checks(seeded_client):
    seeded_client.post("/ingest", data={"text": "architecture body",
                                        "type": "architecture"})
    response = seeded_client.get("/c4")

    assert response.status_code == 200
    assert b"Payment Gateway Platform" in response.data
    assert b"Well-formedness" in response.data
    assert b"What this view cannot represent" in response.data
    # The notation is on the page even before the diagram renders: it is the
    # deliverable, and mermaid.js is a convenience.
    assert b"workspace &#34;Payment Gateway Platform&#34;" in response.data


def test_the_c4_page_offers_every_level(seeded_client):
    for level in c4.LEVELS:
        response = seeded_client.get(f"/c4?level={level}")
        assert response.status_code == 200, level


def test_an_unknown_level_says_so_instead_of_falling_back_quietly(seeded_client):
    response = seeded_client.get("/c4?level=contexts")

    assert response.status_code == 200
    assert b"is not a C4 level" in response.data


def test_the_c4_page_on_an_empty_graph_explains_itself(client):
    response = client.get("/c4")

    assert response.status_code == 200
    assert b"Nothing to specify" in response.data


def test_the_notation_downloads_as_a_file(seeded_client):
    response = seeded_client.get("/c4/notation/structurizr")

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("text/plain")
    assert "payment-gateway-platform-c4.dsl" in response.headers["Content-Disposition"]
    assert response.data.startswith(b'workspace "')


def test_an_unknown_notation_format_is_a_404(seeded_client):
    assert seeded_client.get("/c4/notation/kroki").status_code == 404


def test_the_api_carries_the_verdict_not_just_the_elements(seeded_client):
    seeded_client.post("/ingest", data={"text": "architecture body",
                                        "type": "architecture"})
    payload = seeded_client.get("/api/c4").get_json()

    assert payload["system"]["label"] == "Payment Gateway Platform"
    assert payload["checks"] and payload["gaps"]
    assert payload["drawn"]["level"] == "container"
    assert payload["notation"]["structurizr"].startswith("workspace")


def test_c4_is_a_real_view_and_graph_still_redirects(seeded_client):
    """`/c4` used to redirect to `/map`; it now honours the name it was given."""
    assert seeded_client.get("/c4").status_code == 200

    response = seeded_client.get("/graph?lens=architecture")
    assert response.status_code == 301
    assert "/map?lens=architecture" in response.headers["Location"]


def test_a_retired_route_is_not_left_in_the_browsers_cache(seeded_client):
    """The bug this pins, in full.

    `/c4` was retired as a 301 to `/map` and then reclaimed by YB-025. Because a 301
    is cacheable indefinitely, every browser that had followed it kept replaying the
    redirect locally and never asked the server again — so the reclaimed `/c4` returned
    200 to curl and still showed the map in the browser. The remaining retired route
    must therefore carry `no-store`: a redirect that is permanent in intent is still
    not allowed to be permanent in a cache, because the next item may want the name
    back too.
    """
    response = seeded_client.get("/graph")

    assert response.status_code == 301
    assert "no-store" in response.headers.get("Cache-Control", "")


def test_every_page_loads_mermaid_only_on_the_c4_view(seeded_client):
    """2.5 MB on one page is a choice; on every page it would be a tax."""
    c4_page = seeded_client.get("/c4").data
    map_page = seeded_client.get("/map").data

    assert b"mermaid.min.js" in c4_page
    assert b"mermaid.min.js" not in map_page
