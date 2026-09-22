"""
Shared fixtures.

The extraction agents are never invoked: `create_app` takes an injectable
extractor factory, so the web tests exercise the real ingest, merge, review and
revision code paths against a deterministic payload instead of an LLM. A test
suite that needs a model server is a test suite nobody runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.knowledge import graph_from_extraction

REPO_ROOT = Path(__file__).resolve().parent.parent


# ============================================================================
# Ontology
# ============================================================================


@pytest.fixture(scope="session")
def ontology():
    """The parsed foundational ontologies.

    Session-scoped because parsing four schemas per test buys nothing, with the
    explicit expectation that no test mutates the model — every view over it
    returns new dicts.
    """
    from core.ontology import load_ontology

    return load_ontology(REPO_ROOT / "ontology")


@pytest.fixture(scope="session")
def ontology_dir():
    return REPO_ROOT / "ontology"


# ============================================================================
# Extraction payloads
# ============================================================================


@pytest.fixture
def requirements_output():
    """Shaped like the requirements (REQ-G) profile output."""
    return {
        "triples": [
            {
                "subject": "Payment Gateway Platform",
                "predicate": "has_functional_requirement",
                "object": "Payment Acceptance",
                "confidence": 0.9,
                "source_text": "FR-PM-001 The platform shall accept a payment request.",
                "ontology_class": "FunctionalRequirement",
            },
            {
                "subject": "Payment Acceptance",
                "predicate": "traces_to_goal",
                "object": "Reduce Payment Failure Rate",
                "confidence": 0.7,
                "source_text": "in support of the failure-rate goal",
                "ontology_class": "BusinessGoal",
            },
            {
                "subject": "Payment Gateway Platform",
                "predicate": "enforces",
                "object": "Role-Based Access Control",
                "confidence": 0.5,
                "source_text": "RBAC is enforced for operator access.",
                "ontology_class": "ConstraintRequirement",
            },
        ],
        "entities": [
            {
                "name": "Payment Gateway Platform",
                "ontology_class": "System",
                "initiative_refs": ["INIT-2024-001"],
            },
            {"name": "Payment Acceptance", "ontology_class": "FunctionalRequirement"},
            {"name": "Role-Based Access Control", "ontology_class": "ConstraintRequirement"},
        ],
        "relationships": [],
        "initiatives": [{"id": "INIT-2024-001", "name": "Payment Modernisation"}],
    }


@pytest.fixture
def architecture_output():
    """Shaped like the architecture (ARC-G) profile output."""
    return {
        "elements": [
            {
                "name": "Payment Gateway Platform",
                "element_type": "SoftwareSystem",
                "description": "The gateway.",
                "initiative_id": "INIT-2024-001",
            },
            {
                "name": "Payment Orchestrator",
                "element_type": "Container",
                "parent": "Payment Gateway Platform",
                "responsibilities": ["Routing", "Validation"],
            },
            {
                "name": "Transaction Store",
                "element_type": "DataStore",
                "parent": "Payment Gateway Platform",
            },
        ],
        "connections": [
            {
                "source": "Payment Orchestrator",
                "target": "Transaction Store",
                "description": "persists",
            },
        ],
        "technology_stacks": [
            {"name": "PostgreSQL", "category": "database", "used_by": ["Transaction Store"]},
        ],
        "references": [
            {
                "element": "Payment Orchestrator",
                "relationship": "implements_requirement",
                "reference": "FR-PM-001",
                "confidence": 0.8,
                "source_text": "handles Request Acceptance and Validation",
            },
        ],
    }


@pytest.fixture
def req_extraction(requirements_output):
    graph, _run = graph_from_extraction(
        requirements_output, {"model_id": "fake"}, document_ref="req.md", document_text="req source"
    )
    return graph


@pytest.fixture
def arch_extraction(architecture_output):
    graph, _run = graph_from_extraction(
        architecture_output,
        {"model_id": "fake"},
        document_ref="arch.md",
        document_text="arch source",
    )
    return graph


# ============================================================================
# Fake extractor
# ============================================================================


class FakeResult:
    def __init__(self, output, metadata=None, success=True, errors=None):
        self.output = output
        self.metadata = metadata or {}
        self.success = success
        self.errors = errors or []

    def model_dump(self):
        """Mirrors `AgentResult.model_dump()`.

        Preserved deliberately: the bug this suite guards against was passing
        *this* envelope to ingest instead of `output`.
        """
        return {
            "success": self.success,
            "output": self.output,
            "confidence": 0.9,
            "errors": self.errors,
            "metadata": self.metadata,
        }


class FakeExtractor:
    """Stands in for a Strands-backed agent."""

    def __init__(self, output, metadata=None, success=True):
        self.output = output
        self.metadata = metadata or {"model_id": "fake-model", "model_calls": 3}
        self.success = success
        self.calls = []

    def run(self, input_data):
        self.calls.append(input_data)
        return FakeResult(self.output, self.metadata, self.success)


@pytest.fixture
def fake_factory(requirements_output, architecture_output):
    def factory(doc_type):
        if doc_type == "architecture":
            return FakeExtractor(architecture_output)
        return FakeExtractor(requirements_output)

    return factory


# ============================================================================
# App
# ============================================================================


@pytest.fixture
def store_root(tmp_path):
    return tmp_path / "sea"


@pytest.fixture
def app(store_root, fake_factory):
    from app import create_app

    return create_app(
        {"TESTING": True, "STORE_ROOT": str(store_root), "REVIEWER": "tester"},
        store_root=str(store_root),
        extractor_factory=fake_factory,
    )


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def seeded_client(client):
    """A client whose working set already holds an ingested graph."""
    client.post("/ingest", data={"text": "FR-PM-001 requirements body", "type": "requirements"})
    return client


@pytest.fixture
def working_graph(app):
    """Read the working graph back out of the store, the way the UI would."""
    from core.knowledge import RevisionStore

    return RevisionStore(app.config["STORE_ROOT"]).ensure().load_working().graph


@pytest.fixture
def load_working(app):
    """Re-read the working graph *after* a request has mutated it.

    A plain fixture is resolved before the test body runs, so anything asserted
    about state after a POST must be re-read or it will be the pre-request graph.
    """
    from core.knowledge import RevisionStore

    store = RevisionStore(app.config["STORE_ROOT"]).ensure()
    return lambda: store.load_working().graph
