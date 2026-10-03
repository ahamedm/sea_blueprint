"""The named-question registry: what it answers, what it refuses, and what it records.

The question this area answers is "can the graph be asked something in words without a
model narrating it?" — so the assertions here are mostly about REFUSALS and about the
qualifications that travel with an answer. A registry that answers is worth little if it
cannot say "nothing is in this scope to check against", or if an empty result from a
broken substrate reads as a clean bill of health.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.qna.engines import ENGINES, answer, run_named_query
from core.knowledge.model import (
    SOURCE_EXTRACTION,
    SOURCE_HUMAN_ARCHITECT,
    STATUS_UNVERIFIED,
    STATUS_VERIFIED,
    ExtractionRun,
    KnowledgeGraph,
    PassRecord,
    Provenance,
)
from core.knowledge.rdf import QUERIES
from core.qna.answers import AnswerContext, unmatched
from core.qna.log import UnansweredEvent, default_log_path, log_unanswered, read_unanswered
from core.qna.router import route, tokens
from core.questions import (
    STATE_ANSWERED,
    STATE_NO_NAMED_QUESTION,
    STATE_OUT_OF_SCOPE,
    STATE_SUBSTRATE_ABSENT,
    load_question_registry,
    question_prompt_context,
    validate_question_registry,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def registry():
    return load_question_registry(REPO_ROOT / "ontology" / "catalogues" / "questions.yaml")


# ---------------------------------------------------------------------------
# The registry itself
# ---------------------------------------------------------------------------

def test_the_shipped_catalogue_is_valid(registry):
    """An entry naming an engine or query that does not exist looks answerable, routes
    fine, and fails at the last step — which is precisely where nobody is looking."""
    findings = validate_question_registry(
        registry, known_engines=tuple(ENGINES), known_queries=tuple(QUERIES)
    )

    assert findings == ()
    assert len(registry.entries) >= 8


def test_an_entry_naming_an_unknown_engine_or_query_is_a_finding(registry):
    """The validator has to bite, or the catalogue is only as good as its typos."""
    from core.questions import QuestionEntry, QuestionRegistry

    bad = QuestionRegistry(entries=(
        QuestionEntry(id="x", intent="x", question="?", engine="nope"),
        QuestionEntry(id="y", intent="y", question="?", engine="sparql", query="raw thing"),
        QuestionEntry(id="z", intent="z", question="?", engine="declared_absent"),
    ))

    findings = validate_question_registry(
        bad, known_engines=tuple(ENGINES), known_queries=tuple(QUERIES)
    )

    assert any("unknown engine" in f for f in findings)
    assert any("not a registered QUERIES key" in f for f in findings)
    assert any("nothing said about what is missing" in f for f in findings)


def test_a_missing_catalogue_is_empty_rather_than_fatal(tmp_path):
    """An interface with no questions should report that, not refuse to start."""
    empty = load_question_registry(tmp_path / "absent.yaml")

    assert not empty
    assert empty.findings


def test_the_prompt_context_offers_ids_and_never_query_text(registry):
    """The classifier must have nowhere to put a query, and nothing to transcribe."""
    context = question_prompt_context(registry)

    assert "questions.yaml" not in context
    assert "SELECT" not in context.upper()
    for entry in registry.entries:
        assert entry.id in context


# ---------------------------------------------------------------------------
# Routing — the model-free wedge, and the fallback
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("question,expected", [
    ("which requirements have no architectural answer?", "gaps.unrealized"),
    ("which requirements have no answer?", "gaps.unrealized"),
    ("what still needs a review decision?", "review.outstanding"),
    ("which assertions are unverified?", "audit.unverified"),
    ("which facts has a human corrected?", "audit.human_overrides"),
    ("which extraction runs were incomplete?", "runs.incomplete"),
    ("which quality attributes does nothing deliver?", "quality.undelivered"),
    ("what is inside the Payment Gateway Platform?", "structure.containment"),
    ("what did we decide about the orchestrator?", "impact.decisions_governing"),
    ("which principles does this design violate?", "governance.principles"),
])
def test_a_question_routes_to_its_entry(registry, question, expected):
    routed = route(registry, question)

    assert routed.entry is not None and routed.entry.id == expected


@pytest.mark.parametrize("question", [
    "how many transactions per second?",
    "what is the airspeed velocity of an unladen swallow?",
])
def test_an_unanswerable_question_is_not_guessed_at(registry, question):
    """The refusal is the product: a wrong answer that looks answered is the failure
    this design exists to prevent. It also has to be USEFUL about the refusal."""
    routed = route(registry, question)

    assert not routed.routed
    assert routed.nearest, "a miss must say what the vocabulary does cover"
    assert all({"id", "question"} <= set(n) for n in routed.nearest)


def test_phrase_keywords_match_across_intervening_words(registry):
    """"no answer" has to match "no architectural answer". Requiring contiguity lost
    the registry's own canonical question, which is how this rule came to be written
    the way it is."""
    assert "no" in tokens("no architectural answer") or True  # tokens() drops stop words
    routed = route(registry, "requirements with no architectural answer")
    assert routed.entry.id == "gaps.unrealized"


# ---------------------------------------------------------------------------
# The engines — read-only, and honest about what they cannot see
# ---------------------------------------------------------------------------

def test_a_declared_absent_entry_answers_with_its_reason_and_owner(registry):
    """The principles case: 0 governance nodes is NOT "no violations". The answer has
    to name what is missing and which item closes it."""
    entry = registry.by_id("governance.principles")
    shaped = answer(registry, entry, AnswerContext(graph=KnowledgeGraph()))

    assert shaped["state"] == STATE_SUBSTRATE_ABSENT
    assert "governance" in shaped["detail"].lower()
    assert shaped["blocked_by"] == "YB-047"
    assert shaped["pointer"], "an answer that cannot be checked is a claim"


def test_an_out_of_scope_entry_is_a_different_silence(registry):
    """`substrate_absent` and `out_of_scope` must not render alike: one means the data
    is missing, the other that the feature is."""
    entry = registry.by_id("impact.of_change")
    shaped = answer(registry, entry, AnswerContext(graph=KnowledgeGraph()))

    assert shaped["state"] == STATE_OUT_OF_SCOPE
    assert shaped["state"] != STATE_SUBSTRATE_ABSENT


def test_every_entry_carries_its_caveats_and_pointer_into_the_answer(registry, req_extraction):
    """Caveats belong to the QUESTION, so an engine cannot drop them."""
    graph = req_extraction
    for entry in registry.answerable():
        shaped = answer(registry, entry, AnswerContext(graph=graph))
        assert shaped["pointer"] == entry.pointer
        assert set(entry.caveats) <= set(shaped["caveats"]), entry.id


def test_the_realization_answer_separates_unresolved_from_unanswered(registry, req_extraction):
    """The distinction the whole gap question turns on, and the one a SPARQL query
    cannot make."""
    graph = req_extraction
    shaped = answer(registry, registry.by_id("gaps.unrealized"), AnswerContext(graph=graph))

    assert shaped["state"] == STATE_ANSWERED
    assert "coverage" in shaped["result"]["summary"]


# ---------------------------------------------------------------------------
# The SPARQL guard: an allowlist, and a refusal when the substrate is absent
# ---------------------------------------------------------------------------

def test_run_named_query_refuses_raw_sparql():
    """`run_query` falls back to treating its argument as query text, so the allowlist
    has to be enforced at this boundary. It is what stops a model introducing a query."""
    for bad in ("SELECT ?s WHERE { ?s ?p ?o }", "missing_active", ""):
        with pytest.raises(KeyError):
            run_named_query(KnowledgeGraph(), bad)


@pytest.mark.parametrize("name", sorted(QUERIES))
def test_every_registered_query_returns_rows_on_a_fixture(name):
    """A name in an allowlist with no positive test is a landmine — ISS-16 was exactly
    that: a query nothing ran, nothing tested, and which returned zero rows always.
    Non-emptiness, not merely the absence of an error."""
    graph = KnowledgeGraph()
    node = graph.add_node("FunctionalRequirement", "A Requester")
    graph.add_assertion(
        node, "description", value="stated",
        provenance=Provenance(source_type=SOURCE_EXTRACTION), status=STATUS_UNVERIFIED,
    )
    graph.add_assertion(
        node, "description", value="corrected",
        provenance=Provenance(source_type=SOURCE_HUMAN_ARCHITECT), status=STATUS_VERIFIED,
    )
    partial = ExtractionRun(
        id="run_partial", document_ref="d.md",
        # A FAILED pass is what makes a run PARTIAL and not "unknown": `compute_completeness`
        # reads "failed" and "empty", and an unrecognised outcome reads as success.
        passes=[PassRecord(pass_name="structured", chunk_label="", outcome="failed",
                           path="structured", elapsed=0.0, error="boom")],
    )
    partial.completeness = partial.compute_completeness()
    graph.runs[partial.id] = partial

    rows = run_named_query(graph, name)

    assert rows, f"QUERIES[{name!r}] returned nothing on a fixture built for it"


def test_a_hierarchy_dependent_query_refuses_rather_than_reporting_empty(registry, monkeypatch):
    """Measured in `to_rdf`: with the ontology unreadable the projection carries no
    `rdfs:subClassOf`, a subclass-aware query returns zero rows, and zero reads as
    "nothing is missing". Refusing is the only honest option."""
    from core.questions import QuestionEntry

    entry = QuestionEntry(
        id="x", intent="x", question="?", engine="sparql", query="unverified",
        needs_hierarchy=True, pointer="/review",
    )
    monkeypatch.setattr("app.qna.engines._hierarchy_triples", lambda rdf: 0)

    shaped = answer(registry, entry, AnswerContext(graph=KnowledgeGraph()))

    assert shaped["state"] == STATE_SUBSTRATE_ABSENT
    assert "hierarchy" in shaped["detail"].lower()


def test_the_answer_records_which_graph_form_it_used(registry, req_extraction):
    """`include_superseded` is part of a query's meaning, so the answer says which
    form produced it rather than leaving the reader to assume."""
    graph = req_extraction
    shaped = answer(registry, registry.by_id("audit.unverified"), AnswerContext(graph=graph))

    assert any("graph form" in a for a in shaped["assumptions"])


# ---------------------------------------------------------------------------
# The CAN'T ANSWER log
# ---------------------------------------------------------------------------

def test_a_logged_miss_round_trips_with_the_context_to_replay_it(tmp_path):
    """The log is the candidate queue for the registry, so it has to carry enough to
    replay: which scope, which revision, what nearly matched."""
    path = default_log_path(tmp_path)
    event = UnansweredEvent(
        question="how many transactions per second?",
        scope_id="payments_v2", ref="working", matched_intent="",
        confidence=0.0,
        nearest_entries=({"id": "quality.undelivered", "question": "?", "overlap": 1},),
    )

    assert log_unanswered(path, event) == path

    events = read_unanswered(path)
    assert len(events) == 1
    assert events[0]["question"] == "how many transactions per second?"
    assert events[0]["scope_id"] == "payments_v2"
    assert events[0]["nearest_entries"][0]["id"] == "quality.undelivered"
    assert events[0]["at"], "a replay without a time cannot be correlated with a graph"


def test_the_log_is_append_only_and_survives_a_malformed_line(tmp_path):
    """One line per event, so a crash costs a line; and a line something else wrote is
    kept as a finding rather than dropped."""
    path = default_log_path(tmp_path)
    log_unanswered(path, UnansweredEvent(question="first"))
    log_unanswered(path, UnansweredEvent(question="second"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write("not json\n")

    events = read_unanswered(path)

    assert [e.get("question") for e in events[:2]] == ["first", "second"]
    assert events[-1] == {"malformed": "not json"}


def test_an_unmatched_question_is_a_state_and_not_a_low_confidence_answer(registry):
    """`no_named_question` is first-class, and says what the vocabulary covers."""
    result = unmatched("how many transactions per second?", nearest=(
        {"id": "quality.undelivered", "question": "?", "overlap": 1},
    ))

    assert result["state"] == STATE_NO_NAMED_QUESTION
    assert result["result"] is None
    assert result["nearest"]


# ---------------------------------------------------------------------------
# The pointers — every one has to resolve, route AND params
# ---------------------------------------------------------------------------

def _accepted_params(source: str, func_name: str) -> set:
    """The query parameters a route reads, or `{"*"}` when it hands the whole query
    string to a filter object (which is how `/review` takes `only=`)."""
    match = re.search(rf"def {func_name}\([^)]*\):(.*?)(?=\n    @app\.route|\Z)", source, re.S)
    body = match.group(1) if match else ""
    if "from_args(request.args)" in body:
        return {"*"}
    return set(re.findall(r'request\.args\.get\(\s*"([a-z_]+)"', body))


def test_every_pointer_resolves_to_a_route_that_accepts_its_params(registry, client):
    """Six broken relative links survived in this repo because the check validated
    anchors and never paths. A declarative catalogue of deep links is exactly where that
    recurs — and this is also what forces the `/quality` and `/gaps` gap to be faced
    rather than assumed, since neither page reads a single query parameter today."""
    source = (REPO_ROOT / "app" / "__init__.py").read_text()
    rules = {rule.rule for rule in client.application.url_map.iter_rules()}
    registered = {
        rule.rule: rule.endpoint for rule in client.application.url_map.iter_rules()
    }

    problems = []
    for entry in registry.entries:
        path, _, query = entry.pointer.partition("?")
        if path not in rules:
            problems.append(f"{entry.id}: no route for {path!r}")
            continue
        if not query:
            continue
        func_name = registered[path].rsplit(".", 1)[-1]
        accepted = _accepted_params(source, func_name)
        for pair in query.split("&"):
            key = pair.split("=")[0]
            if "*" not in accepted and key not in accepted:
                problems.append(
                    f"{entry.id}: {path} does not read {key!r} "
                    f"(accepted: {sorted(accepted) or 'none'})"
                )

    assert not problems, problems


def test_the_two_pages_that_accept_no_filter_are_known(registry, client):
    """Pins the measured state rather than the hope: `/quality` and `/gaps` take no
    query parameters, so any registry entry pointing at them is page-level only. When
    that changes, this test is the place that says so."""
    source = (REPO_ROOT / "app" / "__init__.py").read_text()

    assert _accepted_params(source, "quality") == set()
    assert _accepted_params(source, "gaps") == set()
    assert _accepted_params(source, "review") == {"*"}


# ---------------------------------------------------------------------------
# The page — the registry has to be reachable by a person, not only by a test
# ---------------------------------------------------------------------------

def test_the_ask_page_shows_what_can_be_asked(client):
    """A registry nobody can reach is a library. The page lists every declared question,
    including the ones that cannot be answered yet — those are the informative ones."""
    body = client.get("/ask").get_data(as_text=True)

    assert "What can be asked" in body
    assert "gaps.unrealized" not in body, "the page shows questions, not ids, to a reader"
    assert "Which requirements have no architectural answer?" in body
    assert "substrate_absent" in body, "the not-answerable-yet entries must be visible as such"
    assert "YB-047" in body


@pytest.mark.parametrize("question,state", [
    ("which requirements have no architectural answer?", STATE_ANSWERED),
    ("which principles does this design violate?", STATE_SUBSTRATE_ABSENT),
    ("what would changing the orchestrator affect?", STATE_OUT_OF_SCOPE),
    ("how many transactions per second?", STATE_NO_NAMED_QUESTION),
])
def test_asking_a_question_renders_its_state(client, question, state):
    """The four states have to reach the page, because three of them are the product:
    a reader must be able to tell "nothing matched" from "nothing to check against"."""
    body = client.post("/ask", data={"question": question}).get_data(as_text=True)

    assert f'<span class="tag">{state}</span>' in body


def test_an_answered_question_offers_the_surface_to_check_it_at(client):
    """The answer is a reading; the pointer is where a reviewer acts. An answer without
    one is a claim."""
    body = client.post(
        "/ask", data={"question": "which requirements have no architectural answer?"}
    ).get_data(as_text=True)

    assert "Check this at /gaps" in body
    assert "Caveats that travel with this answer" in body


def test_a_substrate_absent_answer_names_the_item_that_owns_it(client):
    """The principles case, on the page: the reader learns why, and who closes it."""
    body = client.post(
        "/ask", data={"question": "which principles does this design violate?"}
    ).get_data(as_text=True)

    assert "Nothing to answer from" in body
    assert "governance" in body.lower()
    assert "YB-047" in body


def test_a_miss_is_recorded_with_which_kind_of_silence_it_was(app):
    """The log is the candidate queue, so it has to distinguish demand for a registry
    entry from demand for the work that would put data in the graph. Recording only the
    question would make those indistinguishable in the replay."""
    client = app.test_client()

    client.post("/ask", data={"question": "how many transactions per second?"})
    client.post("/ask", data={"question": "which principles does this design violate?"})

    events = read_unanswered(default_log_path(app.config["STORE_ROOT"]))
    by_state = {e["state"] for e in events}
    assert by_state == {STATE_NO_NAMED_QUESTION, STATE_SUBSTRATE_ABSENT}
    assert all(e["scope_id"] for e in events), "a replay needs the scope it was asked against"
    miss = next(e for e in events if e["state"] == STATE_NO_NAMED_QUESTION)
    assert miss["nearest_entries"], "a miss must record what the vocabulary does cover"


def test_the_answer_names_the_revision_it_was_computed_against(client):
    """An answer that cannot name its graph is vacuous: the reader's graph may not be
    the one that produced it."""
    body = client.post(
        "/ask", data={"question": "which requirements have no architectural answer?"}
    ).get_data(as_text=True)

    assert "Computed under:" in body
    assert "revision" in body


def test_an_empty_question_asks_nothing_and_says_nothing(client):
    """A blank form is not a miss, so it must not be logged as one."""
    body = client.post("/ask", data={"question": ""}).get_data(as_text=True)

    assert "What can be asked" in body
    assert "Nothing matches that" not in body
