"""The TODO index and its entries.

`TODO.md` is no longer hand-written. It is generated from the front matter in
`docs/todos/entries/`, which is what makes status and priority fields instead of
sentences. The failure mode this suite guards against is drift: a hand edit to
the generated index, a duplicate identifier, a status outside the vocabulary, or
a cross-reference that points at nothing.

The v1 file had exactly those defects — two `item 19`s, an `item 0` appended
after `item 18`, and fourteen different spellings of status — so these are
regression tests for a real file, not hypothetical hygiene.
"""

from __future__ import annotations

import re
import subprocess
import sys

from tests.conftest import REPO_ROOT

TODO = REPO_ROOT / "TODO.md"
TODO_PY = REPO_ROOT / "scripts" / "todo.py"
ENTRY_DIR = REPO_ROOT / "docs/todos/entries"
ID_MAP = REPO_ROOT / "docs/todos/id-map.md"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(TODO_PY), *args],
                          capture_output=True, text=True, cwd=REPO_ROOT)


def test_check_passes():
    """The validator itself, on the real tree."""
    result = _run("check")
    assert result.returncode == 0, result.stdout + result.stderr


def test_index_is_in_sync_with_the_entries():
    """`render` must be idempotent; a stale index means someone edited it by hand."""
    before = TODO.read_text()
    assert _run("render").returncode == 0
    assert TODO.read_text() == before


def test_generated_files_link_only_to_files_that_exist():
    """Relative links must resolve from each file's own directory, not the root."""
    generated = [TODO, ID_MAP, REPO_ROOT / "docs/decisions/README.md"]
    checked = 0
    for path in generated:
        base = path.parent
        links = [l for l in re.findall(r"\]\(([^)]+)\)", path.read_text())
                 if not l.startswith(("http", "#"))]
        assert links, f"{path.name} should link to its items"
        missing = [l for l in links if not (base / l).exists()]
        assert not missing, f"dangling links in {path.name}: {missing}"
        checked += len(links)
    assert checked > 20


def test_index_stays_an_index():
    """The whole point is that the 2,500-line file became a scannable one."""
    assert len(TODO.read_text().splitlines()) < 150


def test_identifiers_are_unique_across_entries_and_records():
    ids = [re.search(r"^id: (\S+)", p.read_text(), re.M).group(1)
           for p in list(ENTRY_DIR.glob("*.md")) + list((REPO_ROOT / "docs/decisions").glob("ADR-*.md"))]
    duplicates = {i for i in ids if ids.count(i) > 1}
    assert not duplicates, f"duplicate ids: {duplicates}"


def test_every_legacy_item_number_still_resolves():
    """Seventeen items are referenced by number from code and docs.

    Renumbering would silently break those references, so the map is part of the
    contract — including both of v1's `item 19`s.
    """
    text = ID_MAP.read_text()
    for number in [*range(0, 19), 20, 21, 22, 23, 24, 25, 26]:
        assert re.search(rf"^\| {number} \|", text, re.M), f"legacy item {number} is unmapped"
    assert "YB-019a" in text and "YB-019b" in text


def test_entries_carry_the_fields_the_index_needs():
    for path in ENTRY_DIR.glob("*.md"):
        text = path.read_text()
        for field in ("id:", "title:", "status:", "priority:", "area:", "created:"):
            assert re.search(rf"^{field} ", text, re.M), f"{path.name} is missing {field}"
