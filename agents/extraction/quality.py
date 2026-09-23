"""
Deterministic quality classification for non-functional requirements.

WHY THIS IS NOT A MODEL TASK
----------------------------
Measured on the live model: with the full ISO/IEC 25010:2023 taxonomy in the
prompt, **every** NFR came back with `quality_category`, `subcharacteristic` and
`quality_attribute` empty. The model instead invented its own attribute names —
`Latency`, `Throughput`, `Usability`, `Observability` — and attached them to the
System with `satisfies_quality_attributes`, so they landed as untyped `Concept`
nodes outside the vocabulary the auditor reads.

That is the same shape of failure the deterministic validators already exist to
catch (`check_containment`, `check_object_contract`, `check_element_types`): the
model is unreliable at mechanical fidelity, so the mechanical part should not be
its job. Mapping a requirement's wording onto a closed taxonomy is keyword and
shape matching. It is reviewable, reproducible, and it either fires or does not —
none of which is true of asking a 4B model to classify.

WHAT IT DOES AND DOES NOT CLAIM
-------------------------------
It reads the text of a requirement — the source passage plus the requirement's own
name — and scores it against ISO 25010 sub-characteristics by keyword. The highest
scoring sub-characteristic wins, and the score is recorded so a reviewer can see
how strongly it fired.

Two honest limits, both deliberate:

- **It is a first pass, not a verdict.** A requirement that mentions no keyword is
  left UNCLASSIFIED rather than guessed at. Reporting "we could not classify this"
  is more useful than a confident wrong category, because the wrong one silently
  becomes the answer an audit reasons over.
- **Keyword matching is not comprehension.** "The system must not expose latency
  guarantees" contains `latency` and would score TIME_BEHAVIOUR. This is why the
  score and the matched terms are returned: a low or single-term match is visibly
  weaker than a multi-term one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ============================================================================
# Keyword patterns per ISO/IEC 25010:2023 sub-characteristic
# ============================================================================
#
# Written as regexes rather than bare substrings so a stem cannot match inside an
# unrelated word. Deliberately NOT using `\b` boundaries: they make "encrypt"
# fail to match "encrypted" and "encryption", because `\b` after a stem requires a
# non-word character. A trailing `\w*` is the honest way to express "this stem and
# its inflections".
QUALITY_SIGNALS: Dict[str, Tuple[str, ...]] = {
    # -- Performance Efficiency --
    "TIME_BEHAVIOUR": (r"\blatenc", r"\brespons\w*\s*time", r"\bthroughput",
                       r"\bper\s+second", r"\btps\b", r"\bmilliseconds?\b", r"\bms\b",
                       r"\bseconds?\b", r"\bsub-second", r"\breal-?time"),
    "RESOURCE_UTILIZATION": (r"\bcpu\b", r"\bmemor", r"\bresource\s+usage",
                             r"\butilisation", r"\butilization", r"\bstorage\s+footprint"),
    "CAPACITY": (r"\bcapacity", r"\bconcurrent", r"\bvolume", r"\bpeak\s+load",
                 r"\btransactions?\s+per", r"\bscale\s+to"),
    # -- Reliability --
    "AVAILABILITY": (r"\bavailab", r"\buptime", r"\b24\s*/\s*7", r"\bnines\b",
                     r"\bfailover", r"\bredundan", r"\breplic", r"\bactive-?active",
                     r"\bdisaster\s+recover", r"\bservice\s+continuity"),
    "FAULT_TOLERANCE": (r"\bfault", r"\bgraceful\w*\s+degrad", r"\bdegrad\w*\s+graceful",
                        r"\bcircuit\s+break", r"\bretr(y|ies|ying)", r"\bresilien"),
    "RECOVERABILITY": (r"\brecover", r"\brestore", r"\bbackup", r"\brestart",
                       r"\breplay", r"\breprocess"),
    "FAULTLESSNESS": (r"\bdefect", r"\berror\s+rate", r"\bfailure\s+rate", r"\baccuracy"),
    # -- Security --
    "CONFIDENTIALITY": (r"\bencrypt", r"\bconfidential", r"\bsecre", r"\bprivacy",
                        r"\bmask", r"\btokeni[sz]", r"\bpci-?dss", r"\bcardholder",
                        r"\btls\b", r"\baes\b", r"\bcvv\b", r"\bpan\b"),
    "INTEGRITY": (r"\bintegrit", r"\btamper", r"\bimmutab", r"\bchecksum",
                  r"\bhash", r"\bnon-?repudiation"),
    "NON_REPUDIATION": (r"\bnon-?repudiation", r"\bundeniable", r"\bproof\s+of"),
    "ACCOUNTABILITY": (r"\baudit", r"\btraceab", r"\baccountab", r"\brbac\b",
                       r"\baccess\s+control", r"\bauthoris", r"\bauthoriz",
                       r"\bwho\s+did\s+what", r"\battribution"),
    "AUTHENTICITY": (r"\bauthentic", r"\bidentity", r"\bidentity\s+provider",
                     r"\bmfa\b", r"\bmulti-?factor", r"\bcredential"),
    "RESISTANCE": (r"\brate\s+limit", r"\bthrottl", r"\bdenial\s+of\s+service",
                   r"\bdos\b", r"\bddos", r"\bwaf\b"),
    # -- Maintainability --
    "MODULARITY": (r"\bmodular", r"\bdecoupl", r"\bbounded\s+context",
                   r"\bmodule\s+boundar", r"\bindependently\s+deploy"),
    "REUSABILITY": (r"\breusab", r"\breusable", r"\bshared\s+component", r"\blibrar"),
    # Observability is a maintainability concern in ISO 25010 terms — it is what
    # makes a system analysable in production. Included here rather than left out
    # because real documents treat it as an NFR, and a taxonomy that cannot place
    # it forces the classifier to mislabel it. Measured: a requirement about
    # logging, metrics and monitoring scored TIME_BEHAVIOUR on the single word
    # `real-time`, which is the wrong axis entirely.
    "ANALYSABILITY": (r"\banalysab", r"\banalyzab", r"\bdiagnos", r"\bimpact\s+analysis",
                      r"\blogging", r"\bmetrics\b", r"\bmonitoring", r"\bobservab",
                      r"\btracing", r"\bsiem\b"),
    "MODIFIABILITY": (r"\bconfigurab", r"\bextensib", r"\bplug-?in", r"\bcustomis",
                      r"\bcustomiz", r"\bmaintainab", r"\bevolv"),
    "TESTABILITY": (r"\btestab", r"\bunit\s+test", r"\bautomated\s+test",
                    r"\btest\s+coverage"),
    # -- Flexibility (2023: replaced Portability; scalability lives here) --
    "SCALABILITY": (r"\bscalab", r"\bscale\s+(up|out|horizont|vertical)",
                    r"\bhorizontal\s+scal", r"\belastic", r"\bauto-?scal",
                    r"\bstateless", r"\bpartition"),
    "ADAPTABILITY": (r"\badaptab", r"\bportab", r"\bmulti-?region", r"\bmulti-?tenant",
                     r"\bplatform\s+independent"),
    "INSTALLABILITY": (r"\binstall", r"\bprovision", r"\bdeployab", r"\brollout"),
    "REPLACEABILITY": (r"\breplac", r"\bswap\s+out", r"\binterchangeab"),
    # -- Interaction Capability (2023: renamed from Usability) --
    "OPERABILITY": (r"\busab", r"\buseab", r"\boperab", r"\bintuitiv", r"\bsimplicity"),
    "LEARNABILITY": (r"\blearnab", r"\btraining", r"\bonboard", r"\bdocumentation"),
    "USER_ERROR_PROTECTION": (r"\bvalidation", r"\binput\s+checks?", r"\bprevent\w*\s+error",
                              r"\bconfirmation", r"\bguard"),
    "USER_ASSISTANCE": (r"\bhelp\s+desk", r"\bsupport\s+channel", r"\btooltip",
                        r"\bguidance", r"\bassistance"),
    "INCLUSIVITY": (r"\baccessib", r"\bwcag", r"\binclusiv", r"\bscreen\s+reader"),
    "APPROPRIATENESS_RECOGNIZABILITY": (r"\brecognis", r"\brecogniz", r"\bdiscoverab"),
    "USER_ENGAGEMENT": (r"\bengag", r"\bresponsive\s+design", r"\buser\s+experience",
                        r"\bux\b"),
    "SELF_DESCRIPTIVENESS": (r"\bself-?descriptive", r"\bapi\s+documentation",
                             r"\bdescriptive\s+error"),
    # -- Compatibility --
    "INTEROPERABILITY": (r"\binteroperab", r"\bintegrat", r"\bapi\b", r"\bgrpc\b",
                         r"\brest\b", r"\bprotocol", r"\biso\s*8583", r"\biso\s*20022",
                         r"\bwebhook", r"\bthird-?party"),
    "CO_EXISTENCE": (r"\bco-?exist", r"\bshared\s+environment", r"\bmulti-?app"),
    # -- Functional Suitability --
    "FUNCTIONAL_COMPLETENESS": (r"\bcomplete", r"\bcover\w*\s+all", r"\ball\s+require"),
    "FUNCTIONAL_CORRECTNESS": (r"\bcorrect", r"\baccura", r"\bexact", r"\bvalid\s+result"),
    "FUNCTIONAL_APPROPRIATENESS": (r"\bappropriat", r"\bfit\s+for\s+purpose",
                                   r"\bsuitab"),
    # -- Safety --
    "FAIL_SAFE": (r"\bfail-?safe", r"\bsafe\s+mode", r"\bfail\s+closed"),
    "HAZARD_WARNING": (r"\balert", r"\bwarn", r"\bnotif\w*\s+of\s+failure",
                       r"\bmonitor\w*\s+alert"),
    "RISK_IDENTIFICATION": (r"\brisk", r"\bthreat\s+detect", r"\banomal"),
    "OPERATIONAL_CONSTRAINT": (r"\blimit", r"\bconstrain", r"\bthreshold", r"\bquota"),
    "SAFE_INTEGRATION": (r"\bsafe\s+integrat", r"\bsafety\s+integrity"),
}

# Sub-characteristic -> its ISO characteristic, derived from `core.ontology`
# rather than restated, so the taxonomy has one definition.
def _characteristic_of(subcharacteristic: str) -> str:
    from core.ontology import SUBCHARACTERISTIC_PARENT

    return SUBCHARACTERISTIC_PARENT.get(subcharacteristic, "")


# Keyword hits that are too weak to classify on alone. Kept tiny and explicit: a
# requirement whose ONLY signal is one of these is reported unclassified, because
# these words appear in ordinary prose about almost anything.
WEAK_SIGNALS = frozenset({r"\bapi\b", r"\brest\b", r"\blimit", r"\bmonitor\w*\s+alert"})

# Signals that are PRECISE indicators of their sub-characteristic, and should
# outrank a requirement that merely mentions a related quantity. Measured case:
# "processing a minimum of [X] transactions per second (TPS) with horizontal
# scaling capabilities" scored TIME_BEHAVIOUR (throughput, per second, tps) over
# SCALABILITY (scalability 3:2) — the wrong call, because the phrase names
# scaling as the requirement and throughput only as a quantity.
STRONG_SIGNALS = frozenset({
    # Scalability, not raw speed.
    r"\bscalab", r"\bhorizontal\s+scal", r"\bscale\s+(up|out|horizont)", r"\belastic",
    r"\bauto-?scal", r"\bstateless", r"\bpartition",
    # Availability, not incidental resilience.
    r"\bavailab", r"\buptime", r"\bnines\b", r"\bfailover", r"\bactive-?active",
    r"\bdisaster\s+recover",
    # Security specifics, not generic integrity.
    r"\bencrypt", r"\bpci-?dss", r"\bcardholder", r"\baes\b", r"\brbac\b",
    # Data protection, not generic "error".
    r"\bconfidential", r"\btokeni[sz]", r"\bmask",
})

MIN_SCORE = 1

# Functional requirements carry no quality attribute by definition, so a single
# keyword is not enough to attach one. Measured false positives at MIN_SCORE=1 on
# functional requirements:
#
#   FR-PM-003 "current state of every payment request (PENDING, AUTHORIZED, ...)"
#       scored ACCOUNTABILITY on the word `AUTHORIZED`, which is a payment state
#   FR-SR-001 "...based on transaction volume and time triggers"
#       scored CAPACITY on `volume`, which describes a trigger, not a capacity goal
#
# Both are the classifier reading domain vocabulary as quality vocabulary — the
# failure mode of keyword matching, and the reason a functional requirement has to
# clear a higher bar. Non-functional requirements keep the lower one: their whole
# purpose is to state a quality concern, so one clear signal is meaningful.
MIN_SCORE_FUNCTIONAL = 6


def _is_non_functional(entity: Dict[str, object]) -> bool:
    kind = str(entity.get("ontology_class") or entity.get("entity_type") or "")
    if "NonFunctional" in kind:
        return True
    return str(entity.get("requirement_id") or "").upper().startswith("NFR")


@dataclass
class QualityClassification:
    """The verdict, with the evidence that produced it."""

    characteristic: str = ""
    subcharacteristic: str = ""
    attribute_name: str = ""
    score: int = 0
    matched_terms: List[str] = field(default_factory=list)

    @property
    def is_classified(self) -> bool:
        return bool(self.subcharacteristic and self.score >= MIN_SCORE)

    def to_dict(self) -> Dict[str, object]:
        return {
            "quality_category": self.characteristic,
            "subcharacteristic": self.subcharacteristic,
            "quality_attribute": self.attribute_name,
            "score": self.score,
            "matched_terms": list(self.matched_terms),
        }


def humanise(subcharacteristic: str) -> str:
    """`TIME_BEHAVIOUR` -> `Time Behaviour`. The ontology's own naming style."""
    return subcharacteristic.replace("_", " ").title()


def classify_requirement(
    text: str, min_score: int = MIN_SCORE
) -> QualityClassification:
    """Classify one requirement's text. No model, no guessing.

    The default is the light threshold: one clear signal is enough, which is the
    right rule for the text of a NON-functional requirement — stating a quality
    concern is its entire purpose. Callers that have established the text is a
    functional requirement should pass `min_score=MIN_SCORE_FUNCTIONAL`, because
    an FR carries no quality attribute and one incidental word should not attach
    one. Text of unknown kind gets the light rule and should be treated as a
    proposal, not a verdict.

    Returns an unclassified result when nothing clears the bar, which callers must
    render as "not classified" rather than treating as a category.
    """
    if not text or not text.strip():
        return QualityClassification()

    haystack = text.lower()
    best: Optional[QualityClassification] = None

    for subcharacteristic, patterns in QUALITY_SIGNALS.items():
        matched: List[str] = []
        score = 0
        for pattern in patterns:
            if re.search(pattern, haystack):
                matched.append(pattern)
                if pattern in WEAK_SIGNALS:
                    continue          # cannot carry a classification alone
                score += 6 if pattern in STRONG_SIGNALS else 1
        if score < 1:
            continue
        if best is None or score > best.score:
            best = QualityClassification(
                characteristic=_characteristic_of(subcharacteristic),
                subcharacteristic=subcharacteristic,
                attribute_name=humanise(subcharacteristic),
                score=score,
                matched_terms=matched,
            )

    if best is None or best.score < min_score:
        return QualityClassification()
    return best


# ============================================================================
# Locating a requirement's text in its source
# ============================================================================

# A requirement identifier: FR-PM-001, NFR-SC-002, CON-3, REQ_12.
_IDENTIFIER = re.compile(r"\b[A-Z][A-Z0-9]{1,6}(?:[-_][A-Z0-9]{1,6}){1,3}\b")

# How far from an identifier to look for its text, in characters. Generous enough
# for a bullet or a paragraph, tight enough not to swallow the next requirement.
CONTEXT_WINDOW = 700


def source_passage(source: str, requirement_id: str, requirement_name: str = "") -> str:
    """The source text belonging to a requirement.

    Prefers an identified passage (`**NFR-PS-001 (Latency):** ...`) and falls back
    to the vicinity of the requirement's name. Returns "" when neither is found,
    which the caller should treat as "cannot classify" rather than "no quality".
    """
    if not source:
        return ""

    needles = [n for n in (requirement_id, requirement_name) if n and n.strip()]
    for needle in needles:
        index = source.find(needle)
        if index < 0:
            continue

        line_start = source.rfind("\n", 0, index)
        line_start = 0 if line_start < 0 else line_start + 1
        line_end = source.find("\n", index)
        line_end = len(source) if line_end < 0 else line_end
        line = source[line_start:line_end]

        # In a Markdown table the ROW is the requirement, so the passage must not
        # start at the line beginning. Measured leak: including the row's
        # `| FR-SR-003 | Reporting | ... |` preamble let the table header on the
        # following line contribute "Non-Functional Requirements", close enough to
        # the next section's "Performance and Scalability" heading that a REPORTING
        # requirement was classified SCALABILITY with the highest score in the
        # whole document.
        if line.lstrip().startswith("|"):
            start = line_start + max(0, line.find(needle))
        else:
            # Otherwise keep the heading above, which is often the clearest signal
            # — `### Performance Requirements` says more than the sentence under it.
            start = max(0, line_start - 1)

        end = min(len(source), index + CONTEXT_WINDOW)

        if line.lstrip().startswith("|"):
            # A table row IS the requirement: it ends at its own line. Without
            # this the 700-character window ran past the row, over the table
            # header, and into the next section's "Performance and Scalability"
            # heading — which classified a REPORTING requirement as SCALABILITY.
            end = line_end

        # Stop at the next requirement on a FOLLOWING LINE, so one requirement's
        # text cannot bleed into the next one's classification.
        #
        # Two things here are load-bearing, both found by measurement:
        #
        # 1. "On a following line": a passage often mentions its own identifier
        #    again (`**NFR-SC-001 (PCI-DSS Compliance):** ... PCI-DSS`). Cutting at
        #    the next identifier regardless of position truncated the passage to
        #    `"**NFR-SC-001 ("` — classified as nothing, from text that is almost
        #    entirely security keywords.
        # 2. The offset is `line_start + found.start()`, where `found.start()` is
        #    relative to the LINE. Using it as an offset into the tail puts the cut
        #    at the wrong character — a bug that looked exactly like a missing
        #    keyword.
        tail = source[index + len(needle):end]
        offset = 0
        for position, tail_line in enumerate(tail.split("\n")):
            if position > 0:
                found = _IDENTIFIER.search(tail_line)
                if found and found.group(0) != requirement_id:
                    end = index + len(needle) + offset + found.start()
                    break
            offset += len(tail_line) + 1

        return source[start:end]

    return ""


def classify_entity(entity: Dict[str, object], source: str = "") -> QualityClassification:
    """Classify an extracted requirement entity from its source passage.

    The passage is preferred over the entity's own `name`, because the
    identifier is often all the model put there (`name: "NFR-PS-001"`), which
    carries no quality signal at all. The name is still included: it can carry
    signal the passage lacks, and vice versa.

    Defaults to the strict threshold like `classify_requirement`; callers that
    know the entity is an NFR should pass `min_score=MIN_SCORE`.
    """
    requirement_id = str(entity.get("requirement_id") or "").strip()
    name = str(entity.get("name") or "").strip()

    passage = source_passage(source, requirement_id, name)
    combined = " \n ".join(part for part in (passage, name) if part)
    threshold = MIN_SCORE if _is_non_functional(entity) else MIN_SCORE_FUNCTIONAL
    return classify_requirement(combined, min_score=threshold)


def is_requirement_entity(entity: Dict[str, object]) -> bool:
    """Whether this entity is a requirement the classifier should touch."""
    kind = str(entity.get("ontology_class") or entity.get("entity_type") or "")
    if "Requirement" in kind:
        return True
    return bool(re.match(r"^(FR|NFR|CON|REQ)[-_]", str(entity.get("requirement_id") or "")))


# ============================================================================
# Deterministic requirement inventory
# ============================================================================
#
# WHY THIS EXISTS. Quality classification originally keyed off the entity's
# `requirement_id` — which put it at the mercy of the model. Measured on two runs
# of the SAME document with the same config: one emitted 18 of 18 identifiers, the
# next emitted **0 of 18**. The classifier then had nothing to key on and produced
# nothing, which is the non-determinism this module exists to remove.
#
# So identifiers are read from the SOURCE rather than requested from the model.
# They are literal strings in a small number of document shapes, which makes this
# pattern matching rather than language understanding. The two shapes the fixtures
# use:
#
#   *   **NFR-SC-002 (Data Encryption):** All CHD must be encrypted ...
#   | **FR-SR-003** | **Reporting** | The PGP shall generate ... | Medium |
#
# A shape not matched yields no inventory entry, which degrades to "unclassified"
# — visible, and better than a wrong category.

_TABLE_ROW = re.compile(
    r"^\s*\|\s*\*\*(?P<id>[A-Z][A-Z0-9_-]+)\*\*\s*\|"      # | **FR-SR-003** |
    r"\s*\*\*(?P<name>[^|*]+?)\*\*\s*\|"                       # | **Reporting** |
    r"(?P<body>[^|]*)",                                               # | description |
    re.MULTILINE,
)

_BULLET = re.compile(
    r"^\s*[*+-]\s+\*\*(?P<id>[A-Z][A-Z0-9_-]+)"                  # *   **NFR-SC-002
    r"(?:\s*\((?P<name>[^)]+)\))?"                                 #   (Data Encryption)
    r"\s*:?\s*\*\*:?\s*(?P<body>.*)$",                           # :** rest of line
    re.MULTILINE,
)


@dataclass
class SourceRequirement:
    """A requirement as the document states it."""

    identifier: str
    name: str
    passage: str
    classification: QualityClassification


# Requirement-key prefixes, taken from the sources this project reads. An explicit
# allow-list rather than a shape heuristic, because the shapes collide: `AES-256`
# and `TLS-1.2` are perfectly identifier-shaped and are not requirements.
_REQUIREMENT_PREFIXES = frozenset({"FR", "NFR", "CON", "REQ", "BR", "SR", "AR", "DR", "US", "NRC"})


def _plausible_identifier(candidate: str) -> bool:
    """Reject strings shaped like identifiers but not requirement keys.

    Measured: a shape-only rule accepted `AES-256` and `TLS-1.2`, putting a cipher
    and a protocol version in the identifier column — and when the model was asked
    to supply identifiers it emitted `CHD` and `PGP`, the same class of error.

    An explicit prefix allow-list is stricter than a shape rule and, unlike a rule,
    says which keys this project actually recognises.
    """
    parts = re.split(r"[-_]", candidate)
    if len(parts) < 2:
        return False
    if not re.fullmatch(r"\d{1,4}", parts[-1]):
        return False
    return parts[0].upper() in _REQUIREMENT_PREFIXES





def requirement_inventory(source: str) -> List[SourceRequirement]:
    """Every requirement the source identifies, each with its classification.

    Deterministic and order-preserving: the same source always yields the same
    inventory, which is what lets both identifier recovery and quality
    classification work on a run where the model emitted no ids at all.
    """
    if not source:
        return []

    found: List[SourceRequirement] = []
    seen: set = set()

    def add(identifier: str, name: str, body: str) -> None:
        if not _plausible_identifier(identifier) or identifier in seen:
            return
        seen.add(identifier)
        passage = f"{name} {body}".strip()
        found.append(
            SourceRequirement(
                identifier=identifier,
                name=(name or "").strip(),
                passage=passage,
                classification=classify_requirement(
                    passage,
                    min_score=(
                        MIN_SCORE if identifier.startswith("NFR") else MIN_SCORE_FUNCTIONAL
                    ),
                ),
            )
        )

    for match in _TABLE_ROW.finditer(source):
        add(match.group("id"), match.group("name") or "", match.group("body") or "")
    for match in _BULLET.finditer(source):
        add(match.group("id"), match.group("name") or "", match.group("body") or "")

    return found


def _name_tokens(value: str) -> frozenset:
    return frozenset(
        token for token in re.split(r"[^a-z0-9]+", (value or "").lower()) if len(token) > 2
    )


def match_to_inventory(
    entity: Dict[str, object], inventory: List[SourceRequirement]
) -> Optional[SourceRequirement]:
    """Find the inventory entry an entity corresponds to.

    By identifier where the model supplied one, otherwise by name overlap. Name
    matching is what makes the classifier independent of the model's id output: a
    run may name the entity `Latency` where the source says
    `NFR-PS-001 (Latency)`, and without this it would classify nothing.
    """
    identifier = str(entity.get("requirement_id") or "").strip()
    if identifier:
        for item in inventory:
            if item.identifier == identifier:
                return item

    name = str(entity.get("name") or "").strip()
    if not name:
        return None
    for item in inventory:
        if name == item.identifier:
            return item

    entity_tokens = _name_tokens(name)
    if not entity_tokens:
        return None

    best: Optional[SourceRequirement] = None
    best_shared = 0
    for item in inventory:
        source_tokens = _name_tokens(item.name)
        if not source_tokens:
            continue
        shared = len(entity_tokens & source_tokens)
        # The entity's own name must also appear in the requirement's passage.
        # Overlap alone matched `Payment Gateway Platform` to `Payment Acceptance`
        # on the single shared word `payment` — and a system being named in a
        # requirement's text is not that system BEING that requirement.
        if shared > best_shared and name.lower() in (item.passage or "").lower():
            best, best_shared = item, shared

    if best is None:
        return None
    # Two options, both requiring the match to be near-total rather than partial:
    #   - a single-word entity name that matches exactly (`Latency` ->
    #     `NFR-PS-001 (Latency)`), or
    #   - at least two shared significant words.
    # `Payment Gateway Platform` shares only `payment` with any requirement name,
    # so it qualifies for neither.
    if len(entity_tokens) == 1 and best_shared == 1:
        return best
    return best if best_shared >= 2 else None


def enrich_entities(
    entities: List[Dict[str, object]], source: str = ""
) -> List[Dict[str, object]]:
    """Attach deterministic identifiers and quality classification to requirements.

    Built on the source inventory rather than on what the model emitted, because
    the model's identifier output is not reliable enough to key on: two runs of the
    same document with the same config produced 18/18 and then 0/18 identifiers.

    Three things happen per entity, each only where the model was silent:

    - a missing `requirement_id` is recovered from the source inventory by name
    - missing quality fields are filled from the inventory's classification
    - where no inventory entry matches, the entity's own text is classified, with
      the strict threshold for functional requirements

    The model's own answers are never overwritten. Where it committed to a
    classification it saw the document, and that is better evidence than a keyword
    score — this fills silence, it does not overrule.
    """
    inventory = requirement_inventory(source)
    enriched: List[Dict[str, object]] = []

    for entity in entities:
        if not isinstance(entity, dict):
            enriched.append(entity)
            continue
        item = dict(entity)
        if not is_requirement_entity(item):
            enriched.append(item)
            continue

        matched = match_to_inventory(item, inventory) if inventory else None

        # Recover the identifier. This is what makes the classification below work
        # on a run where the model named the entity `Latency` and emitted no id.
        if matched is not None and not str(item.get("requirement_id") or "").strip():
            item["requirement_id"] = matched.identifier
            if not str(item.get("name") or "").strip():
                item["name"] = matched.name

        if any(item.get(k) for k in ("quality_category", "subcharacteristic", "quality_attribute")):
            enriched.append(item)
            continue

        if matched is not None and matched.classification.is_classified:
            verdict = matched.classification
        else:
            verdict = classify_entity(item, source)

        if verdict.is_classified:
            item["quality_category"] = verdict.characteristic
            item["subcharacteristic"] = verdict.subcharacteristic
            item["quality_attribute"] = verdict.attribute_name
            item["quality_classification_score"] = verdict.score
            item["quality_classification_source"] = "deterministic_source_pass"
        enriched.append(item)

    return enriched
