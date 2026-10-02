"""Licensing and attribution metadata stays true to what the repository ships.

The license is a claim the repository makes about itself, and every way that
claim can drift is silent. The LICENSE file can be edited — filling in the
appendix is enough — and hash-identifying scanners then read it as a modified or
unknown license. A notice file can be renamed out of `license-files`, and
setuptools only *warns*: the wheel still builds, without the notices. A library
can be vendored with its copyright header stripped, and nothing in the tree
attributes it. An ontology schema can keep declaring the license the project
used to have.

None of that turns a test red anywhere else, which is why the invariants are
asserted here. Deliberately offline: the licence texts were verified against
their upstream tags by hand when they were written, and a test that fetched them
would be neither deterministic nor runnable without a network.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from tests.conftest import REPO_ROOT

# The canonical Apache License 2.0 text as published by the ASF. Pinned by hash
# because that is how licence scanners identify it: an edited file changes the
# digest and is then reported as modified, or as nothing at all.
APACHE_2_0_SHA256 = "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
PROJECT_LICENSE = "Apache-2.0"
PROJECT_LICENSE_NAME = "Apache License"

LICENSE_FILE = REPO_ROOT / "LICENSE"
NOTICE_FILE = REPO_ROOT / "NOTICE"
THIRD_PARTY_FILE = REPO_ROOT / "THIRD-PARTY-NOTICES"
PYPROJECT_FILE = REPO_ROOT / "pyproject.toml"
ONTOLOGY_DIR = REPO_ROOT / "ontology"
VENDORED_DIR = REPO_ROOT / "app" / "static" / "js"

# Sentences that appear only in a full permission notice, never in a bare
# copyright line. At least one must sit under every attribution, because MIT and
# BSD require the permission notice itself to travel with the distribution.
PERMISSION_MARKERS = (
    "Permission is hereby granted",       # MIT
    "Permission to use, copy, modify",    # ISC
    "Redistributions of source code",     # BSD
)

# Declarations, not prose: a document discussing another project's MIT licence is
# not a claim about this one.
_DECLARED_LICENSE = re.compile(
    r"^\s*license\s*[:=]\s*[\"']?(MIT|BSD|GPL|ISC|MPL|Proprietary)\b", re.IGNORECASE)
_LICENSE_CLASSIFIER = re.compile(r"License\s*::\s*OSI Approved", re.IGNORECASE)

_SCAN_SKIP_DIRS = frozenset({
    ".git", ".venv", ".uv-cache", ".pytest_cache", ".ruff_cache", ".codegraph",
    ".letta", ".deepeval", "__pycache__", "data", "build", "static",
})
# No `.py`: a declaration is data or prose, and Python files in this repo carry
# no per-file licence headers — only regexes and assertion messages that talk
# about licences, which is how the sweep first flagged itself.
_SCAN_SUFFIXES = frozenset({".yaml", ".yml", ".toml", ".md", ".txt",
                            ".cfg", ".ini", ".example"})


def _project_table() -> str:
    """The raw text of pyproject.toml's `[project]` table."""
    text = PYPROJECT_FILE.read_text()
    start = text.index("[project]")
    end = text.find("\n[", start + 1)
    return text[start:end if end != -1 else len(text)]


def _declared_license_value() -> str:
    """The right-hand side of `license = ...`, verbatim.

    Text rather than `tomllib`, which is 3.11+ and this project still declares
    3.10.
    """
    for line in _project_table().splitlines():
        if re.match(r"^license\s*=", line):
            return line.split("=", 1)[1].strip()
    raise AssertionError("pyproject.toml's [project] table declares no license")


def _declared_license_files() -> list:
    match = re.search(r"^license-files\s*=\s*\[(.*?)\]", _project_table(),
                      re.MULTILINE | re.DOTALL)
    if not match:
        raise AssertionError("pyproject.toml declares no license-files")
    return re.findall(r'"([^"]+)"', match.group(1))


def _notice_sections() -> dict:
    """Heading to body, split on the dash rules the notices file is built from."""
    parts = re.split(r"^-{20,}$", THIRD_PARTY_FILE.read_text(), flags=re.MULTILINE)
    return {parts[i].strip(): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def _component_sections() -> dict:
    """Only the per-library sections, not the preamble or the trailing note."""
    return {heading: body for heading, body in _notice_sections().items()
            if "— " in heading and "License" in heading}


def _vendored_assets() -> list:
    return sorted(VENDORED_DIR.glob("*.js"))


def _attribution_line(asset: Path) -> str:
    for line in THIRD_PARTY_FILE.read_text().splitlines():
        if asset.name in line:
            return line
    raise AssertionError(f"{asset.name} is vendored but named nowhere in "
                         f"THIRD-PARTY-NOTICES")


def _claimed_version(asset: Path) -> str:
    match = re.search(r"v(\d+\.\d+\.\d+)", _attribution_line(asset))
    assert match, f"THIRD-PARTY-NOTICES names {asset.name} with no version"
    return match.group(1)


def _scanned_files() -> list:
    return [path for path in REPO_ROOT.rglob("*")
            if path.is_file()
            and path.suffix in _SCAN_SUFFIXES
            and not _SCAN_SKIP_DIRS.intersection(path.relative_to(REPO_ROOT).parts)]


# ============================================================================
# The license file itself
# ============================================================================


def test_the_license_file_is_the_unmodified_apache_text():
    """Editing it — even to fill in the appendix — changes how scanners read it."""
    digest = hashlib.sha256(LICENSE_FILE.read_bytes()).hexdigest()

    assert digest == APACHE_2_0_SHA256, (
        "LICENSE no longer matches the canonical Apache 2.0 text. If the "
        "intention was to state a copyright holder, that belongs in NOTICE: "
        "hash-identifying scanners report an edited licence as modified or "
        "unknown."
    )


def test_the_project_states_its_license_as_an_spdx_expression():
    """The PEP 639 spelling, not the legacy `{text = ...}` table."""
    value = _declared_license_value()

    assert value == f'"{PROJECT_LICENSE}"', (
        f"expected the SPDX expression \"{PROJECT_LICENSE}\", found {value}"
    )
    assert not value.startswith("{"), (
        "the legacy `license = {text = ...}` table is deprecated in setuptools"
    )


def test_no_license_classifier_contradicts_the_expression():
    """An SPDX expression plus a licence classifier is a hard build error.

    Not a style preference: setuptools raises InvalidConfigError on the pair, so
    reintroducing the classifier breaks the build rather than the metadata.
    """
    assert not _LICENSE_CLASSIFIER.search(_project_table()), (
        "a `License :: OSI Approved ::` classifier alongside an SPDX expression "
        "is an InvalidConfigError on setuptools>=77"
    )


def test_every_declared_license_file_exists():
    """setuptools only warns here, so a rename would ship a wheel without them.

    Verified against setuptools 84: two of three declared files missing still
    exits 0, with deprecation warnings nobody reads and no License-File entries
    for the absent ones.
    """
    missing = [name for name in _declared_license_files()
               if not (REPO_ROOT / name).exists()]

    assert not missing, f"declared in license-files but not on disk: {missing}"


def test_the_license_file_is_declared_so_it_ships_in_the_wheel():
    """The one file that must never be dropped from the distribution."""
    assert "LICENSE" in _declared_license_files()


def test_the_notice_carries_the_project_copyright():
    """A NOTICE with no attribution notice is a file with a filename."""
    notice = NOTICE_FILE.read_text()

    assert "Copyright" in notice
    assert PROJECT_LICENSE_NAME in notice


def test_the_notice_does_not_carry_third_party_license_texts():
    """Our NOTICE must not become the place other licences are discharged.

    Apache 2.0 s4(d) obliges anyone redistributing a derivative to propagate the
    NOTICE, so third-party notices there would both burden downstream and read
    as though those components were Apache-licensed.
    """
    notice = NOTICE_FILE.read_text()
    leaked = [marker for marker in PERMISSION_MARKERS if marker in notice]

    assert not leaked, (
        f"NOTICE carries third-party permission notices ({leaked}); they belong "
        f"in THIRD-PARTY-NOTICES"
    )


# ============================================================================
# The vendored assets and their attribution
# ============================================================================


def test_every_vendored_asset_is_attributed():
    """A file dropped into app/static/js/ with no notice is a silent gap."""
    unattributed = []
    for asset in _vendored_assets():
        try:
            _attribution_line(asset)
        except AssertionError:
            unattributed.append(asset.name)

    assert not unattributed, (
        f"vendored but attributed nowhere: {unattributed}. Add the version, the "
        f"licence and its full text to THIRD-PARTY-NOTICES."
    )


def test_the_attributions_and_the_vendored_files_agree_in_number():
    """Catches the other direction: a notice for a library no longer bundled."""
    sections = _component_sections()

    assert len(sections) == len(_vendored_assets()), (
        f"{len(sections)} attributed components vs "
        f"{len(_vendored_assets())} files in app/static/js/"
    )


def test_each_attributed_version_is_the_one_actually_bundled():
    """A notice that outlives the upgrade it describes is worse than none.

    The version strings are readable in the bundles themselves, so the claim can
    be checked without trusting the notice.
    """
    stale = []
    for asset in _vendored_assets():
        claimed = _claimed_version(asset)
        if claimed not in asset.read_text(errors="replace"):
            stale.append(f"{asset.name} claims {claimed}")

    assert not stale, f"attribution names a version the bundle does not: {stale}"


def test_each_attribution_carries_the_full_permission_notice():
    """Copyright alone does not discharge MIT or BSD; the permission notice must
    travel too."""
    incomplete = []
    for heading, body in _component_sections().items():
        has_copyright = "Copyright" in body
        has_permission = any(marker in body for marker in PERMISSION_MARKERS)
        if not (has_copyright and has_permission):
            incomplete.append(heading)

    assert not incomplete, (
        f"attributed with a copyright line but no permission notice: {incomplete}"
    )


# ============================================================================
# The rest of the repository's own declarations
# ============================================================================


def test_every_linkml_schema_declares_the_project_license():
    """A schema added without one silently contradicts the top-level LICENSE."""
    undeclared, wrong = [], []
    for path in sorted(ONTOLOGY_DIR.rglob("*.yaml")):
        document = yaml.safe_load(path.read_text()) or {}
        if "prefixes" not in document:      # a catalogue, not a schema
            continue
        declared = document.get("license")
        if declared is None:
            undeclared.append(str(path.relative_to(REPO_ROOT)))
        elif declared != PROJECT_LICENSE:
            wrong.append(f"{path.relative_to(REPO_ROOT)}: {declared}")

    assert not undeclared, f"LinkML schemas declaring no license: {undeclared}"
    assert not wrong, f"schemas declaring a different license: {wrong}"


def test_no_project_owned_file_still_declares_a_different_license():
    """The sweep that the relicensing needed by hand, done mechanically."""
    offenders = []
    for path in _scanned_files():
        text = path.read_text(errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            if _DECLARED_LICENSE.search(line) or _LICENSE_CLASSIFIER.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{number}")

    assert not offenders, f"these still declare another license: {offenders}"
