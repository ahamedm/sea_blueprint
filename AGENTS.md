# Intro
This is a platform shrinked to a tool/app to prove the core idea of leveraging Ontology for System Architecture reasoning, validation and evolution in an Enterprise ecosystem.

# Convention
- TODOs/Deferred Items tracking in TODO.md
- Documents under docs/ folder
- Configurations Externalized
- Use uv instead of direct python for build/dependency management etc.
- Agents under agents/ folder
- Shared domain code under core/ folder (knowledge model, ontology reader) —
  read by both the agents and the app; core/ must not import agents/ or app/
- Foundational Ontologies under ontology/ folder
- Use Memory to store Architecture Overview and Critical Decisions
- MVP UI is under app/ folder

# References
- [PRD](docs/yeah_blueprint_PRD_v0_1.md)
- [Strands Agent SDK Integration](docs/strands-integration.md)
- [Earlier Architecture Review](docs/archtiecture-review.md) **Done before User Journey, might have deviation**
- [Intended User Journey](docs/user-journey.md)
- [Notes on Architecture as Living System](docs/living-system-architecture.md)
- [Review Gate: projection, review and change management](docs/ui-review-workflow.md) **MVP UI — implemented, current state**
