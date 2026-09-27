---
id: YB-012
legacy: "12"
title: "C4 notation parser — deterministic extraction from structured architecture sources"
status: open
priority: medium
area: "new `agents/extraction/c4_parser.py`, integration with architecture extraction agent"
created: 2026-09-21
updated: 2026-09-26
design: docs/design/c4-notation-parser.md
record: null
superseded_by: []
related: ["YB-006", "YB-024", "ADR-0029"]
blocks: []
blocked_by: []
---

# YB-012 — C4 notation parser — deterministic extraction from structured architecture sources

> **Open work.** This file is the source of truth for this item; `TODO.md` is generated from it.

**Legacy status:** Not started — **flagged CRITICAL**
**Legacy priority:** Critical
**Legacy area:** new `agents/extraction/c4_parser.py`, integration with architecture extraction agent

> **Reprioritised 2026-09-26: critical → medium.** The old label was aspirational and the
> item's own analysis concedes it — *"critical, not because it's blocking the current
> workflow, but because it's the right way to do architecture extraction."* In the
> meantime the defects that actually corrupt the graph (YB-030, YB-031, YB-032, YB-038)
> sat beneath it, which is a priority field pointing away from the damage. The value here
> is real but **conditional on the input being C4 notation**; today's corpus is prose
> markdown, where this item changes nothing. It competes for attention now rather than
> leading it.

**Full analysis:** [`docs/design/c4-notation-parser.md`](../../design/c4-notation-parser.md)

The complete write-up for this item lives in the design document above, preserved verbatim from `TODO.md` v1 (YB-012).
