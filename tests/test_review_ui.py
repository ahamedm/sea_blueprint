"""
The review queue's layout: filter and select from the table's own header, and the
assertion pane as a floating overlay.

Both changes are pinned here because both had a concrete complaint behind them and
both would regress silently:

  1. **Reaching the controls cost a scroll.** The queue is long and "filter, glance,
     select, verify" is the whole loop, so those three live in the *table's* header —
     the first row of the sticky `<thead>` — and stay pinned while the rows scroll
     under them. The row checkboxes reach that form by `form=`, which is what lets the
     form live up there and the rows far below.
  2. **The detail pane reserved a column whether or not it was open.** It sat in a
     380 px grid column beside the table, and the table has seven columns, so it
     scrolled horizontally on a normal screen. It floats now, and the table owns the
     full width.
"""

from __future__ import annotations

import re


def _table_head(body: str) -> str:
    """The table's header block — the thing the controls belong in."""
    head = re.search(r"<thead>(.*?)</thead>", body, re.S)
    assert head, "no table header rendered"
    return head.group(1)


def _page_head(body: str) -> str:
    """The site's top bar, which is a different thing and must stay out of this."""
    return body.split("</header>", 1)[0]


def _chip_labels(body: str) -> list[str]:
    return re.findall(r'class="chip[^"]*"[^>]*>\s*([^<]+?)\s*<span', body)


def _filter_labels(body: str) -> list[str]:
    select = re.search(r"Filter the review queue.*?</select>", body, re.S)
    assert select, "the table header has no filter select"
    return [
        re.sub(r"\s*\(\d+\)\s*$", "", label).strip()
        for label in re.findall(r"<option[^>]*>([^<]+)</option>", select.group(0))
    ]


def test_the_filter_and_the_selection_are_in_the_table_header(seeded_client):
    body = seeded_client.get("/review").get_data(as_text=True)
    head = _table_head(body)

    assert "Filter the review queue" in head
    assert 'id="select-all"' in head
    assert 'id="bulk-form"' in head
    assert "Verify selected" in head
    # …and NOT in the site's top bar: the nav is not the place for a table's tools.
    assert 'id="bulk-form"' not in _page_head(body)
    # …and they are the only copy of the bulk form: a second one would submit a
    # second, disagreeing selection.
    assert body.count('id="bulk-form"') == 1


def test_the_tools_are_the_first_sticky_row_of_the_header(seeded_client):
    """Order is the behaviour here: the toolbar is pinned at `top: 0` and the column
    labels tuck under it, so the tools stay visible while the rows scroll."""
    body = seeded_client.get("/review").get_data(as_text=True)
    head = _table_head(body)

    assert head.index('class="table-tools"') < head.index('class="col-heads"')
    # The toolbar spans the table, so the column row still lines up with the cells.
    assert 'colspan="7"' in head


def test_the_row_checkboxes_are_wired_to_the_table_header_form(seeded_client):
    body = seeded_client.get("/review").get_data(as_text=True)
    checks = re.findall(r'<input type="checkbox" class="row-check"[^>]*>', body)

    assert checks, "the queue rendered no selectable rows"
    for check in checks:
        assert 'name="ids"' in check
        assert 'form="bulk-form"' in check


def test_the_table_filter_offers_exactly_the_chip_presets(seeded_client):
    """One source of truth: the select and the chips render from the same list, so a
    preset cannot mean two things depending on which control you used."""
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
    # The table wraps the rows directly, with no column reserved beside it.
    assert 'id="review-rows"' in body


def test_the_pane_can_be_closed(seeded_client):
    """A floating pane needs its own way out, or it traps the page."""
    body = seeded_client.get("/review").get_data(as_text=True)
    assertion_id = re.search(r'name="ids" value="([^"]+)"', body).group(1)

    detail = seeded_client.get(f"/review/{assertion_id}").get_data(as_text=True)

    assert "data-close-drawer" in detail
    assert "Close" in detail


def test_an_empty_working_set_offers_no_table_controls(client):
    """Nothing in the graph means no table, so no toolbar either."""
    body = client.get("/review").get_data(as_text=True)

    assert "Nothing to review yet" in body
    assert "<thead>" not in body
    assert 'id="bulk-form"' not in body


def test_filters_that_match_nothing_keep_a_way_back(seeded_client):
    """An empty *result* is not an empty graph. The table goes away with the rows, so
    the chips and the filter card above it are what a reviewer navigates back with."""
    body = seeded_client.get("/review", query_string={"only": "retired"}).get_data(as_text=True)

    assert "No assertions match these filters" in body
    assert "<thead>" not in body
    assert _chip_labels(body), "the chips are the way back from an empty result"
