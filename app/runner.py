"""
One execution path for a run, shared by the request and the worker (YB-026 phase 2).

WHY THIS MODULE EXISTS
----------------------
The work used to live inside the `/ingest` route body, which is why it could only
happen while somebody was holding an HTTP connection open. Moving it out is not a
copy: a second implementation of "extract, merge, persist, publish the verdict"
would be free to disagree with the first about completeness, provenance or the
version guard — the exact class of drift this codebase keeps refusing to accept.

So the body is here, takes its dependencies **explicitly**, and knows nothing about
Flask. The route calls it synchronously when no job store is configured; the worker
calls it after claiming a job. `request` is never imported, and scope is an
argument — a worker resolving scope from request state would run one product's
document into another product's graph.

THE VERSION GUARD IS THE POINT OF THE APPLY STEP
------------------------------------------------
`before` is re-read here, at the moment of merging, rather than being carried from
before the model call. A background run can take twenty minutes; a reviewer editing
assertions during it must not be silently overwritten by a snapshot taken before
their edit. On a concurrency-safe store a stale write raises `StoreConflict` and
the job fails visibly. On a store that cannot guard, the worker refuses the scope
(see `app/worker.py`) rather than pretending.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from agents.extraction.progress import JournalProgress
from core.events import RunJournal
from core.knowledge import (
    Snapshot,
    graph_from_extraction,
    merge_graphs,
    new_run_id,
)
from core.knowledge.model import (
    SCOPE_BASELINE,
    SOURCE_DESIGN_ASSISTANT,
    ExtractionRun,
    GraphDelta,
    KnowledgeGraph,
    compute_graph_delta,
)

__all__ = [
    "RunFailed",
    "ExtractorLoadFailed",
    "DomainPackFailed",
    "IngestOutcome",
    "DesignOutcome",
    "DesignBaseline",
    "resolve_design_baseline",
    "run_ingest",
    "run_design",
]


class RunFailed(RuntimeError):
    """The agent ran and reported failure — as opposed to raising or crashing.

    Kept distinct from a raised exception because the two are retried differently
    once YB-037 owns retry: "the model said no" is not the same as "the process
    died", and the job record should be able to say which.
    """


class ExtractorLoadFailed(RuntimeError):
    """The extractor could not be constructed. Reported to the user verbatim."""


class DomainPackFailed(RuntimeError):
    """The chosen domain pack could not be bound to the agent.

    Its own type because the message is user-facing and names a fix ("pick a pack
    that exists"), while a generic extraction failure is not actionable that way.
    """


@dataclass
class IngestOutcome:
    filename: str
    doc_type: str
    run_record: ExtractionRun
    added: int
    changed: int
    removed: int
    added_nodes: int

    @property
    def completeness(self) -> str:
        return self.run_record.completeness


@dataclass
class DesignOutcome:
    run: ExtractionRun
    draft: Any
    proposal_graph: KnowledgeGraph
    merged: KnowledgeGraph
    delta: GraphDelta
    output: Dict[str, Any]
    domain_pack: str = ""


def _progress(journal: RunJournal, run_id: str, scope_id: str) -> JournalProgress:
    return JournalProgress(journal, run_id, scope_id=scope_id)


def _select_domain_pack(agent: Any, domain_pack: str) -> str:
    """Bind the pack to the agent and report which one is actually in force.

    Best-effort on the read, exactly as the route was: provenance that cannot be
    read is recorded as absent rather than guessed. A *failed bind*, by contrast, is
    raised as `DomainPackFailed` — it is the difference between "this run used no
    pack" and "this run silently used the wrong vocabulary".
    """
    select = getattr(agent, "use_domain_pack", None)
    if domain_pack and callable(select):
        try:
            select(domain_pack)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            raise DomainPackFailed(f"Could not load the domain pack: {exc}") from exc
    read = getattr(agent, "active_domain_pack_id", None)
    if callable(read):
        try:
            return read() or ""
        except Exception:  # noqa: BLE001 - provenance is best-effort here
            return ""
    return ""


# ============================================================================
# Ingest
# ============================================================================


def run_ingest(
    *,
    store: Any,
    journal: RunJournal,
    scope_id: str,
    document: str,
    filename: str,
    doc_type: str,
    extractor_factory: Callable[[str], Any],
    initiative_id: str = "",
    domain_pack: str = "",
    actor: str = "",
    revision_label: str = "",
    note: str = "",
    document_digest: str = "",
    run_id: Optional[str] = None,
) -> IngestOutcome:
    """Extract one document, merge it into the working set, commit a revision.

    Publishes `run.started` / `run.finished` (or `run.failed`) on the journal. The
    verdict is read from the run record *after* it is persisted — never inferred
    from the stream ending (ADR-0013).
    """
    run_id = run_id or new_run_id()
    progress = _progress(journal, run_id, scope_id)

    try:
        try:
            agent = extractor_factory(doc_type)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            raise ExtractorLoadFailed(
                f"Could not load the {doc_type} extractor: {exc}"
            ) from exc
        active_pack = _select_domain_pack(agent, domain_pack)
        progress.started(
            document_ref=filename, document_type=doc_type, domain_pack=active_pack
        )

        result = agent.run({
            "document": document,
            "document_type": doc_type,
            "domain_pack": active_pack,
            "initiative_id": initiative_id,
            "progress": progress,
        })

        if not result.success:
            raise RunFailed("; ".join(result.errors) or "unknown error")

        output = result.output if isinstance(result.output, dict) else {}
        metadata = dict(result.metadata or {})
        metadata.setdefault("document_type", doc_type)
        metadata["run_id"] = run_id

        # Re-read HERE, not before the model call: this is the version guard.
        before: Snapshot = store.load_working()
        incoming, run_record = graph_from_extraction(
            output,
            metadata=metadata,
            document_ref=filename,
            document_text=document,
            initiative_id=initiative_id,
            # Recorded on every assertion, so the vocabulary a fact was extracted
            # under stays knowable after the Initiative is re-run under another.
            domain_pack=active_pack,
        )
        merged = merge_graphs(before.graph, incoming)
        delta = compute_graph_delta(before.graph, merged)

        meta = dict(before.meta)
        meta["initiative_id"] = initiative_id
        meta["domain_pack"] = active_pack
        meta["last_ingest"] = {
            "document": filename,
            "doc_type": doc_type,
            "run_id": run_record.id,
            "completeness": run_record.completeness,
            "domain_pack": active_pack,
            "nodes": len(merged.nodes) - len(before.graph.nodes),
            "facts": len(delta.added_assertions),
            "changed": len(delta.changed_assertions),
            # The bytes this run actually read. `document_hash` fingerprints the
            # text; this is what a re-run can compare against to skip work.
            "document_digest": document_digest,
        }
        expected = before.meta.get("version") if store.concurrency_safe else None
        store.save_working(merged, before.log, meta, expected_version=expected)

        store.commit(
            merged,
            before.log,
            label=revision_label or f"Ingest · {filename}",
            actor=actor,
            note=note,
            initiative_id=initiative_id,
        )
    except Exception as exc:  # noqa: BLE001 - a dead run still gets a verdict
        progress.failed(exc)
        progress.close()
        raise

    progress.finished(run_record.completeness)
    progress.close()
    return IngestOutcome(
        filename=filename,
        doc_type=doc_type,
        run_record=run_record,
        added=len(delta.added_assertions),
        changed=len(delta.changed_assertions),
        removed=len(delta.removed_assertions),
        added_nodes=len(merged.nodes) - len(before.graph.nodes),
    )


# ============================================================================
# Design
# ============================================================================


@dataclass
class DesignBaseline:
    """Which architecture a design extends, and what to call it.

    `promoted` distinguishes the two kinds of baseline the prompt can be given: a
    frozen revision (`False`) is a snapshot with a frozen-at guarantee, while the
    promoted baseline (`True`) is the live set of facts review has moved into
    `SYSTEM_BASELINE`, which keeps growing. The design says which one it read, so a
    reviewer can tell "extend what we froze" from "extend what we have accepted so
    far".
    """

    graph: Optional[KnowledgeGraph] = None
    ref: str = ""
    label: str = ""
    promoted: bool = False


def resolve_design_baseline(store: Any, graph: KnowledgeGraph) -> DesignBaseline:
    """The architecture a design run should extend, in order of authority.

    Three states, and the middle one is the reason this function exists:

      1. A frozen revision, when one exists. It is a snapshot; nothing else in the
         store carries a frozen-at guarantee.
      2. Otherwise the PROMOTED baseline — the facts review has moved into
         `SYSTEM_BASELINE`. That is the architecture the enterprise has accepted,
         and it is a genuine baseline even though no revision was ever frozen.
         Before this existed the design was handed the working set instead, which
         meant extending a draft nobody had signed off, mixed in with facts still
         under review.
      3. Otherwise nothing, and `design_input` says so rather than pretending the
         working set is a baseline.

    The two callers (the route and the worker) share this so they cannot disagree
    about which graph a submitted job actually reads.
    """
    baselines = store.baselines()
    if baselines:
        revision = baselines[0]
        return DesignBaseline(
            graph=store.load_revision(revision.id).graph,
            ref=revision.id,
            label=revision.label or revision.id,
            promoted=False,
        )
    promoted = graph.scoped(SCOPE_BASELINE)
    if promoted.assertions:
        return DesignBaseline(
            graph=promoted,
            ref=SCOPE_BASELINE,
            label="Promoted baseline (SYSTEM_BASELINE)",
            promoted=True,
        )
    return DesignBaseline()


def run_design(
    *,
    store: Any,
    journal: RunJournal,
    scope_id: str,
    snapshot: Snapshot,
    design_factory: Callable[[], Any],
    drafts: Any,
    baseline: Optional[KnowledgeGraph] = None,
    base_ref: str = "",
    baseline_promoted: bool = False,
    initiative_id: str = "",
    domain_pack: str = "",
    run_id: Optional[str] = None,
) -> DesignOutcome:
    """Draft an architecture proposal. Writes a DRAFT; never touches the working set.

    The input is the graph, not a document (`input_kind=graph`), which is why this
    takes a snapshot rather than bytes. `baseline_promoted` says whether `baseline`
    is a frozen revision or the promoted set of accepted facts; the prompt states
    which, because only one of the two is a snapshot.
    """
    run_id = run_id or new_run_id()
    progress = _progress(journal, run_id, scope_id)
    document_ref = f"design:{initiative_id}@{base_ref or 'working'}"

    try:
        try:
            agent = design_factory()
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            raise ExtractorLoadFailed(f"Could not load the Design Assistant: {exc}") from exc
        active_pack = _select_domain_pack(agent, domain_pack)
        progress.started(
            document_ref=document_ref, document_type="architecture",
            domain_pack=active_pack,
        )

        result = agent.run({
            "graph": snapshot.graph,
            "baseline": baseline,
            "base_ref": base_ref,
            "baseline_promoted": baseline_promoted,
            "initiative_id": initiative_id,
            "domain_pack": active_pack,
            "progress": progress,
        })
        if not result.success:
            raise RunFailed("; ".join(result.errors) or "unknown error")

        output = result.output if isinstance(result.output, dict) else {}
        metadata = dict(result.metadata or {})
        metadata.setdefault("document_type", "architecture")
        metadata["run_id"] = run_id

        proposal_graph, run = graph_from_extraction(
            output,
            metadata=metadata,
            document_ref=document_ref,
            document_text=output.get("design_digest", ""),
            initiative_id=initiative_id,
            domain_pack=active_pack,
            source_type=SOURCE_DESIGN_ASSISTANT,
        )
        # The delta is what APPLYING would change, so it is computed against the
        # merge. Against the raw proposal every fact the proposal does not restate
        # would count as *removed*, which is how a draft would look destructive.
        merged = merge_graphs(snapshot.graph, proposal_graph)
        delta = compute_graph_delta(snapshot.graph, merged)
        draft = drafts.save(
            proposal_graph,
            label=f"Design draft · {initiative_id}",
            initiative_id=initiative_id,
            base_ref=base_ref or "working",
            run_id=run.id,
            completeness=run.completeness,
            domain_pack=active_pack,
            caveats=metadata.get("design_caveats") or [],
            findings=output.get("findings") or [],
            pattern_resolutions=output.get("pattern_resolutions") or [],
            digest=output.get("design_digest", ""),
        )
    except Exception as exc:  # noqa: BLE001
        progress.failed(exc)
        progress.close()
        raise

    progress.finished(run.completeness)
    progress.close()
    return DesignOutcome(
        run=run, draft=draft, proposal_graph=proposal_graph, merged=merged,
        delta=delta, output=output, domain_pack=active_pack,
    )
