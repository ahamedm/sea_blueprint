"""
The ISO/IEC 25010:2023 quality taxonomy, and the canonical name for a quality
concern.

WHY THIS LIVES IN `core/` RATHER THAN BESIDE THE EXTRACTOR
-----------------------------------------------------------
It was written for one job — classifying the wording of a requirement onto a
sub-characteristic (ADR-0011) — and it lived in `agents/extraction/quality.py`
because that was the only caller. There is now a second caller: the coverage
census in `core.knowledge.quality` has to decide which ISO characteristic an
*attribute node* belongs to, so that `Availability` and `High Availability` are
one gap rather than two.

That decision must not exist twice. Two taxonomies drift, and the drift is
silent: the extractor would file a requirement under one concern while the census
grouped it under another, and the disagreement would look like a coverage gap.
`core/` is where the schema's own taxonomy already lives
(`core.ontology.SUBCHARACTERISTIC_PARENT`), so the keyword signals that map
wording onto it belong here too, readable by both the agents and the knowledge
layer. The agent module imports from here and re-exports, so its own callers are
unchanged.

WHAT IT DOES AND DOES NOT CLAIM
-------------------------------
`classify_quality_text` reads free text — a requirement's passage, or a bare
attribute label — and scores it against sub-characteristics by keyword. The
highest scoring one wins and the score is kept, so a reviewer can see how
strongly it fired. `canonical_quality_concern` is the stricter entry point: it
prefers the taxonomy's own names over any keyword guess, and reports an
unresolved label as unresolved rather than guessing.

Two honest limits, both deliberate:

- **It is a first pass, not a verdict.** Text that matches no keyword is left
  UNCLASSIFIED rather than guessed at. "We could not classify this" is more
  useful than a confident wrong category, because the wrong one silently becomes
  the answer an audit reasons over.
- **Keyword matching is not comprehension.** "The system must not expose latency
  guarantees" contains `latency`. This is why the score and the matched terms are
  returned: a weak match is visibly weaker than a strong one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional, Tuple

from core.ontology import (
    ISO_25010_2023,
    NON_ISO_QUALITY_CONCERNS,
    SUBCHARACTERISTIC_PARENT,
    enum_name,
)

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


def characteristic_of(subcharacteristic: str) -> str:
    """`TIME_BEHAVIOUR` -> `PERFORMANCE_EFFICIENCY`. Derived, never restated."""
    return SUBCHARACTERISTIC_PARENT.get(subcharacteristic, "")


def humanise(subcharacteristic: str) -> str:
    """`TIME_BEHAVIOUR` -> `Time Behaviour`. The ontology's own naming style."""
    return subcharacteristic.replace("_", " ").title()


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


def classify_quality_text(text: str, min_score: int = MIN_SCORE) -> QualityClassification:
    """Classify one piece of text against the sub-characteristic taxonomy.

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
                characteristic=characteristic_of(subcharacteristic),
                subcharacteristic=subcharacteristic,
                attribute_name=humanise(subcharacteristic),
                score=score,
                matched_terms=matched,
            )

    if best is None or best.score < min_score:
        return QualityClassification()
    return best


# ============================================================================
# Canonical names — the one key a census may group by
# ============================================================================

# The characteristic set: the eight ISO 25010:2023 characteristics the
# sub-characteristics roll up to, plus the concerns this project carries that are
# NOT from ISO. Declared from the ontology's own table so a change there moves
# here, rather than being restated and drifting.
ISO_CHARACTERISTICS: Tuple[str, ...] = tuple(characteristic for characteristic, _ in ISO_25010_2023)
NON_ISO_CHARACTERISTICS: Tuple[str, ...] = tuple(sorted(NON_ISO_QUALITY_CONCERNS))
CHARACTERISTIC_NAMES: FrozenSet[str] = frozenset(ISO_CHARACTERISTICS) | frozenset(
    NON_ISO_CHARACTERISTICS
)

# Stable display order: the ontology's own order, then the non-ISO concerns.
CHARACTERISTIC_ORDER: Tuple[str, ...] = ISO_CHARACTERISTICS + NON_ISO_CHARACTERISTICS

# Sub-characteristic -> the label the extractor writes for it. The requirements
# side names its attribute node after the sub-characteristic (`humanise`), which
# is why this reverse map is the most common way a label resolves.
LABEL_TO_SUBCHARACTERISTIC: Dict[str, str] = {
    humanise(sub).upper(): sub for sub in SUBCHARACTERISTIC_PARENT
}
LABEL_TO_CHARACTERISTIC: Dict[str, str] = {
    humanise(name).upper(): name for name in CHARACTERISTIC_NAMES
}

# Short forms people actually write, where the scheme's own name is long or has
# been renamed. An explicit table rather than a fuzzy rule: these are decisions,
# and a decision that cannot be read is one nobody can correct.
CHARACTERISTIC_ALIASES: Dict[str, str] = {
    "PERFORMANCE": "PERFORMANCE_EFFICIENCY",
    "USABILITY": "INTERACTION_CAPABILITY",
    "PORTABILITY": "FLEXIBILITY",
    "MAINTAINABILITY": "MAINTAINABILITY",
    "FUNCTIONALITY": "FUNCTIONAL_SUITABILITY",
}

# `enum_name` moved to `core.ontology` when the pattern catalogue became the
# second thing that had to normalise a label into an enum's spelling; it is
# re-exported here because this module's callers import it from here.


@dataclass(frozen=True)
class QualityConcern:
    """One quality concern, under the name the taxonomy gives it.

    `key` is the grouping identity a census must use. It is the sub-characteristic
    when one is known, the characteristic when only that is known, and the
    normalised label when neither is — so two spellings of one concern collapse
    and an unrecognised label stays visibly its own thing instead of being folded
    into a neighbour.
    """

    label: str
    key: str
    category: str = ""
    subcharacteristic: str = ""
    resolved: bool = False
    method: str = ""

    @property
    def display(self) -> str:
        """What to show a reader: the precise concern, else the label itself."""
        if self.subcharacteristic:
            return humanise(self.subcharacteristic)
        if self.category:
            return humanise(self.category)
        return self.label


def canonical_quality_concern(label: str) -> QualityConcern:
    """Resolve a quality-attribute label onto the ISO 25010 taxonomy.

    WHY THIS IS STRICTER THAN THE KEYWORD CLASSIFIER. The label on a
    `QualityAttribute` node is free text written by whichever profile produced it:
    the requirements side writes the taxonomy's own name (`Time Behaviour`),
    because the deterministic classifier chose it; the architecture side writes
    whatever the model called the attribute (`High Availability`, `Low Latency`).
    A census that grouped on the label would report the same concern as two gaps,
    which is the wording-collision failure YB-029 exists to stop. So the
    taxonomy's own names are tried FIRST, and the keyword classifier is the
    fallback for a label that is a synonym rather than a name.

    Order matters and is the whole design:

    1. the exact enum name of a sub-characteristic (`SCALABILITY`)
    2. the exact enum name of a characteristic (`RELIABILITY`, `SECURITY`)
    3. a short form the scheme itself renamed (`PERFORMANCE`, `USABILITY`)
    4. the humanised name the extractor writes (`Time Behaviour`)
    5. the keyword classifier, for a synonym (`High Availability`, `Low Latency`)
    6. unresolved — grouped under its own label, and reported as unresolved

    `resolved` is False only at step 6, so "the census could not place this
    attribute" is a state a caller can count rather than infer.
    """
    raw = (label or "").strip()
    name = enum_name(raw)
    if not name:
        return QualityConcern(label=raw, key="UNNAMED", method="empty")

    if name in SUBCHARACTERISTIC_PARENT:
        return QualityConcern(
            label=raw, key=name, category=SUBCHARACTERISTIC_PARENT[name],
            subcharacteristic=name, resolved=True, method="subcharacteristic",
        )

    if name in CHARACTERISTIC_NAMES:
        return QualityConcern(
            label=raw, key=name, category=name, resolved=True, method="characteristic",
        )

    if name in CHARACTERISTIC_ALIASES:
        category = CHARACTERISTIC_ALIASES[name]
        return QualityConcern(
            label=raw, key=category, category=category, resolved=True, method="alias",
        )

    if name in LABEL_TO_SUBCHARACTERISTIC:
        sub = LABEL_TO_SUBCHARACTERISTIC[name]
        return QualityConcern(
            label=raw, key=sub, category=SUBCHARACTERISTIC_PARENT.get(sub, ""),
            subcharacteristic=sub, resolved=True, method="humanised",
        )

    if name in LABEL_TO_CHARACTERISTIC:
        category = LABEL_TO_CHARACTERISTIC[name]
        return QualityConcern(
            label=raw, key=category, category=category, resolved=True, method="humanised",
        )

    verdict = classify_quality_text(raw)
    if verdict.subcharacteristic:
        return QualityConcern(
            label=raw, key=verdict.subcharacteristic,
            category=verdict.characteristic or SUBCHARACTERISTIC_PARENT.get(
                verdict.subcharacteristic, ""
            ),
            subcharacteristic=verdict.subcharacteristic,
            resolved=True, method="keyword",
        )

    return QualityConcern(label=raw, key=f"UNRESOLVED:{name}", method="unresolved")
