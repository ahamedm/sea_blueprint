"""
Architecture viewpoints — how the architecture is *described*, not how the graph
is read.

This package is the other half of a distinction that was previously fused inside a
single module. Both halves are loosely called "projection"; they are not the same
thing and they do not change for the same reasons.

| | `app.projections` | `app.viewpoints` |
|---|---|---|
| Concern | presenting the graph | describing the architecture |
| Knows about | assertions, confidence, provenance, filters | notations: C4 levels, element kinds |
| Changes when | the knowledge model changes | the notation, or the views offered, changes |
| Example | "give me the rows a reviewer judges" | "draw a C4 container view" |
| Domain-specific | no | yes |

Dependency direction is one way: **viewpoints compose projections.** A viewpoint
calls `node_records`, `edge_records` and `literal_facts` and then *selects* — which
element kinds belong at this level, which facts are detail rather than
relationships, what the renderer receives. It never re-derives assertions itself,
because a second implementation of assertion flattening is a second thing to keep
correct.

A viewpoint is a *deliberate* reduction. C4's context level shows software systems
and the people who use them; a container inside a system is real and is omitted
anyway. That omission is the viewpoint working, not data being lost — the graph
still holds everything, and `excluded_kinds` says what was left out.

Available viewpoints:

    c4.c4_view(graph, level)   — C4 context / container / component

Candidate future occupants of this package: C4/Structurizr parsing (TODO item 12),
a deployment view, a data-flow view, an initiative-vs-baseline comparison view.
"""
