# Reading the tests

The suite is past a thousand tests and 56 files. `pytest -q` answers "did anything
break?" — it cannot answer "what does this protect?", because a node id is not an
explanation and a directory is not a reason. This document is the way in.

## The command

```sh
.venv/bin/python scripts/test_report.py              # run the suite, write reports/
.venv/bin/python scripts/test_report.py --catalog    # describe it, run nothing (~0.1s)
.venv/bin/python scripts/test_report.py --only ingest-seams
.venv/bin/python scripts/test_report.py --only review -k retire
.venv/bin/python scripts/test_report.py --failures-only --format console
```

It writes `reports/test-report.md` and `reports/test-report.html` (gitignored —
regenerate rather than review a stale copy) and prints a console summary.

| Flag | What it is for |
|---|---|
| `--catalog` | The comprehension view. Reads the sources and prints every area, file and test with its description, without executing anything. This is the one to read when you are new to the suite, or when the suite will not run. |
| `--only AREA` | One area, and only its files. Fast enough to keep open while working. |
| `-k EXPR` | Passed through to pytest. |
| `--failures-only` | Console output that shows only the failures. |
| `--format` | `console`, `md`, `html` or `all` (default). |
| `--no-harness` | Skip the extraction-harness catalogue. |

## Areas are questions

Nine areas, each answering one question. They are not directories: `tests/` is flat
and always has been, so the grouping is an explicit layer over it.

| Area | The question it answers |
|---|---|
| Platform foundations | Is the ground the rest of the suite stands on still flat? |
| Ontology and vocabularies | Is the vocabulary extraction is held to well-formed and complete? |
| The canonical graph | Does a fact survive being keyed, written, saved and loaded? |
| Ingest and the seams | Is anything the model produced dropped, mangled or impossible once stored? |
| Extraction agents and run reliability | Do the passes produce well-shaped output, on budget, on the configured endpoint? |
| Review gate and audit trail | Can a human vouch for, correct or remove a fact — and can the graph prove it? |
| Views and projections | Does the graph render as an artefact without inventing or hiding structure? |
| The web app | Does the UI behave over HTTP the way the domain layer promises? |
| Background runs and progress | Can work outlive the request that started it, and say how it is going? |

Each area's full intent — what it protects and why the failure would matter — is in
the report and in `scripts/test_report.py`.

## Adding a test file is a decision

`AREAS` in `scripts/test_report.py` names every test file. `tests/test_test_report.py`
fails when a file on disk belongs to no area or to two, so the suite cannot grow past
the report's ability to describe it:

```sh
# after adding tests/test_something_new.py
.venv/bin/python -m pytest tests/test_test_report.py
# FAILED ... these test files are in no area, so no report describes them: [...]
```

Pick the area by the QUESTION the file answers. If none of the nine fits, that is
the useful signal: either a new area is warranted, or the file is doing two things.

## Every test gets a sentence

A test's description is the first paragraph of its docstring. Where there is no
docstring, the name is humanised and the entry is marked **"description from the
name"** — visible in the HTML, footnoted in the Markdown, and counted per area in
the summary table as `described/total`.

That marker is not decoration. It is the difference between "945 tests" and "945
tests of which 471 are only as descriptive as their function name", and it makes the
gap improvable one docstring at a time — starting with the area whose intent you
care about. A test whose reason is not written down is a test the next reader will
delete.

## The second layer: the extraction harness

`scripts/run_extraction_tests.py` is the same idea for the expensive layer — live
model runs, checked against hard gates and toleranced budgets. It costs real money,
so `test_report.py` catalogs its cases (gates vs budgets, and the baseline) and never
runs it implicitly. The free, deterministic half is:

```sh
.venv/bin/python scripts/run_extraction_tests.py --validate-only          # offline
.venv/bin/python scripts/run_extraction_tests.py --validate-only --scorecard-root data/sea_home_01
```

Exit codes there mean what they say: `1` is a broken run (a gate failed), and a
budget outside its band is a WARN that does not fail the build. See
[ADR-0030](decisions/ADR-0030-boundary-refusals-and-output-accounting.md) §4 and
[ADR-0031](decisions/ADR-0031-test-reporting-taxonomy.md).
