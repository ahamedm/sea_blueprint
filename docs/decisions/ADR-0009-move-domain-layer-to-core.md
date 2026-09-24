---
id: ADR-0009
legacy: "17"
title: "Move the shared domain layer out of agents/ into core/"
status: accepted
date: 2026-09-22
area: "core/, pyproject.toml, AGENTS.md, tests/test_layering.py"
related: []
---

# ADR-0009 — Move the shared domain layer out of agents/ into core/

> **Record.** Closed work, preserved verbatim from `TODO.md` v1 (ADR-0009).
> Legacy source: [`docs/todos/legacy-todo-v1.md`](../todos/legacy-todo-v1.md).

**Legacy status:** ✅ IMPLEMENTED
**Legacy priority:** Closed
**Legacy area:** `core/`, `pyproject.toml`, `AGENTS.md`, `tests/test_layering.py`

---

### Why

`agents/knowledge/` and `agents/ontology.py` were not agents. Neither was an agent
runtime concern, and **at the time of the move no agent imported either** — the
consumers were `app/`, `scripts/` and `tests/`. The extraction agents import only
`..base_agent`, `..knowledge_extraction` and `..extraction`.

So the justification is not present sharing but a naming error: putting the domain
model under `agents/` made it look like part of the agent runtime and invited the
belief that reading a graph requires the agent stack. The move is also
forward-looking — the Semantic Auditor, Ontology Engineer and Domain Context agents
are stubs that will read exactly these modules.

### Layout: three-way, by who reads what

| Package | Role |
|---|---|
| `core/` | the domain — knowledge model, ingest, review, reconciliation, schema reader |
| `agents/` | LLM agents that act on the graph; stateless functions over it |
| `app/` | the human interface — projections, architecture viewpoints, routes |

**Moved:** `agents/knowledge/` (8 modules, ~3,060 LOC) → `core/knowledge/`;
`agents/ontology.py` → `core/ontology.py`. Renames via `git mv`, so history follows.

**Stayed, by the same criterion** (shared by both → moves; one consumer → stays):
`agents/base_agent.py`, `agents/cli.py`, `agents/extraction/` (agent-side pass
infrastructure), the two real extraction agents, the four stub agents. On the app
side, `app/projections.py`, `app/viewpoints/` and `app/ontology_reference.py` have
only the app as a consumer, so they stay.

### The rule, and the guards that hold it

**Agents and app may import core; core imports neither.** That is what keeps the
knowledge model usable without an LLM and the web layer replaceable without
touching the model.

`tests/test_layering.py` asserts it as source rather than trusting convention:
no file under `core/` imports `agents` or `app`; `agents.knowledge`/`agents.ontology`
do not come back; `core*` is registered in `pyproject.toml`; and — the concrete
payoff — **importing `core.knowledge` and `core.ontology` in a subprocess must not
load `strands`, `flask` or `linkml_runtime`.**

Consequence worth noting: the test suite no longer imports the agent runtime at all.
Importing `agents.knowledge` used to execute `agents/__init__.py`, which imports
`base_agent` and its pydantic model — the suite's pydantic deprecation warnings
disappeared with the move, which is the decoupling showing up as a side effect.

### Deliberately not done

Pre-existing lint debt in `core/knowledge/model.py` (W293/I001/E501), `rdf.py`
(I001/F401) and `ingest.py` (5 × E501) was **not** swept up. Auto-fixing it inside
a rename commit would make the move unreviewable and would silently reformat files
the change has no business touching. It remains recorded here rather than hidden.
