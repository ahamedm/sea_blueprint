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
    Flag,
    check_containment,
    check_deployment_levels,
    check_element_types,
    check_enum_membership,
    check_expected_present,
    check_nonempty_field,
    check_object_contract,
    check_schema_consistency,
    ontology_classes,
    ontology_enum,
)

__all__ = [
    "Chunk", "chunk_document", "summarise_chunks",
    "merge_triples", "merge_records", "completeness",
    "triple_key", "element_key", "connection_key", "named_key",
    "Flag", "check_object_contract", "check_containment", "check_element_types",
    "check_deployment_levels", "check_enum_membership", "check_expected_present",
    "check_nonempty_field", "check_schema_consistency",
    "ontology_enum", "ontology_classes",
]
