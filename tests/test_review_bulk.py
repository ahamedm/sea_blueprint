"""
Bulk review actions: one action, many selected assertions (YB-… review gate).

The table header offers Verify, Dispute, Reopen and Remove over the same selection,
so the properties that matter are:

  1. **each action reaches the same place a single decision does** — a bulk dispute
     writes the decision a single dispute would, so the audit trail cannot tell them
     apart in a way that matters;
  2. **the selection is re-derived from the graph**, not trusted from the form: an id
     that has since disappeared is skipped rather than raising, and one that is
     already in the target state is not decided twice;
  3. **the count is honest** — "3 of 5" rather than a number the reviewer has to
     re-check by hand;
  4. **the threshold path stays verify-only.** "Remove everything below 0.6" is not a
     decision anyone should be able to make from a number.
"""

from __future__ import annotations

import pytest

from core.knowledge import BULK_ACTIONS, ReviewError, bulk_apply
from core.knowledge.model import (
    STATUS_DISPUTED,
    STATUS_UNVERIFIED,
    STATUS_VERIFIED,
)


def _graph(app):
    """Re-read the working set AFTER a request; a fixture would snapshot it first."""
    from core.knowledge import RevisionStore

    return RevisionStore(app.config["STORE_ROOT"]).ensure().load_working().graph


def _active_ids(graph, count: int) -> list[str]:
    return [a.id for a in graph.active()][:count]


def test_bulk_dispute_marks_every_selected_fact(seeded_client, app):
    ids = _active_ids(_graph(app), 3)

    response = seeded_client.post(
        "/review/bulk",
        data={"ids": ids, "action": "dispute", "actor": "tester"},
        follow_redirects=True,
    )

    assert b"Disputed 3 of 3" in response.data
    graph = _graph(app)
    assert all(graph.assertions[i].status == STATUS_DISPUTED for i in ids)


def test_bulk_remove_takes_the_facts_out_of_the_active_graph(seeded_client, app):
    ids = _active_ids(_graph(app), 3)

    response = seeded_client.post(
        "/review/bulk",
        data={"ids": ids, "action": "retire", "actor": "tester"},
        follow_redirects=True,
    )

    assert b"Removed 3 of 3" in response.data
    graph = _graph(app)
    assert all(not graph.assertions[i].is_active for i in ids)
    # Removed, not deleted: the audit trail still carries the decision.
    assert all(i in graph.assertions for i in ids)


def test_bulk_reopen_restores_removed_facts(seeded_client, app):
    """Reopen is the RESTORE path, so it has to work on an assertion that is already
    inactive — which is why `reset` cannot require `is_active`."""
    ids = _active_ids(_graph(app), 2)
    seeded_client.post("/review/bulk", data={"ids": ids, "action": "retire"})

    response = seeded_client.post(
        "/review/bulk", data={"ids": ids, "action": "reset"}, follow_redirects=True
    )

    assert b"Reopened 2 of 2" in response.data
    graph = _graph(app)
    assert all(graph.assertions[i].is_active for i in ids)
    assert all(graph.assertions[i].status == STATUS_UNVERIFIED for i in ids)


def test_the_count_says_how_many_the_action_actually_changed(seeded_client, app):
    """A selection and an action can disagree. Reporting the smaller number with no
    explanation teaches a reviewer to distrust the number."""
    graph = _graph(app)
    first, second = _active_ids(graph, 2)
    seeded_client.post("/review/bulk", data={"ids": [first], "action": "dispute"})

    response = seeded_client.post(
        "/review/bulk",
        data={"ids": [first, second], "action": "dispute"},
        follow_redirects=True,
    )

    assert b"Disputed 1 of 2" in response.data
    assert b"already in that state" in response.data


def test_an_id_that_no_longer_exists_is_skipped_not_fatal(seeded_client, app):
    real = _active_ids(_graph(app), 1)

    response = seeded_client.post(
        "/review/bulk",
        data={"ids": [*real, "assertion-that-never-existed"], "action": "dispute"},
        follow_redirects=True,
    )

    assert b"Disputed 1 of 2" in response.data


def test_an_unknown_bulk_action_changes_nothing(seeded_client, app):
    ids = _active_ids(_graph(app), 2)
    before = {i: _graph(app).assertions[i].status for i in ids}

    response = seeded_client.post(
        "/review/bulk", data={"ids": ids, "action": "obliterate"}, follow_redirects=True
    )

    assert b"Unknown bulk action" in response.data
    graph = _graph(app)
    assert {i: graph.assertions[i].status for i in ids} == before


def test_an_empty_selection_says_so(seeded_client):
    response = seeded_client.post(
        "/review/bulk", data={"action": "verify"}, follow_redirects=True
    )

    assert b"No assertions selected" in response.data


def test_the_threshold_path_still_verifies_by_confidence(seeded_client, app):
    """It carries only `bulk_scope`, no action — and that must keep meaning verify."""
    response = seeded_client.post(
        "/review/bulk",
        data={"bulk_scope": "threshold", "threshold": "1.0"},
        follow_redirects=True,
    )

    assert b"Verified" in response.data
    graph = _graph(app)
    assert any(a.status == STATUS_VERIFIED for a in graph.assertions.values())


def test_the_header_buttons_do_not_collide_with_the_threshold_button(seeded_client):
    """The header's buttons carry `action`; the threshold button carries
    `bulk_scope`. A hidden `bulk_scope=selected` would also be submitted by the
    threshold button and win, because it comes first in the form."""
    body = seeded_client.get("/review").get_data(as_text=True)

    assert 'name="action" value="verify"' in body
    assert 'name="bulk_scope" value="threshold"' in body
    assert 'name="bulk_scope" value="selected"' not in body


# ============================================================================
# The core rule
# ============================================================================


def test_bulk_apply_refuses_an_action_it_cannot_apply_to_many(req_extraction):
    from core.knowledge import ReviewLog

    with pytest.raises(ReviewError):
        bulk_apply(req_extraction, ReviewLog(), ["anything"], "correct")


def test_bulk_apply_does_not_decide_the_same_thing_twice(req_extraction):
    from core.knowledge import ReviewLog

    log = ReviewLog()
    ids = [a.id for a in req_extraction.active()][:2]

    assert len(bulk_apply(req_extraction, log, ids, "verify", actor="t")) == len(ids)
    # Already verified: a second pass records nothing rather than two more entries.
    assert bulk_apply(req_extraction, log, ids, "verify", actor="t") == []


def test_bulk_actions_are_the_reviewers_verbs():
    assert set(BULK_ACTIONS) == {"verify", "dispute", "retire", "reset"}
