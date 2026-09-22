"""
Shared domain layer — the model and schema that both the agents and the app read.

WHY THIS IS NOT UNDER `agents/`
-------------------------------
`core.knowledge` is the canonical knowledge model; `core.ontology` reads the
foundational LinkML schemas. Neither is an agent, and neither imports one. They
lived under `agents/` by accident of where the work started, which made the domain
layer look like part of the agent runtime and invited the belief that you need the
agent stack to read a graph.

The layout is three-way, by who reads what:

  core/     the domain — knowledge model, ingest, review, reconciliation, schema reader
  agents/   LLM agents that act on the graph; stateless functions over it
  app/      the human interface — projections, architecture viewpoints, routes

Dependency direction is one way: **agents and app may import core; core imports
neither.** That is what keeps the knowledge model usable without an LLM, the web
layer replaceable without touching the model, and the schema reader available to
the Ontology Engineer and Domain Context agents. `tests/test_layering.py` holds
that line.

Consumers today: `app/`, `scripts/`, `tests/`, and — once the Semantic Auditor,
Ontology Engineer and Domain Context agents are implemented — `agents/`.
"""
