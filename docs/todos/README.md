# TODO system

`TODO.md` at the repo root is an **index**, not a document. It is generated from
the files in this directory. Everything below exists so that status is a field
rather than a sentence, and so that a finished item leaves the open list instead
of growing it.

## Layout

| Path | Holds | Edited by hand? |
|---|---|---|
| `TODO.md` | the generated index — open work, then records | **no** |
| `entries/YB-NNN-*.md` | one open item per file, with YAML front matter | yes |
| `../decisions/ADR-NNNN-*.md` | closed work: what was decided, with the evidence | yes |
| `../design/*.md` | long analysis and design sketches, moved out of entries | yes |
| `id-map.md` | legacy `item N` → current id | generated |
| `legacy-todo-v1.md` | the original 2,555-line `TODO.md`, frozen for provenance | **no** |

## Commands

```sh
uv run scripts/todo.py render    # regenerate TODO.md, id-map.md, decisions/README.md
uv run scripts/todo.py check     # validate front matter, references, and freshness
```

`check` is covered by `tests/test_todo.py`, so `pytest` fails on a stale index, a
duplicate id, a bad status, or a cross-reference that points at nothing.

## Entry front matter

```yaml
---
id: YB-012                              # minted once, never renumbered
legacy: "12"                            # the v1 item number, for old references
title: C4 notation parser — deterministic extraction from structured sources
status: open                            # open | in-progress | blocked | parked | done | superseded
priority: critical                      # critical | high | medium | low
area: new `agents/extraction/c4_parser.py`
created: 2026-09-21
updated: 2026-09-24
design: docs/design/c4-notation-parser.md   # long analysis, if any
record: null                                # required when status is done
superseded_by: []                       # required when status is superseded
related: [YB-006, YB-024]
blocks: []
blocked_by: []
---
```

Body conventions, straight from what the v1 items got right:

- **State the mechanism, not just the symptom.** The entries that aged well
  explain *why* something fails; the ones that aged badly only said *that* it did.
- **Record the measurement.** Counts, percentages, and the document they came
  from — a later reader cannot re-derive them.
- **Keep the lesson.** "Check examples before tuning instructions" is worth more
  than the diff that fixed it.
- **Cite the file.** `**Full analysis:**` links out when the body would exceed a
  screen or two.

## Adding an item

1. Copy the front matter block above into `entries/YB-NNN-<slug>.md`, using the
   next free number. Numbers are never reused and never reassigned.
2. Write the body.
3. `uv run scripts/todo.py render && uv run scripts/todo.py check`

## Closing an item

Closing is a move, not a status change — that is what keeps the open list honest.

1. Write `docs/decisions/ADR-NNNN-<slug>.md`, moving the item's body across
   verbatim. Keep the write-up: the root cause and the measurement are the point.
2. Delete the entry file. It is in `git log` and in `legacy-todo-v1.md` if
   anyone needs it.
3. If the item carries follow-ups that stay open, keep the entry with
   `status: done` and `record: docs/decisions/ADR-NNNN-<slug>.md`.
4. `uv run scripts/todo.py render && uv run scripts/todo.py check`

## Why the legacy numbering is frozen

`item N` is referenced from `agents/`, `app/`, `config/`, and `docs/`. The
identifiers here were deliberately kept aligned with the v1 numbers — `YB-005` is
old item 5 — so those references still resolve. `id-map.md` bridges the rest,
including the second `item 19`, which is now `YB-019b`.

New work starts at `YB-027`; `ADR-0012` is the next record.
