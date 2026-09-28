"""Compatibility import for the domain-owned Notes operation result."""

from src.snotes.domain.notes_operation_result import (  # noqa: F401
    NotesOperationResult,
    deserialize_operation_result_json,
    notes_operation_result,
    serialize_notes_operation_result,
)
