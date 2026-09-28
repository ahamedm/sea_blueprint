---
id: ADR-0031
title: "Test reporting — areas are questions, every test gets a sentence, and the taxonomy is checked"
status: accepted
date: 2026-09-28
area: "`scripts/test_report.py` (new), `tests/test_test_report.py` (new), `docs/testing.md` (new), `.gitignore`"
related: ["ADR-0030", "YB-007"]
---

# ADR-0031 — A test report that says what the suite protects

> **Record.** The suite reached 945 tests across 55 files and stopped being
> comprehensible from `pytest -q`: a progress bar and a failure list say *whether*
> something broke, never *what* the suite protects or *why* a test exists. The
> grouping lived in the heads of whoever wrote each file. This adds a reporting
> library that makes the grouping explicit, data-driven and self-checked.

### The problem, stated precisely

It was not runtime — the suite runs in ~10 seconds. It was comprehension:

- **A node id is not an explanation.** `tests/test_output_consumption.py::test_an_explained_unrouted_key_is_recorded_but_not_an_alarm`
  is precise and unreadable in a summary.
- **A directory is not a reason.** `tests/` is flat by design; nothing on disk says
  that `test_irreflexive_guard` and `test_connections_ingest` belong together while
  `test_c4_view` does not.
- **Descriptions were optional and invisible.** 471 of 945 tests had no docstring at
  all, and nothing counted them, so the gap could only grow.
- **The expensive layer was invisible from the fast one.** The extraction harness
  (live model runs, gates, toleranced budgets) is part of the same question and was
  only discoverable by knowing the script name.

### Decisions

**1. An area is a question, and the taxonomy is data.** `AREAS` in
`scripts/test_report.py` maps every test file to exactly one of nine areas, each
carrying the question it answers and a paragraph of intent. Grouping by meaning
rather than by directory is the whole value: "Ingest and the seams — is anything the
model produced dropped, mangled or impossible once stored?" is the question the last
tranche of work existed to answer, and it now names eight files that share it.

**2. Coverage is asserted, not trusted.** `tests/test_test_report.py` fails when a
test file on disk belongs to no area or to two. This is the mechanism that matters
most: without it the report would silently under-report the suite as it grows, which
is the original problem wearing a nicer UI. It follows the harness's own principle
(ADR-0030 §4) — an unclassified invariant aborts rather than passing by omission.

**3. Every test gets a sentence, and the gap is counted.** The first paragraph of a
test's docstring is its description; where there is none the name is humanised and
the entry is MARKED "description from the name". The per-area summary prints
`described/total`. Deriving a name into a sentence is honest only as an interim
measure — the marking is what turns "945 tests" into "471 of them are only as
descriptive as their function name", which is actionable.

**4. Dependency-free, three-way rendering.** Console for the working loop, Markdown
for a diffable/readable record, self-contained HTML for seeing the shape (inline CSS
and a filter box, no network). Generated into `reports/`, which is gitignored: a
committed report is a claim about a suite that has since changed.

**5. `--catalog` is the comprehension view.** It reads the sources and prints every
area, file and test with its description, executing nothing — fast enough to keep
open while reading, and still correct when the suite cannot run at all (the reporter
survives an import error in a test file and reports it as a collection error rather
than as absence).

**6. The expensive layer is catalogued, never run implicitly.** The report reads the
harness's cases, gates and budgets, plus the committed baseline. Running live
extraction costs minutes and real money, so it stays an explicit command
(`run_extraction_tests.py --validate-only` is the free half).

### What this measured on its first run

- **959 tests, 9 areas, 55 files**, of which **484 carry a docstring** and 475 do not.
- The two known failures land in two different areas (`review`, `app`) — which is
  itself the argument for areas: they read as "the fixture is stale in two places",
  not as a flat list of two node ids.
- The taxonomy immediately showed an imbalance worth knowing: *views* holds 101 tests
  in 3 files while *ingest-seams* holds 68 in 8. Density like that is invisible in a
  flat run.

### What it deliberately does not do

- **It does not re-run the extraction harness.** No report generation spends model
  money.
- **It does not measure coverage.** Which lines execute is a different question from
  which intentions are protected, and line coverage rewards a test for touching a
  line it does not understand.
- **It does not enforce a docstring.** It makes the absence visible and countable;
  a gate on style would produce `"""Tests the thing."""` faster than it would produce
  understanding.
- **It does not commit a report.** `reports/` is generated, and the generator is the
  deliverable.

### Related

- [ADR-0030](ADR-0030-boundary-refusals-and-output-accounting.md) — the harness whose
  gates and budgets are attached as the report's second layer, and the self-checked
  classification this one mirrors.
- [docs/testing.md](../testing.md) — the reader-facing half of this record.
