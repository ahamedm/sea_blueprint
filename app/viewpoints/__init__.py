"""
Architecture viewpoints — how the architecture is *described*, not how the graph
is read.

This package is the other half of a distinction that was previously fused inside a
single module. Both halves are loosely called "projection"; they are not the same
thing and they do not change for the same reasons.

| | `app.projections` | `app.viewpoints` |
|---|---|---|
| Concern | presenting the graph | describing the architecture |
| Knows about | assertions, confidence, provenance, filters | node kinds, layers, lenses, notation |
| Changes when | the knowledge model changes | the notation, or the views offered, changes |
| Example | "give me the rows a reviewer judges" | "draw every requirement and architecture concept" |
| Domain-specific | no | yes |

Dependency direction is one way: **viewpoints compose projections.** A viewpoint
calls `node_records`, `edge_records` and `literal_facts` and then *selects* — which
element kinds belong at this level, which facts are detail rather than
relationships, what the renderer receives. It never re-derives assertions itself,
because a second implementation of assertion flattening is a second thing to keep
correct.

A viewpoint is a *deliberate* reduction. A lens shows part of the map and omits
the rest; what it omits is real and is still in the graph. That omission is the
viewpoint working, not data being lost — `excluded_kinds` says what was left out,
so "not in this lens" cannot be misread as "not in the graph".

Available viewpoints:

    merged.merged_view(graph, lens)  — the whole knowledge graph, filtered by lens

Both sides of the graph, because one that drew only architecture elements left the
requirements graph with no view at all — the failure this package's default now
exists to prevent.

Candidate future occupants: a C4 *specification* view with a canonical notation and
a stable artefact (YB-025), a deployment view, a data-flow view, an
initiative-vs-baseline comparison view.
"""
