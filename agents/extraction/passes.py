"""
Focused extraction passes (Option C).

Replaces one mega-prompt asking for every collection at once. That approach hit
a hard ceiling: as the schema grew, the model silently dropped whole categories
of content — traceability predicates, technologies, responsibilities, monitoring
platforms. Each drop looked like a prompt bug; the actual cause was asking for
too much in one call.

A pass is a small, single-purpose extraction: one purpose, one schema, one short
instruction block. Passes run per chunk, so cost scales with document size rather
than being bounded by it.

The trade is explicit: more calls, each small and reliable, instead of one call
that is fast when it works and silently lossy when it does not.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Type

from .chunking import Chunk


@dataclass
class PassSpec:
    """One focused extraction pass."""

    name: str
    schema: Type
    instructions: str
    """Short, single-purpose instruction block. Keep it small — length here is
    exactly what caused the dilution this design replaces."""

    output_keys: Dict[str, str] = field(default_factory=dict)
    """Map of output collection name -> attribute name on the schema, e.g.
    {"elements": "elements"}. Used when merging pass results."""


@dataclass
class PassOutcome:
    """What one pass produced for one chunk."""

    pass_name: str
    chunk: Chunk
    result: Optional[Any] = None
    error: Optional[str] = None
    elapsed: float = 0.0
    empty: bool = False
    path: str = "structured"       # structured | text | none

    @property
    def ok(self) -> bool:
        return self.result is not None and not self.error


@dataclass
class PassRunSummary:
    """Aggregate view of a pass run, for logging and diagnosis."""

    total_calls: int = 0
    succeeded: int = 0
    empty: int = 0
    failed: int = 0
    elapsed: float = 0.0

    text_fallbacks: int = 0

    def describe(self, chunks: int, passes: int) -> str:
        return (f"{chunks} chunk(s) x {passes} pass(es) = {self.total_calls} call(s): "
                f"{self.succeeded} ok ({self.text_fallbacks} via text), "
                f"{self.empty} empty, {self.failed} failed "
                f"in {self.elapsed:.0f}s")


def build_pass_prompt(
    spec: PassSpec,
    chunk: Chunk,
    shared_context: str = "",
) -> str:
    """Assemble a pass prompt: shared ontology context + pass instruction + chunk.

    Shared context (available ontology classes) is repeated per call. That looks
    wasteful, but it keeps every pass self-contained and lets the model see only
    the vocabulary relevant to what it is extracting right now.
    """
    parts = []
    if shared_context:
        parts.append(shared_context.rstrip())
    parts.append(spec.instructions.strip())

    header = chunk.header_note()
    if header:
        parts.append(header.rstrip())

    parts.append("## Document content\n" + chunk.text)
    return "\n\n".join(parts) + "\n"


def _text_fallback(spec: PassSpec, text: str, agent) -> Optional[Any]:
    """Build a pass result from a free-form text response.

    Structured output fails often on local models (the server may not honour
    tool_choice at all). Without this, a failed pass contributes nothing — which
    is how the architecture fallback previously produced zero triples from an
    answer that was complete, just wrapped in an unparseable envelope.

    Populates whatever the pass declares, using the profile's parsers. Fields
    that cannot be recovered from text are simply left empty; partial is better
    than nothing, and the caller records which path was used.
    """
    kwargs: Dict[str, Any] = {}
    declared = set(spec.output_keys.values())

    if "triples" in declared:
        kwargs["triples"] = agent._parse_triples_from_text(text)
    if "elements" in declared:
        kwargs["elements"] = agent._parse_entities_from_text(text)
    if "connections" in declared:
        kwargs["connections"] = agent._parse_relationships_from_text(text)
    # technology_stacks / architecture_styles / references have no reliable text
    # parser — they stay empty, and the run is marked as a partial fallback.

    if not any(kwargs.values()):
        return None
    try:
        return spec.schema(**kwargs)
    except Exception:                                                # noqa: BLE001
        # Some collection came back the wrong shape (e.g. the generic parser
        # produced entities where the pass schema expects typed records).
        # Degrade rather than discard: triples are the shared backbone and the
        # most reliable thing to recover from text, so keep those even if the
        # richer collections have to be dropped.
        if kwargs.get("triples"):
            try:
                return spec.schema(triples=kwargs["triples"])
            except Exception:                                        # noqa: BLE001
                return None
        return None


def run_passes(
    agent,
    passes: Sequence[PassSpec],
    chunks: Sequence[Chunk],
    shared_context: str = "",
    log: Optional[Any] = None,
    allow_text_fallback: bool = True,
) -> List[PassOutcome]:
    """Run every pass over every chunk, collecting outcomes.

    Deliberately does not fail fast: one pass failing on one chunk should not
    discard the rest. Failures are recorded and reported, and the merge step
    works with whatever arrived.
    """
    import time

    outcomes: List[PassOutcome] = []

    for chunk in chunks:
        for spec in passes:
            label = f"{spec.name} @ {chunk.label}"
            if log:
                log(f"  pass: {label}")

            t0 = time.time()
            prompt = build_pass_prompt(spec, chunk, shared_context)

            try:
                result = agent.invoke_structured(prompt, spec.schema)
            except Exception as e:                                   # noqa: BLE001
                result, err = None, f"{type(e).__name__}: {e}"
            else:
                err = None

            path = "structured"

            # Structured failed or returned nothing — try the text path before
            # writing the pass off, since a complete answer may still be present
            # in an envelope the structured call could not use.
            if result is None and allow_text_fallback:
                try:
                    raw = agent.invoke(prompt)
                    result = _text_fallback(spec, str(raw), agent)
                    path = "text" if result is not None else "none"
                except Exception as e:                               # noqa: BLE001
                    err = f"text fallback failed: {type(e).__name__}: {e}"
                    path = "none"

            elapsed = time.time() - t0
            empty = result is None and err is None

            outcomes.append(PassOutcome(
                pass_name=spec.name, chunk=chunk, result=result,
                error=err, elapsed=elapsed, empty=empty, path=path,
            ))

            if log:
                if err:
                    log(f"    failed: {err[:120]}")
                elif empty:
                    log(f"    empty ({elapsed:.0f}s)")
                else:
                    log(f"    ok via {path} ({elapsed:.0f}s)")

    return outcomes


def collect(outcomes: Sequence[PassOutcome], pass_name: str,
            attribute: str) -> List[List[Any]]:
    """Gather one collection from every chunk's outcome for a given pass.

    Returns a list of lists (one per chunk) ready for `merge_records` /
    `merge_triples`, which is why it does not flatten here.
    """
    groups: List[List[Any]] = []
    for o in outcomes:
        if o.pass_name != pass_name or not o.ok:
            continue
        values = getattr(o.result, attribute, None)
        if values:
            groups.append(list(values))
    return groups


def summarise(outcomes: Sequence[PassOutcome], chunks: int, passes: int) -> PassRunSummary:
    s = PassRunSummary(total_calls=len(outcomes))
    for o in outcomes:
        s.elapsed += o.elapsed
        if o.error:
            s.failed += 1
        elif o.empty:
            s.empty += 1
        else:
            s.succeeded += 1
            if o.path == "text":
                s.text_fallbacks += 1
    return s
