"""
The evolution journey: a system that has been baselined, and an Initiative after it.

WHY THIS FILE EXISTS. The individual steps were each tested — freeze refuses while
assertions are outstanding, freeze succeeds once they are reviewed, promote reports
what it left behind, the diff page renders. What was never tested is the PATH: that a
baselined system can then be evolved by a *later* Initiative, that the merge into the
system baseline actually happens and persists, and that the second Initiative's facts
stay distinguishable from established truth.

That gap was not academic. Driving the path found a live defect: `SCOPE_INITIATIVE`
was declared twice in `core/knowledge/model.py` — once as the assertion lifecycle
(`"INITIATIVE_PROPOSAL"`) and once as the identifier identity scope (`"INITIATIVE"`)
— and the later declaration silently won. `serialise` restored a scope-less
assertion as `"INITIATIVE_PROPOSAL"`, so `promote_to_baseline`, which compared against
`"INITIATIVE"`, skipped it. It fell through a bare `continue` that counted nothing, so
the merge reported "0 promoted, 0 left behind" — a fact that could never be baselined,
reading exactly like there was nothing to baseline.

Measured before the fix: 628 assertions in `data/sea_home_01` and 827 in
`data/sea_home_02`, **every one** at scope `"INITIATIVE"` and **not one** at
`"SYSTEM_BASELINE"`. The merge had never succeeded anywhere.

The tests below drive the whole path through the real routes, because a unit test of
`promote_to_baseline` on a hand-built graph is exactly what missed this.
"""

from __future__ import annotations

from core.knowledge import RevisionStore
from core.knowledge.model import (
    SCOPE_BASELINE,
    SCOPE_INITIATIVE,
    is_initiative_scope,
)


def store_for(app) -> RevisionStore:
    return RevisionStore(app.config["STORE_ROOT"]).ensure()


def _scopes(graph):
    """assertion count per scope, as the merge sees them."""
    counts = {}
    for a in graph.assertions.values():
        counts[a.scope] = counts.get(a.scope, 0) + 1
    return counts


def _verify_everything(client):
    """The review gate, satisfied: every assertion reviewed in one bulk decision."""
    client.post("/review/bulk", data={"bulk_scope": "threshold", "threshold": "1.0"})


def _baseline_the_system(client, app, label="System baseline v1"):
    """Stages 3-4 of the journey: review, commit, freeze.

    Three steps and not one, deliberately: a revision is a snapshot, so reviewing the
    working set afterwards does not review a revision taken before it.
    """
    _verify_everything(client)
    client.post("/changes/commit", data={"label": label})
    revision = store_for(app).latest()
    response = client.post(
        "/changes/freeze",
        data={"revision_id": revision.id, "label": label},
        follow_redirects=True,
    )
    assert b"Frozen as baseline" in response.data, response.data[:400]
    return store_for(app).get_revision(revision.id)


# ============================================================================
# 1. The scope vocabulary itself
# ============================================================================


def test_the_assertion_scope_and_the_identity_scope_are_different_names():
    """The collision that cost the whole merge.

    `SCOPE_INITIATIVE` was declared twice: as the assertion lifecycle value
    `"INITIATIVE_PROPOSAL"` and, later in the same module, as the identifier identity
    scope `"INITIATIVE"`. Whichever line came last won, so the name meant two things
    depending on where you read it, and `serialise`'s hardcoded default disagreed with
    the constant actually in force.

    The assertion lifecycle keeps the documented value — `docs/living-system-architecture.md`
    §2 states the merge promotes `INITIATIVE_PROPOSAL` to `SYSTEM_BASELINE` — and the
    identity scope is now named for what it is.
    """
    from core.knowledge.model import (
        IDENTITY_SCOPE_DOCUMENT,
        IDENTITY_SCOPE_ENTERPRISE,
        IDENTITY_SCOPE_INITIATIVE,
        IDENTITY_SCOPE_RUN,
    )

    assert SCOPE_INITIATIVE == "INITIATIVE_PROPOSAL"
    assert SCOPE_BASELINE == "SYSTEM_BASELINE"
    # The identity scopes keep their values: the ontology's `IdentityScope` enum fixes
    # them, so the rename is a rename and not a re-valuing.
    assert IDENTITY_SCOPE_INITIATIVE == "INITIATIVE"
    assert {IDENTITY_SCOPE_ENTERPRISE, IDENTITY_SCOPE_DOCUMENT, IDENTITY_SCOPE_RUN} == {
        "ENTERPRISE", "DOCUMENT", "RUN",
    }
    # They are distinct namespaces now, and the assertion one is not accidental.
    assert SCOPE_INITIATIVE != IDENTITY_SCOPE_INITIATIVE


def test_a_fact_that_loads_without_a_scope_is_still_promotable():
    """The defect, pinned at the seam that caused it.

    `serialise` restores a missing scope from a hardcoded literal. When that literal
    disagreed with the constant in force, the restored fact carried a scope no code
    recognised and could never reach the baseline.
    """
    from core.knowledge.model import KnowledgeGraph, STATUS_VERIFIED
    from core.knowledge.serialise import graph_from_dict, graph_to_dict

    graph = KnowledgeGraph()
    graph.add_assertion("a:one", "p", value="v", scope=SCOPE_INITIATIVE,
                        status=STATUS_VERIFIED)

    data = graph_to_dict(graph)
    assert list(data["assertions"].values())[0]["scope"] == SCOPE_INITIATIVE

    # A payload written before the field existed, or by hand.
    stripped = dict(data)
    stripped["assertions"] = {
        k: {kk: vv for kk, vv in v.items() if kk != "scope"}
        for k, v in data["assertions"].items()
    }
    restored = list(graph_from_dict(stripped).assertions.values())[0]

    assert is_initiative_scope(restored.scope), (
        f"a scope-less assertion restored as {restored.scope!r}, which no reader "
        f"recognises as an initiative proposal"
    )


def test_a_legacy_initiative_value_still_merges():
    """Real graphs carry `"INITIATIVE"` — every assertion in `data/` does, because
    that is what the shadowing constant wrote. A fix that recognised only the new
    value would have made all of them permanently unbaselineable, which is the very
    failure being fixed."""
    from core.knowledge.model import KnowledgeGraph, STATUS_VERIFIED
    from core.knowledge.review import ReviewLog, promote_to_baseline

    graph = KnowledgeGraph()
    graph.add_assertion("a:legacy", "p", value="v", scope="INITIATIVE",
                        status=STATUS_VERIFIED)

    result, _ = promote_to_baseline(graph, ReviewLog(), actor="t")

    assert result.promoted == 1
    assert list(graph.assertions.values())[0].scope == SCOPE_BASELINE


# ============================================================================
# 2. Nothing is skipped without being counted
# ============================================================================


def test_the_merge_accounts_for_every_fact_it_considers():
    """The silent skip, which is what made the defect invisible for so long.

    `promote_to_baseline` used a bare `continue` for a scope it did not recognise, so
    the fact vanished from the report. "Promoted 0, left behind 0" is
    indistinguishable from "nothing to promote", and the one situation a reviewer
    cannot detect is the one they most need told.
    """
    from core.knowledge.model import (
        KnowledgeGraph,
        STATUS_DISPUTED,
        STATUS_UNVERIFIED,
        STATUS_VERIFIED,
    )
    from core.knowledge.review import ReviewLog, promote_to_baseline

    graph = KnowledgeGraph()
    graph.add_assertion("a:verified", "p", value="1",
                        scope=SCOPE_INITIATIVE, status=STATUS_VERIFIED)
    graph.add_assertion("a:unverified", "p", value="2",
                        scope=SCOPE_INITIATIVE, status=STATUS_UNVERIFIED)
    graph.add_assertion("a:disputed", "p", value="3",
                        scope=SCOPE_INITIATIVE, status=STATUS_DISPUTED)
    graph.add_assertion("a:baseline", "p", value="4", scope=SCOPE_BASELINE)
    graph.add_assertion("a:foreign", "p", value="5", scope="SOMETHING_ELSE")

    result, _ = promote_to_baseline(graph, ReviewLog(), actor="t")

    assert result.promoted == 1
    assert result.skipped_unverified == 1
    assert result.skipped_disputed == 1
    assert result.already_baseline == 1
    assert result.unrecognised_scope == 1
    # The property, rather than the five numbers: every fact landed somewhere.
    assert result.considered == len(graph.assertions)


# ============================================================================
# 3. The path: baseline, then evolve, then merge
# ============================================================================


def _add_initiative_fact(app, label, initiative_id):
    """A second Initiative's proposal, written into the working set.

    Seeded through the store rather than by re-ingesting, because the fake extractor
    returns one fixed graph for any document: a second ingest of the same fixture
    folds into the facts already there and produces no new proposal to merge. The
    journey under test is baseline -> evolve -> merge, so the evolution is supplied
    directly and the merge is driven through the real route.
    """
    store = store_for(app)
    snapshot = store.load_working()
    node_id = snapshot.graph.add_node("FunctionalRequirement", label)
    snapshot.graph.add_assertion(
        node_id, "description", value=f"{label} proposed by {initiative_id}",
        scope=SCOPE_INITIATIVE, initiative_id=initiative_id,
    )
    store.save_working(snapshot.graph, snapshot.log, snapshot.meta)
    return node_id


def test_the_system_baseline_is_frozen_before_any_evolution(seeded_client, app):
    """Stages 3-4. A baseline is a revision a human vouched for, and it is the thing
    later work diffs against.

    The refusal path — freezing a revision nobody has reviewed — is not re-asserted
    here; it belongs to the gate itself and lives in `test_app.py`.
    """
    revision = _baseline_the_system(seeded_client, app)

    assert revision.is_baseline
    assert revision.frozen_by == "tester"
    assert store_for(app).baselines(), "no baseline after freezing one"
    assert store_for(app).baselines()[0].id == revision.id


def test_an_initiative_merges_into_the_system_baseline_and_it_persists(seeded_client, app):
    """The merge, through the route a human uses, read back from disk.

    The in-memory mutation is not the claim; surviving a reload is. A promotion that
    only changed the object in the request would look identical on the page.
    """
    _baseline_the_system(seeded_client, app)
    # Everything is verified, so the merge should take everything.
    client = seeded_client
    response = client.post("/changes/promote", follow_redirects=True)

    assert b"Promoted" in response.data
    assert b"unrecognised scope" not in response.data, response.data[:600]

    reloaded = store_for(app).load_working().graph
    scopes = _scopes(reloaded)
    assert scopes.get(SCOPE_BASELINE), f"nothing reached the baseline: {scopes}"
    assert not any(is_initiative_scope(s) for s in scopes), (
        f"facts left unmerged after a promote over verified input: {scopes}"
    )


def test_a_later_initiative_evolves_the_baseline_rather_than_replacing_it(seeded_client, app):
    """THE JOURNEY. A baselined system, then a second Initiative.

    This is the path `docs/user-journey.md` describes and nothing exercised: the
    baseline is established truth, a later project proposes changes against it, and
    merging those changes must ADD to the baseline rather than rewrite it. If the
    second ingest could overwrite or discard what was already baselined, the platform
    would lose the system's memory on every project — the one thing it exists to hold.
    """
    client = seeded_client

    # -- the system is baselined ------------------------------------------
    _baseline_the_system(client, app, label="REQ-G v1")
    client.post("/changes/promote", follow_redirects=True)
    established = {
        a.id for a in store_for(app).load_working().graph.assertions.values()
        if a.scope == SCOPE_BASELINE
    }
    assert established, "the first initiative produced no baseline facts"

    # -- a second initiative arrives --------------------------------------
    _add_initiative_fact(app, "Settlement Report", "INIT-B")

    evolved = store_for(app).load_working().graph
    assert established <= set(evolved.assertions), (
        "a later Initiative's change discarded facts already in the baseline — the "
        "platform lost the system's memory on the next project"
    )

    # Its own facts are initiative-scoped and attributed to the new Initiative.
    initiative_facts = [a for a in evolved.assertions.values()
                        if is_initiative_scope(a.scope)]
    assert initiative_facts, "the second initiative's facts did not arrive as proposals"
    assert {a.initiative_id for a in initiative_facts} == {"INIT-B"}, (
        "the later Initiative's facts are not attributed to it, so a promotion "
        "cannot tell whose change it is merging"
    )

    # -- and they merge, on top of what is already there -------------------
    _verify_everything(client)
    client.post("/changes/promote", follow_redirects=True)

    final = store_for(app).load_working().graph
    assert established <= set(final.assertions), "the merge dropped baselined facts"
    assert all(not is_initiative_scope(a.scope) or not a.is_active
               for a in final.assertions.values()), (
        "an active fact was left unmerged after a promote over verified input"
    )
    assert len([a for a in final.assertions.values() if a.scope == SCOPE_BASELINE]) \
        >= len(established)


def test_the_diff_shows_what_the_later_initiative_changed(seeded_client, app):
    """Evolution is only reviewable as a change set. The baseline revision is the
    'before', and a later Initiative's addition must show against it — an evolution
    nobody can diff is an evolution nobody can approve."""
    client = seeded_client
    revision = _baseline_the_system(client, app, label="REQ-G v1")

    client.post("/changes/promote", follow_redirects=True)

    # A promotion changes each fact's SCOPE, and the delta reports that honestly: an
    # approver looking at "what changed since the baseline" should see a merge happen.
    after_promote = client.get(f"/changes/diff?from={revision.id}&to=working")
    assert after_promote.status_code == 200

    _add_initiative_fact(app, "Settlement Report", "INIT-B")

    changed = client.get(f"/changes/diff?from={revision.id}&to=working")
    assert changed.status_code == 200
    assert b"Settlement Report" in changed.data, (
        "the diff did not report a later Initiative's addition"
    )


def test_the_frozen_baseline_reaches_the_design_run(seeded_client, app, design_output):
    """The ARC-G half of the question: how an architecture gets promoted.

    A frozen revision is what a design extends, and this checks the seam that makes
    that true — the app resolving `baselines()[0]` into the `baseline` and `base_ref`
    the agent receives. The recorded input is the assertion, not the rendered page:
    the fake design factory hardcodes its own `design_caveats`, so the preview text
    says "no frozen baseline" whatever the app does. Asserting on the page here would
    have tested the fixture.
    """
    from tests.conftest import FakeExtractor

    built = []

    def capturing_factory():
        extractor = FakeExtractor(design_output, metadata={"model_id": "fake-design-model"})
        built.append(extractor)
        return extractor

    app.config["DESIGN_FACTORY"] = capturing_factory

    seeded_client.post("/design/draft", data={})
    before = built[-1].calls[-1]
    assert before["baseline"] is None, (
        "a design run with no frozen baseline was handed one"
    )
    assert before["base_ref"] == ""

    revision = _baseline_the_system(seeded_client, app, label="ARC-G v1")

    seeded_client.post("/design/draft", data={})
    after = built[-1].calls[-1]

    assert after["baseline"] is not None, (
        "a frozen baseline exists but the design run was handed none — the proposal "
        "would extend nothing and duplicate the architecture it cannot see"
    )
    assert after["base_ref"] == revision.id, (
        f"the design run was told to extend {after['base_ref']!r}, not the frozen "
        f"baseline {revision.id!r}"
    )
    assert after["baseline"].nodes, "the baseline handed to the design run is empty"
