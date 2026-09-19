"""
Knowledge Extraction Agent

Transforms unstructured human language (Markdown documents) into structured
knowledge graph triples using the SEA requirements ontology.

Uses Strands structured output for type-safe, validated extraction results.
"""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from ..base_agent import SEABaseAgent, AgentConfig, AgentResult


# ============================================================================
# Structured Output Models
# ============================================================================

class ExtractedTriple(BaseModel):
    """A single extracted knowledge triple with ontology mapping."""
    
    subject: str = Field(..., description="Subject entity")
    predicate: str = Field(..., description="Relationship predicate (mapped to ontology)")
    object: str = Field(..., description="Object entity")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score (0.0-1.0)")
    source_text: str = Field(..., description="Original text this triple was extracted from")
    ontology_class: Optional[str] = Field(None, description="Mapped ontology class (e.g., BusinessRequirement, NonFunctionalRequirement)")
    requirement_type: Optional[str] = Field(None, description="Requirement type if applicable (BUSINESS, FUNCTIONAL, NON_FUNCTIONAL, CONSTRAINT)")


class ExtractedEntity(BaseModel):
    """An extracted business entity with ontology mapping."""
    
    name: str = Field(..., description="Entity name")
    entity_type: str = Field(default="DomainConcept", description="Entity type (e.g., Stakeholder, System, Process)")
    type: str = Field(default="", description="Entity type (alias)")
    description: str = Field(default="", description="Brief description of the entity")
    ontology_class: Optional[str] = Field(None, description="Mapped ontology class (e.g., BusinessRequirement, BusinessCapability)")
    requirement_type: Optional[str] = Field(None, description="Requirement type if applicable (BUSINESS, FUNCTIONAL, NON_FUNCTIONAL, CONSTRAINT)")
    
    def model_post_init(self, __context):
        """Use 'type' as fallback for 'entity_type' and vice versa."""
        if self.type and not self.entity_type:
            self.entity_type = self.type
        if self.entity_type and not self.type:
            self.type = self.entity_type


class ExtractedRelationship(BaseModel):
    """An extracted relationship type."""
    
    relationship_type: str = Field(..., description="Type of relationship")
    description: str = Field(..., description="Description of the relationship")


class ExtractionResult(BaseModel):
    """Structured output model for knowledge extraction."""
    
    triples: List[ExtractedTriple] = Field(default_factory=list, description="Extracted knowledge triples")
    entities: List[ExtractedEntity] = Field(default_factory=list, description="Extracted entities")
    relationships: List[ExtractedRelationship] = Field(default_factory=list, description="Extracted relationship types")


class Stage1Result(BaseModel):
    """Stage 1: Extract entities and relationships only."""
    
    entities: List[ExtractedEntity] = Field(default_factory=list, description="Extracted entities")
    relationships: List[ExtractedRelationship] = Field(default_factory=list, description="Extracted relationship types")


# ============================================================================
# Agent Implementation
# ============================================================================

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
        
    def run(self, input_data: Dict[str, Any]) -> AgentResult:
        """
        Extract knowledge from a document using a single-pass text-based approach.
        
        This avoids structured output validation loops that cause local models to retry
        endlessly. Instead, we extract everything in one pass and parse from text.
        
        Args:
            input_data: Dictionary containing:
                - document: Markdown document text
                - document_type: Type of document (requirements, architecture, etc.)
                - domain: Business domain (optional)
                
        Returns:
            AgentResult with extracted triples and metadata
        """
        try:
            document = input_data.get("document", "")
            document_type = input_data.get("document_type", "requirements")
            domain = input_data.get("domain", "generic")
            
            if not document:
                return AgentResult(
                    success=False,
                    output=None,
                    errors=["No document provided"],
                )
            
            self.log(f"Extracting knowledge from {document_type} document...")
            
            # Single-pass extraction using text-based approach
            extraction_prompt = self._build_extraction_prompt(document, document_type, domain)
            result = self.invoke(extraction_prompt)
            response_text = str(result)
            
            # Parse entities, relationships, and triples from text
            entities = self._parse_entities_from_text(response_text)
            relationships = self._parse_relationships_from_text(response_text)
            triples = self._parse_triples_from_text(response_text)
            
            # Separate low-confidence items
            low_confidence = [
                t for t in triples 
                if t.confidence < self.confidence_threshold
            ]
            
            # Calculate statistics
            statistics = self._calculate_statistics_from_lists(triples, entities, relationships)
            
            self.log(
                f"Extracted {len(triples)} triples, {len(entities)} entities, "
                f"{len(relationships)} relationships ({len(low_confidence)} low confidence)",
                level="success"
            )
            
            # Build output dict
            output = {
                "triples": [t.model_dump() for t in triples],
                "entities": [e.model_dump() for e in entities],
                "relationships": [r.model_dump() for r in relationships],
                "low_confidence_items": [t.model_dump() for t in low_confidence],
                "statistics": statistics,
            }
            
            return AgentResult(
                success=True,
                output=output,
                confidence=self._calculate_overall_confidence_from_list(triples),
                metadata={
                    "document_type": document_type,
                    "domain": domain,
                    "total_triples": len(triples),
                    "total_entities": len(entities),
                    "total_relationships": len(relationships),
                    "low_confidence_count": len(low_confidence),
                },
            )
            
        except Exception as e:
            self.log(f"Extraction failed: {str(e)}", level="error")
            return AgentResult(
                success=False,
                output=None,
                errors=[str(e)],
            )
    
    def _build_extraction_prompt(
        self, 
        document: str, 
        document_type: str,
        domain: str
    ) -> str:
        """Build the extraction prompt for the LLM with ontology context."""
        
        # Get ontology context
        ontology_context = ""
        if self.ontology:
            ontology_context = self._format_ontology_context()
        
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

### 4. Relationships
Map relationships to ontology predicates:
- `traces_to_goals`, `traces_to_capabilities`, `traces_to_processes`
- `depends_on`, `conflicts_with`, `refines`
- `governed_by_rules`, `binds_to_system`, `binds_to_application`

### 5. Triples (MOST IMPORTANT)
Generate subject-predicate-object triples that capture EA facts.
Each triple should have: subject, predicate, object, confidence (0.0-1.0), source_text, and ontology_class.

**Example triples:**
```json
{{
  "triples": [
    {{
      "subject": "Payment Gateway Platform",
      "predicate": "traces_to_goal",
      "object": "Centralize Payment Processing",
      "confidence": 1.0,
      "source_text": "Provide a single, unified entry point for all payment requests",
      "ontology_class": "BusinessGoal"
    }},
    {{
      "subject": "Payment Authorization",
      "predicate": "has_nfr",
      "object": "Complete within 500ms",
      "confidence": 1.0,
      "source_text": "95% of all payment authorization requests must complete within 500 milliseconds",
      "ontology_class": "NonFunctionalRequirement"
    }}
  ]
}}
```

### Confidence Guidelines
- 0.9-1.0: Explicitly stated, unambiguous
- 0.7-0.9: Clearly implied, high certainty
- 0.5-0.7: Reasonably inferred, some uncertainty
- 0.0-0.5: Speculative, requires human review

## Output Requirements
- Generate as many triples as possible (aim for 10-30+ from a typical document)
- Map entities to ontology classes
- List all unique entities found with their ontology mapping
- List all relationship types identified
- Be thorough: every meaningful EA relationship should be captured as at least one triple
"""
        return prompt
    
    def _parse_triples_from_text(self, text: str) -> List[ExtractedTriple]:
        """Parse triples from the model's text response.
        
        Handles multiple formats:
        - JSON code blocks: ```json { "triples": [...] } ```
        - Markdown tables: | SUBJECT | PREDICATE | OBJECT | CONF | SOURCE |
        - Pipe-separated: SUBJECT | PREDICATE | OBJECT | CONF | SOURCE
        - Bullet lists: - SUBJECT --PREDICATE--> OBJECT (CONF)
        """
        import re
        import json
        
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
    
    def _parse_entity_dict(self, e: dict) -> Optional[ExtractedEntity]:
        """Parse a single entity from a dictionary."""
        try:
            # Handle both 'name' and 'entity' fields
            name = e.get('name', e.get('entity', '')).strip()
            entity_type = e.get('entity_type', e.get('type', 'DomainConcept')).strip()
            description = e.get('description', '').strip()
            ontology_class = e.get('ontology_class', '').strip() or None
            
            if name:
                return ExtractedEntity(
                    name=name,
                    entity_type=entity_type,
                    description=description,
                    ontology_class=ontology_class,
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
        """Calculate extraction statistics from lists."""
        if not triples:
            return {"total_triples": 0}
        
        confidences = [t.confidence for t in triples]
        
        return {
            "total_triples": len(triples),
            "total_entities": len(entities),
            "total_relationships": len(relationships),
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
