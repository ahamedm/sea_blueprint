"""
The review queue's layout: filter and select from the sticky bar, and the assertion
pane as a floating overlay.

Both changes are pinned here because both had a concrete complaint behind them and
both would regress silently:

  1. **Reaching the controls cost a scroll.** The queue is long, and "filter, glance,
     select, verify" is the whole loop — so the filter presets, the select-all and the
     bulk verify live in the sticky bar, and the row checkboxes reach their form from
     far below it.
  2. **The detail pane reserved a column whether or not it was open.** It sat in a
     380 px grid column beside the table, and the table has seven columns, so it
     scrolled horizontally on a normal screen. It floats now, and the table owns the
     full width.
"""

from __future__ import annotations

import re


def _header(body: str) -> str:
    return body.split("</header>", 1)[0]


def _chip_labels(body: str) -> list[str]:
    return re.findall(r'class="chip[^"]*"[^>]*>\s*([^<]+?)\s*<span', body)


def _filter_labels(body: str) -> list[str]:
    select = re.search(r"Filter the review queue.*?</select>", body, re.S)
    assert select, "the header has no filter select"
    return [
        re.sub(r"\s*\(\d+\)\s*$", "", label).strip()
        for label in re.findall(r"<option[^>]*>([^<]+)</option>", select.group(0))
    ]


def test_the_filter_and_the_selection_live_in_the_sticky_bar(seeded_client):
    body = seeded_client.get("/review").get_data(as_text=True)
    header = _header(body)

    # The filter, the select-all and the bulk action are all in the header…
    assert "Filter the review queue" in header
    assert 'id="select-all"' in header
    assert 'id="bulk-form"' in header
    assert "Verify selected" in header
    # …and they are the *only* copy of the bulk from: a second form would submit a
    # second, disagreeing selection.
    assert body.count('id="bulk-form"') == 1


def test_the_row_checkboxes_are_wired_to_the_header_form(seeded_client):
    """The form is in the header and the rows are far below it — `form=` is what
    connects them, so it has to be on every checkbox or the selection posts nothing."""
    body = seeded_client.get("/review").get_data(as_text=True)
    checks = re.findall(r'<input type="checkbox" class="row-check"[^>]*>', body)

    assert checks, "the queue rendered no selectable rows"
    for check in checks:
        assert 'name="ids"' in check
        assert 'form="bulk-form"' in check


def test_the_header_filter_offers_exactly_the_chip_presets(seeded_client):
    """One source of truth: the select and the chips are rendered from the same list,
    so a preset can never mean two things depending on which control you used."""
    body = seeded_client.get("/review").get_data(as_text=True)

    chips = _chip_labels(body)
    options = _filter_labels(body)

    assert chips, "no chips rendered"
    assert chips == options, f"chips {chips} != filter options {options}"


def test_the_detail_pane_floats_and_reserves_no_column(seeded_client):
    body = seeded_client.get("/review").get_data(as_text=True)

    # The old two-column grid is what made the table scroll; its absence is the fix.
    assert "layout-split" not in body
    # The pane and its dismissal exist, and the pane starts closed.
    assert 'class="drawer"' in body
    assert 'id="drawer-backdrop"' in body
    assert 'aria-hidden="true"' in body
    # The table wraps the rows directly, with no column reserved beside it.
    assert 'id="review-rows"' in body


def test_the_pane_can_be_closed(seeded_client):
    """A floating pane needs its own way out, or it traps the page."""
    body = seeded_client.get("/review").get_data(as_text=True)
    assertion_id = re.search(r'name="ids" value="([^"]+)"', body).group(1)

    detail = seeded_client.get(f"/review/{assertion_id}").get_data(as_text=True)

    assert "data-close-drawer" in detail
    assert "Close" in detail


def test_an_empty_working_set_offers_no_selection_controls(client):
    """Nothing in the graph means nothing to select, so the header stays bare."""
    body = client.get("/review").get_data(as_text=True)

    assert "Nothing to review yet" in body
    assert 'id="bulk-form"' not in body
    assert 'id="select-all"' not in body


def test_filters_that_match_nothing_keep_the_filter_reachable(seeded_client):
    """The distinction matters: an empty *result* is not an empty graph. The filter
    has to stay in the header, because it is how a reviewer gets back."""
    body = seeded_client.get("/review", query_string={"only": "retired"}).get_data(as_text=True)

    assert "No assertions match these filters" in body
    assert "Filter the review queue" in _header(body)
