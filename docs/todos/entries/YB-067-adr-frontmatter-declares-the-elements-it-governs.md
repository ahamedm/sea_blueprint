---
id: YB-067
legacy: null
title: "The 38 recorded decisions govern nothing — ADR frontmatter should name the elements they affect"
status: open
priority: high
area: "`docs/decisions/ADR-*.md` (frontmatter), `core/knowledge/decisions.py` (`load_adr_records`), `core/knowledge/ingest.py` (the `architecture_decisions` path already reads `affects_elements`)"
created: 2026-10-03
updated: 2026-10-03
design: null
record: null
superseded_by: []
related: [YB-066, YB-010, YB-056]
blocks: []
blocked_by: []
---

# YB-067 — The recorded decisions govern nothing

> **Open, high.** Split out of [YB-066](YB-066-natural-language-enquiry.md) on 2026-10-03.
> It blocks that item's impact class, and it is a decision about the ADR corpus rather than a
> coding task.

## The measurement

`d831adf` added `ArchitectureDecision` ingest, `TradeOff` nodes and the digest rendering, and
`core/knowledge/decisions.py` deliberately parses the 38 ADRs conservatively:

> `consequences`, `alternatives_considered`, `affects_elements` and `supersedes` are **NOT
> inferred from prose** — those richer links come from the Design Assistant's decisions pass,
> which is a proposal, not an extraction.

The refusal is right, and its consequence is the problem: **the recorded decisions have no edge
into the graph.** `affects_elements` is written only for *proposed* decisions, so
"which decisions govern X?" answers from the proposals and returns nothing for the 38 ADRs —
i.e. nothing for the part a human actually vouched for. [M] 0 `ArchitectureDecision` nodes exist
in any of the five revisions of either scope today, and the loader parses all 38 files cleanly
(38 files → 38 records, 0 skipped), so the gap is the missing edges, not a parse failure.

## Update 2026-10-03 — the extraction profile could not record a decision either

[ISS-18](../../../ISSUES.md#iss-18--an-architecture-ingest-could-never-record-a-decision-and-nothing-failed-when-it-did-not-fixed-2026-10-03)
found a second reason the graph held 0 decision nodes: `ARCHITECTURE_PASSES` had no
`decisions` pass at all, so an architecture INGEST could never emit one — the ADR loader
was the only producer. That is now fixed: the extraction profile runs a decisions pass, and
it emits `affects_elements` for decisions a document states.

That narrows this item rather than closing it, and the distinction is the one that matters:
a decision extracted from a document is `EXTRACTION_AGENT` and `UNVERIFIED`, so its
`affects_elements` is a CLAIM awaiting review. The 38 recorded ADRs are a human's own
record, and inferring their element links from prose would stamp that guess
`HUMAN_ARCHITECT`. Both routes now exist and they answer different questions — "what does
this document assert?" versus "what did we decide?" — and the second still needs the
frontmatter.

## Why it cannot be fixed by inference

The obvious fix — read `affects_elements` out of ADR prose — is the one `decisions.py` already
declines, for the reason this repo keeps re-learning: an inferred link between a decision and an
element would be **stamped `HUMAN_ARCHITECT` while being a model's guess**. That is the
false-assurance shape the review gate exists to prevent, and it would poison the one provenance
value the platform sells.

## The options

1. **Human-written frontmatter (recommended).** Add an `affects_elements:` list to the 38 ADR
   files and have `load_adr_records` read it. Small, honest, and consistent: the ADRs are
   human-written records, so their links should be human-written too, and the existing ingest
   path already consumes the field, so this is a loader change plus 38 frontmatter edits.
2. **A human step in the app.** Surface "which elements does this decision govern?" as a review
   action over the ingested decisions. More machinery, but it puts the annotation where a
   reviewer already is, and it scales past the current corpus.
3. **Infer, then require review.** Ingest proposed links with `status: proposed` until a human
   accepts them. Cheapest to populate, and it is the only option that makes the provenance
   channel ambiguous — the ingest would have to keep the two origins visibly distinct
   everywhere the graph is read, which is the risk YB-057 already carries.

## Why it is not merely a nice-to-have

An impact answer is only as good as the edges it walks. Without this, the honest answer to
*"what would changing X contradict?"* names the **Design Assistant's proposals** and stays silent
about the decisions a human made — which is precisely backwards, and would present the
least-authoritative part of the graph as the authority.

## What closes it

A chosen option, the loader reading whatever it writes, the 38 ADRs (or a review path) carrying
the links, the decisions ingested into a scope so the nodes exist at all, and an impact answer
that can name a **recorded** decision. The `test_the_real_adr_directory_parses` bound should move
from `>= 10` to the corpus size at the same time — skipping is silent by design, so that
assertion currently allows 28 ADRs to vanish without a failure.
