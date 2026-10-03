"""
Architecture Extraction Agent  [DRAFT]

Extracts a solution-architecture knowledge graph (ARC-G) from a document, using
the C4-aligned architecture ontology.

DESIGN — why this is not a single call
--------------------------------------
This agent previously made one call asking for every collection at once. That hit
a hard ceiling: as the schema grew, the model silently dropped whole categories —
traceability predicates, technologies, responsibilities, monitoring platforms.
Each drop looked like a prompt bug; the cause was asking for too much at once.

It now runs four FOCUSED PASSES (see `passes.py`) over CHUNKS of the document
(see `extraction/chunking.py`). Two consequences worth stating:

  - Cost scales with document size. Real requirement and architecture documents
    are large and verbose; the samples in `test_data/` shape the ontology but do
    not represent the volume this meets in production.
  - More calls, each small and reliable, instead of one call that is fast when it
    works and silently lossy when it does not.

Results are merged across chunks and passes without losing content, then checked
by deterministic validators whose vocabularies come from the ontology.
"""

from typing import Any, Dict, List, Optional

from ..base_agent import AgentResult
from ..knowledge_extraction.agent import KnowledgeExtractionAgent
from ..extraction import (
    chunk_document,
    completeness,
    connection_key,
    element_key,
    merge_records,
    merge_triples,
    named_key,
    summarise_chunks,
    check_attribution_endpoints,
    check_connection_endpoints,
    check_containment,
    check_containment_kinds,
    check_deployment_levels,
    check_element_types,
    check_enum_membership,
    check_names_are_anchored,
    check_nonempty_field,
    check_object_contract,
    check_quotations_are_grounded,
    check_reference_kinds,
)
from ..extraction.passes import collect, outcome_records, run_passes, summarise
from .repair import merge_style_elements, repair_containment, style_as_element
from .passes import (
    ARCHITECTURE_PASSES,
    ElementRecord,
    ConnectionRecord,
    TechnologyStackRecord,
    ArchitectureStyleRecord,
    ReferenceRecord,
)


def _reference_key(record: Dict[str, Any]) -> tuple:
    return (
        str(record.get("element") or "").strip().lower(),
        str(record.get("relationship") or "").strip().lower(),
        str(record.get("reference") or "").strip().lower(),
    )


class ArchitectureExtractionAgent(KnowledgeExtractionAgent):
    """Extracts architecture knowledge against the C4-aligned ARC-G ontology."""

    def _output_keys(self):
        return {"nodes": "elements", "edges": "connections"}

    # ------------------------------------------------------------------
    # Typed text-fallback parsing
    # ------------------------------------------------------------------
    # The base parsers produce generic ExtractedEntity/Relationship objects. On
    # the fallback path that loses C4 typing (element_type, parent, c4_level),
    # so containment and classification invariants become unverifiable. These
    # overrides parse the real record types so both paths agree on shape.

    _VALID_ELEMENT_TYPES = {
        "SoftwareSystem", "ExternalSystem", "Person", "Container",
        "DataStore", "Component", "CodeElement", "DeploymentNode",
    }

    def _parse_entities_from_text(self, text):
        raw = self._extract_named_list(text, "elements")
        if raw:
            parsed = []
            for d in raw:
                if not isinstance(d, dict):
                    continue
                name = str(d.get("name") or "").strip()
                if not name:
                    continue
                etype = str(d.get("element_type") or "").strip()
                if etype not in self._VALID_ELEMENT_TYPES:
                    # A guess with a flag beats losing the element; the
                    # validator will catch an implausible classification.
                    etype = "Container"
                parsed.append(ElementRecord(
                    name=name,
                    element_type=etype,
                    c4_level=str(d.get("c4_level") or "").strip(),
                    parent=str(d.get("parent") or "").strip(),
                    system_class=str(d.get("system_class") or "").strip(),
                    origin=str(d.get("origin") or "").strip(),
                    deployment_model=str(d.get("deployment_model") or "").strip(),
                    responsibilities=list(d.get("responsibilities") or []),
                    description=str(d.get("description") or ""),
                ))
            if parsed:
                return parsed
        return super()._parse_entities_from_text(text)

    def _parse_relationships_from_text(self, text):
        raw = self._extract_named_list(text, "connections", "relationships")
        if raw:
            parsed = []
            for d in raw:
                if not isinstance(d, dict):
                    continue
                src = str(d.get("source") or "").strip()
                tgt = str(d.get("target") or "").strip()
                if not (src and tgt):
                    continue
                parsed.append(ConnectionRecord(
                    source=src, target=tgt,
                    description=str(d.get("description") or ""),
                    protocol=str(d.get("protocol") or ""),
                    style=str(d.get("style") or ""),
                ))
            if parsed:
                return parsed
        return super()._parse_relationships_from_text(text)

    # ------------------------------------------------------------------

    def run(self, input_data: Dict[str, Any]) -> AgentResult:
        try:
            document = input_data.get("document", "")
            document_type = input_data.get("document_type", "architecture")
            domain = input_data.get("domain", "generic")

            if not document:
                return AgentResult(success=False, output=None,
                                   errors=["No document provided"])

            # ---- 1. chunk ----
            max_chars = int(input_data.get("chunk_max_chars", 7000))
            chunks = chunk_document(document, max_chars=max_chars)
            self.log(f"Document: {len(document):,} chars — {summarise_chunks(chunks)}")

            # ---- 2. run passes over chunks ----
            shared = self._format_ontology_context()
            # A sink the caller attached, if any. The pipeline emits; who listens is
            # not this profile's business — and a dead sink cannot fail the run
            # (`run_passes` swallows sink errors deliberately).
            progress = input_data.get("progress")
            outcomes = run_passes(self, ARCHITECTURE_PASSES, chunks, shared,
                                  log=self.log, progress=progress)
            summary = summarise(outcomes, len(chunks), len(ARCHITECTURE_PASSES))
            # The real per-attempt records, not just the totals below. Without
            # them ingest reconstructs `pass_name="(unspecified)"` and the run
            # cannot say which pass lost content.
            pass_records = outcome_records(outcomes)
            self.log(summary.describe(len(chunks), len(ARCHITECTURE_PASSES)),
                     level="success" if summary.failed == 0 else "warning")

            # ---- 3. merge across chunks and passes ----
            #
            # Every stage from here on runs UNDER A GUARD. The model calls are the
            # expensive part and they have already succeeded, so an error in
            # merging, repair or validation must not discard the run: a
            # representation mismatch across one seam threw away 12/12 successful
            # passes, 182 triples and 19 connections in a single session (YB-051
            # defect 1). Whatever merged is returned; the error becomes a finding
            # AND a failed pass record, so `compute_completeness` reports PARTIAL
            # instead of a job failure with nothing stored.
            triples: List[Any] = []
            elements: List[Any] = []
            connections: List[Any] = []
            technology: List[Any] = []
            styles: List[Any] = []
            techniques: List[Any] = []
            conventions: List[Any] = []
            references: List[Any] = []
            stage_flags: List[Any] = []
            post_errors: List[str] = []

            try:
                # NOTE: `collect` returns one group per chunk. For triples we want a
                # flat list of those groups (from every pass), NOT a list of lists of
                # groups — the extra nesting would hand `merge_triples` a list where
                # it expects a triple.
                triple_groups: List[List[Any]] = []
                for spec in ARCHITECTURE_PASSES:
                    triple_groups.extend(collect(outcomes, spec.name, "triples"))
                triples = merge_triples(triple_groups)
                elements = merge_records(
                    collect(outcomes, "structure", "elements"), element_key, completeness)
                connections = merge_records(
                    collect(outcomes, "connections", "connections"), connection_key, completeness)
                technology = merge_records(
                    collect(outcomes, "technology", "technology_stacks"), named_key, completeness)
                styles = merge_records(
                    collect(outcomes, "technology", "architecture_styles"), named_key, completeness)
                techniques = merge_records(
                    collect(outcomes, "technology", "design_techniques"), named_key, completeness)
                conventions = merge_records(
                    collect(outcomes, "technology", "engineering_conventions"), named_key, completeness)
                references = merge_records(
                    collect(outcomes, "traceability", "references"), _reference_key, completeness)

                self.log(
                    f"Merged: {len(triples)} triples, {len(elements)} elements, "
                    f"{len(connections)} connections, {len(technology)} technologies, "
                    f"{len(styles)} styles, {len(techniques)} techniques, "
                    f"{len(conventions)} conventions, {len(references)} references"
                )
            except Exception as exc:                                 # noqa: BLE001
                post_errors.append(f"merge: {exc}")
                self.log(
                    f"Post-extraction merge failed: {exc} — keeping what merged "
                    f"({len(triples)} triples, {len(elements)} elements, "
                    f"{len(connections)} connections)", level="error")

            # ---- 3b. repair structure across chunk boundaries ----
            # A pass sees one chunk, so a container named in another chunk cannot be
            # attached by the model. Repaired here, where the whole document is in
            # view, and recorded as findings rather than silently rewritten.
            #
            # The style merge runs first: an element that should not exist at all is
            # folded into the system before the containment repair decides what is
            # unplaced, so its edges are re-pointed once.
            try:
                elements, styles, triples, style_flags = merge_style_elements(
                    elements, styles, triples)
                if style_flags:
                    self.log(f"  {len(style_flags)} architectural style(s) merged into "
                             f"the system under design", level="warning")
                elements, triples, repair_flags = repair_containment(elements, triples)
                if repair_flags:
                    self.log(f"  {len(repair_flags)} unplaced element(s) attached to the "
                             f"system under design", level="warning")
                stage_flags += list(style_flags) + list(repair_flags)
            except Exception as exc:                                 # noqa: BLE001
                post_errors.append(f"repair: {exc}")
                self.log(f"Structure repair failed: {exc} — the merged facts are "
                         f"kept un-repaired rather than dropped", level="error")

            # ---- 4. validate (Option B) ----
            try:
                flags = list(stage_flags)
                flags += check_object_contract(triples)
                flags += check_containment(elements, triples)
                # Parents ATTACHED by `repair_containment` above are excluded: the
                # repair already reports each as `containment_repaired`, and one
                # document gap counted as two defects is how a report overstates the
                # damage (the C4 scorecard was caught doing exactly that).
                inferred = [f.subject for f in stage_flags
                            if getattr(f, "kind", "") == "containment_repaired"]
                flags += check_containment_kinds(elements, inferred_parents=inferred)
                # The range the ontology declares for a reference slot, which
                # `check_containment_kinds` only enforces for containment: `serves`
                # ranges over SoftwareSystem, and nothing stopped an edge to a
                # Product or to a name no run declared (which ingest turns into a
                # `Concept` placeholder rather than refusing).
                flags += check_reference_kinds(elements)
                flags += check_element_types(elements)
                flags += check_deployment_levels(elements)
                flags += check_enum_membership(elements)
                flags += check_nonempty_field(elements, "container_type",
                                              applies_to=("Container", "DataStore"))
                flags += check_connection_endpoints(connections, elements)
                # The same rule for the attribution lists, which had no backstop at
                # all: `used_by` / `adopted_by` / `applies_to` name elements by hand,
                # so a group label there ("All Microservices") became a node of its
                # own through `_resolve`'s `Concept` fallback — one per phrasing, on
                # a run that reports COMPLETE. See ISS-1.
                flags += check_attribution_endpoints(
                    {
                        "technology_stacks": technology,
                        "architecture_styles": styles,
                        "design_techniques": techniques,
                        "engineering_conventions": conventions,
                    },
                    elements,
                )
                # Span anchoring (§3.6). Extraction only — a design is allowed to
                # invent, an extractor is not — and against the WHOLE document rather
                # than the chunk, because `merge_records` has already merged across
                # chunks and an element carries no record of which one it came from.
                flags += check_names_are_anchored(elements, document)
                flags += check_quotations_are_grounded(elements, document)
                flags += style_as_element(elements)
                flag_dicts = [f.to_dict() for f in flags]
            except Exception as exc:                                 # noqa: BLE001
                post_errors.append(f"validation: {exc}")
                flag_dicts = [f.to_dict() for f in stage_flags]
                self.log(f"Validation failed: {exc} — the facts are kept "
                         f"unvalidated rather than dropped", level="error")

            if post_errors:
                # A finding, not only a log line: this is what makes the run page
                # show that part of the pipeline did not run.
                flag_dicts.append({
                    "kind": "post_extraction_error",
                    "subject": "",
                    "messages": post_errors,
                })
            if flag_dicts:
                self.log(f"  {len(flag_dicts)} findings flagged for review",
                         level="warning")

            # ---- 5. output ----
            if post_errors:
                # A failed pass record, so the run's OWN completeness verdict says
                # PARTIAL. The merge and repair stages are not passes, but they are
                # part of what the run claims to have done — and a run that lost
                # its repair stage while reporting COMPLETE is the false assurance
                # ADR-0013 exists to prevent. Imported locally for the same reason
                # `outcome_records` does it: this layer stays off core's import path.
                from core.knowledge.model import PassRecord

                pass_records.append(PassRecord(
                    pass_name="(post-extraction)",
                    chunk_label="",
                    outcome="failed",
                    path="none",
                    error="; ".join(post_errors)[:200],
                ))

            node_dicts = [self._as_output_dict(e) for e in elements]
            output = {
                "triples": triples,
                "elements": node_dicts,
                "connections": [self._as_output_dict(c) for c in connections],
                "technology_stacks": [self._as_output_dict(t) for t in technology],
                "architecture_styles": [self._as_output_dict(s) for s in styles],
                "design_techniques": [self._as_output_dict(d) for d in techniques],
                "engineering_conventions": [self._as_output_dict(c) for c in conventions],
                "references": [self._as_output_dict(r) for r in references],
                "findings": flag_dicts,
                "statistics": {
                    "total_triples": len(triples),
                    "total_elements": len(elements),
                    "total_connections": len(connections),
                    "total_technology_stacks": len(technology),
                    "total_architecture_styles": len(styles),
                    "total_design_techniques": len(techniques),
                    "total_engineering_conventions": len(conventions),
                    "total_references": len(references),
                    "findings": len(flag_dicts),
                    "chunks": len(chunks),
                    "passes": len(ARCHITECTURE_PASSES),
                    "model_calls": summary.total_calls,
                    "text_fallbacks": summary.text_fallbacks,
                    "post_extraction_errors": len(post_errors),
                },
            }

            return AgentResult(
                success=True,
                output=output,
                confidence=self._overall_confidence(triples),
                metadata={
                    "document_type": document_type,
                    "domain": domain,
                    "domain_pack": self.active_domain_pack_id(),
                    "extraction_path": "passes",
                    "document_chars": len(document),
                    "chunks": len(chunks),
                    # Completeness input. Without the model and the per-attempt
                    # records, this run cannot say what produced it or which pass
                    # lost content — ingest falls back to `(unspecified)` records
                    # reconstructed from the counters.
                    "model_id": self.config.model_id,
                    "model_calls": summary.total_calls,
                    "text_fallback_calls": summary.text_fallbacks,
                    "failed_calls": summary.failed,
                    "empty_calls": summary.empty,
                    "elapsed_seconds": round(summary.elapsed, 1),
                    "findings": len(flag_dicts),
                    "passes": [self._pass_record_dict(r) for r in pass_records],
                    # What the run cost. A hosted endpoint bills per token, and this
                    # is the profile that makes the most calls — four passes over
                    # every chunk — so it is the one whose usage matters most.
                    "usage": self.usage_totals(),
                },
            )

        except Exception as e:                                       # noqa: BLE001
            self.log(f"Extraction failed: {e}", level="error")
            return AgentResult(success=False, output=None, errors=[str(e)])

    @staticmethod
    def _as_output_dict(record: Any) -> Dict[str, Any]:
        if isinstance(record, dict):
            return record
        if hasattr(record, "model_dump"):
            return record.model_dump()
        return dict(record)

    @staticmethod
    def _overall_confidence(triples: List[Dict[str, Any]]) -> float:
        values = [t.get("confidence") or 0.0 for t in triples if isinstance(t, dict)]
        return sum(values) / len(values) if values else 0.0


def create_architecture_extraction_agent() -> ArchitectureExtractionAgent:
    """Factory for the architecture extraction agent."""
    from config import get_default_agent_config
    from ..base_agent import AgentConfig

    config_dict = get_default_agent_config("architecture_extraction")
    return ArchitectureExtractionAgent(AgentConfig(**config_dict))
