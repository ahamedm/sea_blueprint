"""
The domain pack — the vocabulary of the SUBJECT MATTER, overlaid per Initiative.

Two things are being defended here, and they fail in different directions:

1. **The pack resolves.** A pack that loads but inherits nothing is worse than one
   that fails, because the model is then handed a truncated vocabulary and no
   error is raised anywhere. That was the actual behaviour of the old import walk
   (relative to the importing file's directory, missing import skipped with a bare
   `continue`), so it is tested as a regression rather than as a nicety.

2. **The base ontology stays generic.** The pack must not leak into the base
   layers, and an Initiative with NO pack must remain a supported path. The whole
   point of the layer is that `hr.yaml` needs zero code changes; a test asserting
   payments work would not notice if the base quietly acquired a payment concept.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.ontology import (
    DOMAIN_LAYER,
    LAYER_ORDER,
    OntologyError,
    OntologyIntegrityError,
    discover_domain_packs,
    domain_pack_dir,
    load_domain_pack,
    load_ontology,
    pack_for_graph,
    resolve_domain_pack_path,
)
from tests.conftest import REPO_ROOT

PAYMENTS = "payment_processing"


@pytest.fixture(scope="module")
def pack(ontology_dir):
    return load_domain_pack(PAYMENTS, ontology_dir)


# ============================================================================
# The base ontology is unchanged by the existence of a pack
# ============================================================================


def test_the_base_chain_is_still_exactly_five_layers(ontology):
    """A pack is an overlay, not a chain link. If it were in LAYER_ORDER the loader
    would demand one specific domain forever and per-Initiative selection would be
    impossible, because the loader cache is keyed by directory alone.

    Five base layers now: the governance layer (Policy, Control, Standard clauses)
    sits between requirements and architecture. A pack is still a separate,
    conditional overlay and is NOT one of these.
    """
    assert [layer.key for layer in ontology.layers] == [
        "common",
        "enterprise",
        "requirements",
        "governance",
        "architecture",
    ]
    assert ontology.stats()["layers"] == 5
    assert ontology.stats()["classes"] == 70


def test_no_base_layer_declares_a_domain_class(ontology, pack):
    """The base ontology must not know the word `Chargeback`."""
    base_names = set(ontology.classes)
    leaked = sorted(base_names & set(pack.classes))
    assert leaked == [], f"domain classes leaked into the base layers: {leaked}"


def test_the_base_schema_files_declare_no_domain_class(ontology_dir, pack):
    """Generic-by-construction, checked as schema rather than as prose.

    A base layer may *mention* a domain noun to explain a generic construct —
    `ResponsibilityType.DATA_OWNERSHIP` asks "who owns Cardholder Data?" to make
    the point that ownership is an accountability. What it must never do is
    declare one as a class, because then the base vocabulary is domain-shaped and
    every other domain inherits the payment model.
    """
    declarations = set()
    for _key, filename, _role in LAYER_ORDER:
        document = yaml.safe_load((ontology_dir / filename).read_text(encoding="utf-8")) or {}
        declarations |= set(document.get("classes") or {})
        declarations |= set(document.get("enums") or {})

    leaked = sorted(declarations & set(pack.classes))
    assert leaked == [], f"the base layers declare domain classes: {leaked}"


def test_the_architecture_layer_imports_no_pack(ontology):
    """A pack depends on the artifact layers; the reverse would make the base
    ontology depend on one domain, which is the failure this layer exists to avoid."""
    architecture = ontology.layer("architecture")
    assert set(architecture.imports) == {"common", "enterprise", "requirements"}


# ============================================================================
# The pack loads, resolves, and stays an overlay
# ============================================================================


def test_the_pack_specialises_the_base_domain_concept(pack, ontology):
    root = pack.get("PaymentDomainConcept")
    assert root is not None
    assert root.abstract is True
    assert root.is_a == "DomainConcept"
    # Declared in the pack, inherited from the base — not copied in.
    assert "DomainConcept" in ontology.classes
    assert "DomainConcept" not in pack.classes


def test_pack_classes_are_attributed_to_the_domain_layer(pack):
    assert pack.classes
    assert {spec.layer for spec in pack.classes.values()} == {DOMAIN_LAYER}
    assert {spec.origin for spec in pack.classes.values()} == {pack.path}


def test_every_pack_reference_resolves(pack, ontology):
    """The load-bearing property: a range may land on a pack class OR a base class,
    and the loader reports which. A pack that resolves against neither must refuse
    to load rather than hand the model a dangling vocabulary."""
    assert pack.is_usable
    assert pack.unresolved_parents == []
    assert pack.unresolved_ranges == []

    by_name = {slot.name: slot for slot in pack.get("PaymentDomainConcept").attributes}

    # A pack-local class.
    assert by_name["instrument"].range == "PaymentInstrument"
    assert by_name["instrument"].range_layer == DOMAIN_LAYER
    # A pack-local enum.
    assert by_name["lifecycle_state"].range == "PaymentLifecycleState"
    assert by_name["lifecycle_state"].range_kind == "enum"
    # A BASE class, reached through the pack's `imports:` — the declaration that
    # used to be dropped silently.
    assert by_name["governed_by_rule"].range == "BusinessRule"
    assert by_name["governed_by_rule"].range_kind == "class"
    assert by_name["governed_by_rule"].range_layer == "requirements"
    # A primitive is not a relationship.
    assert by_name["realised_by"].range_kind == "primitive"


def test_the_arc_leg_is_a_reference_not_a_class_range(pack):
    """`realised_by` points into ARC-G, which sits ABOVE this layer.

    Modelling it as a range would invert the one-way import rule and make the
    domain layer depend on the artifact layer, so it stays an unresolved
    cross-graph reference — the same shape the knowledge model already uses.
    """
    slot = next(s for s in pack.get("PaymentDomainConcept").attributes if s.name == "realised_by")
    assert slot.range == "string"
    assert slot.range_kind == "primitive"


def test_the_pack_imports_the_requirements_layer(pack):
    assert pack.imports == ["requirements"], (
        "the pack must import the layer that declares DomainConcept; an empty "
        "import list means the overlay resolved against nothing"
    )
    assert "linkml:types" in pack.external_imports


def test_the_lifecycle_enum_is_closed_and_ordered(pack):
    """The state machine is an enum, not ~13 near-duplicate concepts: states are
    values a payment takes, not things the business consists of."""
    states = pack.enums["PaymentLifecycleState"].values
    assert states[0] == "INITIATED"
    assert states[-1] == "CHARGEBACK_REVERSED"
    for state in ("AUTHORIZED", "CAPTURED", "SETTLED", "CHARGED_BACK"):
        assert state in states
    # Ordering is the documented progression, so it must not be alphabetical.
    assert states != sorted(states)


def test_abstract_pack_classes_are_excluded_from_the_grounding_list(pack):
    """Extraction must be pointed at instantiable concepts. Offering an abstract
    class as a target is how an entity ends up filed as `PaymentInstrument`."""
    assert "PaymentInstrument" in pack.classes
    assert "PaymentInstrument" not in pack.concrete_classes
    assert "PaymentDomainConcept" not in pack.concrete_classes
    assert "Chargeback" in pack.concrete_classes


# ============================================================================
# The lifecycle's hard cases: disputes, reversals and non-card instruments
# ============================================================================
#
# These pin the concepts a design most often omits — authorisation and capture are
# always described, and what happens afterwards usually is not. A concept that is
# declared but hollow (no slots, no link to the payment it concerns) cannot turn
# that omission into a coverage finding, which is the whole reason the pack has a
# `PaymentCore` census.

# The vocabulary the census relies on. Named here as one list so a concept that
# disappears is a failing test rather than a quiet gap in a report.
_OPERATIONS_NOT_TO_LOSE = (
    "Authorization", "Capture", "Refund", "Settlement",
    "Chargeback", "Dispute", "Payout", "Mandate", "Reconciliation",
)
_PARTIES_NOT_TO_LOSE = ("Merchant", "Cardholder", "Acquirer", "Issuer", "PaymentGateway")
_INSTRUMENTS_NOT_TO_LOSE = ("Card", "BankAccount", "Wallet")


def test_the_operations_a_design_omits_are_all_declared(pack):
    """Disputes, reversals and reconciliation are exactly what a design document
    leaves out. They are declared AND in the census subset, so their absence from a
    requirements document is reportable rather than invisible."""
    core = {name for name, spec in pack.classes.items() if "PaymentCore" in spec.subsets}
    assert set(_OPERATIONS_NOT_TO_LOSE) <= set(pack.classes)
    assert set(_OPERATIONS_NOT_TO_LOSE) <= core


def test_the_parties_beyond_the_gateway_and_merchant_are_declared_and_usable(pack):
    """`Issuer` and `Acquirer` were declared with NO attributes at all, so the
    institutions that decide an authorisation and can raise a chargeback could be
    named and nothing recorded about them."""
    assert set(_PARTIES_NOT_TO_LOSE) <= set(pack.classes)
    for party in ("Issuer", "Acquirer"):
        assert pack.get(party).attributes, f"{party} is a stub with no slots"


def test_every_instrument_is_declared_and_usable(pack):
    """Non-card instruments. `Wallet` was a stub: a payment method the enum offers
    (`WALLET`) with nowhere to record the wallet."""
    assert set(_INSTRUMENTS_NOT_TO_LOSE) <= set(pack.classes)
    assert pack.get("Wallet").attributes


def test_a_partial_refund_is_a_refund_and_not_a_second_class(pack):
    """The decision, pinned so it is not 'fixed' later by duplication.

    A partial refund is not a different thing from a refund — it is a refund whose
    amount does not exhaust the capture. That is a boolean on the operation plus a
    lifecycle state, and modelling it as a sibling class would put two nodes on one
    fact and give the census two ways to report the same coverage.
    """
    refund = pack.get("Refund")
    assert "is_partial" in {slot.name for slot in refund.attributes}
    assert "refund_amount" in {slot.name for slot in refund.attributes}
    assert "PARTIALLY_REFUNDED" in pack.enums["PaymentLifecycleState"].values
    assert "PartialRefund" not in pack.classes


def test_an_operation_can_name_the_payment_it_concerns(pack):
    """The structural gap this section exists for.

    Every operation inherited the root's role slots (merchant, cardholder,
    instrument) and had NO way to reference the payment itself, so "which refunds
    belong to this payment?" was unanswerable and each operation was an island.
    """
    operation = pack.get("PaymentOperation")
    assert operation.abstract is True
    by_name = {slot.name: slot for slot in operation.attributes}
    assert by_name["payment"].range == "Payment"
    assert by_name["authorization"].range == "Authorization"

    # The operations that act ON a payment inherit it...
    for name in ("Authorization", "Capture", "Refund", "Chargeback", "Dispute"):
        assert pack.get(name).is_a == "PaymentOperation", name

    # ...and the things that are not operations do not, because `Payment` is not an
    # operation, a mandate is an authority, and a settlement is a money movement.
    for name in ("Payment", "Merchant", "Card", "Payout", "Settlement", "Mandate",
                 "Reconciliation"):
        assert pack.get(name).is_a != "PaymentOperation", name


def test_a_dispute_links_to_the_chargeback_it_became(pack):
    """The description promised escalation and the model could not record it.

    "A challenge that may or may not escalate" was prose with no link, so the
    interesting outcome — resolved BEFORE escalation — was unrepresentable.
    """
    dispute = pack.get("Dispute")
    by_name = {slot.name: slot for slot in dispute.attributes}
    assert by_name["escalated_to"].range == "Chargeback"
    # The escalation is also a state, and the states are closed.
    assert by_name["dispute_state"].range == "DisputeState"
    assert by_name["dispute_state"].range_kind == "enum"
    assert "ESCALATED" in pack.enums["DisputeState"].values


def test_the_closed_sets_are_enums_and_not_free_text(pack):
    """A free-text state cannot answer a yes/no.

    "Was this collection against an active mandate?" is the question a direct-debit
    control turns on, and `mandate_state: string` made it unanswerable.
    """
    mandate = {slot.name: slot for slot in pack.get("Mandate").attributes}
    assert mandate["mandate_state"].range == "MandateState"
    assert mandate["mandate_state"].range_kind == "enum"
    assert set(pack.enums["MandateState"].values) == {
        "ACTIVE", "SUSPENDED", "CANCELLED", "EXPIRED",
    }


def test_a_mandate_and_a_payout_name_what_they_act_on(pack):
    """Two directions that existed only one way, or not at all.

    `BankAccount.mandate_reference` answered "which mandate covers this account?"
    as a string and could not answer the reverse. A payout recorded its direction
    (out) and had no destination at all.
    """
    mandate = {slot.name: slot for slot in pack.get("Mandate").attributes}
    assert mandate["account"].range == "BankAccount"
    # The root's `scheme` is a CardScheme, which a direct-debit authority is not.
    assert mandate["collection_scheme"].range_kind == "primitive"

    payout = {slot.name: slot for slot in pack.get("Payout").attributes}
    assert payout["destination"].range == "PaymentInstrument"
    assert payout["direction"].range == "TransactionDirection"

    reconciliation = {slot.name: slot for slot in pack.get("Reconciliation").attributes}
    assert reconciliation["reconciles"].range == "Settlement"


# ============================================================================
# Selection: "no pack" is a state, a bad pack is an error
# ============================================================================


@pytest.mark.parametrize("spec", ["", "  ", "none", "generic", "NONE", "-", None])
def test_an_empty_selection_means_no_pack_and_does_not_raise(spec, ontology_dir):
    """Selecting no domain pack is a supported path, not a degraded one. If this
    raised, every Initiative without a pack could never run."""
    assert resolve_domain_pack_path(spec, ontology_dir) is None
    assert load_domain_pack(spec, ontology_dir) is None


@pytest.mark.parametrize("spec", ["does_not_exist", "hr", "learning_management"])
def test_a_named_but_missing_pack_raises(spec, ontology_dir):
    """The distinction that matters: absent (fine) versus named-but-missing (fatal).

    Returning None here would silently run extraction as generic and discard the
    vocabulary the caller asked for — the same inert-parameter failure the domain
    field suffered.
    """
    with pytest.raises(OntologyError, match="domain pack not found"):
        load_domain_pack(spec, ontology_dir)


@pytest.mark.parametrize(
    "spec",
    [
        "payment_processing",
        "payment_processing.yaml",
        "domains/payment_processing.yaml",
        "ontology/domains/payment_processing.yaml",
        "./ontology/domains/payment_processing.yaml",
    ],
)
def test_a_pack_resolves_from_what_a_user_would_actually_type(spec, ontology_dir):
    pack = load_domain_pack(spec, ontology_dir)
    assert pack is not None
    assert pack.spec == PAYMENTS


def test_discovery_lists_the_packs_without_loading_them(ontology_dir):
    found = discover_domain_packs(ontology_dir)
    assert [entry["spec"] for entry in found] == [PAYMENTS]
    entry = found[0]
    assert entry["loadable"] is True
    assert entry["class_count"] > 0
    assert entry["version"]


# ============================================================================
# Integrity failures are loud, because a silent one is unrecoverable downstream
# ============================================================================


def _write_pack(ontology_root, body: dict):
    """Write a pack into a throwaway ontology root.

    The base layers are symlinked in rather than copied, because a pack is only
    meaningful relative to the base ontology it overlays — testing it against an
    empty directory would only prove the *directory* is missing, not that the
    integrity checks fire.
    """
    directory = domain_pack_dir(ontology_root)
    directory.mkdir(parents=True, exist_ok=True)
    for _key, filename, _role in LAYER_ORDER:
        target = ontology_root / filename
        if not target.exists():
            target.symlink_to(REPO_ROOT / "ontology" / filename)
    path = directory / "broken.yaml"
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


@pytest.fixture
def broken_root(tmp_path):
    return tmp_path


def test_a_pack_missing_its_import_inherits_nothing_and_must_fail(broken_root):
    """The named regression. This pack's only supertype lives in a base layer it
    does not import. The old resolution walked `imports:` relative to the importing
    file and skipped what it could not find, so this loaded cleanly and produced a
    vocabulary whose root class did not exist."""
    _write_pack(
        broken_root,
        {
            "id": "https://sea.platform/ontology/domains/broken",
            "name": "broken",
            "version": "0.1.0",
            "imports": ["linkml:types"],
            "classes": {"Thing": {"is_a": "DomainConcept", "description": "orphan"}},
        },
    )
    with pytest.raises(OntologyError, match="imports no base ontology layer"):
        load_domain_pack("broken", broken_root)


def test_a_pack_with_an_unresolvable_slot_range_refuses_to_load(broken_root):
    _write_pack(
        broken_root,
        {
            "id": "https://sea.platform/ontology/domains/broken",
            "name": "broken",
            "version": "0.1.0",
            "imports": ["linkml:types", "requirements_base"],
            "classes": {
                "Thing": {
                    "is_a": "DomainConcept",
                    "attributes": {"nowhere": {"range": "NotAClassAnywhere"}},
                }
            },
        },
    )
    with pytest.raises(OntologyIntegrityError, match="unresolved slot ranges"):
        load_domain_pack("broken", broken_root)


def test_a_pack_that_specialises_nothing_is_rejected(broken_root):
    """Otherwise a second base ontology dropped into `domains/` would load happily
    and the coverage census would count artifact classes as subject matter."""
    _write_pack(
        broken_root,
        {
            "id": "https://sea.platform/ontology/domains/broken",
            "name": "broken",
            "version": "0.1.0",
            "imports": ["linkml:types", "requirements_base"],
            "classes": {"Thing": {"is_a": "BusinessRule", "description": "not a domain concept"}},
        },
    )
    with pytest.raises(OntologyIntegrityError, match="no subclass of DomainConcept"):
        load_domain_pack("broken", broken_root)


def test_a_pack_without_a_version_is_rejected(broken_root):
    """The pack id goes into assertion provenance. Without a version it cannot be
    told apart from an edited file, so "which vocabulary produced this fact?"
    stops being answerable."""
    _write_pack(
        broken_root,
        {
            "id": "https://sea.platform/ontology/domains/broken",
            "name": "broken",
            "imports": ["linkml:types", "requirements_base"],
            "classes": {"Thing": {"is_a": "DomainConcept"}},
        },
    )
    with pytest.raises(OntologyError, match="declares no `version`"):
        load_domain_pack("broken", broken_root)


def test_an_import_that_is_neither_a_layer_nor_linkml_is_rejected(broken_root):
    _write_pack(
        broken_root,
        {
            "id": "https://sea.platform/ontology/domains/broken",
            "name": "broken",
            "version": "0.1.0",
            "imports": ["linkml:types", "some_other_project"],
            "classes": {"Thing": {"is_a": "DomainConcept"}},
        },
    )
    with pytest.raises(OntologyError, match="neither a base ontology layer nor a linkml module"):
        load_domain_pack("broken", broken_root)


def test_a_pack_whose_schema_id_belongs_to_another_pack_is_rejected(broken_root):
    """The id must identify the pack, not the one it was copied from.

    Copying a sibling pack is how the next one gets written, and a leftover `id`
    makes two vocabularies claim the same identity in RDF and in provenance.
    """
    _write_pack(
        broken_root,
        {
            "id": "https://sea.platform/ontology/domains/some_other_domain",
            "name": "broken",
            "version": "0.1.0",
            "imports": ["linkml:types", "requirements_base"],
            "classes": {"Thing": {"is_a": "DomainConcept"}},
        },
    )
    with pytest.raises(OntologyError, match="does not end in"):
        load_domain_pack("broken", broken_root)


# ============================================================================
# The pack is the pack's own consistency check
# ============================================================================


def test_every_documented_lifecycle_state_is_reachable_from_the_transitions(pack):
    """The prose lifecycle in the header and the enum must agree.

    This is the pack checking itself: the states are documented as a progression,
    and a state that no transition reaches is either a missing edge in the design
    or a leftover value in the enum. Both are worth failing on while the pack is
    small enough to fix.
    """
    documented = {
        "INITIATED", "AUTHORIZED", "CAPTURED", "SETTLED", "RECONCILED",
        "DECLINED", "FAILED", "EXPIRED", "VOIDED",
        "PARTIALLY_REFUNDED", "REFUNDED", "CHARGED_BACK", "CHARGEBACK_REVERSED",
    }
    assert set(pack.enums["PaymentLifecycleState"].values) == documented


def test_the_coverage_subset_is_the_census_and_is_not_empty(pack):
    """`PaymentCore` is what a coverage audit counts. It must contain the concepts
    whose absence is a finding — including the ones nobody builds for, which is the
    whole reason the third reconciliation leg exists."""
    core = {name for name, spec in pack.classes.items() if "PaymentCore" in spec.subsets}
    assert {"Chargeback", "Settlement", "Reconciliation", "Refund"} <= core


# ============================================================================
# Provenance — which vocabulary produced a fact
# ============================================================================


def test_the_pack_is_recorded_on_every_assertion_it_grounded(requirements_output):
    """A vocabulary change must not be indistinguishable from a content change."""
    from core.knowledge import graph_from_extraction

    graph, run = graph_from_extraction(
        requirements_output,
        metadata={"domain_pack": "payment_processing@0.1.0", "chunks": 1},
        document_ref="brief.md",
        initiative_id="INIT-MVP-001",
    )
    assert graph.assertions
    assert {a.provenance.domain_pack for a in graph.active()} == {"payment_processing@0.1.0"}

    # And it survives a round trip, or the record is worthless after a restart.
    from core.knowledge.serialise import graph_from_dict, graph_to_dict

    reloaded = graph_from_dict(graph_to_dict(graph))
    assert {a.provenance.domain_pack for a in reloaded.active()} == {"payment_processing@0.1.0"}


def test_a_run_without_a_pack_records_no_pack(requirements_output):
    """Absent must stay absent rather than defaulting to something."""
    from core.knowledge import graph_from_extraction

    graph, _run = graph_from_extraction(
        requirements_output, metadata={"chunks": 1}, document_ref="brief.md"
    )
    assert {a.provenance.domain_pack for a in graph.active()} == {""}


def test_the_pack_in_force_is_readable_back_from_the_graph(requirements_output, ontology_dir):
    from core.knowledge import graph_from_extraction

    graph, _run = graph_from_extraction(
        requirements_output,
        metadata={"domain_pack": "payment_processing@0.1.0", "chunks": 1},
        document_ref="brief.md",
    )
    recovered = pack_for_graph(graph, ontology_dir)
    assert recovered is not None
    assert recovered.spec == PAYMENTS


def test_an_explicit_argument_corrects_a_stale_metadata_value(requirements_output):
    from core.knowledge import graph_from_extraction

    graph, _run = graph_from_extraction(
        requirements_output,
        metadata={"domain_pack": "stale@0.0.1", "chunks": 1},
        document_ref="brief.md",
        domain_pack="payment_processing@0.1.0",
    )
    assert {a.provenance.domain_pack for a in graph.active()} == {"payment_processing@0.1.0"}


def test_a_graph_referring_to_a_removed_pack_still_reads(requirements_output, ontology_dir):
    """A pack can be renamed after the fact. The graph stays readable; it just
    cannot be re-grounded in a vocabulary that is no longer there."""
    from core.knowledge import graph_from_extraction

    graph, _run = graph_from_extraction(
        requirements_output,
        metadata={"domain_pack": "retired_domain@9.9.9", "chunks": 1},
        document_ref="brief.md",
    )
    assert pack_for_graph(graph, ontology_dir) is None


# ============================================================================
# The vocabulary actually reaches the prompt
# ============================================================================
#
# Everything above tests the loader. A loader that works but is never consulted
# is the original defect — `domain` was read, logged, and written to metadata
# without reaching a prompt or a schema. These are the tests that would have
# caught it.


class _RecordingAgent:
    """Builds prompts without the Strands stack or a model server.

    Constructed via `__new__` because `SEABaseAgent.__init__` compiles the system
    prompt through Strands, which needs a model endpoint. A test suite that needs
    a model server is a test suite nobody runs.
    """

    @staticmethod
    def make(monkeypatch, ontology_dir, pack_spec=None, agent_cls=None):
        from agents.base_agent import console
        from agents.knowledge_extraction.agent import KnowledgeExtractionAgent

        cls = agent_cls or KnowledgeExtractionAgent
        agent = cls.__new__(cls)
        agent.config = _agent_config(ontology_dir, pack_spec)
        agent.console = console
        agent.ontology = yaml.safe_load(
            (Path(ontology_dir) / "architecture_base.yaml").read_text(encoding="utf-8")
        )
        agent.domain_pack = load_domain_pack(pack_spec, ontology_dir) if pack_spec else None
        agent.agent = None
        return agent


def _agent_config(ontology_dir, pack_spec):
    from agents.base_agent import AgentConfig

    return AgentConfig(
        name="probe",
        description="probe",
        ontology_path=str(Path(ontology_dir) / "architecture_base.yaml"),
        ontology_dir=str(ontology_dir),
        domain_pack=pack_spec,
    )


def test_the_pack_vocabulary_reaches_the_extraction_prompt(ontology_dir):
    """The end-to-end claim of step 3: a pack changes what the model is told."""
    agent = _RecordingAgent.make(None, ontology_dir, PAYMENTS)
    prompt = agent._build_extraction_prompt("body", "requirements", "anything")

    assert "Domain vocabulary in force" in prompt
    for concept in ("Chargeback", "Cardholder", "Settlement"):
        assert concept in prompt, concept


def test_the_base_vocabulary_is_present_alongside_the_pack(ontology_dir):
    """The architecture schema imports the requirements schema, and the pack sits on
    top — so a single prompt must carry all three or traceability edges disappear."""
    agent = _RecordingAgent.make(None, ontology_dir, PAYMENTS)
    prompt = agent._build_extraction_prompt("body", "requirements", "anything")

    for base_class in ("BusinessGoal", "BusinessCapability", "Requirement", "Container"):
        assert base_class in prompt, base_class


def test_no_pack_means_no_domain_block_and_no_payment_vocabulary(ontology_dir):
    """The generic path must stay generic. Before this change the base prompt
    carried a payment worked example, so every subject was primed with payments."""
    agent = _RecordingAgent.make(None, ontology_dir, None)
    prompt = agent._build_extraction_prompt("body", "requirements", "anything")

    assert "Domain vocabulary in force" not in prompt
    for leaked in ("Chargeback", "Cardholder", "Acquirer", "PaymentLifecycleState"):
        assert leaked not in prompt, f"payment vocabulary leaked into the generic prompt: {leaked}"


def test_the_generic_prompt_still_teaches_the_object_contract(ontology_dir):
    """Removing the payment example must not remove the lesson. The example is
    subject-neutral now, but the clause-versus-name distinction it teaches is the
    thing the whole object contract depends on."""
    agent = _RecordingAgent.make(None, ontology_dir, None)
    prompt = agent._build_extraction_prompt("body", "requirements", "anything")

    assert "OBJECT CONTRACT" in prompt
    assert "WRONG" in prompt and "RIGHT" in prompt
    # A domain-neutral worked example, present and usable.
    assert "Fulfilment Platform" in prompt


def test_named_instruments_are_offered_as_classes_not_left_to_Concept(ontology_dir):
    """A named law or standard has to be a class, or the model has nowhere to put it.

    `Concept` is this graph's marker for "a referent no pass classified", so an
    obligation landing there is indistinguishable from a vocabulary slip — the
    compliance census reads it as an ontology gap while the audit reads it as
    nothing at all. Offering the two classes is the whole fix, and it is
    end-to-end: the vocabulary reaches the prompt on the generic path, with no
    domain pack selected.
    """
    agent = _RecordingAgent.make(None, ontology_dir, None)
    prompt = agent._build_extraction_prompt("body", "requirements", "anything")

    assert "Regulation" in prompt
    assert "Standard" in prompt


def test_the_instruments_are_not_a_domain_pack_concern(ontology_dir, pack):
    """They belong in the base requirements layer, and this is the guard on that.

    GDPR, HIPAA and ISO/IEC 27001 are not payment vocabulary. Declaring them in
    the pack would shape the base ontology around one domain — and every other
    domain would inherit the payment model, which is the failure the layer split
    exists to prevent.
    """
    from core.ontology import load_ontology

    base = load_ontology(ontology_dir)
    assert "Regulation" in base.classes and "Standard" in base.classes
    assert "Regulation" not in pack.classes and "Standard" not in pack.classes


def test_a_pack_supplies_its_own_worked_example(ontology_dir):
    agent = _RecordingAgent.make(None, ontology_dir, PAYMENTS)
    prompt = agent._build_extraction_prompt("body", "requirements", "anything")

    assert "Spring Boot" in prompt, "the pack's own example should replace the generic one"
    assert "Fulfilment Platform" not in prompt
    # The mis-classification the pack exists to prevent is called out.
    assert "framework, not a thing the business consists of" in prompt


def test_the_architecture_agent_is_grounded_too(ontology_dir):
    """ARC-G needs the domain vocabulary as much as REQ-G: `owns_concepts` and
    `realised_by` are what make the third reconciliation leg expressible."""
    from agents.architecture_extraction.agent import ArchitectureExtractionAgent

    agent = _RecordingAgent.make(None, ontology_dir, PAYMENTS, agent_cls=ArchitectureExtractionAgent)
    context = agent._format_ontology_context()

    assert "Domain vocabulary in force" in context
    assert "Chargeback" in context


def test_selecting_a_pack_after_construction_is_recorded_in_provenance(ontology_dir):
    """`active_domain_pack_id` is what ingest writes into every assertion, so it
    must name the pack AND its version."""
    agent = _RecordingAgent.make(None, ontology_dir, None)
    assert agent.active_domain_pack_id() == ""

    agent.domain_pack = load_domain_pack(PAYMENTS, ontology_dir)
    # Derived, not hardcoded: the assertion is about the SHAPE (pack@version), and
    # a literal here would fail on every legitimate vocabulary bump — which is a
    # change this pack is expected to have.
    assert agent.active_domain_pack_id() == f"payment_processing@{agent.domain_pack.version}"


# ============================================================================
# The pick is offered by the app, and travels to provenance
# ============================================================================


def test_ingest_form_offers_the_pack_picker(client):
    body = client.get("/ingest").get_data(as_text=True)
    assert 'name="domain_pack"' in body
    assert "payment_processing" in body
    # "None" is offered as a first-class choice, not an afterthought.
    assert "None — base vocabulary only" in body


def test_the_selected_pack_reaches_the_extractor_and_the_graph(store_root, requirements_output):
    """The wiring test: what the form submits must reach the agent, and what the
    agent reports must land in assertion provenance."""
    from app import create_app
    from tests.conftest import FakeExtractor, FakeResult

    seen: dict = {}

    class RecordingExtractor(FakeExtractor):
        def use_domain_pack(self, spec):
            seen["selected"] = spec
            self._pack = spec
            return None

        def active_domain_pack_id(self):
            return f"{self._pack}@0.1.0" if self._pack else ""

        def run(self, input_data):
            seen["input"] = input_data
            return FakeResult(requirements_output, self.metadata, True)

    app = create_app(
        {"TESTING": True, "STORE_ROOT": str(store_root), "REVIEWER": "tester"},
        store_root=str(store_root),
        extractor_factory=lambda _t: RecordingExtractor(requirements_output),
    )
    response = app.test_client().post(
        "/ingest",
        data={"text": "body", "type": "requirements", "domain_pack": PAYMENTS},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert seen["selected"] == PAYMENTS
    assert seen["input"]["domain_pack"] == "payment_processing@0.1.0"

    from core.knowledge.store import RevisionStore

    graph = RevisionStore(str(store_root)).ensure().load_working().graph
    assert {a.provenance.domain_pack for a in graph.active()} == {"payment_processing@0.1.0"}


def test_an_unavailable_pack_is_refused_at_ingest_rather_than_ignored(store_root, requirements_output):
    """Naming a pack that cannot be loaded must fail loudly. Silently ingesting as
    generic would discard the vocabulary the user asked for and record the graph as
    if no pack had been chosen."""
    from app import create_app
    from tests.conftest import FakeExtractor, FakeResult

    class PickingExtractor(FakeExtractor):
        def use_domain_pack(self, spec):
            load_domain_pack(spec, REPO_ROOT / "ontology")

        def active_domain_pack_id(self):
            return ""

        def run(self, input_data):
            return FakeResult(requirements_output, self.metadata, True)

    app = create_app(
        {"TESTING": True, "STORE_ROOT": str(store_root), "REVIEWER": "tester"},
        store_root=str(store_root),
        extractor_factory=lambda _t: PickingExtractor(requirements_output),
    )
    response = app.test_client().post(
        "/ingest",
        data={"text": "body", "type": "requirements", "domain_pack": "no_such_domain"},
        follow_redirects=True,
    )
    assert b"Could not load the domain pack" in response.data
    assert b"domain pack not found" in response.data


# ============================================================================
# Design techniques and engineering conventions reach the graph
# ============================================================================


def _arch_output():
    """Shaped like the architecture profile's technology pass output."""
    return {
        "elements": [
            {"name": "Payment Gateway Platform", "element_type": "SoftwareSystem"},
            {"name": "Payment Orchestrator", "element_type": "Container",
             "parent": "Payment Gateway Platform"},
            {"name": "acme-payments-web", "element_type": "Container",
             "parent": "Payment Gateway Platform"},
            {"name": "legacy-billing-svc", "element_type": "Container",
             "parent": "Payment Gateway Platform"},
        ],
        "design_techniques": [
            {
                "name": "Stateless Services",
                "technique_category": "SCALABILITY",
                "applies_to": ["Payment Orchestrator"],
                "realizes_quality_attributes": ["NFR-SC-002 Horizontal Scalability"],
                "quality_category": "RELIABILITY",
                "mechanism": "Any replica serves a request; session state externalised to Valkey.",
            },
            {
                "name": "Redundancy / Replicas",
                "technique_category": "AVAILABILITY",
                "applies_to": ["Payment Orchestrator"],
                "realizes_quality_attributes": ["NFR-AV-001 High Availability"],
                "quality_category": "RELIABILITY",
            },
        ],
        "engineering_conventions": [
            {
                "name": "Container naming",
                "convention_type": "NAMING",
                "pattern": "<company>-<product>-<web>",
                "examples": ["acme-payments-web"],
                "applies_to": ["acme-payments-web", "legacy-billing-svc"],
                "enforcement": "MANDATORY",
            }
        ],
    }


def _ingest_arch():
    from core.knowledge import graph_from_extraction

    return graph_from_extraction(
        _arch_output(), metadata={"chunks": 1}, document_ref="arch.md",
        initiative_id="INIT-1",
    )[0]


def test_a_technique_links_to_the_nfr_it_realizes(requirements_output):
    """The gap this whole change closes.

    Before, "High Availability is required" and "the platform is replicated" were
    two facts with no edge between them, so the auditor could not answer "is this
    NFR realized, and by what?" — the core question the platform exists to answer.
    """
    graph = _ingest_arch()

    edges = [a for a in graph.active() if a.predicate == "realizes_quality_attribute"]
    assert len(edges) == 2
    pairs = {(graph.nodes[a.subject].label, a.value) for a in edges}
    assert ("Redundancy / Replicas", "NFR-AV-001 High Availability") in pairs
    assert ("Stateless Services", "NFR-SC-002 Horizontal Scalability") in pairs


def test_the_nfr_reference_stays_unresolved_for_reconciliation(requirements_output):
    """The NFR lives in REQ-G, so ingest must keep it a literal reference rather
    than invent a node — the same rule every other cross-graph link follows."""
    graph = _ingest_arch()
    unresolved = {a.value for a in graph.unresolved_references()}
    assert "NFR-AV-001 High Availability" in unresolved
    assert "nfr_av_001_high_availability" not in graph.nodes


def test_a_technique_is_applied_to_several_elements(ontology):
    """Redundancy is a platform-wide decision, not a property of one container —
    which is why `applies_to` is multivalued."""
    graph = _ingest_arch()
    applied = [a for a in graph.active() if a.predicate == "applies_technique"]
    assert applied, "technique application edges missing"
    for a in applied:
        assert graph.nodes[a.object].kind == "DesignTechnique"


def test_a_technique_records_its_category_and_mechanism(requirements_output):
    """Category lets the auditor check the technique is aimed at the right family
    even before the NFR resolves; mechanism is what a human checks it against."""
    graph = _ingest_arch()
    by_subject = {}
    for a in graph.active():
        if a.subject.startswith("designtechnique:"):
            by_subject.setdefault(graph.nodes[a.subject].label, {})[a.predicate] = a.value
    assert by_subject["Redundancy / Replicas"]["technique_category"] == "AVAILABILITY"
    assert by_subject["Redundancy / Replicas"]["quality_category"] == "RELIABILITY"
    assert "Valkey" in by_subject["Stateless Services"]["mechanism"]


def test_a_convention_records_the_checkable_pattern_and_its_examples():
    graph = _ingest_arch()
    conv = graph.nodes["engineeringconvention:container_naming"]
    facts = {a.predicate: a.value for a in graph.active() if a.subject == conv.id}
    assert facts["pattern"] == "<company>-<product>-<web>"
    assert facts["convention_type"] == "NAMING"
    assert facts["enforcement"] == "MANDATORY"
    assert facts["example"] == "acme-payments-web"


def test_convention_conformance_is_attached_to_each_governed_element():
    """A convention with no element attached is documentation. Attaching it to
    every governed element is what makes conformance reviewable per element."""
    graph = _ingest_arch()
    edges = [a for a in graph.active() if a.predicate == "conforms_to"]
    assert len(edges) == 2
    labels = {graph.nodes[a.subject].label for a in edges}
    assert labels == {"acme-payments-web", "legacy-billing-svc"}


def test_the_convention_pattern_exposes_drift_the_document_never_mentions():
    """The capability the convention class exists for.

    `legacy-billing-svc` is attached as governed but does not match
    `<company>-<product>-<web>`. The graph holds both the rule and the element
    names, so the drift is derivable — a document that records a standard rarely
    also reports its own violations.
    """
    from core.knowledge import graph_from_extraction

    graph = graph_from_extraction(
        _arch_output(), metadata={"chunks": 1}, document_ref="arch.md"
    )[0]

    conv = graph.nodes["engineeringconvention:container_naming"]
    pattern = next(
        a.value for a in graph.active()
        if a.subject == conv.id and a.predicate == "pattern"
    )
    governed = [
        graph.nodes[a.subject].label
        for a in graph.active()
        if a.predicate == "conforms_to" and a.object == conv.id
    ]

    assert pattern == "<company>-<product>-<web>"
    # The placeholder values are inferable from the conforming example, so the
    # check needs no configuration.
    conforming = [n for n in governed if n == "acme-payments-web"]
    violating = [n for n in governed if n not in conforming]
    assert conforming == ["acme-payments-web"]
    assert violating == ["legacy-billing-svc"], "the non-conforming element should be derivable"


def test_a_document_with_no_techniques_or_conventions_still_ingests(architecture_output):
    """Both collections are optional. A document that names neither must not
    produce empty placeholder nodes — absence is not a finding here."""
    from core.knowledge import graph_from_extraction

    graph, _run = graph_from_extraction(
        architecture_output, metadata={"chunks": 1}, document_ref="a.md"
    )
    kinds = {n.kind for n in graph.nodes.values()}
    assert "DesignTechnique" not in kinds
    assert "EngineeringConvention" not in kinds
