---
id: ADR-0025
title: "Headless reconciliation — a dry run by default, and the declines are reported"
status: accepted
date: 2026-09-26
area: "scripts/reconcile.py (new), tests/test_reconcile_cli.py (new)"
related: ["YB-005", "YB-027", "ADR-0012"]
---

# ADR-0025 — Headless reconciliation

> **Record.** Built as an enabler. Reconciliation has only ever been reachable
> through the web route.

---

### Why

Reconciliation could be run one way: open `/reconcile`, read the proposals, press
"bulk resolve". That makes a routine, repeatable pass depend on a browser and a
running server, so it cannot run in CI, on a schedule, or on a machine with no UI.

The reconciliation itself never needed the web layer. `reference_candidates` and
`bulk_resolve` live in `core/`, so `scripts/reconcile.py` is a thin shell over them.
**Nothing on this path imports Flask** — the extraction pipeline must not know what
a subscriber is, and the same holds here.

### Dry run is the default

Binding a traceability link is a judgement call, and the standing rule is that a
wrong link is worse than a missing one: it makes the audit *confidently* wrong
rather than merely incomplete. So the script proposes and reports, and writes
nothing until `--apply` is given. The pass always runs in memory, so a dry run also
shows what the coverage *would* be — labelled as such, because a dry run that
printed only the before-state would be useless and one that printed an after-state
without saying it was hypothetical would be a lie.

### It reports what it declined

A reference below the threshold and a reference with no candidate of the expected
kind are named, not silently dropped. This is the part that makes the tool worth
having: on the real store it reported **1 resolvable at 0.93 and 10 below
threshold at 0.28–0.62**, which is YB-027's paraphrase gap reproduced as a number
rather than as an argument. A tool that reported only "1 resolved" would hide the
finding that matters.

### What was rejected

- **A `sea-agent` subcommand instead of a script.** Consistent with the other
  agent CLIs, but reconciliation is not an agent run — it is a graph operation, and
  `scripts/` is where the other graph operations live (`run_real_ingest.py`,
  `review_assertions.py`).
- **Applying by default with a `--dry-run` escape.** Inverts the safe default for
  the convenience of the common case, on an action that writes traceability into
  the audit trail.
- **Reimplementing the matching in the script.** It would drift from the route's
  semantics immediately; the script calls the same `bulk_resolve` the route does,
  including the property that it persists only when something was resolved.

### Evidence

```
tests/test_reconcile_cli.py — dry run leaves the store byte-identical (sha256 of
                              every file); --apply binds and persists; below-
                              threshold and no-candidate stay open and are
                              reported; threshold 1.0 writes nothing; the actor
                              lands in provenance; and a subprocess import asserts
                              flask / strands / app are absent from sys.modules
```

Live: on the DeepSeek store, a dry run proposed 14 references and bound 1; `--apply`
moved realization coverage from 0 to 1 with the audit trail naming the actor.
