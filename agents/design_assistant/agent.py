"""
Design Assistant Agent.

WHAT IT IS. A profile that reads REQ-G and, where one exists, the baseline ARC-G,
and PROPOSES a core architecture: Containers, Components, External Systems, the
Design Techniques that deliver the stated quality attributes, the Architecture
Patterns it adopts from the catalogue, and the Quality Scenarios that make each
quality attribute testable.

WHAT IT IS NOT. It is not an extractor: there is no document, so it cannot quote
one, and `source_text` will be empty on its facts. It is not an authority: every
fact it produces carries `SOURCE_DESIGN_ASSISTANT` provenance and `UNVERIFIED`
status, so the review gate treats a proposal as a proposal. And it is not a
replacement for the architect — the PRD's step 5 is "Agent suggests, Architect
refines and approves".

WHY IT IS A PROFILE AND NOT A PIPELINE
--------------------------------------
Its output is the same kind of thing architecture extraction produces, so it reuses
the same pass harness, the same record schemas, the same merge, the same validators
and the same ingest. The one genuinely new problem — "what is the document when
there is no document?" — is answered by `core.knowledge.digest`, which is passed in
as a single chunk. One chunk on purpose: a design is a global act, and splitting it
would let one call choose a monolith and the next a microservice.
"""

from typing import Any, Dict, List, Optional

from core.knowledge import KnowledgeGraph
from core.knowledge.digest import design_input
from core.patterns import (
    DEFAULT_CATALOGUE_PATH,
    PatternCatalogue,
    canonical_pattern,
    load_pattern_catalogue,
    pattern_prompt_context,
    validate_pattern_catalogue,
)

from ..architecture_extraction.agent import ArchitectureExtractionAgent, _decision_key
from ..base_agent import AgentResult
from ..extraction import (
    check_connection_endpoints,
    check_containment,
    check_containment_kinds,
    check_deployment_levels,
    check_element_types,
    check_enum_membership,
    check_nonempty_field,
    check_object_contract,
    check_reference_kinds,
    completeness,
    connection_key,
    element_key,
    merge_records,
    merge_triples,
    named_key,
)
from ..extraction.passes import Chunk, collect, outcome_records, run_passes, summarise
from .passes import design_passes
from .validators import (
    check_decision_affects_known_elements,
    check_decision_supersedes_known,
    check_grounded_elements,
    check_name_collisions,
    check_pattern_resolution,
    check_quality_linkage,
    check_scenario_shape,
    check_techniques_are_linked,
    check_techniques_are_mechanisms,
)


def _reference_key(record: Dict[str, Any]) -> tuple:
    """Identity of a traceability reference, for merging across passes.

    Duplicated from the architecture profile rather than imported, because it is
    private there and the two profiles may legitimately diverge: a design proposal
    has no `source_text` to key on, so a reference is its three named parts.
    """
    return (
        str(record.get("element") or "").strip().lower(),
        str(record.get("relationship") or "").strip().lower(),
        str(record.get("reference") or "").strip().lower(),
    )


class DesignAssistantAgent(ArchitectureExtractionAgent):
    """Proposes a core ARC-G from REQ-G and the baseline."""

    # ------------------------------------------------------------------

    def _catalogue(self) -> PatternCatalogue:
        """The pattern library in force. Missing is survivable, and says so."""
        return load_pattern_catalogue(getattr(self.config, "pattern_catalogue", "")
                                      or DEFAULT_CATALOGUE_PATH)

    def run(self, input_data: Dict[str, Any]) -> AgentResult:
        try:
            graph = input_data.get("graph")
            if graph is None or not isinstance(graph, KnowledgeGraph):
                return AgentResult(
                    success=False, output=None,
                    errors=["No knowledge graph provided — a design run reads REQ-G, "
                            "not a document"],
                )

            baseline: Optional[KnowledgeGraph] = input_data.get("baseline")
            initiative_id = str(input_data.get("initiative_id") or "")
            base_ref = str(input_data.get("base_ref") or "")
            # A frozen revision and the promoted baseline are both "the architecture
            # to extend", and they promise different things. The prompt says which
            # one this run was handed rather than letting the reader assume.
            baseline_promoted = bool(input_data.get("baseline_promoted"))

            catalogue = self._catalogue()
            model = self._ontology_model()
            if model is not None:
                for finding in validate_pattern_catalogue(catalogue, model):
                    self.log(f"  pattern catalogue: {finding}", level="warning")

            # ---- 1. the input document: REQ-G + the baseline ARC-G ----
            digest = design_input(
                graph, baseline=baseline, initiative_id=initiative_id,
                base_ref=base_ref, promoted_baseline=baseline_promoted,
            )
            for caveat in digest.caveats:
                self.log(f"  digest caveat: {caveat}", level="warning")
            self.log(
                f"Design input: {len(digest.text):,} chars from "
                f"{digest.counts.get('requirements', 0)} requirement(s) and "
                f"{digest.counts.get('containers', 0)} existing container(s)"
            )

            chunks = [Chunk(index=0, total=1, text=digest.text,
                            heading_path="REQ-G + baseline ARC-G")]

            # ---- 2. run every pass over that one chunk ----
            #
            # The quality attributes REQ-G STATES are the techniques pass's job
            # list, so they are gathered here and bound into that pass rather than
            # left for the model to infer from prose. This is the YB-038 fix: an
            # open-ended request for "mechanisms" came back as the requirements'
            # own names, because the task had no closed set to answer.
            stated_attributes = self._stated_quality_attributes(graph)
            self.log(
                f"Stated quality attributes to answer: "
                f"{', '.join(stated_attributes) if stated_attributes else '(none)'}"
            )
            shared = self._format_ontology_context()
            passes = design_passes(
                pattern_prompt_context(catalogue), quality_attributes=stated_attributes
            )
            outcomes = run_passes(self, passes, chunks, shared, log=self.log,
                                  progress=input_data.get("progress"))
            summary = summarise(outcomes, len(chunks), len(passes))
            self.log(summary.describe(len(chunks), len(passes)),
                     level="success" if summary.failed == 0 else "warning")

            # ---- 3. merge across passes ----
            triple_groups: List[List[Any]] = []
            for spec in passes:
                triple_groups.extend(collect(outcomes, spec.name, "triples"))
            triples = merge_triples(triple_groups)
            elements = merge_records(
                collect(outcomes, "structure", "elements"), element_key, completeness)
            connections = merge_records(
                collect(outcomes, "connections", "connections"), connection_key, completeness)
            techniques = merge_records(
                collect(outcomes, "techniques", "design_techniques"), named_key, completeness)
            patterns = merge_records(
                collect(outcomes, "patterns", "architecture_patterns"), named_key, completeness)
            scenarios = merge_records(
                collect(outcomes, "scenarios", "quality_scenarios"), named_key, completeness)
            references = merge_records(
                collect(outcomes, "traceability", "references"), _reference_key, completeness)
            decisions = merge_records(
                collect(outcomes, "decisions", "architecture_decisions"), _decision_key, completeness)

            self.log(
                f"Proposed: {len(elements)} elements, {len(connections)} connections, "
                f"{len(techniques)} techniques, {len(patterns)} patterns, "
                f"{len(scenarios)} scenarios, {len(decisions)} decisions, "
                f"{len(references)} references"
            )

            # ---- 4. resolve the chosen patterns against the catalogue ----
            #
            # Deterministic, and separate from the validators so the page can show
            # what each name resolved TO (category, mechanism, trade-offs) rather
            # than only whether it resolved.
            resolutions = [
                canonical_pattern(str(p.get("name") or ""), catalogue).to_dict()
                for p in patterns
            ]

            # ---- 5. validate ----
            flags = []
            flags += check_object_contract(triples)
            flags += check_containment(elements, triples)
            # No repair runs in this profile, so nothing is excluded as inferred.
            flags += check_containment_kinds(elements)
            # The design profile reuses `StructurePassResult`, so it can propose a
            # DeploymentNode with `serves` too — and would otherwise have the same
            # unchecked reference range the extraction profile just closed.
            flags += check_reference_kinds(elements)
            flags += check_element_types(elements)
            flags += check_deployment_levels(elements)
            flags += check_enum_membership(elements)
            flags += check_nonempty_field(elements, "container_type",
                                          applies_to=("Container", "DataStore"))
            # `known_labels` is the existing graph, and it is load-bearing here: this
            # profile is told to REUSE an existing element by its exact name rather
            # than re-propose it (structure rule 3), so an endpoint that Input 2
            # declares legitimately appears in no proposed element record. Without
            # the graph, every reused endpoint would be reported as undeclared.
            flags += check_connection_endpoints(
                connections, elements, known_labels=list(self._existing_kinds(graph))
            )
            flags += check_grounded_elements(elements, references)
            flags += check_scenario_shape(scenarios)
            flags += check_pattern_resolution(patterns, catalogue)
            flags += check_quality_linkage(techniques, patterns, catalogue)
            flags += check_techniques_are_mechanisms(
                techniques, self._requirement_labels(graph)
            )
            flags += check_techniques_are_linked(techniques)
            flags += check_name_collisions(elements, self._existing_kinds(graph))
            flags += check_decision_affects_known_elements(
                decisions, elements, known_labels=list(self._existing_kinds(graph))
            )
            flags += check_decision_supersedes_known(
                decisions, self._existing_decision_titles(baseline)
            )
            findings = [f.to_dict() for f in flags]
            if findings:
                self.log(f"  {len(findings)} finding(s) flagged for review", level="warning")

            # ---- 6. output ----
            pass_records = self._outcome_records(outcomes)
            output = {
                "triples": triples,
                "elements": [self._as_output_dict(e) for e in elements],
                "connections": [self._as_output_dict(c) for c in connections],
                "design_techniques": [self._as_output_dict(t) for t in techniques],
                "architecture_patterns": [self._as_output_dict(p) for p in patterns],
                "quality_scenarios": [self._as_output_dict(s) for s in scenarios],
                "architecture_decisions": [self._as_output_dict(d) for d in decisions],
                "references": [self._as_output_dict(r) for r in references],
                "pattern_resolutions": resolutions,
                "findings": findings,
                # The prompt input itself. Ingest ignores keys it does not know, so
                # this rides along for the audit trail — "what did the model actually
                # see" is the first question asked of a surprising proposal, and the
                # digest is deterministic so this is reproducible rather than a log.
                "design_digest": digest.text,
                "statistics": {
                    "total_triples": len(triples),
                    "total_elements": len(elements),
                    "total_connections": len(connections),
                    "total_design_techniques": len(techniques),
                    "total_architecture_patterns": len(patterns),
                    "total_quality_scenarios": len(scenarios),
                    "total_architecture_decisions": len(decisions),
                    "total_references": len(references),
                    "patterns_resolved": sum(1 for r in resolutions if r["resolved"]),
                    "findings": len(findings),
                    "chunks": len(chunks),
                    "passes": len(passes),
                    "model_calls": summary.total_calls,
                    "text_fallbacks": summary.text_fallbacks,
                    "catalogue_size": len(catalogue.entries),
                },
            }

            return AgentResult(
                success=True,
                output=output,
                confidence=self._overall_confidence(triples),
                metadata={
                    # ARCHITECTURE, not "design": the proposed nodes ARE
                    # architecture-side, and `reconcile._node_sides` reads this to
                    # decide which side of the join a node is on. What makes it a
                    # proposal is the provenance source and the document_ref below,
                    # not a third side the reconciliation would have to learn.
                    "document_type": "architecture",
                    "domain": input_data.get("domain", "generic"),
                    "domain_pack": self.active_domain_pack_id(),
                    "extraction_path": "design_passes",
                    "document_chars": len(digest.text),
                    "chunks": len(chunks),
                    "model_calls": summary.total_calls,
                    "text_fallback_calls": summary.text_fallbacks,
                    "failed_calls": summary.failed,
                    "empty_calls": summary.empty,
                    "elapsed_seconds": round(summary.elapsed, 1),
                    "passes": [self._pass_record_dict(p) for p in pass_records],
                    "findings": len(findings),
                    # The design-specific provenance: what the proposal was grounded
                    # on, and what the digest had to leave out to fit.
                    "design_base": digest.base_ref,
                    "design_caveats": list(digest.caveats),
                    "pattern_catalogue": catalogue.path,
                    # What the run cost. A hosted endpoint bills per token, and
                    # the run that cannot say what it consumed cannot be budgeted.
                    "usage": self.usage_totals(),
                },
            )

        except Exception as exc:                                     # noqa: BLE001
            self.log(f"Design run failed: {exc}", level="error")
            return AgentResult(success=False, output=None, errors=[str(exc)])

    # ------------------------------------------------------------------

    @staticmethod
    def _stated_quality_attributes(graph: KnowledgeGraph) -> List[str]:
        """The quality attributes REQ-G states, as the closed list the design must answer.

        Read through `quality_report` rather than off the NFR nodes directly,
        because the census is what already decides that `Availability` and
        `High Availability` are ONE concern. Two spellings of one attribute would
        otherwise become two list items and the pass would answer it twice.

        "Stated" specifically: an attribute the architecture delivers and no
        requirement asks for is not a gap for this pass to close, and asking for a
        technique for it would invent work.
        """
        from core.knowledge.quality import quality_report

        report = quality_report(graph)
        return [
            str(entry.get("label") or "").strip()
            for entry in report.get("attributes", [])
            if (entry.get("states") or {}).get("stated_in_requirements")
            and str(entry.get("label") or "").strip()
        ]

    @staticmethod
    def _existing_kinds(graph: KnowledgeGraph) -> Dict[str, str]:
        """label (lowercased) -> kind, for the collision check."""
        return {n.label.strip().lower(): n.kind for n in graph.nodes.values()}

    @staticmethod
    def _existing_decision_titles(baseline: Optional[KnowledgeGraph]) -> List[str]:
        """Decision titles the design input showed, for the supersedes check.

        The digest merges the recorded ADRs into the architecture source, and the
        baseline may already carry ArchitectureDecision nodes — both are decisions
        a proposal may legitimately claim to replace.
        """
        from core.knowledge.decisions import DEFAULT_DECISIONS_DIR, load_adr_records

        titles = {
            str(record.get("title") or "").strip().lower()
            for record in load_adr_records(DEFAULT_DECISIONS_DIR)
        }
        if baseline is not None:
            titles |= {
                node.label.strip().lower()
                for node in baseline.nodes.values()
                if node.kind == "ArchitectureDecision"
            }
        return sorted(title for title in titles if title)

    @staticmethod
    def _requirement_labels(graph: KnowledgeGraph) -> List[str]:
        """Every requirement label in the graph, for the mechanism check."""
        from core.knowledge.model import REQUIREMENT_KINDS

        return [n.label for n in graph.nodes.values() if n.kind in REQUIREMENT_KINDS]

    def _ontology_model(self):
        """The parsed ontology, for catalogue validation. None when unavailable."""
        try:
            from core.ontology import load_ontology

            return load_ontology(self.config.ontology_dir or "ontology")
        except Exception:                                            # noqa: BLE001
            return None

    @staticmethod
    def _outcome_records(outcomes) -> List[Any]:
        """One `PassRecord` per pass attempt, so the run can report its own completeness.

        Shared with the architecture profile, which now records its real passes
        too rather than leaving ingest to reconstruct `(unspecified)` entries.
        This is what ADR-0013 asked for, applied to every profile that runs passes.
        """
        return outcome_records(outcomes)


def create_design_assistant_agent() -> DesignAssistantAgent:
    """Factory for the Design Assistant agent."""
    from config import get_default_agent_config

    from ..base_agent import AgentConfig

    config_dict = get_default_agent_config("design_assistant")
    return DesignAssistantAgent(AgentConfig(**config_dict))
