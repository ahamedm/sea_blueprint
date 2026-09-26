"""
Knowledge Extraction Agent

Transforms unstructured human language (Markdown documents) into structured
knowledge graph triples using the SEA requirements ontology.

Uses Strands structured output for type-safe, validated extraction results.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator
from ..base_agent import SEABaseAgent, AgentConfig, AgentResult
from ..extraction import (
    chunk_document,
    completeness,
    merge_records,
    merge_triples,
    summarise_chunks,
)
from ..extraction.quality import enrich_entities
from core.knowledge.model import PassRecord

# ============================================================================
# Structured Output Models
# ============================================================================
#
# SCHEMA DESIGN NOTE — these models are deliberately PERMISSIVE.
#
# Every `required` field is a way for a small local model to fail validation,
# which triggers an SDK re-prompt, which is what caused the original endless
# retry loop. So:
#   - nearly everything has a sensible default
#   - confidence accepts "0.95", "95%", 95, or garbage (coerced, never rejected)
#   - source_text / description are optional but encouraged in the prompt
#
# The goal is: the model's *content* is judged, not its formatting discipline.
# Formatting variance should be normalised by validators, not rejected.
# ============================================================================

# The worked example used when no domain pack supplies one.
#
# It was previously a payment example embedded in the base prompt, which meant
# every extraction run — whatever the subject — was primed with payments. A pack
# may override it via its `worked_example` annotation; this default is
# deliberately subject-neutral, because what the example has to teach is the
# *shape* of a good triple, and that is the one part no domain should restate.
#
# NOTE: kept as a plain (non-f) string, so JSON braces here are literal and the
# example reaches the model exactly as written.
_GENERIC_WORKED_EXAMPLE = '''\
Source: *"The Fulfilment Platform shall dispatch confirmed orders within the
promised window. Each order must be assigned to a single Stock Location.
Dispatch criteria shall include Destination, Service Level, and Item Weight."*

WRONG — clause objects, comma-joined list, headings as entities:
```json
{
  "triples": [
    {"subject": "Fulfilment Platform", "predicate": "has_functional_requirement",
      "object": "Dispatch confirmed orders within the promised window", "confidence": 1.0},
    {"subject": "Dispatch Criteria", "predicate": "has_criteria",
      "object": "Destination, Service Level, Item Weight", "confidence": 1.0},
    {"subject": "Performance Requirements", "predicate": "has_nfr",
      "object": "Complete within 500 milliseconds", "confidence": 1.0}
  ]
}
```

RIGHT — named concepts, one triple per item, no headings:
```json
{
  "triples": [
    {"subject": "Fulfilment Platform", "predicate": "has_functional_requirement",
      "object": "Order Dispatch", "confidence": 1.0,
      "ontology_class": "FunctionalRequirement"},
    {"subject": "Order", "predicate": "assigned_to",
      "object": "Stock Location", "confidence": 1.0,
      "ontology_class": "DomainConcept"},
    {"subject": "Dispatch Criteria", "predicate": "is_determined_by",
      "object": "Destination", "confidence": 1.0,
      "ontology_class": "ConceptAttribute"},
    {"subject": "Dispatch Criteria", "predicate": "is_determined_by",
      "object": "Service Level", "confidence": 1.0,
      "ontology_class": "ConceptAttribute"},
    {"subject": "Dispatch Criteria", "predicate": "is_determined_by",
      "object": "Item Weight", "confidence": 1.0,
      "ontology_class": "ConceptAttribute"}
  ]
}
```

Note the trade: the RIGHT version has **more** triples from the same source. Splitting
lists into separate triples is not losing information — it is making each criterion
independently queryable, which is the entire point of the graph.'''


class ExtractedTriple(BaseModel):
    """A single extracted knowledge triple with ontology mapping.
    
    OBJECT CONTRACT — the field descriptions below are the primary prompt for
    structured output, so they carry the graph-shape rules directly.
    
    Subject and object must NAME a thing, not describe behaviour. They become
    graph nodes; a clause becomes a dangling node that cannot link or be queried.
    """
    
    subject: str = Field(
        ...,
        description=(
            "NAME of the subject entity: a short noun phrase (2-5 words), "
            "e.g. 'Payment Gateway Platform', 'Cardholder Data'. "
            "Never a sentence, never a description of behaviour."
        ),
    )
    predicate: str = Field(
        ...,
        description=(
            "Relationship in snake_case, reading as a verb phrase, "
            "e.g. 'has_functional_requirement', 'traces_to_goal', 'encrypted_using'."
        ),
    )
    object: str = Field(
        ...,
        description=(
            "NAME of the object entity: a short noun phrase (2-5 words), "
            "e.g. 'Payment Request Validation', 'Cardholder Data', 'TLS 1.2+'. "
            "NEVER a sentence, clause, or verb phrase. "
            "WRONG: 'Accept and validate incoming payment requests'. "
            "RIGHT: 'Payment Request Validation'. "
            "A short qualifier in parentheses is acceptable, e.g. 'Authorization Latency (<=500ms)'. "
            "If the source names several things, emit ONE TRIPLE PER THING rather "
            "than joining them with commas."
        ),
    )
    confidence: float = Field(
        default=0.8, ge=0.0, le=1.0,
        description=(
            "Confidence between 0.0 and 1.0. Vary this — do not default everything "
            "to 1.0. Use 0.9-1.0 for explicitly stated facts, 0.7-0.9 for clearly "
            "implied, 0.5-0.7 for inferred, below 0.5 for speculative."
        ),
    )
    source_text: str = Field(
        default="", description="Original sentence this triple was extracted from"
    )
    ontology_class: Optional[str] = Field(
        None,
        description=(
            "Ontology class of the OBJECT, chosen from the listed classes, "
            "e.g. FunctionalRequirement, BusinessGoal, DomainConcept."
        ),
    )
    requirement_type: Optional[str] = Field(
        None, description="One of: BUSINESS, FUNCTIONAL, NON_FUNCTIONAL, CONSTRAINT"
    )
    
    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, v):
        """Accept 0.95, '0.95', '95%', 95, or None — never fail on formatting.
        
        Percentage detection only fires above 2.0. Values in (1.0, 2.0] are
        treated as an out-of-range score to CLAMP, not as a percentage — dividing
        1.5 by 100 would silently turn a high-confidence triple into a near-zero
        one, which is worse than rejecting it.
        """
        if v is None:
            return 0.8
        if isinstance(v, str):
            v = v.strip().rstrip("%")
            try:
                v = float(v)
            except (ValueError, TypeError):
                return 0.8
        try:
            v = float(v)
        except (ValueError, TypeError):
            return 0.8
        if v > 2.0:            # e.g. 95 or 150 -> percentage
            v = v / 100.0
        return max(0.0, min(1.0, v))


class ExtractedEntity(BaseModel):
    """An extracted business entity with ontology mapping."""
    
    name: str = Field(..., description="Entity name (short noun phrase)")
    entity_type: str = Field(
        default="DomainConcept",
        description="Entity type, e.g. Stakeholder, System, BusinessProcess, DomainConcept"
    )
    description: str = Field(default="", description="Brief description of the entity")
    ontology_class: Optional[str] = Field(
        None, description="Mapped ontology class from the provided ontology"
    )
    requirement_type: Optional[str] = Field(
        None, description="One of: BUSINESS, FUNCTIONAL, NON_FUNCTIONAL, CONSTRAINT"
    )
    requirement_id: str = Field(
        default="",
        description=(
            "Stable identifier for this requirement EXACTLY as the source gives "
            "it, e.g. 'FR-PM-001' or 'NFR-SC-002'. Preserve it verbatim — it is "
            "the join key architecture uses to trace back to this requirement. "
            "Leave empty only if the source genuinely provides no identifier; do "
            "not invent one."
        ),
    )
    initiative_refs: List[str] = Field(
        default_factory=list,
        description=(
            "Identifiers of any Initiative / business case / work item the source "
            "associates with this entity, e.g. 'INIT-2026-014'. Verbatim."
        ),
    )
    # --- quality classification (only for non-functional requirements) ---
    #
    # Two levels, because the ISO 25010 characteristic alone cannot separate
    # concerns that are met by entirely different designs: "within 500ms" and
    # "1000 TPS with horizontal scaling" are both PERFORMANCE_EFFICIENCY, but
    # they are TIME_BEHAVIOUR and SCALABILITY, and one is answered by caching
    # while the other needs statelessness plus replication.
    quality_category: str = Field(
        default="",
        description=(
            "For a NonFunctionalRequirement ONLY: the ISO 25010:2023 top-level "
            "characteristic — one of FUNCTIONAL_SUITABILITY, "
            "PERFORMANCE_EFFICIENCY, COMPATIBILITY, INTERACTION_CAPABILITY, "
            "RELIABILITY, SECURITY, MAINTAINABILITY, FLEXIBILITY, SAFETY. "
            "REGULATORY_COMPLIANCE is available for legal/contractual obligations, "
            "which are not an ISO 25010 characteristic. Empty for anything that is "
            "not an NFR."
        ),
    )
    subcharacteristic: str = Field(
        default="",
        description=(
            "For a NonFunctionalRequirement ONLY: the precise ISO 25010:2023 "
            "sub-characteristic the requirement targets, when the source makes it "
            "clear. Examples: 'within 500 milliseconds' -> TIME_BEHAVIOUR; "
            "'1000 TPS with horizontal scaling' -> SCALABILITY; 'must be encrypted' "
            "-> CONFIDENTIALITY; 'must be available 99.9%' -> AVAILABILITY. "
            "Empty if only the top-level characteristic is clear."
        ),
    )
    quality_attribute: str = Field(
        default="",
        description=(
            "For a NonFunctionalRequirement ONLY: the quality ATTRIBUTE this "
            "requirement is about, as a name — 'Time Behaviour', 'Scalability', "
            "'Availability', 'Confidentiality'. Use the standard's own term, never "
            "a mechanism: 'Redundancy' is how availability is delivered, not the "
            "attribute. Empty if the requirement states no clear quality concern."
        ),
    )


class ExtractedRelationship(BaseModel):
    """An extracted relationship type (predicate)."""
    
    relationship_type: str = Field(..., description="Predicate name in snake_case")
    description: str = Field(default="", description="What the relationship means")


class ExtractionResult(BaseModel):
    """Structured output model for knowledge extraction."""
    
    triples: List[ExtractedTriple] = Field(
        default_factory=list, description="Extracted knowledge triples (the primary output)"
    )
    entities: List[ExtractedEntity] = Field(
        default_factory=list, description="Extracted entities"
    )
    relationships: List[ExtractedRelationship] = Field(
        default_factory=list, description="Relationship types used in the triples"
    )


# ============================================================================
# Agent Implementation
# ============================================================================


def _entity_key(record: Any) -> tuple:
    """Identity of an entity across chunks: its name within its own class.

    The class is part of the key on purpose. Two chunks can name the same string
    as different things, and collapsing them here would decide a conflict that
    ingest reports as a finding instead.
    """
    return (
        str(record.get("name") or "").strip().lower(),
        str(record.get("ontology_class") or "").strip(),
    )


def _relationship_key(record: Any) -> tuple:
    """Identity of a relationship across chunks: source, predicate, target."""
    return (
        str(record.get("source") or "").strip().lower(),
        str(record.get("predicate") or "").strip().lower(),
        str(record.get("target") or "").strip().lower(),
    )


def _as_records(record_cls: Any, items: Any) -> List[Any]:
    """Rebuild typed records from the dicts `merge_records`/`merge_triples` return.

    The merge layer works in dicts so it can be shared between profiles; the rest of
    this module works in pydantic records, because the deterministic classifiers and
    the output envelope both expect attributes rather than keys.
    """
    out: List[Any] = []
    for item in items or []:
        out.append(item if isinstance(item, record_cls) else record_cls(**item))
    return out


class KnowledgeExtractionAgent(SEABaseAgent):
    """
    Agent for extracting structured knowledge from unstructured documents.
    
    Uses Strands structured output to get validated, type-safe extraction results
    instead of parsing JSON from text responses.
    
    Key features:
    - Parses Markdown documents
    - Identifies entities and relationships
    - Maps concepts to ontology terms
    - Assigns confidence scores (0.0-1.0) to each triple
    - Flags low-confidence items for human review
    """
    
    def __init__(self, config: AgentConfig):
        super().__init__(config)
        self.confidence_threshold = 0.7  # Below this requires human review
    
    # ------------------------------------------------------------------
    # Extraction-profile hooks (overridable by subclasses)
    # ------------------------------------------------------------------
    # Different graphs (REQ-G, ARC-G) differ only in: the structured schema,
    # the prompt, and what the node/edge lists are called. Everything else —
    # guard, fallbacks, parsing, contract validation, statistics — is shared.
    # Subclasses override these three and reuse the rest.
    
    def _schema(self):
        """Structured-output schema class for this extraction profile."""
        return ExtractionResult
    
    def _unpack_structured(self, structured):
        """Map a validated schema instance to (triples, nodes, edges)."""
        return (
            list(structured.triples),
            list(structured.entities),
            list(structured.relationships),
        )
    
    def _output_keys(self):
        """Internal node/edge names -> keys used in the output dict."""
        return {"nodes": "entities", "edges": "relationships"}
    
    def _profile_flags(self, triples, nodes, edges) -> List[Dict[str, Any]]:
        """Profile-specific structural checks, appended to contract violations.
        
        Overridden by subclasses whose graph has structural invariants beyond the
        object contract (e.g. ARC-G containment). Returns [] by default.
        """
        return []
    
    def _extra_collections(self, structured) -> Dict[str, Any]:
        """Additional top-level output collections from the structured result.
        
        Lets a profile surface construct types that are neither nodes nor edges
        in its primary lists — e.g. ARC-G's technology stacks and architecture
        styles. Returns {} by default.
        """
        return {}
    
    def run(self, input_data: Dict[str, Any]) -> AgentResult:
        """
        Extract knowledge from a document.
        
        Strategy: structured-output-first with a text-parsing fallback.
        
          1. Ask the model for validated structured output (Pydantic schema).
             Guarded by a hard turn cap so a model that can't satisfy the schema
             fails fast instead of looping (see SEABaseAgent.invoke_structured).
          2. If that returns nothing, fall back to the free-form path and parse
             the text response. Entities are then derived from triples if the
             model didn't emit an explicit entity list.
        
        Whichever path is used is recorded in `metadata["extraction_path"]` so
        runs can be compared rather than guessed at.
        
        Args:
            input_data: Dictionary containing:
                - document: Markdown document text
                - document_type: Type of document (requirements, architecture, etc.)
                - domain: Business domain (optional)
                - force_text_parsing: bool — skip structured output for this run
                
        Returns:
            AgentResult with extracted triples and metadata
        """
        try:
            document = input_data.get("document", "")
            document_type = input_data.get("document_type", "requirements")
            domain = input_data.get("domain", "generic")
            # The vocabulary in force. Read from the agent rather than from
            # `input_data`, because the pack is compiled into the system prompt at
            # selection time — a value passed in here would be metadata that
            # describes nothing, which is precisely what the old `domain` field was.
            domain_pack = self.active_domain_pack_id()
            force_text = bool(input_data.get("force_text_parsing", False))
            
            if not document:
                return AgentResult(
                    success=False,
                    output=None,
                    errors=["No document provided"],
                )
            
            self.log(f"Extracting knowledge from {document_type} document...")

            # ---- 1. chunk ----
            #
            # THIS PROFILE USED TO BE ONE CALL. The architecture profile has always
            # chunked and merged; this one sent the whole document and asked for
            # every collection back in one response. That was survivable while the
            # corpus was samples, and is the wrong shape for a real PRD: the output
            # budget is the document's, one dropped collection cannot be recovered,
            # and a truncated response is a failed run rather than a partial one.
            #
            # A document that fits one chunk takes EXACTLY the old path — the same
            # prompt, no merge, no conversion — so nothing about small-document
            # behaviour moves. Only a document that does not fit is chunked.
            max_chars = int(input_data.get("chunk_max_chars", 7000))
            chunks = chunk_document(document, max_chars=max_chars)
            if len(chunks) > 1:
                self.log(f"Document: {len(document):,} chars — {summarise_chunks(chunks)}")

            use_structured = self.config.use_structured_output and not force_text
            path = "text_parsing"
            structured_obj = None
            structured_error: Optional[str] = None
            pass_records: List[Any] = []
            per_chunk: List[tuple] = []
            extras: dict = {}

            # ---- 2. extract each chunk ----
            for chunk in chunks:
                prompt = self._build_extraction_prompt(chunk.text, document_type, domain)
                c_triples, c_entities, c_relationships, c_path, c_error, c_obj = (
                    self._extract_from_prompt(prompt, use_structured)
                )
                per_chunk.append((c_triples, c_entities, c_relationships))
                if c_path == "structured_output":
                    path = "structured_output"
                if c_error and not structured_error:
                    structured_error = c_error
                if c_obj is not None:
                    structured_obj = structured_obj or c_obj
                    for key, values in (self._extra_collections(c_obj) or {}).items():
                        extras.setdefault(key, []).extend(values or [])
                pass_records.extend(self._pass_records(
                    path=c_path,
                    structured_error=c_error,
                    triples=len(c_triples),
                    entities=len(c_entities),
                    chunk_label=chunk.label if len(chunks) > 1 else "",
                ))

            # ---- 3. merge across chunks ----
            if len(chunks) == 1:
                triples, entities, relationships = per_chunk[0]
            else:
                triples = _as_records(
                    ExtractedTriple, merge_triples([g[0] for g in per_chunk])
                )
                entities = _as_records(
                    ExtractedEntity,
                    merge_records([g[1] for g in per_chunk], _entity_key, completeness),
                )
                relationships = _as_records(
                    ExtractedRelationship,
                    merge_records([g[2] for g in per_chunk], _relationship_key, completeness),
                )
                self.log(
                    f"  Merged {len(chunks)} chunks: {len(triples)} triples, "
                    f"{len(entities)} entities, {len(relationships)} relationships"
                )
            
            # ---- Normalise ----
            # Derive entities from triples if none were produced. Subjects and
            # objects ARE the graph nodes, so this is always semantically valid.
            if not entities and triples:
                entities = self._derive_entities_from_triples(triples)
                self.log(
                    f"  Derived {len(entities)} entities from triples",
                    level="info"
                )
            
            # Derive relationships from triple predicates if none were produced.
            # The predicate IS the relationship, so this keeps the two views
            # self-consistent by construction.
            if not relationships and triples:
                relationships = self._derive_relationships_from_triples(triples)
                self.log(
                    f"  Derived {len(relationships)} relationships from triple predicates",
                    level="info"
                )

            # ---- Deterministic quality classification ----
            #
            # Runs AFTER both paths, on whatever entities exist, so it also reaches
            # the entities derived from triples — which is the only kind the text
            # path produces. Measured: the model leaves `quality_category`,
            # `subcharacteristic` and `quality_attribute` empty on every NFR even
            # with the ISO taxonomy in the prompt, and invents its own attribute
            # names instead. Mapping wording onto a closed taxonomy is a mechanical
            # task, so it is done here rather than asked for.
            #
            # Fills only what the model left blank; its answer wins where it
            # committed to one.
            if entities:
                classified = enrich_entities([e.model_dump() for e in entities], document)
                gained = sum(
                    1
                    for before, after in zip(entities, classified)
                    if not before.quality_category and after.get("quality_category")
                )
                if gained:
                    self.log(
                        f"  Classified {gained} requirement(s) against ISO/IEC 25010 "
                        f"(deterministic keyword pass)",
                        level="info",
                    )
                entities = [type(e)(**item) for e, item in zip(entities, classified)]
            
            # Separate low-confidence items
            low_confidence = [
                t for t in triples 
                if t.confidence < self.confidence_threshold
            ]
            
            # Calculate statistics
            statistics = self._calculate_statistics_from_lists(triples, entities, relationships)
            
            # Deterministic object-contract check (independent of model behaviour).
            # Flags for human review; never drops content.
            contract_flags = self._flag_contract_violations(triples)
            
            # Profile-specific structural checks (e.g. ARC-G containment).
            profile_flags = self._profile_flags(triples, entities, relationships)
            contract_flags.extend(profile_flags)
            
            if contract_flags:
                self.log(
                    f"  {len(contract_flags)}/{len(triples)} triples violate the object "
                    f"contract (clause-shaped or comma-listed nodes) — flagged for review",
                    level="warning",
                )
            
            self.log(
                f"Extracted {len(triples)} triples, {len(entities)} entities, "
                f"{len(relationships)} relationships ({len(low_confidence)} low confidence)",
                level="success"
            )
            
            # Build output dict. Node/edge key names come from the profile hook
            # so an architecture run emits `elements`/`connections` while a
            # requirements run emits `entities`/`relationships`.
            keys = self._output_keys()
            output = {
                "triples": [t.model_dump() for t in triples],
                keys["nodes"]: [e.model_dump() for e in entities],
                keys["edges"]: [r.model_dump() for r in relationships],
                "low_confidence_items": [t.model_dump() for t in low_confidence],
                "contract_violations": contract_flags,
                "statistics": statistics,
            }
            
            # Profile-specific extra collections (e.g. ARC-G technology stacks
            # and architecture styles, which are neither nodes nor edges). Gathered
            # across every chunk by the loop above, so a collection split by a chunk
            # boundary is reunited rather than half-lost.
            for key, values in extras.items():
                output[key] = [
                    v.model_dump() if hasattr(v, "model_dump") else v for v in values
                ]

            fell_back_to_text = any(p.path == "text" for p in pass_records)
            
            return AgentResult(
                success=True,
                output=output,
                confidence=self._calculate_overall_confidence_from_list(triples),
                metadata={
                    "document_type": document_type,
                    "domain": domain,
                    "domain_pack": domain_pack,
                    "extraction_path": path,
                    "structured_output_error": structured_error,
                    "total_triples": len(triples),
                    "total_entities": len(entities),
                    "total_relationships": len(relationships),
                    "low_confidence_count": len(low_confidence),
                    "contract_violation_count": len(contract_flags),
                    # Completeness input. Without these the run reports UNKNOWN,
                    # which is indistinguishable from "we could not tell" — see
                    # the docstring on `_pass_records`.
                    "model_id": self.config.model_id,
                    "model_calls": len(pass_records),
                    "failed_calls": sum(1 for p in pass_records if p.outcome == "failed"),
                    "empty_calls": sum(1 for p in pass_records if p.outcome == "empty"),
                    "text_fallback_calls": int(fell_back_to_text),
                    "passes": [self._pass_record_dict(p) for p in pass_records],
                    # What the run cost. A hosted endpoint bills per token, and a
                    # run that cannot say what it consumed cannot be budgeted or
                    # compared against a cheaper one.
                    "usage": self.usage_totals(),
                },
            )
            
        except Exception as e:
            self.log(f"Extraction failed: {str(e)}", level="error")
            return AgentResult(
                success=False,
                output=None,
                errors=[str(e)],
            )

    # ------------------------------------------------------------------
    # Completeness — what this run actually managed to read
    # ------------------------------------------------------------------

    def _extract_from_prompt(
        self, extraction_prompt: str, use_structured: bool
    ) -> tuple:
        """One extraction attempt-pair over one prompt: structured, then text.

        Returns `(triples, entities, relationships, path, structured_error, obj)`.
        Extracted from `run()` so a chunked document runs the SAME two attempts per
        chunk rather than a second, drifting copy of them.

        Both attempts live together on purpose: the text fallback exists because the
        structured path fails often on a weak model, and splitting them across
        methods is how one of them silently stops being called.
        """
        path = "text_parsing"
        triples: List[ExtractedTriple] = []
        entities: List[ExtractedEntity] = []
        relationships: List[ExtractedRelationship] = []
        structured_obj = None
        structured_error: Optional[str] = None

        # ---- Attempt 1: structured output (guarded) ----
        if use_structured:
            self.log(
                f"Attempting structured output "
                f"(turn cap: {self.config.max_structured_turns})..."
            )
            structured = self.invoke_structured(extraction_prompt, self._schema())

            if structured is not None:
                structured_obj = structured
                triples, entities, relationships = self._unpack_structured(structured)
                if triples or entities:
                    path = "structured_output"
                    self.log(
                        f"  Structured output accepted: {len(triples)} triples, "
                        f"{len(entities)} entities, {len(relationships)} relationships",
                        level="success",
                    )
                else:
                    structured_error = "structured output returned empty"
                    self.log(
                        "  Structured output empty — falling back to text parsing",
                        level="warning",
                    )
            else:
                structured_error = "model did not satisfy schema within turn budget"
                self.log("  Falling back to text parsing", level="warning")

        # ---- Attempt 2: text parsing fallback ----
        if path == "text_parsing":
            text_result = self.invoke(extraction_prompt)
            response_text = str(text_result)

            entities = self._parse_entities_from_text(response_text)
            relationships = self._parse_relationships_from_text(response_text)
            triples = self._parse_triples_from_text(response_text)

        return triples, entities, relationships, path, structured_error, structured_obj

    def _pass_records(
        self,
        path: str,
        structured_error: Optional[str],
        triples: int,
        entities: int,
        chunk_label: str = "",
    ) -> List[PassRecord]:
        """One record per model attempt, so the run can report its own completeness.

        WHY THIS EXISTS. `ExtractionRun.compute_completeness()` returns `UNKNOWN`
        when a run has no pass records, and that is correct — absence of pass
        detail is ignorance, not success. But it was the permanent answer for every
        requirements run, because this profile emitted no pass metadata at all:
        ingest reconstructed records from `model_calls` / `failed_calls` /
        `empty_calls`, none of which were set, so the list came back empty and
        every REQ-G graph was `UNKNOWN` and therefore never auditable (YB-023).

        WHY TEXT IS `empty` RATHER THAN `ok`. The text parser is a fallback with no
        reliable parser for several collections (technology stacks, styles,
        techniques, conventions, references), so a run that used it genuinely is
        incomplete and has to say so. Reporting `COMPLETE` because *something* came
        back is the false assurance the completeness field exists to prevent.

        The two attempts are the whole inventory: at most one structured call, then
        at most one text call if the first produced nothing usable. `path` is the
        agent's own verdict on which attempt won, which is why the structural
        facts are not passed in separately.
        """
        content = bool(triples or entities)

        if path == "structured_output":
            return [
                PassRecord(
                    pass_name="structured",
                    chunk_label=chunk_label,
                    outcome="ok",
                    path="structured",
                    triples_produced=triples,
                )
            ]

        records: List[PassRecord] = []

        # A structured attempt only happened if one failed. `force_text_parsing`
        # skips it entirely, and then reporting a failed attempt would invent one.
        if structured_error is not None:
            # `empty` rather than `failed`: nothing usable came out of the call,
            # but nothing raised either — `invoke_structured` returns None for a
            # model that cannot satisfy the schema, for a cancelled call, and for
            # an unsupported tool_choice alike. The reason is kept on the record
            # because those are different problems to fix.
            records.append(
                PassRecord(
                    pass_name="structured",
                    chunk_label=chunk_label,
                    outcome="empty",
                    path="structured" if "empty" in structured_error else "none",
                    error=structured_error,
                )
            )

        records.append(
            PassRecord(
                pass_name="text_fallback",
                chunk_label=chunk_label,
                outcome="ok" if content else "empty",
                path="text",
                triples_produced=triples,
            )
        )
        return records

    @staticmethod
    def _pass_record_dict(record: PassRecord) -> Dict[str, Any]:
        """A PassRecord as plain data, for the metadata envelope.

        Emitted alongside the counters so ingest can read the real outcomes instead
        of reconstructing them from totals. Two shapes of the same information is
        what let the requirements profile report nothing for so long without anyone
        noticing; carrying the records means the reconstruction is a fallback for
        old output rather than the only path.
        """
        return {
            "pass_name": record.pass_name,
            "chunk_label": record.chunk_label,
            "outcome": record.outcome,
            "path": record.path,
            "elapsed": record.elapsed,
            "error": record.error,
            "triples_produced": record.triples_produced,
            "temperature": record.temperature,
        }

    def _build_extraction_prompt(
        self, 
        document: str, 
        document_type: str,
        domain: str
    ) -> str:
        """Build the extraction prompt for the LLM with ontology context."""
        
        # Get ontology context. Gated on `or self.domain_pack` as well as the base
        # schema: a pack is a complete, self-sufficient vocabulary, and gating on
        # `self.ontology` alone would silently drop it whenever an agent is run
        # with a pack but no explicit `ontology_path`.
        ontology_context = ""
        if self.ontology or self.domain_pack:
            ontology_context = self._format_ontology_context()

        # Interpolated rather than called inside the template: the example is JSON,
        # so its braces would otherwise have to be doubled throughout, and a domain
        # pack could not supply its own without the pack author knowing that.
        worked_example = self._worked_example()
        
        prompt = f"""Extract structured Enterprise Architecture knowledge from the following {document_type} document.

## Document Content
{document}

{ontology_context}

## Instructions

This is an Enterprise Architecture extraction task. Focus on extracting:

### 1. Business Requirements
- **Business Requirements**: Strategic objectives, goals, business capabilities
- **Functional Requirements**: System behaviors, features, functions
- **Non-Functional Requirements**: Quality attributes (performance, security, reliability, etc.)
- **Constraints**: Regulatory, technical, business constraints

### 2. Business Context
- **Stakeholders**: Who has interests in the system
- **Business Goals**: Strategic objectives the system supports
- **Business Capabilities**: What the business does/needs to do
- **Business Processes**: How capabilities are realized
- **Domain Concepts**: Key business entities and their relationships

### 3. Entities
For each entity, map it to an ontology class:
- BusinessRequirement, FunctionalRequirement, NonFunctionalRequirement, ConstraintRequirement
- Stakeholder, BusinessGoal, BusinessCapability, BusinessProcess
- DomainConcept, ConceptAttribute, ConceptRelationship
- Product, System, Application, Platform (enterprise constructs)

### 4. Relationships and traceability

Use ontology predicates. **Do not omit traceability** — it is the primary purpose
of this graph. The Semantic Auditor finds gaps by walking these edges; an edge
that was never recorded is a gap it cannot report.

- `traces_to_goal` — requirement → the business goal it serves
- `traces_to_capability` — requirement → the capability it supports
- `traces_to_process` — requirement → the process it enables
- `binds_to_system` / `binds_to_application` / `binds_to_platform` — scope a requirement to a construct
- `governed_by_rules` — the rule that constrains behaviour
- `depends_on`, `conflicts_with`, `refines` — requirement-to-requirement

**Where a goal, capability, or process is not stated explicitly but is clearly
implied, still emit the traceability triple** and score confidence 0.5–0.8 to
reflect that it was inferred. An imperfectly-confident edge is far more useful
than a missing one.

Naming a concept cleanly (section 5) and recording traceability (this section)
are not in tension — do both. A run that produces tidy nodes but no traceability
edges is less useful than a messy one that does.

### 5. THE OBJECT CONTRACT — read this before generating any triple

Every subject and object must be the **NAME of a thing**, not a description of
what something does. They become nodes in a graph. A clause becomes a node that
nothing can link to and nothing can query — it is effectively lost.

| | |
|---|---|
| **DO** name things | `Dispatch Schedule`, `Operator Credential`, `Return Window`, `Stock Location` |
| **DO NOT** write clauses | ~~`Dispatch every order within 24 hours`~~, ~~`Complete within 500ms`~~, ~~`Implement a fallback strategy for routing`~~ |

Rules:

1. **2–5 words.** If it needs more, it is a clause — name the concept instead.
2. **No verbs leading the phrase.** `Dispatch every order...` is behaviour.
   Name it: `Dispatch Schedule`.
3. **No commas in a subject or object.** If the source names several things,
   emit **one triple per thing** — do not join them.
   - Source: *"criteria include Destination, Service Level, and Item Weight"*
   - WRONG: one triple with object `"Destination, Service Level, Item Weight"`
   - RIGHT: three triples, objects `Destination`, `Service Level`, `Item Weight`
4. **Ignore document structure.** Section headings (`Security Requirements`,
   `Performance Requirements`, `Routing Rules`) are scaffolding, not domain
   concepts. Do not emit them as entities unless they name a real thing in the
   system being described.
5. **Qualifiers are allowed in parentheses** when they are part of the name:
   `Authorization Latency (<=500ms)` is fine. `Complete within 500 milliseconds
   under normal load` is not.

### 6. Worked example

{worked_example}

### 7. Confidence Guidelines
- 0.9-1.0: Explicitly stated, unambiguous
- 0.7-0.9: Clearly implied, high certainty
- 0.5-0.7: Reasonably inferred, some uncertainty
- 0.0-0.5: Speculative, requires human review

**Vary your confidence scores.** A flat 1.0 across every triple carries no
information and prevents the human-review threshold from doing its job.

## Output Requirements
- Be thorough: every meaningful EA relationship should become at least one triple
- Split multi-valued statements into one triple per value
- Name concepts rather than describing behaviour
- Map each object to an ontology class
- List all unique entities and relationship types you used

### Preserve identifiers — they are the join keys

Sources frequently label requirements with stable identifiers (`FR-PM-001`,
`NFR-SC-002`) and work with Initiatives / business cases (`INIT-2026-014`).
**Carry them through verbatim** into `requirement_id` and `initiative_refs`.

These identifiers are how the architecture graph later traces back to a specific
requirement. If they are dropped here, the two graphs cannot be joined and
cross-verification becomes impossible — so a missing identifier is a worse defect
than an imperfectly-worded description.

If the source provides no identifier, leave the field empty. **Do not invent one.**
"""
        return prompt

    def _worked_example(self) -> str:
        """The worked example shown to the model.

        Deliberately split from the prompt body. The example was previously
        payment-specific inside a base prompt, which made every extraction run —
        HR, learning management, anything — read as if the subject were payments.
        A domain vocabulary now supplies its own example through the pack's
        `worked_example` annotation, and the default below is subject-neutral.

        The example teaches the object contract, so it is kept generic on purpose:
        what it demonstrates is the *shape* of a good triple, not what the
        business is about. That is exactly the part no domain should have to
        restate.
        """
        if self.domain_pack is not None:
            example = self.domain_pack.annotations.get("worked_example", "")
            if example:
                return example
        return _GENERIC_WORKED_EXAMPLE
    
    def _extract_named_list(self, text: str, *names: str) -> List[Any]:
        """Find a named list anywhere the model might have put it.

        The model varies its output envelope between runs — observed shapes:
          - an XML tool-call envelope:  <parameter=triples>[ ... ]</parameter>
          - a fenced JSON block:        ```json {"triples": [ ... ]}```
          - a bare JSON object

        Content is the same either way; only the packaging moves. Rather than
        teach each parser one envelope, this looks in all of them for any of the
        supplied key names (e.g. ("entities", "elements")).

        Returns the first non-empty list found, or [].
        """
        import re
        import json

        # 1. XML tool-call envelope
        blocks = self._extract_parameter_blocks(text)
        for name in names:
            value = blocks.get(name)
            if isinstance(value, list) and value:
                return value

        # 2. Fenced JSON blocks, and the raw text as a last resort
        candidates = re.findall(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        candidates.append(text)
        for blob in candidates:
            blob = blob.strip()
            start, end = blob.find("{"), blob.rfind("}")
            if start == -1 or end <= start:
                continue
            try:
                data = json.loads(blob[start:end + 1])
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(data, dict):
                for name in names:
                    value = data.get(name)
                    if isinstance(value, list) and value:
                        return value

        return []

    def _extract_parameter_blocks(self, text: str) -> Dict[str, Any]:
        """Recover data from a <tool_call>/<parameter=NAME> XML envelope.

        Models sometimes emit a tool call as XML *text* rather than invoking the
        tool:

            <tool_call>
            <function=ExtractionResult>
            <parameter=triples>
            [ {...}, {...} ]
            </parameter>
            <parameter=elements>
            [ ... ]
            </parameter>

        The content is complete and correct — only the envelope is unreadable by
        the JSON-code-block path. Recovering it is what makes the text fallback
        usable for schemas with several named collections at once, which is
        exactly the architecture profile.

        Returns {parameter_name: parsed_json}.
        """
        import re
        import json

        blocks: Dict[str, Any] = {}
        for name, body in re.findall(
            r"<parameter=([A-Za-z_]\w*)>(.*?)</parameter>", text, re.DOTALL
        ):
            body = body.strip()
            # Strip a fenced code block if the model wrapped the payload.
            body = re.sub(r"^```[A-Za-z]*\s*", "", body)
            body = re.sub(r"\s*```$", "", body).strip()
            if not body:
                continue
            try:
                blocks[name] = json.loads(body)
            except (json.JSONDecodeError, TypeError):
                continue
        return blocks

    def _parse_triples_from_text(self, text: str) -> List[ExtractedTriple]:
        """Parse triples from the model's text response.
        
        Handles multiple formats:
        - XML tool-call envelope: <parameter=triples>[ ... ]</parameter>
        - JSON code blocks: ```json { "triples": [...] } ```
        - Markdown tables: | SUBJECT | PREDICATE | OBJECT | CONF | SOURCE |
        - Pipe-separated: SUBJECT | PREDICATE | OBJECT | CONF | SOURCE
        - Bullet lists: - SUBJECT --PREDICATE--> OBJECT (CONF)
        """
        import re
        import json
        
        # Envelope-agnostic: find "triples" in an XML tool-call envelope, a
        # fenced JSON block, or bare JSON.
        named = self._extract_named_list(text, "triples")
        if named:
            out = [t for t in (self._parse_triple_dict(d) for d in named
                               if isinstance(d, dict)) if t]
            if out:
                return out
        
        triples = []
        
        # First, try to find JSON in the response (most common for structured models)
        json_triples = self._extract_triples_from_json(text)
        if json_triples:
            return json_triples
        
        # Fall back to line-by-line parsing
        lines = text.split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Skip markdown table header/separator rows
            if line.startswith('|') and ('---' in line or 'SUBJECT' in line.upper() or 'PREDICATE' in line.upper()):
                continue
            
            # Skip comment lines
            if line.startswith('#'):
                continue
            
            # Try markdown table / pipe-separated format
            if '|' in line:
                parts = [p.strip() for p in line.split('|')]
                # Filter out empty parts (from leading/trailing |)
                parts = [p for p in parts if p]
                
                if len(parts) >= 4:
                    try:
                        subject = parts[0].strip('"\'` ')
                        predicate = parts[1].strip('"\'` ')
                        object_val = parts[2].strip('"\'` ')
                        
                        # Parse confidence - handle various formats
                        conf_str = parts[3].strip().rstrip('%')
                        confidence = float(conf_str)
                        if confidence > 1:
                            confidence = confidence / 100.0
                        
                        source_text = parts[4].strip('"\'` ') if len(parts) > 4 else ""
                        
                        if subject and predicate and object_val and 0 <= confidence <= 1:
                            triples.append(ExtractedTriple(
                                subject=subject,
                                predicate=predicate,
                                object=object_val,
                                confidence=confidence,
                                source_text=source_text,
                            ))
                    except (ValueError, IndexError):
                        continue
            
            # Try arrow format: Subject --predicate--> Object (confidence)
            elif '-->' in line or '→' in line:
                arrow = '-->' if '-->' in line else '→'
                parts = line.split(arrow)
                if len(parts) == 2:
                    subject = parts[0].strip('- ').strip('"\'` ')
                    rest = parts[1].strip()
                    
                    conf_match = re.search(r'\((\d+\.?\d*)\)\s*$', rest)
                    if conf_match:
                        confidence = float(conf_match.group(1))
                        if confidence > 1:
                            confidence = confidence / 100.0
                        rest = rest[:conf_match.start()].strip()
                    else:
                        confidence = 0.8
                    
                    pred_obj = rest.split(None, 1)
                    if len(pred_obj) >= 2:
                        predicate = pred_obj[0].strip('"\'` ')
                        object_val = pred_obj[1].strip('"\'` ')
                        
                        if subject and predicate and object_val:
                            triples.append(ExtractedTriple(
                                subject=subject,
                                predicate=predicate,
                                object=object_val,
                                confidence=confidence,
                                source_text="",
                            ))
        
        return triples
    
    def _extract_triples_from_json(self, text: str) -> List[ExtractedTriple]:
        """Extract triples from JSON code blocks or inline JSON in the response."""
        import re
        import json
        
        triples = []
        
        # Try to find JSON code blocks
        json_blocks = re.findall(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        
        for block in json_blocks:
            try:
                data = json.loads(block)
                
                # Handle various JSON structures
                if isinstance(data, dict):
                    # Look for triples in common keys
                    triples_data = data.get('triples', [])
                    if isinstance(triples_data, list):
                        for t in triples_data:
                            triple = self._parse_triple_dict(t)
                            if triple:
                                triples.append(triple)
                
                elif isinstance(data, list):
                    # Direct list of triples
                    for t in data:
                        triple = self._parse_triple_dict(t)
                        if triple:
                            triples.append(triple)
                
            except (json.JSONDecodeError, TypeError):
                continue
        
        return triples
    
    def _parse_triple_dict(self, t: dict) -> Optional[ExtractedTriple]:
        """Parse a single triple from a dictionary."""
        try:
            subject = t.get('subject', t.get('s', '')).strip()
            predicate = t.get('predicate', t.get('p', '')).strip()
            object_val = t.get('object', t.get('o', '')).strip()
            
            confidence = t.get('confidence', t.get('conf', 0.8))
            if isinstance(confidence, str):
                confidence = float(confidence.rstrip('%'))
                if confidence > 1:
                    confidence = confidence / 100.0
            else:
                confidence = float(confidence)
            
            source_text = t.get('source_text', t.get('source', t.get('text', '')))
            if isinstance(source_text, str):
                source_text = source_text.strip()
            
            ontology_class = t.get('ontology_class', t.get('class', ''))
            if isinstance(ontology_class, str):
                ontology_class = ontology_class.strip() or None
            
            requirement_type = t.get('requirement_type', t.get('req_type', ''))
            if isinstance(requirement_type, str):
                requirement_type = requirement_type.strip() or None
            
            if subject and predicate and object_val and 0 <= confidence <= 1:
                return ExtractedTriple(
                    subject=subject,
                    predicate=predicate,
                    object=object_val,
                    confidence=confidence,
                    source_text=source_text,
                    ontology_class=ontology_class,
                    requirement_type=requirement_type,
                )
        except (ValueError, TypeError, AttributeError):
            pass
        return None
    
    def _parse_entities_from_text(self, text: str) -> List[ExtractedEntity]:
        """Parse entities from the model's text response."""
        import re
        import json
        
        entities = []
        
        # Envelope-agnostic. "elements" is accepted because the architecture
        # profile names its node list that, and the JSON-block path below only
        # looks for entity-oriented keys.
        for d in self._extract_named_list(text, "entities", "elements"):
            if isinstance(d, dict):
                ent = self._parse_entity_dict(d)
                if ent:
                    entities.append(ent)
        if entities:
            return entities
        
        # Try to find JSON with entities
        json_blocks = re.findall(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        for block in json_blocks:
            try:
                data = json.loads(block)
                if isinstance(data, dict):
                    # Try standard 'entities' key first
                    entities_data = data.get('entities', [])
                    if isinstance(entities_data, list):
                        for e in entities_data:
                            entity = self._parse_entity_dict(e)
                            if entity:
                                entities.append(entity)
                    
                    # Try 'extracted_entities' key (categorized format)
                    extracted_entities = data.get('extracted_entities', {})
                    if isinstance(extracted_entities, dict):
                        for category, entity_list in extracted_entities.items():
                            if isinstance(entity_list, list):
                                for e in entity_list:
                                    entity = self._parse_entity_dict(e)
                                    if entity:
                                        entities.append(entity)
                    
                    # Try 'entities_identified' key (list format)
                    entities_identified = data.get('entities_identified', [])
                    if isinstance(entities_identified, list):
                        for e in entities_identified:
                            if isinstance(e, dict):
                                entity_name = e.get('entity_name', '').strip()
                                ontology_class = e.get('ontology_class', 'DomainConcept').strip()
                                if entity_name:
                                    entities.append(ExtractedEntity(
                                        name=entity_name,
                                        entity_type=ontology_class,
                                        ontology_class=ontology_class,
                                        description=""
                                    ))
                    
                    # Try 'entities_summary' key (dict format: entity_name -> ontology_class)
                    entities_summary = data.get('entities_summary', {})
                    if isinstance(entities_summary, dict):
                        for entity_name, ontology_class in entities_summary.items():
                            if isinstance(ontology_class, str):
                                entities.append(ExtractedEntity(
                                    name=entity_name.strip(),
                                    entity_type=ontology_class.strip(),
                                    ontology_class=ontology_class.strip(),
                                    description=""
                                ))
                    
                    # Try 'entity_mapping' key (dict format: entity_name -> ontology_class)
                    entity_mapping = data.get('entity_mapping', {})
                    if isinstance(entity_mapping, dict):
                        for entity_name, ontology_class in entity_mapping.items():
                            if isinstance(ontology_class, str):
                                entities.append(ExtractedEntity(
                                    name=entity_name.strip(),
                                    entity_type=ontology_class.strip(),
                                    ontology_class=ontology_class.strip(),
                                    description=""
                                ))
                    
                    # Try 'identified_entities' key (dict format: entity_name -> ontology_class)
                    identified_entities = data.get('identified_entities', {})
                    if isinstance(identified_entities, dict):
                        for entity_name, ontology_class in identified_entities.items():
                            if isinstance(ontology_class, str):
                                entities.append(ExtractedEntity(
                                    name=entity_name.strip(),
                                    entity_type=ontology_class.strip(),
                                    ontology_class=ontology_class.strip(),
                                    description=""
                                ))
                    
                    # Try 'summary.unique_entities_found' key (dict format)
                    summary = data.get('summary', {})
                    if isinstance(summary, dict):
                        unique_entities = summary.get('unique_entities_found', {})
                        if isinstance(unique_entities, dict):
                            for entity_name, ontology_class in unique_entities.items():
                                entities.append(ExtractedEntity(
                                    name=entity_name,
                                    entity_type=ontology_class,
                                    ontology_class=ontology_class,
                                    description=""
                                ))
            except (json.JSONDecodeError, TypeError):
                continue
        
        # If no JSON entities found, try to parse from markdown tables
        if not entities:
            lines = text.split('\n')
            for line in lines:
                line = line.strip()
                if not line or line.startswith('#') or '---' in line:
                    continue
                
                # Skip table headers - check for common header patterns
                line_lower = line.lower()
                if ('name' in line_lower and 'type' in line_lower) or \
                   ('entity' in line_lower and 'ontology' in line_lower) or \
                   ('name' in line_lower and 'description' in line_lower):
                    continue
                
                # Try to parse markdown table row: | Name | Type | Description |
                if '|' in line and line.count('|') >= 3:
                    parts = [p.strip() for p in line.split('|')]
                    parts = [p for p in parts if p]  # Remove empty parts
                    
                    if len(parts) >= 3:
                        name = parts[0].strip()
                        entity_type = parts[1].strip()
                        description = parts[2].strip() if len(parts) > 2 else ""
                        
                        if name and entity_type:
                            entities.append(ExtractedEntity(
                                name=name,
                                entity_type=entity_type,
                                description=description,
                            ))
        
        return entities
    
    def _derive_entities_from_triples(
        self,
        triples: List[ExtractedTriple]
    ) -> List[ExtractedEntity]:
        """Derive entities from triples as a structural fallback.
        
        In a knowledge graph, subjects and objects ARE the entities (nodes)
        and triples are the edges. When the model does not emit an explicit
        entity list, we reconstruct it from the triples it did emit.
        
        Ontology class is inferred from context:
        - If the entity appears as an object with an ontology_class, use it
        - Otherwise fall back to DomainConcept
        """
        # Map entity name -> ontology class (first seen wins, prefer explicit)
        entity_classes: Dict[str, Optional[str]] = {}
        
        for t in triples:
            for name in (t.subject, t.object):
                cleaned = name.strip()
                if not cleaned:
                    continue
                if cleaned not in entity_classes:
                    entity_classes[cleaned] = None
            
            # Prefer the object's ontology_class (it describes the target concept)
            if t.object.strip() in entity_classes and t.ontology_class:
                existing = entity_classes[t.object.strip()]
                if existing is None:
                    entity_classes[t.object.strip()] = t.ontology_class
        
        entities = []
        for name, ontology_class in entity_classes.items():
            entities.append(ExtractedEntity(
                name=name,
                entity_type=ontology_class or "DomainConcept",
                ontology_class=ontology_class,
                description="Derived from extracted triples",
            ))
        
        return entities
    
    def _derive_relationships_from_triples(
        self,
        triples: List[ExtractedTriple]
    ) -> List[ExtractedRelationship]:
        """Derive relationship types from triple predicates.
        
        A triple's predicate IS its relationship type — the two views describe
        the same edge. Deriving one from the other keeps them consistent by
        construction, and avoids relying on the model to emit a separate,
        differently-shaped relationship list (which it frequently doesn't).
        
        Deduplicated, order-preserving.
        """
        seen: Dict[str, ExtractedRelationship] = {}
        for t in triples:
            pred = t.predicate.strip()
            if not pred or pred in seen:
                continue
            seen[pred] = ExtractedRelationship(
                relationship_type=pred,
                description=f"Derived from triple predicate (e.g. "
                            f"{t.subject} -> {t.object})",
            )
        return list(seen.values())
    
    # Words that almost always signal a clause rather than a name.
    # Deliberately conservative — this FLAGS for human review, it never drops.
    _CLAUSE_MARKERS = (" shall ", " must ", " will ", " should ", " and ", " or ")

    def _flag_contract_violations(
        self,
        triples: List[ExtractedTriple],
    ) -> List[Dict[str, Any]]:
        """Flag triples whose subject/object break the object contract.

        Deterministic post-check, independent of model behaviour. Prompts drift
        and models vary; this catches clause-like nodes either way and routes
        them to human review rather than silently polluting the graph.

        Flags, never drops: a long object may still be legitimate, and silently
        discarding extracted content is worse than surfacing it.

        Detects:
          - over-long subjects/objects (clause-shaped)
          - comma-lists in a node (should be split into separate triples)
          - clause markers (shall/must/will/should/and/or)
        """
        flags: List[Dict[str, Any]] = []

        for t in triples:
            reasons: List[str] = []

            for field, raw in (("subject", t.subject), ("object", t.object)):
                value = (raw or "").strip()
                if not value:
                    continue

                if len(value) > 40:
                    reasons.append(f"{field} is {len(value)} chars (clause-shaped)")
                if "," in value:
                    reasons.append(
                        f"{field} contains a comma-list — split into one triple per item"
                    )

                padded = f" {value.lower()} "
                for marker in self._CLAUSE_MARKERS:
                    if marker in padded:
                        reasons.append(
                            f"{field} contains clause marker {marker.strip()!r}"
                        )
                        break

            if reasons:
                flags.append({
                    "subject": t.subject,
                    "predicate": t.predicate,
                    "object": t.object,
                    "reasons": reasons,
                })

        return flags

    def _parse_entity_dict(self, e: dict) -> Optional[ExtractedEntity]:
        """Parse a single entity from a dictionary.
        
        Carries identifier fields through. Losing `requirement_id` on the text
        path is what left the PRD run with 0 identifiers while the structured
        run captured 16 — and identifiers are the join key architecture uses to
        trace back, so a silent drop here breaks cross-verification (YB-005).
        """
        try:
            # Handle both 'name' and 'entity' fields
            name = e.get('name', e.get('entity', '')).strip()
            entity_type = e.get('entity_type', e.get('type', 'DomainConcept')).strip()
            description = e.get('description', '').strip()
            ontology_class = e.get('ontology_class', '').strip() or None
            requirement_type = (e.get('requirement_type') or '').strip() or None
            requirement_id = (e.get('requirement_id') or '').strip()
            
            initiative_refs = e.get('initiative_refs') or []
            if isinstance(initiative_refs, str):
                initiative_refs = [initiative_refs] if initiative_refs.strip() else []
            initiative_refs = [str(x).strip() for x in initiative_refs if str(x).strip()]
            
            if name:
                return ExtractedEntity(
                    name=name,
                    entity_type=entity_type,
                    description=description,
                    ontology_class=ontology_class,
                    requirement_type=requirement_type,
                    requirement_id=requirement_id,
                    initiative_refs=initiative_refs,
                )
        except (ValueError, TypeError, AttributeError):
            pass
        return None
    
    def _parse_relationships_from_text(self, text: str) -> List[ExtractedRelationship]:
        """Parse relationships from the model's text response."""
        import re
        import json
        
        relationships = []
        
        # Try to find JSON with relationships
        json_blocks = re.findall(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
        for block in json_blocks:
            try:
                data = json.loads(block)
                if isinstance(data, dict):
                    # Try standard 'relationships' key first
                    rels_data = data.get('relationships', [])
                    if isinstance(rels_data, list):
                        for r in rels_data:
                            rel = self._parse_relationship_dict(r)
                            if rel:
                                relationships.append(rel)
                    
                    # Try 'identified_relationships' key (list format)
                    identified_rels = data.get('identified_relationships', [])
                    if isinstance(identified_rels, list):
                        for r in identified_rels:
                            # Handle both string format and dict format
                            if isinstance(r, str):
                                rel = ExtractedRelationship(
                                    relationship_type=r,
                                    description=""
                                )
                                relationships.append(rel)
                            elif isinstance(r, dict):
                                rel = self._parse_relationship_dict(r)
                                if rel:
                                    relationships.append(rel)
                    
                    # Try 'extracted_relationships' key (list format)
                    extracted_rels = data.get('extracted_relationships', [])
                    if isinstance(extracted_rels, list):
                        for r in extracted_rels:
                            # Handle both string format and dict format
                            if isinstance(r, str):
                                rel = ExtractedRelationship(
                                    relationship_type=r,
                                    description=""
                                )
                                relationships.append(rel)
                            elif isinstance(r, dict):
                                rel = self._parse_relationship_dict(r)
                                if rel:
                                    relationships.append(rel)
                    
                    # Try 'relationships_identified' key (list format)
                    relationships_identified = data.get('relationships_identified', [])
                    if isinstance(relationships_identified, list):
                        for r in relationships_identified:
                            if isinstance(r, str):
                                relationships.append(ExtractedRelationship(
                                    relationship_type=r,
                                    description=""
                                ))
                    
                    # Try 'summary.relationship_types_identified' key (list format)
                    summary = data.get('summary', {})
                    if isinstance(summary, dict):
                        rel_types = summary.get('relationship_types_identified', [])
                        if isinstance(rel_types, list):
                            for r in rel_types:
                                if isinstance(r, str):
                                    rel = ExtractedRelationship(
                                        relationship_type=r,
                                        description=""
                                    )
                                    relationships.append(rel)
            except (json.JSONDecodeError, TypeError):
                continue
        
        # If no JSON relationships found, try to parse from markdown lists with backticks
        if not relationships:
            # Look for patterns like: *   `relationship_name`
            backtick_pattern = re.findall(r'\*\s+`([^`]+)`', text)
            for rel_type in backtick_pattern:
                relationships.append(ExtractedRelationship(
                    relationship_type=rel_type.strip(),
                    description=""
                ))
        
        # If still no relationships found, try to parse from markdown tables
        if not relationships:
            lines = text.split('\n')
            in_relationships_section = False
            
            for i, line in enumerate(lines):
                line_stripped = line.strip()
                
                # Look for section headers that indicate relationships
                if 'relationship' in line_stripped.lower() and ('type' in line_stripped.lower() or '##' in line_stripped):
                    in_relationships_section = True
                    continue
                
                # Stop if we hit another section
                if in_relationships_section and line_stripped.startswith('##') and 'relationship' not in line_stripped.lower():
                    in_relationships_section = False
                    continue
                
                if not in_relationships_section:
                    continue
                
                if not line_stripped or '---' in line_stripped:
                    continue
                
                # Skip table headers
                if 'Relationship Type' in line_stripped and 'Description' in line_stripped:
                    continue
                
                # Skip if this looks like an entity (has "Name" and "Type" columns)
                if 'Name' in line_stripped and 'Type' in line_stripped:
                    continue
                
                # Try to parse markdown table row: | Relationship Type | Description |
                if '|' in line_stripped and line_stripped.count('|') >= 2:
                    parts = [p.strip() for p in line_stripped.split('|')]
                    parts = [p for p in parts if p]  # Remove empty parts
                    
                    if len(parts) >= 2:
                        rel_type = parts[0].strip()
                        description = parts[1].strip()
                        
                        # Skip if this looks like an entity entry
                        if rel_type in ['Name', 'Payment Gateway Platform', 'Payment Requests', 'B2C Storefront', 'B2E Storefront']:
                            continue
                        
                        if rel_type and description and len(rel_type) < 100:  # Reasonable length for a relationship type
                            relationships.append(ExtractedRelationship(
                                relationship_type=rel_type,
                                description=description,
                            ))
        
        return relationships
    
    def _parse_relationship_dict(self, r: dict) -> Optional[ExtractedRelationship]:
        """Parse a single relationship from a dictionary."""
        try:
            rel_type = r.get('relationship_type', r.get('type', '')).strip()
            description = r.get('description', '').strip()
            
            if rel_type:
                return ExtractedRelationship(
                    relationship_type=rel_type,
                    description=description,
                )
        except (ValueError, TypeError, AttributeError):
            pass
        return None
    
    def _calculate_statistics_from_lists(
        self,
        triples: List[ExtractedTriple],
        entities: List[ExtractedEntity],
        relationships: List[ExtractedRelationship]
    ) -> Dict[str, Any]:
        """Calculate extraction statistics from lists.
        
        Node/edge totals use the profile's output keys, so a requirements run
        reports `total_entities`/`total_relationships` and an architecture run
        reports `total_elements`/`total_connections`.
        """
        if not triples:
            return {"total_triples": 0}
        
        confidences = [t.confidence for t in triples]
        keys = self._output_keys()
        
        return {
            "total_triples": len(triples),
            f"total_{keys['nodes']}": len(entities),
            f"total_{keys['edges']}": len(relationships),
            "avg_confidence": sum(confidences) / len(confidences),
            "min_confidence": min(confidences),
            "max_confidence": max(confidences),
            "high_confidence_count": sum(1 for c in confidences if c >= 0.9),
            "medium_confidence_count": sum(1 for c in confidences if 0.7 <= c < 0.9),
            "low_confidence_count": sum(1 for c in confidences if c < 0.7),
        }
    
    def _calculate_overall_confidence_from_list(self, triples: List[ExtractedTriple]) -> float:
        """Calculate overall extraction confidence from a list of triples."""
        if not triples:
            return 0.0
        
        confidences = [t.confidence for t in triples]
        return sum(confidences) / len(confidences)
    
    def _calculate_statistics(self, result: ExtractionResult) -> Dict[str, Any]:
        """Calculate extraction statistics."""
        
        if not result.triples:
            return {"total_triples": 0}
        
        confidences = [t.confidence for t in result.triples]
        
        return {
            "total_triples": len(result.triples),
            "total_entities": len(result.entities),
            "total_relationships": len(result.relationships),
            "avg_confidence": sum(confidences) / len(confidences),
            "min_confidence": min(confidences),
            "max_confidence": max(confidences),
            "high_confidence_count": sum(1 for c in confidences if c >= 0.9),
            "medium_confidence_count": sum(1 for c in confidences if 0.7 <= c < 0.9),
            "low_confidence_count": sum(1 for c in confidences if c < 0.7),
        }
    
    def _calculate_overall_confidence(self, result: ExtractionResult) -> float:
        """Calculate overall extraction confidence."""
        
        if not result.triples:
            return 0.0
        
        confidences = [t.confidence for t in result.triples]
        return sum(confidences) / len(confidences)


def create_knowledge_extraction_agent() -> KnowledgeExtractionAgent:
    """Factory function to create a Knowledge Extraction Agent."""
    from config import get_default_agent_config
    
    config_dict = get_default_agent_config("knowledge_extraction")
    config = AgentConfig(**config_dict)
    return KnowledgeExtractionAgent(config)
