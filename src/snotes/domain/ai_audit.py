"""Metadata-only records for AI read and export audit."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AiAuditEvent:
    called_at: str
    actor_target_id: str
    operation_type: str
    request_id: str
    note_id: int | None = None
    summary: str = ""
    result_count: int = 0
    handling_mode: str = "read"
    status: str = "success"
    failure_reason: str = ""
    is_duplicate: bool = False
    entered_confirmation: bool = False


@dataclass(frozen=True, slots=True)
class AiCallLog:
    id: int
    called_at: str
    actor_target_id: str
    operation_type: str
    request_id: str
    note_id: int | None
    summary: str
    result_count: int
    handling_mode: str
    status: str
    failure_reason: str
    is_duplicate: bool
    entered_confirmation: bool
