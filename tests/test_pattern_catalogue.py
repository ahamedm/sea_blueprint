"""
The architecture pattern catalogue.

WHAT THIS PINS. The Design Assistant chooses patterns by NAME from a library, and
the library's substance — mechanism, trade-offs, quality links — is resolved
deterministically rather than carried in a prompt. Three properties make that
worth having, and each is a test here:

1. **The catalogue cannot drift from the ontology.** A category, quality
   attribute or style the schema does not declare would otherwise reach a prompt
   as a value the model is told to use and the graph cannot hold.
2. **Resolution is strict.** "Bulkhead pattern" resolves; "Monolith" does not
   resolve to "Modular Monolith" just because one contains the other. A pattern
   the catalogue does not carry is reported unresolved, because renaming it to the
   closest published pattern destroys the information a reviewer needs.
3. **The prompt stays small.** Names and categories only — the module's whole
   point is that a mechanism never has to be shipped to the model.
"""

from __future__ import annotations

from core.patterns import (
    DEFAULT_CATALOGUE_PATH,
    PatternCatalogue,
    PatternEntry,
    canonical_pattern,
    load_pattern_catalogue,
    pattern_prompt_context,
    validate_pattern_catalogue,
)


def test_the_shipped_catalogue_loads_and_matches_the_ontology(ontology):
    """The catalogue in the repo is valid against the shipped schemas."""
    catalogue = load_pattern_catalogue()
    assert catalogue.entries, "the catalogue is empty"
    assert catalogue.findings == ()
    assert validate_pattern_catalogue(catalogue, ontology) == []


def test_every_entry_names_a_category_and_a_mechanism(ontology):
    catalogue = load_pattern_catalogue()
    for entry in catalogue.entries:
        assert entry.category, entry.name
        assert entry.mechanism, entry.name
        assert entry.quality_attributes, entry.name


def test_names_and_declared_aliases_resolve():
    catalogue = load_pattern_catalogue()
    for name in ("Circuit Breaker", "CIRCUIT_BREAKER", "circuit-breaker", "breaker"):
        resolution = canonical_pattern(name, catalogue)
        assert resolution.resolved, name
        assert resolution.entry.name == "Circuit Breaker", name


def test_noise_words_are_stripped_but_variants_are_not_guessed():
    """`Bulkhead pattern` is the same pattern; `Monolith` is not `Modular Monolith`."""
    catalogue = load_pattern_catalogue()

    stripped = canonical_pattern("Bulkhead pattern", catalogue)
    assert stripped.resolved and stripped.method == "normalised"
    assert stripped.entry.name == "Bulkhead"

    # The two confusion cases a containment rule would get wrong.
    assert canonical_pattern("Monolith", catalogue).resolved is False
    assert canonical_pattern("Modular Monolith", catalogue).resolved is False
    assert canonical_pattern("Saga orchestration", catalogue).resolved is False


def test_an_unknown_pattern_keeps_its_own_name():
    catalogue = load_pattern_catalogue()
    resolution = canonical_pattern("Quantum Flux Balancer", catalogue)
    assert resolution.resolved is False
    assert resolution.to_dict()["name"] == "Quantum Flux Balancer"
    assert resolution.to_dict()["mechanism"] == ""


def test_a_short_form_must_be_declared_to_resolve():
    """`outbox` is in the catalogue's aliases; a guess would not be."""
    catalogue = load_pattern_catalogue()
    assert canonical_pattern("outbox", catalogue).entry.name == "Transactional Outbox"
    # `caching` is not an alias of Cache-Aside, so it is not folded onto it.
    assert canonical_pattern("in-memory caching", catalogue).resolved is False


def test_an_entry_without_a_mechanism_is_reported(ontology):
    """A pattern whose mechanism is unknown cannot be checked against a scenario."""
    catalogue = PatternCatalogue(
        entries=(PatternEntry(name="Hand-Wavy Pattern", category="RESILIENCE"),)
    )
    findings = validate_pattern_catalogue(catalogue, ontology)
    assert any("no mechanism" in f for f in findings), findings


def test_an_unknown_category_or_quality_attribute_is_reported(ontology):
    catalogue = PatternCatalogue(
        entries=(
            PatternEntry(
                name="Invented",
                category="VIBES",
                mechanism="does something",
                quality_attributes=["GOODNESS"],
                styles=["CLOUD_NATIVE"],
            ),
        )
    )
    findings = validate_pattern_catalogue(catalogue, ontology)
    assert any("unknown category" in f for f in findings), findings
    assert any("unknown quality attribute" in f for f in findings), findings
    assert any("unknown architecture style" in f for f in findings), findings


def test_a_duplicate_name_is_reported_once(tmp_path):
    path = tmp_path / "dupes.yaml"
    path.write_text(
        "version: '1'\n"
        "patterns:\n"
        "  - name: Retry\n"
        "    category: RESILIENCE\n"
        "    mechanism: tries again\n"
        "  - name: retry\n"
        "    category: RESILIENCE\n"
        "    mechanism: tries again\n",
        encoding="utf-8",
    )
    catalogue = load_pattern_catalogue(path)
    assert len(catalogue.entries) == 1
    assert any("duplicate pattern name" in f for f in catalogue.findings)


def test_a_missing_catalogue_is_reported_not_raised(tmp_path):
    """An agent with no library should still be able to propose a design."""
    catalogue = load_pattern_catalogue(tmp_path / "nope.yaml")
    assert catalogue.entries == ()
    assert catalogue.findings
    assert canonical_pattern("Circuit Breaker", catalogue).resolved is False


def test_the_prompt_context_carries_names_and_categories_only():
    """The mechanism must never reach a prompt — that is the point of the module."""
    catalogue = load_pattern_catalogue()
    context = pattern_prompt_context(catalogue)
    assert "Circuit Breaker" in context
    assert "(RESILIENCE)" in context
    # Circuit Breaker's mechanism, verbatim, must not be in the prompt text.
    assert "fails fast once an error threshold" not in context
    # And the whole block stays small enough not to worsen YB-007.
    assert len(context) < 1500, len(context)


def test_the_default_path_is_the_shipped_catalogue():
    assert DEFAULT_CATALOGUE_PATH.endswith("ontology/catalogues/architecture_patterns.yaml")
    assert canonical_pattern("Saga", load_pattern_catalogue()).resolved
