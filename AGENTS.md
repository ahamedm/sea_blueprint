# Intro
This is a platform shrinked to a tool/app to prove the core idea of leveraging Ontology for System Architecture reasoning, validation and evolution in an Enterprise ecosystem.

# Convention
- TODOs/Deferred Items tracked as one file per item under `docs/todos/entries/`; `TODO.md` is a generated index — never edit it by hand, run `uv run scripts/todo.py render` (validate with `check`). Closed work becomes a record in `docs/decisions/`, long analysis lives in `docs/design/`. See [TODO system](docs/todos/README.md)
- Documents under docs/ folder
- Configurations Externalized
- Use uv instead of direct python for build/dependency management etc.
- Agents under agents/ folder
- Shared domain code under core/ folder (knowledge model, ontology reader) —
  read by both the agents and the app; core/ must not import agents/ or app/
- Foundational Ontologies under ontology/ folder
- MVP UI is under app/ folder

# Efficiency Tips
- **Reach for `codegraph_explore` when the question is about code structure** — "how does X
  work", "where is X", "who calls X", "how does X reach Y", or before editing a shared
  function. One call returns the verbatim source of the relevant symbols grouped by file,
  the call path between them, and a **blast radius** (callers, and which tests cover them).
  Treat what it shows as already Read — it re-reads from disk, so do not re-open those files.
  - **It answers structure; grep answers strings.** Use grep/read for exact text, YAML and
    config, docs, test assertions, and existence checks over data keys ("does anything read
    this key"). It indexes symbols, not data, and most questions in this repo are a mix —
    expect to use both, and say which one you used when a conclusion is load-bearing.
  - **Read the blast radius before calling a fix done.** The consumers you did not think of
    are the ones that matter: `merge_triples` has seven callers across four modules, and
    auditing them is what found the profile convention (`_as_records`: the merge layer
    returns dicts, a profile re-types at its own boundary) instead of leaving a real
    contract to be rediscovered as a crash. A seam bug fixed at one call site is not fixed.
- **Verify against the source before asserting.** A name, a type annotation and a docstring
  are not the body. Several diagnoses in this repo were wrong because the first two were
  read and the third was assumed.
- Use Memory to store Architecture Overview and Critical Decisions

# References
- [PRD](docs/yeah_blueprint_PRD_v0_1.md)
- [Strands Agent SDK Integration](docs/strands-integration.md)
- [Earlier Architecture Review](docs/archtiecture-review.md) **Done before User Journey, might have deviation**
- [Intended User Journey](docs/user-journey.md)
- [Notes on Architecture as Living System](docs/living-system-architecture.md)
- [Review Gate: projection, review and change management](docs/ui-review-workflow.md) **MVP UI — implemented, current state**
