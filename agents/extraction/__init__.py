"""
Shared extraction infrastructure.

  chunking.py    — split large documents into structure-aware, overlapping chunks
  merging.py     — deduplicate across chunks and passes without losing content
  validators.py  — deterministic post-hoc checks, vocabularies read from the ontology

Built because real requirement and architecture documents are large and verbose:
the samples in `test_data/` shape the ontology, but they do not represent the
volume the extractor meets in production.
"""

from .chunking import Chunk, chunk_document, summarise_chunks
from .merging import (
    completeness,
    connection_key,
    element_key,
    merge_records,
    merge_triples,
    named_key,
    triple_key,
)
from .validators import (
    CONNECTION_FIELD_ENUMS,
    FIELD_ENUMS,
    TECHNIQUE_FIELD_ENUMS,
    TECHNOLOGY_FIELD_ENUMS,
    Flag,
    allowed_parent_kinds,
    as_record_dicts,
    check_attribution_endpoints,
    check_connection_endpoints,
    check_containment,
    check_concept_attributes,
    check_containment_kinds,
    check_deployment_levels,
    check_element_types,
    check_enum_membership,
    check_expected_present,
    check_names_are_anchored,
    check_nonempty_field,
    check_object_contract,
    check_quotations_are_grounded,
    check_schema_consistency,
    ontology_classes,
    ontology_enum,
    ontology_slot_range,
    ontology_subclasses,
)

__all__ = [
    "Chunk", "chunk_document", "summarise_chunks",
    "merge_triples", "merge_records", "completeness",
    "triple_key", "element_key", "connection_key", "named_key",
    "Flag", "check_object_contract", "check_containment", "check_element_types",
    "check_deployment_levels", "check_enum_membership", "check_expected_present",
    "check_nonempty_field", "check_schema_consistency",
    "ontology_enum", "ontology_classes", "as_record_dicts",
    "check_concept_attributes",
    "check_containment_kinds", "check_connection_endpoints",
    "check_attribution_endpoints",
    "check_names_are_anchored", "check_quotations_are_grounded",
    "allowed_parent_kinds", "ontology_slot_range", "ontology_subclasses",
    "FIELD_ENUMS", "CONNECTION_FIELD_ENUMS",
    "TECHNIQUE_FIELD_ENUMS", "TECHNOLOGY_FIELD_ENUMS",
]
