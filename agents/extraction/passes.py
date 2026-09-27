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
from contextlib import nullcontext
from typing import Any, Callable, Dict, List, Optional, Sequence, Type

from core.events import ERROR, PASS_FINISHED, PASS_STARTED, RunEvent

from ..base_agent import looks_like_a_repetition_loop
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

    temperature: Optional[float] = None
    """Sampling temperature for THIS pass, overriding the agent's. None inherits.

    Per-pass because a profile's passes are not the same kind of act. Naming a
    container produces a merge key that has to be stable across runs; choosing
    which catalogue pattern to adopt is a genuine choice among alternatives whose
    names are pinned by the catalogue. One temperature for both is a compromise
    that serves neither.
    """


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
    temperature: Optional[float] = None
    """The override this pass ran at, for the run record. None means it inherited
    the agent's temperature."""

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
    # technology_stacks / architecture_styles / design_techniques /
    # engineering_conventions / references have no reliable text parser — they
    # stay empty, and the run is marked as a partial fallback. Triples still
    # carry the technique and convention links, so a text-mode run loses the
    # richer records but not the traceability.

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


def _sampling(agent, temperature: Optional[float]):
    """Enter the agent's temperature override, or a no-op when there is nothing to enter.

    Guarded by `getattr` so a duck-typed stand-in without the method still runs the
    pass at the agent's temperature rather than failing.
    """
    override = getattr(agent, "sampling", None)
    if temperature is None or not callable(override):
        return nullcontext()
    return override(temperature)


def _outcome_state(error: Optional[str], empty: bool, result: Any) -> str:
    """Name a pass attempt's outcome: `failed` | `empty` | `ok`.

    The one place that decides the vocabulary, shared by `run_passes` (which
    reports the transition on the progress channel) and `outcome_records` (which
    stores the `PassRecord`). One decider, so a watcher and the stored run record
    can never disagree about what a pass did.
    """
    if error:
        return "failed"
    if empty or result is None:
        return "empty"
    return "ok"


def _triples_produced(result: Any) -> int:
    """How many triples a pass result carried, without assuming its shape.

    A counter is a state transition; the triples themselves are content and never
    travel on the progress channel. `getattr` rather than a schema check because a
    pass result is whatever the profile's schema produced, and a result with no
    `triples` collection simply produced none.
    """
    if result is None:
        return 0
    triples = getattr(result, "triples", None)
    return len(triples) if triples else 0


def _emit_progress(
    progress: Optional[Callable[[RunEvent], Any]],
    kind: str,
    payload: Dict[str, Any],
    log: Optional[Any] = None,
) -> None:
    """Hand one state transition to the progress sink, if one is attached.

    A SINK MUST NEVER BREAK A RUN. Progress is a notification channel: the pass
    result, not the event, is the run's product, and YB-036's failure contract is
    that a slow, broken or dead subscriber degrades *live progress only*. So an
    exception from the sink is caught here and reported on the run log; it must
    not fail the pass, skip the remaining passes, or change a single outcome.
    Sinks are expected to be non-blocking for the same reason — the durable
    append is the source of truth, any live push is best-effort.

    The sink owns run identity and sequencing (`run_id`, `scope_id`, `seq` are
    read off it when it exposes them and stamped by it when it journals); the
    pipeline just names the transition. See `agents.extraction.progress`.
    """
    if progress is None:
        return
    try:
        progress(RunEvent(
            run_id=getattr(progress, "run_id", "") or "",
            scope_id=getattr(progress, "scope_id", "") or "",
            seq=0,  # the sink assigns the monotonic sequence
            kind=kind,
            payload=dict(payload),
        ))
    except Exception as e:                                           # noqa: BLE001
        if log:
            log(f"    progress sink failed (ignored): {type(e).__name__}: {e}")


def run_passes(
    agent,
    passes: Sequence[PassSpec],
    chunks: Sequence[Chunk],
    shared_context: str = "",
    log: Optional[Any] = None,
    progress: Optional[Callable[[RunEvent], Any]] = None,
    allow_text_fallback: bool = True,
) -> List[PassOutcome]:
    """Run every pass over every chunk, collecting outcomes.

    Deliberately does not fail fast: one pass failing on one chunk should not
    discard the rest. Failures are recorded and reported, and the merge step
    works with whatever arrived.

    `progress` is an optional sink called with a `RunEvent` on every state
    transition: `PASS_STARTED` before a pass call and `PASS_FINISHED` (or `ERROR`
    when it failed) after. The payload carries transitions only — pass name,
    chunk label, outcome, elapsed, triples produced, path — never the extracted
    content, which stays in the run record. The pipeline emits and knows nothing
    about subscribers; a sink that raises is logged and ignored (see
    `_emit_progress`), so attaching one cannot change the run.
    """
    import time

    outcomes: List[PassOutcome] = []

    for chunk in chunks:
        for spec in passes:
            label = f"{spec.name} @ {chunk.label}"
            # The temperature belongs in the log line: it is the one sampling knob
            # that differs BETWEEN passes of the same run, so "which pass was warm"
            # is not answerable from the model id alone.
            if spec.temperature is not None:
                label += f" (temperature {spec.temperature})"
            if log:
                log(f"  pass: {label}")

            _emit_progress(progress, PASS_STARTED, {
                "pass_name": spec.name,
                "chunk_label": chunk.label,
            }, log)

            t0 = time.time()
            prompt = build_pass_prompt(spec, chunk, shared_context)

            # The override covers the WHOLE pass — the structured attempt and the
            # text fallback — because both are the same act and should be sampled
            # the same way. It is restored in a `finally`, so a pass that raises
            # cannot leave the model warm for the ones after it.
            with _sampling(agent, spec.temperature):
                try:
                    result = agent.invoke_structured(prompt, spec.schema)
                except Exception as e:                               # noqa: BLE001
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
                        text = str(raw)
                        result = _text_fallback(spec, text, agent)
                        path = "text" if result is not None else "none"
                        # A model that looped produced text but no answer. Recorded
                        # as a FAILURE with a cause rather than an empty pass,
                        # because "empty" reads as "the model had nothing to say"
                        # and sends the next person looking at the prompt instead
                        # of at the sampler.
                        if result is None and looks_like_a_repetition_loop(text):
                            err = (
                                "the model repeated itself instead of answering — a "
                                "degenerate generation, not an empty one. Check the "
                                "sampling parameters (temperature, max_tokens, "
                                "repeat_penalty) reaching the server."
                            )
                    except Exception as e:                           # noqa: BLE001
                        err = f"text fallback failed: {type(e).__name__}: {e}"
                        path = "none"

            elapsed = time.time() - t0
            empty = result is None and err is None

            outcomes.append(PassOutcome(
                pass_name=spec.name, chunk=chunk, result=result,
                error=err, elapsed=elapsed, empty=empty, path=path,
                temperature=spec.temperature,
            ))

            # The transition, not the result: what changed, not what was found.
            outcome_state = _outcome_state(err, empty, result)
            transition: Dict[str, Any] = {
                "pass_name": spec.name,
                "chunk_label": chunk.label,
                "outcome": outcome_state,
                "elapsed": round(elapsed, 3),
                "triples_produced": _triples_produced(result),
                "path": path,
            }
            if err:
                transition["error"] = err[:200]
            _emit_progress(progress, ERROR if err else PASS_FINISHED, transition, log)

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


def outcome_records(outcomes: Sequence[PassOutcome]) -> List[Any]:
    """One `PassRecord` per pass attempt, so a run can report its own completeness.

    `summarise` gives a profile the aggregate counts; this gives it the records
    those counts were made of. A profile that emits only the counts leaves ingest
    to reconstruct `pass_name="(unspecified)"` entries, which is how the
    architecture profile lost the identity of every pass it ran — ADR-0013's
    "the run describes itself", applied to the profile that was written before
    it. The outcome already carries the name, chunk, path, timing and error; this
    only maps it to the record the graph stores.
    """
    from core.knowledge.model import PassRecord  # local: keeps agents off core's import path

    records: List[PassRecord] = []
    for o in outcomes:
        state = _outcome_state(o.error, o.empty, o.result)
        records.append(
            PassRecord(
                pass_name=o.pass_name,
                chunk_label=o.chunk.label,
                outcome=state,
                path=o.path,
                elapsed=round(o.elapsed, 1),
                error=(o.error or "")[:200],
                triples_produced=_triples_produced(o.result),
                temperature=o.temperature,
            )
        )
    return records
