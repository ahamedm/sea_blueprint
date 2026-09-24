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
from typing import Any, Callable, Dict, Optional

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
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
    project_realization,
    project_reconciliation,
    project_review_rows,
    project_review_summary,
)
from app.viewpoints.c4 import c4_view
from core.knowledge import (
    DEFAULT_MATCH_THRESHOLD,
    BaselineNotReady,
    KnowledgeGraph,
    ReconcileError,
    ReviewError,
    RevisionStore,
    Snapshot,
    apply_decisions,
    bulk_resolve,
    bulk_verify,
    graph_from_extraction,
    merge_graphs,
    promote_to_baseline,
    resolve_reference,
    review_progress,
    to_turtle,
)
from core.knowledge.model import compute_graph_delta
from core.ontology import (
    OntologyError,
    discover_domain_packs,
    load_ontology,
    pack_for_graph,
)

DEFAULT_STORE_ROOT = "data/sea"
MAX_UPLOAD_BYTES = 4 * 1024 * 1024


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


# ============================================================================
# App factory
# ============================================================================


def create_app(
    test_config: Optional[Dict[str, Any]] = None,
    store_root: Optional[str] = None,
    extractor_factory: Optional[Callable[[str], Any]] = None,
) -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")

    root = store_root or os.environ.get("SEA_DATA_DIR") or DEFAULT_STORE_ROOT
    app.config.from_mapping(
        STORE_ROOT=root,
        SECRET_KEY=os.environ.get("SEA_SECRET_KEY", "sea-platform-dev-key"),
        REVIEWER=os.environ.get("SEA_REVIEWER", "architect"),
        MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
        EXTRACTOR_FACTORY=extractor_factory or _default_extractor,
        INITIATIVE_ID=os.environ.get("SEA_INITIATIVE", "INIT-MVP-001"),
        ONTOLOGY_DIR=os.environ.get("SEA_ONTOLOGY_DIR", "ontology"),
    )
    if test_config:
        app.config.update(test_config)

    store = RevisionStore(app.config["STORE_ROOT"]).ensure()

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
        return store.load_working()

    def save(snapshot: Snapshot) -> None:
        store.save_working(snapshot.graph, snapshot.log, snapshot.meta)

    def reviewer() -> str:
        return (
            request.form.get("actor") or request.args.get("actor") or current_app.config["REVIEWER"]
        )

    @app.context_processor
    def inject_globals():
        return {
            "low_confidence_threshold": LOW_CONFIDENCE,
            "reviewer": current_app.config["REVIEWER"],
            "initiative_id": current_app.config["INITIATIVE_ID"],
        }

    @app.template_filter("pct")
    def pct(value):
        try:
            return f"{float(value) * 100:.0f}%"
        except (TypeError, ValueError):
            return "—"

    # -- dashboard -------------------------------------------------------

    @app.route("/")
    def index():
        snapshot = state()
        return render_template(
            "dashboard.html",
            view=project_dashboard(
                snapshot.graph, snapshot.log, store.list_revisions(), snapshot.meta
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
            )

        uploaded = request.files.get("document")
        text = (request.form.get("text") or "").strip()
        filename = ""

        if uploaded and uploaded.filename:
            filename = secure_filename(uploaded.filename)
            text = uploaded.read().decode("utf-8", errors="replace")
        elif not text:
            flash("Provide a Markdown file or paste document text.", "error")
            return redirect(url_for("ingest"))
        else:
            filename = (request.form.get("text_name") or "pasted-document.md").strip()

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

        try:
            agent = current_app.config["EXTRACTOR_FACTORY"](doc_type)
        except Exception as exc:  # noqa: BLE001 - surfaced to the user
            flash(f"Could not load the {doc_type} extractor: {exc}", "error")
            return redirect(url_for("ingest"))

        try:
            # Capability-probed rather than assumed. The extractor is injectable,
            # and a test double that stands in for the model stack should not have
            # to implement prompt compilation it never performs. A real agent does
            # have this method; anything without it simply runs with no pack.
            select_pack = getattr(agent, "use_domain_pack", None)
            if domain_pack and callable(select_pack):
                select_pack(domain_pack)
        except Exception as exc:  # noqa: BLE001
            flash(f"Could not load the domain pack: {exc}", "error")
            return redirect(url_for("ingest"))

        active_pack_id = ""
        read_pack_id = getattr(agent, "active_domain_pack_id", None)
        if callable(read_pack_id):
            try:
                active_pack_id = read_pack_id() or ""
            except Exception:  # noqa: BLE001 - provenance is best-effort here
                active_pack_id = ""

        try:
            result = agent.run(
                {
                    "document": text,
                    "document_type": doc_type,
                    "domain_pack": active_pack_id,
                    "initiative_id": initiative_id,
                }
            )
        except Exception as exc:  # noqa: BLE001
            flash(f"Extraction failed: {exc}", "error")
            return redirect(url_for("ingest"))

        if not result.success:
            detail = "; ".join(result.errors) or "unknown error"
            flash(f"Extraction reported failure: {detail}", "error")
            return redirect(url_for("ingest"))

        # `result.output` is the extraction payload. The previous version passed
        # `result.model_dump()`, i.e. the AgentResult envelope, so ingest found no
        # `triples`/`elements` keys and every ingest produced an empty graph.
        output = result.output if isinstance(result.output, dict) else {}
        metadata = dict(result.metadata or {})
        # Which document this run read. Reconciliation needs it to tell the two
        # sides apart — a cross-graph predicate claims its referent is in the
        # OTHER graph — and it is not recoverable after ingest, so it travels with
        # the run rather than being re-derived from a filename.
        metadata.setdefault("document_type", doc_type)

        before = state()
        incoming, run_record = graph_from_extraction(
            output,
            metadata=metadata,
            document_ref=filename,
            document_text=text,
            initiative_id=initiative_id,
            # Recorded on every assertion, so the vocabulary a fact was extracted
            # under stays knowable after the Initiative is re-run under another.
            domain_pack=active_pack_id,
        )
        merged = merge_graphs(before.graph, incoming)
        delta = compute_graph_delta(before.graph, merged)

        meta = dict(before.meta)
        meta["initiative_id"] = initiative_id
        meta["domain_pack"] = active_pack_id
        meta["last_ingest"] = {
            "document": filename,
            "doc_type": doc_type,
            "run_id": run_record.id,
            "completeness": run_record.completeness,
            "domain_pack": active_pack_id,
            "nodes": len(merged.nodes) - len(before.graph.nodes),
            "facts": len(delta.added_assertions),
            "changed": len(delta.changed_assertions),
        }
        save(Snapshot(graph=merged, log=before.log, meta=meta))

        revision_label = request.form.get("revision_label") or f"Ingest · {filename}"
        store.commit(
            merged,
            before.log,
            label=revision_label,
            actor=actor,
            note=request.form.get("note", ""),
            initiative_id=initiative_id,
        )

        removed = len(delta.removed_assertions)
        flash(
            f"{filename} ({doc_type}) → {run_record.completeness}: "
            f"+{len(delta.added_assertions)} facts, {len(delta.changed_assertions)} changed"
            + (f", −{removed} removed" if removed else ""),
            "success",
        )
        if run_record.completeness != "COMPLETE":
            flash(
                "Extraction was not COMPLETE — absence of a fact is NOT evidence "
                "of its absence. Review before auditing.",
                "warning",
            )
        return redirect(url_for("review"))

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

    # -- architecture viewpoint (C4) -------------------------------------
    #
    # Not the graph projection layer: this route asks a *viewpoint* to describe the
    # architecture. `/graph` is kept as a redirect because the old name was exactly
    # the confusion this split removes — "graph" in this project means the knowledge
    # graph, not a diagram of the architecture.

    @app.route("/c4")
    def c4():
        snapshot = state()
        level = request.args.get("level", "context")
        return render_template(
            "c4.html",
            view=c4_view(snapshot.graph, level),
            elements=project_node_index(snapshot.graph)[:200],
        )

    @app.route("/graph")
    def graph_redirect():
        return redirect(url_for("c4", **request.args), code=301)

    @app.route("/api/c4")
    def api_c4():
        snapshot = state()
        return jsonify(c4_view(snapshot.graph, request.args.get("level", "context")))

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
    # graph. `/c4` describes the architecture; this describes the vocabulary.

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
        revisions = store.list_revisions()
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
        revision = store.commit(
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
            revision = store.freeze(
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
                old = store.load_revision(old_id)
            else:
                old = Snapshot(graph=KnowledgeGraph())
            new = state() if new_id == "working" else store.load_revision(new_id)
        except KeyError as exc:
            flash(str(exc), "error")
            return redirect(url_for("changes"))

        delta = compute_graph_delta(old.graph, new.graph)
        return render_template(
            "diff.html",
            view=project_delta(delta, old.graph, new.graph),
            old_ref=store.get_revision(old_id) if old_id else None,
            new_ref=store.get_revision(new_id) if new_id != "working" else None,
            new_is_working=new_id == "working",
            revisions=store.list_revisions(),
            old_id=old_id,
            new_id=new_id,
        )

    @app.route("/changes/discard", methods=["POST"])
    def changes_discard():
        store.discard_working()
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
