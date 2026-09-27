"""
SEA Platform Web Application.

A Flask shell over the canonical knowledge graph. HTMX for interaction, D3 for
the C4 projection, no client-side build step.

WHAT THIS LAYER IS ALLOWED TO DO
--------------------------------
Routes orchestrate: load the working set, call a knowledge-layer operation, save,
project for rendering. They do not implement knowledge logic. Review decisions
live in `core.knowledge.review`, reconciliation in `core.knowledge.reconcile`,
revisions in `core.knowledge.store`, generic rendering in `app.projections`, and
architecture notation in `app.viewpoints`. That separation is what lets the CLI
reviewer and the web gate produce identical graphs from identical decisions.

STATE
-----
The working set is persisted by `RevisionStore`, never held in a module global.
The previous version kept the graph in a process variable, which meant every
review decision was lost on restart and two workers could not agree on what the
graph was. Reads and writes go to disk per request; the graphs here are small
enough (hundreds of assertions) that this is cheaper than the bugs it prevents.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from flask import (
    Flask,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    stream_with_context,
    url_for,
)
from werkzeug.utils import secure_filename

from app.ontology_reference import (
    class_detail,
    class_neighbourhood,
    class_rows,
    enum_rows,
    ontology_overview,
    ontology_payload,
)
from app.projections import (
    LOW_CONFIDENCE,
    ReviewFilters,
    project_assertion,
    project_dashboard,
    project_decisions,
    project_delta,
    project_gap_report,
    project_node_index,
    project_quality_report,
    project_realization,
    project_reconciliation,
    project_review_rows,
    project_review_summary,
)
from app.viewpoints.merged import DEFAULT_LENS, merged_view
from core.knowledge import (
    DEFAULT_MATCH_THRESHOLD,
    BaselineNotReady,
    DesignDraftStore,
    KnowledgeGraph,
    ReconcileError,
    ReviewError,
    RevisionStore,
    Snapshot,
    apply_decisions,
    bulk_resolve,
    bulk_verify,
    merge_graphs,
    new_run_id,
    promote_to_baseline,
    resolve_reference,
    review_progress,
    to_turtle,
)
from core.knowledge.model import compute_graph_delta
# The run journal. `core.events` imports its client lazily, so this costs nothing
# when the deployment does not run Valkey.
from core.events import NullJournal, RunEvent
# The execution substrate: the job record, the bytes a job reads, and the one code
# path a run takes whether a request or the worker drives it.
from core.artifacts import ArtifactStore
from core.jobs import (
    GRAPH,
    INLINE,
    QUEUED,
    RUNNING,
    TRIGGER_UI,
    Job,
    SqliteJobStore,
    new_job_id,
)
from app.runner import (
    DomainPackFailed,
    ExtractorLoadFailed,
    RunFailed,
    run_design,
    run_ingest,
)
# The store contract and the workspace that addresses scopes. `StoreConflict` is
# imported here because a lost update must surface as a message, not a 500.
from core.knowledge import StoreConflict
from core.workspace import (
    Scope,
    WorkspaceError,
    backend_is_concurrency_safe,
    load_workspace,
    scope_data_dir,
    scope_drafts_dir,
)
from core.ontology import (
    OntologyError,
    discover_domain_packs,
    load_ontology,
    pack_for_graph,
)

# The deployment's own settings live in `.env` (`SEA_DATA_DIR`, `SEA_REVIEWER`,
# `SEA_HOST`, ...). The agents already load it through `config.agent_config`, but the
# web app must load it EARLIER and for itself: `create_app` resolves `STORE_ROOT` and
# reads the workspace manifest before any agent is constructed, so a `.env` loaded
# later is one that never applied to where the data lives — the app quietly falls
# back to `data/sea` while the operator's `.env` says otherwise.


def load_env(path: Optional[str] = None) -> None:
    """Populate `os.environ` from `.env`, without overriding a real variable.

    `override=False` is what keeps `SEA_DATA_DIR=data/other uv run sea-app` meaning
    what it says. `path=None` lets python-dotenv discover the file (it walks up from
    this package, so the app finds the repository's `.env` from any working
    directory); passing a path is for tests. A missing `python-dotenv` is not fatal:
    the app falls back to its defaults exactly as it did before this existed.
    """
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=path, override=False)
    except ImportError:  # pragma: no cover - python-dotenv is a declared dependency
        pass


load_env()

DEFAULT_STORE_ROOT = "data/sea"
MAX_UPLOAD_BYTES = 4 * 1024 * 1024
#: How long a `RUNNING` job's silence is worth remarking on. It is not a timeout and
#: nothing acts on it: recovery is YB-037's, and the honest thing this layer can do
#: is say "no worker has been seen for N minutes" instead of showing a spinner.
STALE_AFTER_SECONDS = 120

# -- server-sent progress (phase 3b) -----------------------------------------
#
# A held connection is a resource, and the failure mode of an unbounded one is an
# accidental load test: a browser retries an SSE stream every few seconds by itself,
# so a handful of stale tabs against a dead or aged-out journal is a lot of requests.
# Hence a cap on concurrent streams and a cap on how long one lives.
SSE_MAX_CONNECTIONS = 8
SSE_MAX_SECONDS = 30 * 60
SSE_WAIT_MS = 1000
SSE_HEARTBEAT_SECONDS = 15
_SSE_ACTIVE = [0]
"""Live stream count for this process. A list because a closure needs to mutate
something; the GIL makes the increment safe enough for a bound that only needs to be
approximately right."""

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # Tell a reverse proxy not to buffer the stream, which would defeat the point.
    "X-Accel-Buffering": "no",
}


def _sse_frame(event_id: str, payload: Any) -> str:
    """One SSE frame. JSON-encoded so multi-line HTML cannot break the framing."""
    prefix = f"id: {event_id}\n" if event_id else ""
    return f"{prefix}data: {json.dumps(payload)}\n\n"


def _last_stream_id(events: List[Any]) -> str:
    for event in reversed(events):
        if getattr(event, "stream_id", ""):
            return event.stream_id
    return ""


def _age_seconds(stamp: str) -> Optional[int]:
    """Seconds since an ISO-8601 `Z` timestamp, or None when absent/unreadable.

    Display-only, and tolerant on purpose: a page must not 500 because a clock
    string was surprising. Missing is reported as missing, never as zero — "no
    heartbeat" and "a heartbeat just now" are opposite facts.
    """
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, int((datetime.now(timezone.utc) - when).total_seconds()))

# How many concepts the map's browse table renders. High enough that it is not a
# filter in disguise, low enough that one page cannot grow without bound; the
# template reports the remainder when it bites. Server-side filtering is the next
# step if a graph ever exceeds this.
MAP_CONCEPT_ROWS = 2000


# ============================================================================
# Extraction strategy — injectable so the app is testable without an LLM
# ============================================================================


def _default_extractor(doc_type: str):
    """Resolve the real agent for a document type.

    Imported lazily: pulling in the extraction agents drags in Strands and the
    model client, which a test or an offline deployment should not need.
    """
    if doc_type == "architecture":
        from agents.architecture_extraction import create_architecture_extraction_agent

        return create_architecture_extraction_agent()
    from agents.knowledge_extraction.agent import create_knowledge_extraction_agent

    return create_knowledge_extraction_agent()


def _default_design_agent():
    """The real Design Assistant. Lazily imported for the same reason as above.

    A factory with NO document-type argument, because this profile's input is the
    graph, not a file — routing it through `_default_extractor` would imply a
    document it does not read.
    """
    from agents.design_assistant import create_design_assistant_agent

    return create_design_assistant_agent()


# ============================================================================
# App factory
# ============================================================================


def _default_journal():
    """The run journal this deployment reports progress through.

    `NullJournal` unless a Valkey endpoint is configured, so an MVP install with no
    stream server behaves exactly as before — runs simply report nothing live, which is
    also what an unattended run looked like anyway.

    A configured endpoint whose client library is missing degrades the same way
    rather than stopping the app. The journal is a notification channel: the module
    contract is that losing it degrades *live progress only*, so an install that
    sets `SEA_VALKEY_HOST` before installing `redis`/`valkey` must still boot. The
    failure is recorded, not swallowed, because silently reporting nothing is what
    this fallback exists to make visible.
    """
    host = os.environ.get("SEA_VALKEY_HOST", "")
    if not host:
        return NullJournal()
    try:
        from core.events import ValkeyJournal

        return ValkeyJournal(
            host=host, port=int(os.environ.get("SEA_VALKEY_PORT", "6379"))
        )
    except Exception as exc:  # noqa: BLE001 - a dead journal must not stop the app
        import logging

        logging.getLogger(__name__).warning(
            "run journal configured at %s but unavailable (%s: %s); "
            "live progress is disabled, runs are unaffected",
            host, type(exc).__name__, exc,
        )
        return NullJournal()


def create_app(
    test_config: Optional[Dict[str, Any]] = None,
    store_root: Optional[str] = None,
    extractor_factory: Optional[Callable[[str], Any]] = None,
    design_factory: Optional[Callable[[], Any]] = None,
    journal_factory: Optional[Callable[[], Any]] = None,
    job_store_factory: Optional[Callable[[str], Any]] = None,
) -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")

    root = store_root or os.environ.get("SEA_DATA_DIR") or DEFAULT_STORE_ROOT
    app.config.from_mapping(
        STORE_ROOT=root,
        SECRET_KEY=os.environ.get("SEA_SECRET_KEY", "sea-platform-dev-key"),
        REVIEWER=os.environ.get("SEA_REVIEWER", "architect"),
        MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
        EXTRACTOR_FACTORY=extractor_factory or _default_extractor,
        DESIGN_FACTORY=design_factory or _default_design_agent,
        JOURNAL_FACTORY=journal_factory or _default_journal,
        JOB_STORE_FACTORY=job_store_factory,
        INITIATIVE_ID=os.environ.get("SEA_INITIATIVE", "INIT-MVP-001"),
        ONTOLOGY_DIR=os.environ.get("SEA_ONTOLOGY_DIR", "ontology"),
        SCOPE_ID=os.environ.get("SEA_SCOPE", ""),
    )
    if test_config:
        app.config.update(test_config)

    # A workspace lists the scopes (systems/products) this deployment serves. A bare
    # store directory is a single-scope workspace pointing at itself, so every existing
    # `data/sea` keeps working with no manifest — see `core.workspace`.
    workspace = load_workspace(app.config["STORE_ROOT"])
    app.config["WORKSPACE"] = workspace
    if not app.config.get("SCOPE_ID") and len(workspace.scopes) == 1:
        app.config["SCOPE_ID"] = workspace.scopes[0].scope_id

    # One store per scope, built once and reused. Building a SQL engine per request
    # would open a pool per page view, and the engine is the expensive part.
    stores: Dict[str, Any] = {}
    drafts_by_scope: Dict[str, DesignDraftStore] = {}

    def current_scope_id() -> str:
        """Which scope this request is about.

        The single-scope case needs no selection, which is what keeps the MVP path —
        and every existing deployment — unchanged. A multi-scope workspace with no
        selection falls back to the first scope rather than erroring on every route;
        `inject_globals` exposes which one, so the page can say so instead of the app
        silently reviewing the wrong world.
        """
        chosen = request.args.get("scope") or session.get("scope_id")
        if chosen:
            return chosen
        configured = current_app.config.get("SCOPE_ID") or ""
        if configured:
            return configured
        if workspace.scopes:
            return workspace.scopes[0].scope_id
        return ""

    def current_scope() -> Optional[Scope]:
        try:
            return workspace.scope(current_scope_id() or None)
        except WorkspaceError:
            return None

    def current_store():
        scope_id = current_scope_id()
        if scope_id not in stores:
            try:
                stores[scope_id] = workspace.open_store(scope_id or None)
            except WorkspaceError as exc:
                abort(404, description=str(exc))
        return stores[scope_id]

    journal = app.config["JOURNAL_FACTORY"]()

    def current_journal():
        """The journal is built once per process, not per request: a Valkey client
        holds a connection pool, and rebuilding it per page view would defeat it."""
        return journal

    # -- the execution substrate, per scope ------------------------------
    #
    # Both are built once per process and cached, like the graph stores: a SQLite
    # connection and a directory are cheap to hold and expensive to rebuild per
    # request.
    job_stores: Dict[str, Any] = {}
    artifacts_by_scope: Dict[str, ArtifactStore] = {}

    def artifact_store() -> ArtifactStore:
        """Where this scope's input bytes live. Content-addressed, raw bytes only."""
        scope_id = current_scope_id()
        if scope_id not in artifacts_by_scope:
            artifacts_by_scope[scope_id] = ArtifactStore(
                scope_data_dir(workspace, current_scope()) / "artifacts"
            )
        return artifacts_by_scope[scope_id]

    def current_job_store():
        """The job store for this scope, or None when it cannot run a worker.

        A worker needs two things from storage: an atomic claim, and an apply step
        that can refuse a stale write. A backend that offers neither would give a
        duplicate-worker race and a silent clobber, so for those scopes the
        substrate is simply absent and the route runs the work inline — exactly the
        behaviour every existing install (and every test) already depends on. That
        is the same "degrade, do not pretend" rule the journal follows.
        """
        scope_id = current_scope_id()
        if scope_id in job_stores:
            return job_stores[scope_id]
        store = None
        factory = app.config.get("JOB_STORE_FACTORY")
        scope = current_scope()
        try:
            if factory is not None:
                store = factory(scope_id)
            elif scope is not None and backend_is_concurrency_safe(scope.backend):
                store = SqliteJobStore(scope_data_dir(workspace, scope) / "jobs.sqlite")
            if store is not None and not getattr(store, "concurrency_safe", False):
                store = None
        except Exception as exc:  # noqa: BLE001 - an unavailable queue is not fatal
            app.logger.warning("job store unavailable for scope %s: %s", scope_id, exc)
            store = None
        job_stores[scope_id] = store
        return store

    def current_drafts() -> DesignDraftStore:
        """Drafts are staged per scope, so a proposal drafted against one system can
        never be applied to another."""
        scope_id = current_scope_id()
        if scope_id not in drafts_by_scope:
            base = scope_drafts_dir(workspace, current_scope())
            drafts_by_scope[scope_id] = DesignDraftStore(base).ensure()
        return drafts_by_scope[scope_id]

    # Design proposals are staged beside the working set, never merged into it
    # until a human applies them. See `core.knowledge.drafts` for why.

    # The ontologies are read once, at startup. Parse failure is recorded rather
    # than raised: the reference page can then say what is wrong, instead of the
    # whole app — ingest, review, reconcile — refusing to boot over a bad path.
    try:
        app.config["ONTOLOGY"] = load_ontology(app.config["ONTOLOGY_DIR"])
        app.config["ONTOLOGY_ERROR"] = ""
    except OntologyError as exc:
        app.config["ONTOLOGY"] = None
        app.config["ONTOLOGY_ERROR"] = str(exc)

    # -- helpers ---------------------------------------------------------

    def state() -> Snapshot:
        return current_store().load_working()

    def save(snapshot: Snapshot) -> None:
        """Persist the working set, guarded where the backend can guard.

        `meta["version"]` is what the read returned. Passing it back is what turns a
        silent lost update into a visible conflict: on a concurrency-safe backend a
        stale version raises `StoreConflict`, which the error handler below reports.
        The file backend carries no version and declares `concurrency_safe = False`,
        so the guard is simply absent there rather than pretended.
        """
        store = current_store()
        version = snapshot.meta.get("version") if store.concurrency_safe else None
        store.save_working(snapshot.graph, snapshot.log, snapshot.meta,
                           expected_version=version)

    def reviewer() -> str:
        return (
            request.form.get("actor") or request.args.get("actor") or current_app.config["REVIEWER"]
        )

    @app.errorhandler(StoreConflict)
    def _scope_changed(exc):
        """A write lost the race. The caller's work is NOT applied — say so plainly
        and send them back, because "my edit vanished" is the failure this replaced."""
        flash(
            "Someone else changed this scope while you were working, so your change "
            "was not applied — nothing was overwritten. Reload and reapply it.",
            "error",
        )
        return redirect(request.referrer or url_for("index"))

    @app.context_processor
    def inject_globals():
        scope = current_scope()
        return {
            "low_confidence_threshold": LOW_CONFIDENCE,
            "reviewer": current_app.config["REVIEWER"],
            "initiative_id": current_app.config["INITIATIVE_ID"],
            # Which world this page is showing. A multi-scope workspace renders the
            # wrong scope silently without this, which is the whole hazard.
            "workspace": workspace,
            "scope_id": scope.scope_id if scope else "",
            "scope_name": scope.name if scope else "",
            "scopes": workspace.scopes,
        }

    @app.template_filter("pct")
    def pct(value):
        try:
            return f"{float(value) * 100:.0f}%"
        except (TypeError, ValueError):
            return "—"

    # -- workspace -------------------------------------------------------

    @app.route("/api/workspace")
    def api_workspace():
        """What this deployment serves, and which scope the caller is looking at.

        The page needs this to render a switcher, and an operator needs it to see
        whether a store is still on the file backend or has been migrated.
        """
        current = current_scope()
        return jsonify({
            "workspace_id": workspace.workspace_id,
            "name": workspace.name,
            "brief": workspace.brief,
            "current_scope": current.scope_id if current else "",
            "scopes": [
                {
                    "scope_id": scope.scope_id,
                    "name": scope.name,
                    "kind": scope.kind,
                    "backend": scope.backend,
                    # Asked of the backend, not of an instance: opening a store from a
                    # listing endpoint would create engines (and databases) as a side
                    # effect of asking a question.
                    "concurrency_safe": backend_is_concurrency_safe(scope.backend),
                }
                for scope in workspace.scopes
            ],
        })

    @app.route("/scope/<scope_id>")
    def select_scope(scope_id):
        """Switch scope for this session.

        Validated against the workspace first, so a typo cannot silently create an
        empty store and look like a scope whose work has vanished.
        """
        try:
            workspace.scope(scope_id)
        except WorkspaceError as exc:
            abort(404, description=str(exc))
        session["scope_id"] = scope_id
        return redirect(request.args.get("next") or url_for("index"))

    @app.route("/api/runs/<run_id>/events")
    def api_run_events(run_id):
        """The run's progress events, from `since` onward.

        A read of the journal, not the record: the graph stays the source of truth, and
        this is what a page tails (or polls) to show a run in flight. `since` is an
        exclusive stream id, so a client that reconnects passes the last id it saw and
        receives exactly what it missed — the property that makes this a Stream rather
        than a pub/sub fire-and-forget.
        """
        since = request.args.get("since") or None

        # Scope discipline, not authentication. When this scope has a job store the
        # run must belong to it; an unknown run id 404s rather than answering 200
        # with an empty list, which reads as "nothing has happened yet" instead of
        # "there is no such run". A file-backed scope has no jobs to check against
        # (its runs are synchronous), so the journal is read as before.
        job_store = current_job_store()
        job = None
        if job_store is not None:
            job = job_store.find_by_run(run_id, current_scope_id())
            if job is None:
                return jsonify({"run_id": run_id, "error": "no such run in this scope",
                                "events": []}), 404

        try:
            all_events = current_journal().read(run_id)
        except Exception as exc:  # noqa: BLE001 - a dead journal must not 500 the page
            return jsonify({"run_id": run_id, "since": since, "events": [],
                            "cursor": since,
                            "error": f"journal unavailable: {type(exc).__name__}"}), 503

        # Terminal is a property of the RUN, not of the slice the cursor asked for.
        # Computed from the whole log (and the job record) so a client that has
        # already consumed the terminal event is still told to stop.
        terminal = (job.is_terminal if job is not None else False) or any(
            event.is_terminal for event in all_events
        )

        # The slice is taken here rather than by the journal so `terminal` above can
        # see the whole run; `since` is an event's `stream_id` and the log is ordered.
        events = all_events
        if since:
            index = next(
                (i for i, event in enumerate(all_events) if event.stream_id == since),
                None,
            )
            events = all_events[index + 1:] if index is not None else all_events

        # `cursor` is the value to send back as `since` next time: the last stream id
        # this response carried. Without it a polling client cannot advance, and
        # "replay" degrades to "start over".
        cursor = events[-1].stream_id if events and events[-1].stream_id else since
        return jsonify({
            "run_id": run_id,
            "since": since,
            "cursor": cursor,
            "events": [event.to_dict() for event in events],
            "terminal": terminal,
        })

    # -- the run page ----------------------------------------------------

    def _resolve_job(job_id: Optional[str] = None, run_id: Optional[str] = None):
        """The scope's job for a job id OR a run id, or 404.

        Scope discipline, not authentication: the store is resolved for the scope
        this request is about, so an id from another product simply is not found. An
        unknown id 404s rather than rendering an empty page that looks like a run
        with no progress.

        Two keys because the two sides address the run differently: the queue is
        keyed by job id, while the journal — and therefore `Last-Event-ID` on a
        reconnect — is keyed by run id.
        """
        store = current_job_store()
        if store is None:
            abort(404, description="this scope has no job store, so it has no runs")
        if job_id:
            job = store.get(job_id)
        else:
            job = store.find_by_run(run_id or "", current_scope_id())
        if job is None or (job.scope_id and job.scope_id != current_scope_id()):
            abort(404, description=f"no run {job_id or run_id}")
        return store, job

    def _job_view(job_id: str) -> Dict[str, Any]:
        """Everything the run page and its polling partial render."""
        store, job = _resolve_job(job_id=job_id)
        return _job_context(store, job)

    def _job_view_by_run(run_id: str) -> Dict[str, Any]:
        store, job = _resolve_job(run_id=run_id)
        return _job_context(store, job)

    def _job_context(store: Any, job: Any) -> Dict[str, Any]:
        """The run's state, rendered from the record plus whatever the journal has.

        Re-read on every push, because it is the *current* state of the run that the
        page shows, not a delta: one representation, so a reconnecting or
        late-arriving viewer and a live one cannot disagree.
        """
        journal_error = ""
        events: List[RunEvent] = []
        try:
            events = current_journal().read(job.run_id)
        except Exception as exc:  # noqa: BLE001 - no live progress is not a failure
            journal_error = f"{type(exc).__name__}"

        # A job is terminal when the JOB says so, not only when a terminal event
        # arrived: the default deployment has no journal at all (`NullJournal`), so
        # "any terminal event" would leave a finished run polling forever.
        terminal = job.is_terminal or any(event.is_terminal for event in events)

        # The verdict's home is the run RECORD, and where that lives depends on the
        # kind: an ingest run's record is in the working graph, a design run's is
        # written with its draft because a proposal never touches the working set.
        # The terminal journal event is published FROM the record, so it is a
        # faithful last resort; `""` means "no verdict yet", which is not the same
        # as a failed run and must not be rendered as one.
        completeness = ""
        record = state().graph.runs.get(job.run_id)
        if record is not None:
            completeness = record.completeness
        elif job.kind == "design":
            draft = next(
                (d for d in current_drafts().list() if d.run_id == job.run_id), None
            )
            if draft is not None:
                completeness = draft.completeness
        if not completeness and terminal and events:
            completeness = events[-1].completeness

        stale = _age_seconds(job.worker_heartbeat_at) if job.state == "RUNNING" else None
        queued_for = _age_seconds(job.created_at) if job.state == "QUEUED" else None
        # The template renders a compact one-line detail per event rather than the
        # raw payload: the payload is a closed set of counters and labels, and
        # formatting it here keeps the view free of presentation logic that would
        # drift between the page and the polling fragment.
        event_rows = [
            {
                "seq": event.seq,
                "at": event.at,
                "kind": event.kind,
                "detail": ", ".join(
                    f"{key}={value}"
                    for key, value in sorted(event.payload.items())
                    if value not in ("", None)
                ),
                "terminal": event.is_terminal,
            }
            for event in events
        ]
        return {
            "job": job,
            "events": events,
            "event_rows": event_rows,
            "terminal": terminal,
            "completeness": completeness,
            "journal_error": journal_error,
            "position": store.queue_position(job.job_id) if hasattr(store, "queue_position") else 0,
            "queue_depth": store.queue_depth() if hasattr(store, "queue_depth") else 0,
            "stale_seconds": stale,
            "worker_stale": stale is not None and stale >= STALE_AFTER_SECONDS,
            "queued_seconds": queued_for,
            "queue_stalled": queued_for is not None and queued_for >= STALE_AFTER_SECONDS,
            "age_seconds": _age_seconds(job.created_at),
            # Which transport this page should use, decided here rather than in the
            # template: push needs a journal that retains events, and there is
            # nothing to watch once the run is over. With no journal (the default
            # install) the page polls instead — the fragment reads job state from
            # the store, so it still works.
            "stream_url": (
                url_for("run_stream", run_id=job.run_id)
                if getattr(current_journal(), "retains", False) and not terminal
                else ""
            ),
            "poll_url": (
                url_for("run_events_partial", job_id=job.job_id) if not terminal else ""
            ),
        }

    @app.route("/runs/<job_id>")
    def run_view(job_id):
        """The page a submitter is sent to instead of a blank wait.

        Since the run no longer happens inside their request, the page has to say
        what is happening: where the job is in the queue, whether a worker has been
        seen recently, and what the run has done so far.
        """
        return render_template("run.html", **_job_view(job_id))

    @app.route("/api/runs/<run_id>/stream")
    def run_stream(run_id):
        """Phase 3b: the run's progress as Server-Sent Events.

        SSE rather than a WebSocket because progress is one-way: it reconnects by
        itself, `Last-Event-ID` gives the resume cursor for free (phase 1b), and it
        adds no new server capability. The frame payload is the *rendered fragment*,
        not a second JSON representation of it, so the pushed view and the polling
        view cannot drift apart.

        Two things keep this from becoming an accidental load test:
        `SSE_MAX_CONNECTIONS` bounds how many held connections one process serves,
        and `SSE_MAX_SECONDS` bounds how long any one of them lives — a browser
        whose run's events have aged out of the journal's TTL would otherwise retry
        forever. Both close cleanly, and `EventSource` resumes with the cursor.
        """
        _resolve_job(run_id=run_id)  # 404s an unknown or other-scope run

        if _SSE_ACTIVE[0] >= SSE_MAX_CONNECTIONS:
            return (
                jsonify({"run_id": run_id,
                         "error": "too many live progress streams open"}),
                503,
            )

        # `once` makes the stream testable and scriptable: one frame, then close.
        # Without it a test client would block forever on a generator that only ends
        # when the run does.
        once = request.args.get("once") in ("1", "true", "yes")
        journal = current_journal()
        since = request.headers.get("Last-Event-ID") or request.args.get("since") or None

        if not getattr(journal, "retains", False):
            # No stream to tail. Say so once and close, rather than holding a
            # connection that can never produce anything.
            return Response(
                iter([_sse_frame("", {"html": render_template(
                    "partials/run_events.html", **_job_context(*_resolve_job(run_id=run_id))
                ), "terminal": True, "reason": "no journal configured"})]),
                mimetype="text/event-stream",
                headers=_SSE_HEADERS,
            )

        def frames():
            _SSE_ACTIVE[0] += 1
            started = time.monotonic()
            last_key = None
            last_beat = time.monotonic()
            try:
                while True:
                    context = _job_context(*_resolve_job(run_id=run_id))
                    cursor = _last_stream_id(context["events"]) or since or ""
                    # Push only when something actually changed. A run can be silent
                    # for minutes inside one model call; re-sending an identical
                    # fragment every 15 s would be noise, so the heartbeat covers the
                    # silence and the fragment covers the change.
                    key = (cursor, context["job"].state, context["completeness"])
                    if key != last_key:
                        last_key = key
                        html = render_template("partials/run_events.html", **context)
                        yield _sse_frame(cursor, {
                            "html": html,
                            "terminal": bool(context["terminal"]),
                            "state": context["job"].state,
                            "completeness": context["completeness"],
                        })
                        if context["terminal"] or once:
                            return
                        last_beat = time.monotonic()
                    if once:
                        return
                    if time.monotonic() - started > SSE_MAX_SECONDS:
                        # Close rather than retrying forever; the browser reconnects
                        # with its cursor if it still cares.
                        yield ": stream closed after the maximum duration\n\n"
                        return
                    new_events = journal.wait(run_id, since=cursor or None,
                                              timeout_ms=SSE_WAIT_MS)
                    if not new_events and time.monotonic() - last_beat >= SSE_HEARTBEAT_SECONDS:
                        # A comment frame: keeps an idle proxy from severing the
                        # connection without pretending anything happened.
                        yield ": keep-alive\n\n"
                        last_beat = time.monotonic()
            finally:
                _SSE_ACTIVE[0] -= 1

        return Response(
            stream_with_context(frames()),
            mimetype="text/event-stream",
            headers=_SSE_HEADERS,
        )

    @app.route("/runs/<job_id>/events.html")
    def run_events_partial(job_id):
        """The polling fragment — the fallback transport.

        Used when no journal retains events (the default install) or when the run is
        already over. HTMX swaps this over itself every couple of seconds; the
        wrapper stops carrying the polling attributes once the run is terminal.
        """
        return render_template("partials/run_events_poll.html", **_job_view(job_id))

    def jobs_panel(limit: int = 8) -> Dict[str, Any]:
        """The queued/recent runs for this scope, and whether it can run them at all.

        Shown on the ingest page because a background run is otherwise reachable only
        through the redirect that created it — navigate away, or come back later, and
        there is no route to a run you started. It also says *why* a scope is
        synchronous instead of leaving an inert async path looking like a bug.
        """
        store = current_job_store()
        if store is None:
            scope = current_scope()
            backend = scope.backend if scope else "file"
            return {
                "jobs": [],
                "jobs_enabled": False,
                "jobs_running": 0,
                "jobs_note": (
                    f"This scope uses the {backend} store, which cannot guard a "
                    f"concurrent write, so extractions run inside this request and the "
                    f"wait is as long as the model takes. A scope declared with "
                    f"`backend: sqlite` runs them in a background worker instead."
                ),
            }
        scope_id = current_scope_id()
        rows = [
            {
                "job": job,
                "queued_seconds": _age_seconds(job.created_at) if job.state == QUEUED else None,
            }
            for job in store.list(scope_id, limit=limit)
        ]
        running = store.counts(scope_id).get(RUNNING, 0)
        return {
            "jobs": rows,
            "jobs_enabled": True,
            "jobs_running": running,
            "jobs_note": (
                "Extractions for this scope are queued and run by a background "
                "worker, so submitting one returns immediately and the result lands "
                "whether or not this page stays open."
                + ("" if running else " Nothing is running now — start a worker with "
                                      "`uv run sea-worker` if the queue is not moving.")
            ),
        }

    @app.route("/workspace")
    def workspace_view():
        """Every scope this deployment serves, and what is known about each.

        Deliberately NOT loading each scope's graph. Listing 200 products would mean
        200 loads — the thing `workspace-structure.md` §6 says the workspace index
        exists to avoid — so what is shown here comes from the manifest and costs
        nothing. Baseline and review state per scope land with that index (YB-042).
        """
        current = current_scope()
        return render_template(
            "workspace.html",
            rows=[
                {
                    "scope_id": scope.scope_id,
                    "name": scope.name,
                    "kind": scope.kind,
                    "backend": scope.backend,
                    "concurrency_safe": backend_is_concurrency_safe(scope.backend),
                    "current": bool(current and scope.scope_id == current.scope_id),
                }
                for scope in workspace.scopes
            ],
            current=current,
        )

    # -- dashboard -------------------------------------------------------

    @app.route("/")
    def index():
        snapshot = state()
        return render_template(
            "dashboard.html",
            view=project_dashboard(
                snapshot.graph, snapshot.log, current_store().list_revisions(), snapshot.meta
            ),
        )

    # -- ingest ----------------------------------------------------------

    @app.route("/ingest", methods=["GET", "POST"])
    def ingest():
        if request.method == "GET":
            return render_template(
                "ingest.html",
                runs=_run_summaries(state().graph),
                # The domain pack picker: the Architect/BA's per-Initiative choice.
                # Listing is deliberately separate from loading, so one malformed
                # pack cannot take the whole form down with it.
                domain_packs=discover_domain_packs(current_app.config["ONTOLOGY_DIR"]),
                active_domain_pack=state().meta.get("domain_pack", ""),
                # Queued and recent runs, plus whether this scope runs them in the
                # background at all.
                **jobs_panel(),
            )

        uploaded = request.files.get("document")
        text = (request.form.get("text") or "").strip()
        filename = ""
        # The bytes as received, kept alongside the decoded text because the
        # artifact store addresses BYTES. Re-encoding the decoded text would make
        # the digest a hash of a lossy transform (`errors="replace"`), so two
        # different documents could collide and a Latin-1 upload would lose every
        # non-ASCII character before it was ever stored.
        raw_bytes = b""

        if uploaded and uploaded.filename:
            filename = secure_filename(uploaded.filename)
            raw_bytes = uploaded.read()
            text = raw_bytes.decode("utf-8", errors="replace")
        elif not text:
            flash("Provide a Markdown file or paste document text.", "error")
            return redirect(url_for("ingest"))
        else:
            filename = (request.form.get("text_name") or "pasted-document.md").strip()
            # A paste has no bytes of its own; UTF-8 is the honest encoding of it.
            raw_bytes = text.encode("utf-8")

        doc_type = (request.form.get("type") or "requirements").strip()
        initiative_id = (
            request.form.get("initiative_id") or current_app.config["INITIATIVE_ID"]
        ).strip()
        # The domain pack is the Architect/BA's per-Initiative choice, made here.
        # It reaches the extractor through `use_domain_pack` rather than through
        # `input_data`, because the vocabulary is compiled into the system prompt
        # when the agent is constructed — passing it as data would be the same
        # inert-parameter mistake the old hard-coded `domain` field made.
        domain_pack = (request.form.get("domain_pack") or "").strip()
        actor = reviewer()

        # WHERE THE WORK GOES. With a job store the request only *records* the work
        # and returns; the worker runs it. Without one — the file-backed MVP, and
        # every test that injects a fake extractor — it runs here, exactly as
        # before. Both paths publish the same journal, so this is a fallback, not a
        # second mechanism.
        digest = ""
        job_store = current_job_store()
        if job_store is not None:
            try:
                digest = artifact_store().put(raw_bytes)
                job = job_store.enqueue(Job(
                    job_id=new_job_id(),
                    run_id=new_run_id(),
                    scope_id=current_scope_id(),
                    kind="ingest",
                    trigger=TRIGGER_UI,
                    input_kind=INLINE,
                    input={"artifact": digest},
                    parameters={
                        "document_type": doc_type,
                        "initiative_id": initiative_id,
                        "domain_pack": domain_pack,
                        "filename": filename,
                        "revision_label": request.form.get("revision_label") or "",
                        "note": request.form.get("note", ""),
                    },
                    actor=actor,
                ))
            except Exception as exc:  # noqa: BLE001 - fall back to running it here
                flash(
                    f"Could not queue {filename} ({exc}); running it in this request "
                    f"instead.", "warning",
                )
            else:
                flash(
                    f"Queued {filename} ({doc_type}) as {job.job_id} — the worker "
                    f"will extract it. Nothing is written until it runs.", "success",
                )
                # A queue with no consumer is the failure this layer introduces, and
                # it is silent by nature. The cheapest honest signal is the oldest
                # waiting job: if something has been queued longer than a worker
                # would plausibly take to notice, say so now rather than letting the
                # page look busy forever.
                try:
                    waiting = job_store.list(current_scope_id(), states=[QUEUED])
                    oldest = max(
                        (_age_seconds(other.created_at) or 0) for other in waiting
                    )
                    if oldest >= STALE_AFTER_SECONDS:
                        flash(
                            f"No worker appears to be running — the oldest queued job "
                            f"has waited {oldest}s. Start one with "
                            f"`uv run sea-worker`.", "warning",
                        )
                except Exception as exc:  # noqa: BLE001 - a hint is not worth failing over
                    # Logged, not swallowed: a broken hint is how a real queue-depth
                    # bug hides (it did once during development).
                    app.logger.warning("could not compute the queue-age hint: %s", exc)
                return redirect(url_for("run_view", job_id=job.job_id))

        try:
            outcome = run_ingest(
                store=current_store(),
                journal=current_journal(),
                scope_id=current_scope_id(),
                document=text,
                filename=filename,
                doc_type=doc_type,
                extractor_factory=current_app.config["EXTRACTOR_FACTORY"],
                initiative_id=initiative_id,
                domain_pack=domain_pack,
                actor=actor,
                revision_label=request.form.get("revision_label") or "",
                note=request.form.get("note", ""),
                document_digest=digest,
            )
        except (ExtractorLoadFailed, DomainPackFailed) as exc:
            flash(str(exc), "error")
            return redirect(url_for("ingest"))
        except RunFailed as exc:
            flash(f"Extraction reported failure: {exc}", "error")
            return redirect(url_for("ingest"))
        except StoreConflict:
            # A lost update is a *known* outcome with a handler that explains it
            # ("nothing was overwritten") — re-raised so it reaches that handler
            # instead of being flattened into "Extraction failed".
            raise
        except Exception as exc:  # noqa: BLE001
            flash(f"Extraction failed: {exc}", "error")
            return redirect(url_for("ingest"))

        flash(
            f"{outcome.filename} ({outcome.doc_type}) → {outcome.completeness}: "
            f"+{outcome.added} facts, {outcome.changed} changed"
            + (f", −{outcome.removed} removed" if outcome.removed else ""),
            "success",
        )
        if outcome.completeness != "COMPLETE":
            flash(
                "Extraction was not COMPLETE — absence of a fact is NOT evidence "
                "of its absence. Review before auditing.",
                "warning",
            )
        return redirect(url_for("review"))

    # -- design assistant ------------------------------------------------

    def _design_inputs() -> Dict[str, Any]:
        """Which graph a design run reads, and what it should be grounded on."""
        snapshot = state()
        baselines = current_store().baselines()
        return {
            "snapshot": snapshot,
            # The frozen baseline when one exists: a design should extend the
            # architecture the enterprise has accepted, not the draft in progress.
            "baseline": current_store().load_revision(baselines[0].id).graph if baselines else None,
            "base_ref": baselines[0].id if baselines else "",
            "baseline_label": baselines[0].label if baselines else "",
        }

    def _design_preconditions(snapshot: Snapshot) -> Dict[str, Any]:
        """What a reviewer needs to know BEFORE starting a design run."""
        graph = snapshot.graph
        gaps = project_gap_report(graph)
        quality = project_quality_report(graph)["summary"]
        baselines = current_store().baselines()
        return {
            "requirements": gaps["realization"]["summary"]["requirements"],
            "unrealized": gaps["unrealized_count"],
            "quality_concerns": quality["attributes"],
            "architecture_gaps": quality["architecture_gaps"],
            "completeness": gaps["completeness"],
            "completeness_note": gaps["completeness_note"],
            "is_auditable": gaps["is_auditable"],
            "baseline": baselines[0] if baselines else None,
            "blocking": (
                []
                if graph.nodes
                else ["The working set is empty — ingest a requirements document first."]
            ),
        }

    def _proposal_view(output: Dict[str, Any], run, draft, delta, old_graph, new_graph):
        """The draft as the page renders it.

        Reads the agent's own collections rather than re-projecting the draft graph:
        the output IS the proposal, and re-deriving it from the graph would be a
        second rendering of the same thing — free to disagree with the first.
        """
        kinds: Dict[str, int] = {}
        for node in delta.added_nodes:
            kinds[node.kind] = kinds.get(node.kind, 0) + 1
        return {
            "draft": draft.to_dict(),
            "run": {
                "id": run.id,
                "completeness": run.completeness,
                "passes": [
                    {"name": p.pass_name, "outcome": p.outcome, "path": p.path,
                     "triples": p.triples_produced, "error": p.error, "elapsed": p.elapsed}
                    for p in run.passes
                ],
            },
            "elements": output.get("elements") or [],
            "connections": output.get("connections") or [],
            "techniques": output.get("design_techniques") or [],
            "patterns": output.get("architecture_patterns") or [],
            "resolutions": output.get("pattern_resolutions") or [],
            "scenarios": output.get("quality_scenarios") or [],
            "references": output.get("references") or [],
            "findings": output.get("findings") or [],
            "caveats": list(draft.caveats),
            "statistics": output.get("statistics") or {},
            "delta": {
                "added_nodes": len(delta.added_nodes),
                "added_assertions": len(delta.added_assertions),
                "changed_assertions": len(delta.changed_assertions),
                "removed_assertions": len(delta.removed_assertions),
                "new_kinds": dict(sorted(kinds.items())),
            },
        }

    @app.route("/design")
    def design():
        snapshot = state()
        return render_template(
            "design.html",
            preconditions=_design_preconditions(snapshot),
            drafts=current_drafts().list(),
            proposal=None,
            domain_packs=discover_domain_packs(current_app.config["ONTOLOGY_DIR"]),
            active_domain_pack=snapshot.meta.get("domain_pack", ""),
        )

    @app.route("/design/draft", methods=["POST"])
    def design_draft():
        """Propose an architecture. Writes a DRAFT; never touches the working set."""
        inputs = _design_inputs()
        snapshot = inputs["snapshot"]
        if not snapshot.graph.nodes:
            flash("Nothing to design against — ingest a requirements document first.", "error")
            return redirect(url_for("design"))

        domain_pack = (
            request.form.get("domain_pack") or snapshot.meta.get("domain_pack") or ""
        ).strip()
        initiative_id = (
            snapshot.meta.get("initiative_id") or current_app.config["INITIATIVE_ID"]
        ).strip()

        # As with ingest: with a job store the request records the work, and the
        # worker runs it. The input is the graph (`input_kind=graph`), so nothing is
        # written to the artifact store — there are no bytes to read.
        job_store = current_job_store()
        if job_store is not None:
            try:
                job = job_store.enqueue(Job(
                    job_id=new_job_id(),
                    run_id=new_run_id(),
                    scope_id=current_scope_id(),
                    kind="design",
                    trigger=TRIGGER_UI,
                    input_kind=GRAPH,
                    input={"base_ref": inputs["base_ref"] or "working"},
                    parameters={"initiative_id": initiative_id,
                                "domain_pack": domain_pack},
                    actor=reviewer(),
                ))
            except Exception as exc:  # noqa: BLE001 - fall back to running it here
                flash(f"Could not queue the design run ({exc}); running it in this "
                      f"request instead.", "warning")
            else:
                flash(f"Queued design run {job.job_id} — the worker will draft it.",
                      "success")
                return redirect(url_for("run_view", job_id=job.job_id))

        try:
            outcome = run_design(
                store=current_store(),
                journal=current_journal(),
                scope_id=current_scope_id(),
                snapshot=snapshot,
                design_factory=current_app.config["DESIGN_FACTORY"],
                drafts=current_drafts(),
                baseline=inputs["baseline"],
                base_ref=inputs["base_ref"],
                initiative_id=initiative_id,
                domain_pack=domain_pack,
            )
        except (ExtractorLoadFailed, DomainPackFailed) as exc:
            flash(str(exc), "error")
            return redirect(url_for("design"))
        except RunFailed as exc:
            flash(f"Design run reported failure: {exc}", "error")
            return redirect(url_for("design"))
        except Exception as exc:                                     # noqa: BLE001
            flash(f"Design run failed: {exc}", "error")
            return redirect(url_for("design"))

        output, run, draft, delta = (
            outcome.output, outcome.run, outcome.draft, outcome.delta
        )
        # The preview shows what applying WOULD produce; `run_design` already
        # computed the merge for the delta, so use that rather than merging twice.
        merged = outcome.merged
        flash(
            f"Draft {draft.id} ({run.completeness}) — {len(delta.added_nodes)} new "
            f"concept(s), {len(delta.added_assertions)} fact(s). Nothing is applied yet.",
            "success" if run.completeness == "COMPLETE" else "warning",
        )
        return render_template(
            "design.html",
            preconditions=_design_preconditions(snapshot),
            drafts=current_drafts().list(),
            proposal=_proposal_view(output, run, draft, delta, snapshot.graph, merged),
            domain_packs=discover_domain_packs(current_app.config["ONTOLOGY_DIR"]),
            active_domain_pack=outcome.domain_pack,
        )

    @app.route("/design/apply", methods=["POST"])
    def design_apply():
        """An explicit, deliberate second step — see `core.knowledge.drafts`."""
        snapshot = state()
        draft_id = (request.form.get("draft_id") or "").strip()
        try:
            draft, proposal_graph = current_drafts().load(draft_id)
        except KeyError as exc:
            flash(str(exc), "error")
            return redirect(url_for("design"))

        merged = merge_graphs(snapshot.graph, proposal_graph)
        delta = compute_graph_delta(snapshot.graph, merged)
        meta = dict(snapshot.meta)
        meta["last_design"] = {
            "draft": draft.id,
            "run_id": draft.run_id,
            "completeness": draft.completeness,
            "base_ref": draft.base_ref,
        }
        save(Snapshot(graph=merged, log=snapshot.log, meta=meta))
        revision = current_store().commit(
            merged,
            snapshot.log,
            label=draft.label or "Design draft",
            actor=reviewer(),
            note=f"Applied design {draft.id}",
            initiative_id=draft.initiative_id,
        )
        current_drafts().discard(draft_id)
        flash(
            f"Applied {draft.label or draft.id}: +{len(delta.added_nodes)} concept(s), "
            f"+{len(delta.added_assertions)} fact(s) as UNVERIFIED proposals. "
            f"Committed {revision.id}. Review them before trusting the audit.",
            "success",
        )
        return redirect(url_for("review"))

    @app.route("/design/discard", methods=["POST"])
    def design_discard():
        draft_id = (request.form.get("draft_id") or "").strip()
        if current_drafts().discard(draft_id):
            flash(f"Discarded design draft {draft_id}. The graph was never touched.", "warning")
        else:
            flash(f"No design draft {draft_id}.", "error")
        return redirect(url_for("design"))

    @app.route("/api/design")
    def api_design():
        return jsonify({
            "drafts": [d.to_dict() for d in current_drafts().list()],
            "latest": (current_drafts().latest().to_dict() if current_drafts().latest() else None),
        })

    # -- review gate -----------------------------------------------------

    @app.route("/review")
    def review():
        snapshot = state()
        filters = ReviewFilters.from_args(request.args)
        rows = project_review_rows(snapshot.graph, snapshot.log, filters)
        return render_template(
            "review.html",
            rows=rows,
            filters=filters,
            summary=project_review_summary(snapshot.graph, snapshot.log),
            total_rows=len(rows),
            empty=not snapshot.graph.nodes and not snapshot.graph.assertions,
        )

    @app.route("/review/<assertion_id>")
    def review_detail(assertion_id: str):
        snapshot = state()
        detail = project_assertion(snapshot.graph, snapshot.log, assertion_id)
        if not detail:
            abort(404)
        return render_template("partials/assertion_detail.html", a=detail)

    @app.route("/review/<assertion_id>/decision", methods=["POST"])
    def review_decision(assertion_id: str):
        action = (request.form.get("action") or "").strip()
        note = (request.form.get("note") or "").strip()
        target = request.form.get("target")
        predicate = request.form.get("predicate") or None
        actor = reviewer()

        snapshot = state()
        try:
            decision = apply_decisions(
                snapshot.graph,
                snapshot.log,
                assertion_id,
                action,
                actor=actor,
                note=note,
                target=target,
                predicate=predicate,
            )
        except ReviewError as exc:
            if request.headers.get("HX-Request"):
                return f'<tr><td colspan="6" class="error">{exc}</td></tr>', 422
            flash(str(exc), "error")
            return redirect(url_for("review"))

        save(snapshot)

        if request.headers.get("HX-Request"):
            # Re-render from the graph rather than trusting the posted row: after a
            # correction the surviving assertion has a different id, and after a
            # supersede the original is no longer in the active set.
            target_id = decision.replacement_id or assertion_id
            row = next(
                (
                    r
                    for r in project_review_rows(
                        snapshot.graph, snapshot.log, ReviewFilters(), include_superseded=True
                    )
                    if r["id"] == target_id
                ),
                None,
            )
            if row is None:
                return "", 204
            return render_template("partials/review_row.html", row=row, filters=ReviewFilters())

        flash(f"{decision.label}: {assertion_id}", "success")
        return redirect(request.referrer or url_for("review"))

    @app.route("/review/bulk", methods=["POST"])
    def review_bulk():
        snapshot = state()
        actor = reviewer()
        scope = (request.form.get("bulk_scope") or "threshold").strip()
        note = (request.form.get("note") or "").strip()

        if scope == "selected":
            ids = request.form.getlist("ids")
            if not ids:
                flash("No assertions selected.", "warning")
                return redirect(url_for("review"))
            decisions = bulk_verify(
                snapshot.graph,
                snapshot.log,
                assertion_ids=ids,
                actor=actor,
                note=note or "bulk verify (selected)",
            )
        else:
            # Trust the threshold, not the checkbox list: a stale page must not be
            # able to verify assertions the reviewer never saw.
            try:
                threshold = float(request.form.get("threshold") or LOW_CONFIDENCE)
            except ValueError:
                threshold = LOW_CONFIDENCE
            decisions = bulk_verify(
                snapshot.graph,
                snapshot.log,
                max_confidence=threshold,
                actor=actor,
                note=note or f"bulk verify (confidence ≤ {threshold})",
            )

        save(snapshot)
        flash(f"Verified {len(decisions)} assertion(s).", "success" if decisions else "warning")
        return redirect(request.referrer or url_for("review"))

    # -- graph projection ------------------------------------------------

    # -- merged knowledge-graph viewpoint --------------------------------
    #
    # Not the graph projection layer: this route asks a *viewpoint* to draw the
    # graph. It is labelled "Map" rather than "C4" because it is the whole
    # knowledge graph — requirements, architecture and the references between them
    # — and C4 is a *notation*, which this force layout is not. Rendering C4 as C4
    # is YB-025.
    #
    # `/c4` and `/graph` both redirect here. `/graph` is the old name and `/c4`
    # the old label; a bookmarked link should land on the view that replaced it
    # rather than on a 404.

    @app.route("/map")
    def map_view():
        snapshot = state()
        lens = request.args.get("lens", DEFAULT_LENS)
        # The concept table is collapsed, scrollable and filterable, which is what
        # makes a long list usable — so the cap is high and, when it bites, the
        # page says how many rows it is not showing. A filter over a silently
        # truncated list answers "no match" for a concept that exists.
        index = project_node_index(snapshot.graph)
        return render_template(
            "map.html",
            view=merged_view(snapshot.graph, lens, request.args.get("concern", "")),
            elements=index[:MAP_CONCEPT_ROWS],
            element_total=len(index),
            gaps=project_gap_report(snapshot.graph),
        )

    @app.route("/c4")
    def c4_redirect():
        return redirect(url_for("map_view", **request.args), code=301)

    @app.route("/graph")
    def graph_redirect():
        return redirect(url_for("map_view", **request.args), code=301)

    @app.route("/api/map")
    def api_map():
        snapshot = state()
        return jsonify(
            merged_view(
                snapshot.graph,
                request.args.get("lens", DEFAULT_LENS),
                request.args.get("concern", ""),
            )
        )

    @app.route("/api/c4")
    def api_c4_redirect():
        return redirect(url_for("api_map", **request.args), code=301)

    # -- gap report ------------------------------------------------------

    @app.route("/gaps")
    def gaps():
        snapshot = state()
        return render_template("gaps.html", report=project_gap_report(snapshot.graph))

    @app.route("/api/gaps")
    def api_gaps():
        snapshot = state()
        return jsonify(project_gap_report(snapshot.graph))

    @app.route("/api/realization")
    def api_realization():
        """Both directions of the audit as JSON — a machine-readable answer to
        "which requirements have no architectural answer?"."""
        snapshot = state()
        return jsonify(project_realization(snapshot.graph))

    # -- quality attributes ----------------------------------------------

    @app.route("/quality")
    def quality():
        """The architect's census: where the quality gaps are, by ISO characteristic.

        Separate from `/gaps` because it answers a different question. `/gaps` is
        requirement-shaped ("is this requirement answered?"); this is
        attribute-shaped ("which qualities does nothing deliver, and which does the
        architecture deliver unasked?").
        """
        snapshot = state()
        return render_template("quality.html", report=project_quality_report(snapshot.graph))

    @app.route("/api/quality")
    def api_quality():
        snapshot = state()
        return jsonify(project_quality_report(snapshot.graph)["report"])

    # -- reconciliation --------------------------------------------------

    def _threshold(default: float = DEFAULT_MATCH_THRESHOLD) -> float:
        """Read a match threshold from the request, clamped to a sane range.

        A form field is user input: an empty or unparseable value must fall back to
        the conservative default rather than rejecting every candidate silently.
        """
        raw = (request.form.get("threshold") or request.args.get("threshold") or "").strip()
        if not raw:
            return default
        try:
            return min(1.0, max(0.0, float(raw)))
        except ValueError:
            return default

    @app.route("/reconcile")
    def reconcile():
        snapshot = state()
        threshold = _threshold()
        return render_template(
            "reconcile.html",
            view=project_reconciliation(snapshot.graph, threshold),
            coverage=project_realization(snapshot.graph),
            threshold=threshold,
            empty=not snapshot.graph.nodes,
        )

    @app.route("/api/reconcile")
    def api_reconcile():
        snapshot = state()
        return jsonify(project_reconciliation(snapshot.graph, _threshold()))

    @app.route("/reconcile/bulk", methods=["POST"])
    def reconcile_bulk():
        snapshot = state()
        threshold = _threshold()
        actor = reviewer()
        note = (request.form.get("note") or "").strip()
        scope = (request.form.get("bulk_scope") or "threshold").strip()

        selected = request.form.getlist("ids")
        if scope == "selected" and not selected:
            flash("No references selected.", "warning")
            return redirect(url_for("reconcile"))

        result = bulk_resolve(
            snapshot.graph,
            snapshot.log,
            assertion_ids=selected if scope == "selected" else None,
            min_score=threshold,
            actor=actor,
            note=note or f"bulk resolve (threshold {threshold:g})",
        )
        if result.resolved:
            save(snapshot)

        declined = result.below_threshold + [(i, 0.0) for i in result.no_candidate]
        flash(
            f"Resolved {len(result.resolved)} of {result.considered} reference(s) at "
            f"threshold {threshold:g}. Left alone: {len(result.below_threshold)} below "
            f"threshold, {len(result.no_candidate)} with no candidate of the expected kind.",
            "success" if result.resolved else "warning",
        )
        if declined:
            flash(
                "Nothing was bound on a weak match. A wrong traceability link is worse "
                "than a missing one — resolve those individually below, or override "
                "the kind deliberately.",
                "message",
            )
        if result.unknown_ids:
            flash(
                f"{len(result.unknown_ids)} selected reference(s) are no longer open — "
                "the page was stale.",
                "warning",
            )
        # Computed once, after the pass: the realization state is a fresh query
        # over the graph the pass just changed.
        coverage = project_realization(snapshot.graph)["summary"]
        flash(
            f"Requirements with an architectural answer: "
            f"{coverage['realized']} of {coverage['requirements']}."
            + (
                f" {coverage['unrealized']} still unanswered."
                if coverage["unrealized"]
                else ""
            ),
            "message",
        )
        return redirect(url_for("reconcile"))

    @app.route("/reconcile/<assertion_id>/resolve", methods=["POST"])
    def reconcile_resolve(assertion_id: str):
        snapshot = state()
        target = (request.form.get("target_node_id") or "").strip()
        override = request.form.get("allow_kind_override") == "on"
        try:
            decision = resolve_reference(
                snapshot.graph,
                snapshot.log,
                assertion_id,
                target,
                actor=reviewer(),
                note=(request.form.get("note") or "").strip(),
                allow_kind_override=override,
            )
        except ReconcileError as exc:
            flash(str(exc), "error")
            return redirect(url_for("reconcile"))

        save(snapshot)
        node = snapshot.graph.nodes.get(target)
        flash(
            f"Resolved to {node.kind}:{node.label}." if node else decision.label,
            "success",
        )
        return redirect(url_for("reconcile"))

    # -- ontology reference ----------------------------------------------
    #
    # The third kind of view: this one reads the LinkML *schemas*, not the extracted
    # graph. `/map` draws the graph; this describes the vocabulary.

    @app.route("/ontology")
    def ontology():
        model = current_app.config.get("ONTOLOGY")
        if model is None:
            return render_template("ontology.html", view=None,
                                   error=current_app.config.get("ONTOLOGY_ERROR", "")), 200

        layer = request.args.get("layer", "")
        q = request.args.get("q", "")
        only = request.args.get("only", "")
        focus = request.args.get("focus", "")
        snapshot = state()

        # The active domain pack is resolved from the GRAPH's provenance, not from
        # configuration: they diverge the moment an Initiative is re-extracted under
        # a different pack, and this page describes the vocabulary a fact was made
        # in, not what is selected now. `pack_for_graph` returns None for a graph
        # built with no pack, which is the common case and not an error.
        active_pack = pack_for_graph(snapshot.graph, current_app.config["ONTOLOGY_DIR"])

        return render_template(
            "ontology.html",
            view=ontology_overview(model, snapshot.graph, pack=active_pack),
            classes=class_rows(model, snapshot.graph, layer=layer, q=q, only=only),
            enums=enum_rows(model, layer=layer, q=q),
            detail=class_detail(model, focus, snapshot.graph) if focus else None,
            neighbourhood=class_neighbourhood(model, focus) if focus else None,
            domain_packs=discover_domain_packs(current_app.config["ONTOLOGY_DIR"]),
            active_pack=active_pack,
            layer=layer,
            q=q,
            only=only,
            focus=focus,
            error="",
        )

    @app.route("/api/ontology")
    def api_ontology():
        model = current_app.config.get("ONTOLOGY")
        if model is None:
            return jsonify({"error": current_app.config.get("ONTOLOGY_ERROR", "not loaded")}), 503
        return jsonify(ontology_payload(model, state().graph))

    # -- change management -----------------------------------------------

    @app.route("/changes")
    def changes():
        snapshot = state()
        revisions = current_store().list_revisions()
        return render_template(
            "changes.html",
            revisions=revisions,
            log=project_decisions(snapshot.graph, snapshot.log, limit=100),
            summary=project_review_summary(snapshot.graph, snapshot.log),
            working_meta=snapshot.meta,
            baselines=[r for r in revisions if r.is_baseline],
        )

    @app.route("/changes/commit", methods=["POST"])
    def changes_commit():
        snapshot = state()
        label = (request.form.get("label") or "").strip()
        revision = current_store().commit(
            snapshot.graph,
            snapshot.log,
            label=label,
            actor=reviewer(),
            note=request.form.get("note", ""),
        )
        flash(f"Committed {revision.label} ({revision.id}).", "success")
        return redirect(url_for("changes"))

    @app.route("/changes/freeze", methods=["POST"])
    def changes_freeze():
        revision_id = (request.form.get("revision_id") or "").strip() or None
        allow = request.form.get("allow_unverified") == "on"
        try:
            revision = current_store().freeze(
                revision_id,
                label=(request.form.get("label") or "").strip(),
                actor=reviewer(),
                allow_unverified=allow,
            )
            flash(f"Frozen as baseline: {revision.label}.", "success")
        except BaselineNotReady as exc:
            # A revision is a snapshot: reviewing the working set afterwards does
            # not change it. Say which situation the reviewer is actually in,
            # because "refused to freeze" on its own looks like a bug.
            hint = ""
            if review_progress(state().graph).is_auditable and not exc.progress.is_auditable:
                hint = (
                    " The working set is now fully reviewed — commit it as a revision "
                    "first, then freeze that revision."
                )
            flash(
                f"Refused to freeze — {exc}.{hint} Or override deliberately to freeze "
                f"an unreviewed revision.",
                "error",
            )
        except KeyError as exc:
            flash(str(exc), "error")
        return redirect(url_for("changes"))

    @app.route("/changes/promote", methods=["POST"])
    def changes_promote():
        snapshot = state()
        result, _decision = promote_to_baseline(
            snapshot.graph, snapshot.log, actor=reviewer(), note=request.form.get("note", "")
        )
        save(snapshot)
        flash(
            f"Promoted {result.promoted} verified fact(s) to the system baseline. "
            f"Left behind: {result.skipped_unverified} unverified, "
            f"{result.skipped_disputed} disputed — a baseline does not absorb "
            f"unchecked extraction.",
            "success" if result.promoted else "warning",
        )
        return redirect(url_for("changes"))

    @app.route("/changes/diff")
    def changes_diff():
        old_id = request.args.get("from", "")
        new_id = request.args.get("to", "working")

        try:
            if old_id:
                old = current_store().load_revision(old_id)
            else:
                old = Snapshot(graph=KnowledgeGraph())
            new = state() if new_id == "working" else current_store().load_revision(new_id)
        except KeyError as exc:
            flash(str(exc), "error")
            return redirect(url_for("changes"))

        delta = compute_graph_delta(old.graph, new.graph)
        return render_template(
            "diff.html",
            view=project_delta(delta, old.graph, new.graph),
            old_ref=current_store().get_revision(old_id) if old_id else None,
            new_ref=current_store().get_revision(new_id) if new_id != "working" else None,
            new_is_working=new_id == "working",
            revisions=current_store().list_revisions(),
            old_id=old_id,
            new_id=new_id,
        )

    @app.route("/changes/discard", methods=["POST"])
    def changes_discard():
        current_store().discard_working()
        flash("Working set discarded. The next load starts from an empty graph.", "warning")
        return redirect(url_for("changes"))

    # -- exports ---------------------------------------------------------

    @app.route("/export/graph.json")
    def export_json():
        snapshot = state()
        return send_file(
            _buffer(json.dumps(json.loads(_graph_json(snapshot.graph)), indent=2)),
            mimetype="application/json",
            as_attachment=True,
            download_name="sea-graph.json",
        )

    @app.route("/export/graph.ttl")
    def export_rdf():
        snapshot = state()
        return send_file(
            _buffer(to_turtle(snapshot.graph)),
            mimetype="text/turtle",
            as_attachment=True,
            download_name="sea-graph.ttl",
        )

    return app


# ============================================================================
# Small helpers
# ============================================================================


def _buffer(text: str):
    import io

    return io.BytesIO(text.encode("utf-8"))


def _graph_json(graph: KnowledgeGraph) -> str:
    from core.knowledge import graph_to_dict

    return json.dumps(graph_to_dict(graph))


def _run_summaries(graph: KnowledgeGraph):
    return [
        {
            "id": r.id,
            "document": r.document_ref,
            "completeness": r.completeness,
            "passes": len(r.passes),
            "failed": len(r.failed_passes),
        }
        for r in graph.runs.values()
    ]
