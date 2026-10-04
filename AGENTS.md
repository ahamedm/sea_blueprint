# Intro
This is a platform shrinked to a tool/app to prove the core idea of leveraging Ontology for System Architecture reasoning, validation and evolution in an Enterprise ecosystem.

# Convention
- **Changes land on `development`; `main` is reached only by a squashed pull request.**
  Commit straight onto `development` — it is the integration branch, and per-change feature
  branches are not used. When a chunk of work is ready, open a PR into `main` and
  **squash-merge** it: one commit per change on `main`, with the branch's working history left
  behind. `main` requires both a pull request and verified signatures, so the PR is the
  supported path rather than a bypass.
  - **Never use "Rebase and merge" on a PR.** GitHub's documentation is explicit that those
    commits are added "without commit signature verification": GitHub rewrites them into
    commits it cannot sign, so a branch requiring signatures refuses the result. Rebase
    locally and push if that history shape is wanted. "Squash and merge" and "Create a merge
    commit" are both safe — GitHub signs commits it creates through the web interface, so the
    signature requirement is satisfied by the merge itself, not by the branch's commits.
- **Commits are GPG-signed, and the signing key must carry the no-reply address.** Signing is
  configured per clone (`user.signingkey` and `commit.gpgsign` in `.git/config`), so a fresh
  checkout must set it up or its commits are unsigned and `main` refuses them. The key's
  identity must be the `ID+username@users.noreply.github.com` address: GitHub verifies a
  signature only when the committer email matches an identity on the key *and* is a verified
  email on the account. A key carrying any other address yields `bad_email` — unverified —
  even though the signature is cryptographically valid, and that is what the "email in this
  signature doesn't match the committer email" error means. Adding a second UID with the
  no-reply address is the documented fix; note a UID can never be removed from a key, only
  revoked, and a revoked UID's address stays inside the exported key.
- TODOs/Deferred Items tracked as one file per item under `docs/todos/entries/`; `TODO.md` is a generated index — never edit it by hand, run `uv run scripts/todo.py render` (validate with `check`). Closed work becomes a record in `docs/decisions/`, long analysis lives in `docs/design/`. See [TODO system](docs/todos/README.md)
- **A full regression generates the reports.** Any run of the whole suite ends with
  `.venv/bin/python scripts/test_report.py -n 4` (no `--catalog`) so `reports/test-report.md`
  and `reports/test-report.html` carry the outcomes of the code as it now stands. `-n 4` runs
  it across xdist workers: serially the same run is ~28 minutes, which is how it goes stale.
  The reports are gitignored, so nothing fails when they are late — which is exactly why it
  is a step to perform rather than one to hope for. A report that predates the change it
  describes is worse than no report, because it is read as current.
- **Tests are grouped by the question they answer, not by the directory they sit in.** Every
  `tests/test_*.py` is assigned to exactly one area in `scripts/test_report.py`; each area
  states the QUESTION it answers and the intent behind it, and each test states its own
  intent in a docstring. Assign a new file to the area whose question it serves, and if none
  fits, add an area rather than widening one — a file list that has to be read to be
  understood is the drift this taxonomy exists to catch. See [Reading the tests](docs/testing.md)
- Documents under docs/ folder
- **Issues are tracked separately from TODOs, in [ISSUES.md](ISSUES.md).** An issue is a
  measured observation about the current state (`ISS-N`, hand-edited, no tooling); a TODO
  entry is a decision or a piece of work. When an issue needs a decision, a design or an
  estimate, promote it to a `docs/todos/entries/` item and leave the id pointing at it —
  the two lists are different sizes of thing, and that is the whole reason for the split.
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
- **To understand the test suite, read the report, not the run.** `.venv/bin/python
  scripts/test_report.py --catalog` prints every area, file and test with its intent and
  description in ~0.1 s and runs nothing; without `--catalog` it also executes the suite
  and writes `reports/`. A new `tests/test_*.py` must be assigned to an area in
  `scripts/test_report.py` — `tests/test_test_report.py` fails until it is. See
  [Reading the tests](docs/testing.md).
- Use Memory to store Architecture Overview and Critical Decisions

# References
- [PRD](docs/yeah_blueprint_PRD_v0_1.md)
- [Strands Agent SDK Integration](docs/strands-integration.md)
- [Earlier Architecture Review](docs/architecture-review.md) **Done before User Journey, might have deviation**
- [Intended User Journey](docs/user-journey.md)
- [Notes on Architecture as Living System](docs/living-system-architecture.md)
- [Review Gate: projection, review and change management](docs/ui-review-workflow.md) **MVP UI — implemented, current state**
- [Reading the tests](docs/testing.md) **areas, intent and the extraction harness layer**
