# Contributing

## Licensing of contributions

This project is licensed under the [Apache License, Version 2.0](LICENSE). By
submitting a contribution, you agree that it is licensed to the project and to
everyone else under those same terms — Apache-2.0 §5, which applies unless you
explicitly state otherwise in writing.

There is **no CLA**. Instead every commit must carry a Developer Certificate of
Origin sign-off.

### Sign your commits off

Add a sign-off line to each commit with `-s`:

```bash
git commit -s -m "Fix the containment rule for Component nodes"
```

which appends:

```
Signed-off-by: Your Real Name <you@example.com>
```

The name and email must be your real identity and must match the commit author.
By signing off you certify the [Developer Certificate of Origin, version 1.1](https://developercertificate.org)
— in short, that you wrote the contribution or otherwise have the right to
submit it under this project's license, and that you understand the
contribution and the sign-off are public and permanent.

Commits without a sign-off cannot be merged.

### Why this is asked for

The sign-off keeps a verifiable record of where each contribution came from and
under what terms. That is what preserves the project's ability to adjust its
licensing later — for example to dual-license, or to offer an enterprise edition
— without having to track down every past contributor for permission. It costs
one flag per commit now; the alternative is a negotiation later.

## Before you open a pull request

- **Use `uv`**, not a bare `python`, for builds, dependency management and
  scripts. Run tests with `.venv/bin/python -m pytest`.
- **A new `tests/test_*.py` must be assigned to an area** in
  `scripts/test_report.py`. The suite is grouped by the *question* each file
  answers, not by directory, and `tests/test_test_report.py` fails until the
  assignment exists. If no existing area fits, add one rather than widening an
  existing area. Every test states its intent in a docstring. See
  [Reading the tests](docs/testing.md).
- **A full regression ends with the reports.** Any run of the whole suite
  finishes with `.venv/bin/python scripts/test_report.py -n 4`, so
  `reports/test-report.md` and `reports/test-report.html` describe the code as
  it now stands.
- **Record decisions in the TODO system, not by hand.** TODOs are one file per
  item under `docs/todos/entries/`; `TODO.md` is a generated index. Run
  `uv run scripts/todo.py render` to regenerate it and `check` to validate.
  Closed work moves to `docs/decisions/` as an ADR record.
- **Respect the layering.** `core/` is the shared domain layer read by both the
  agents and the app, so `core/` must not import `agents/` or `app/`.

[AGENTS.md](AGENTS.md) is the fuller statement of these conventions and takes
precedence over this file if the two ever disagree.

## Adding third-party assets

If you vendor a third-party library, font, icon set or other asset, add it to
[THIRD-PARTY-NOTICES](THIRD-PARTY-NOTICES) with its version, license and the
**full license text**, and confirm the versions match what is actually bundled.
A minified file that has had its upstream copyright header stripped is not
self-attributing: the notice has to exist here instead.
