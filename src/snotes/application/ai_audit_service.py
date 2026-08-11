"""Single application owner for metadata-only AI call audit."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from src.snotes.application.ai_actor_registry import target_definition
from src.snotes.application.ports import AiAuditRepository
from src.snotes.domain.ai_audit import AiAuditEvent, AiCallLog


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
AUDIT_OPERATION_TYPES = (
    "export",
    "search",
    "get_note",
    "operation_status",
    "list_directories",
    "list_tags",
    "create",
    "prepend",
    "append",
    "metadata",
    "restore",
)


@dataclass(frozen=True, slots=True)
class AiCallLogView:
    id: int
    called_at: str
    actor_target_id: str
    actor_display: str
    operation_type: str
    request_id: str
    note_id: int | None
    result_count: int
    handling_mode: str
    status: str
    failure_reason: str
    is_duplicate: bool
    entered_confirmation: bool


class AiAuditService:
    """Own audit privacy normalization and expose read-only call-log views."""

    def __init__(self, repository: AiAuditRepository) -> None:
        self._repository = repository

    def record_event(
        self,
        actor_target_id: object,
        operation_type: object,
        *,
        request_id: str | None = None,
        note_id: int | None = None,
        result_count: int = 0,
        handling_mode: object = "read",
        status: object,
        failure_reason: object = "",
        is_duplicate: bool = False,
        entered_confirmation: bool = False,
    ) -> str:
        clean_request_id = str(request_id or uuid.uuid4().hex)
        event = AiAuditEvent(
            called_at=datetime.now(SHANGHAI_TZ).isoformat(timespec="seconds"),
            actor_target_id=_safe_actor_id(actor_target_id),
            operation_type=_short_text(operation_type),
            request_id=_short_text(clean_request_id),
            note_id=note_id,
            summary="",
            result_count=max(0, int(result_count)),
            handling_mode=_short_text(handling_mode),
            status=_short_text(status),
            failure_reason=_short_text(failure_reason),
            is_duplicate=bool(is_duplicate),
            entered_confirmation=bool(entered_confirmation),
        )
        self._repository.record(event)
        return clean_request_id

    def list_call_logs(
        self,
        operation_type: str | None = None,
    ) -> tuple[AiCallLogView, ...]:
        clean_type = str(operation_type or "").strip() or None
        return tuple(self._view(row) for row in self._repository.list(clean_type))

    def operation_filter_options(self) -> tuple[str, ...]:
        return AUDIT_OPERATION_TYPES

    @staticmethod
    def _view(row: AiCallLog) -> AiCallLogView:
        definition = target_definition(row.actor_target_id)
        return AiCallLogView(
            id=row.id,
            called_at=row.called_at,
            actor_target_id=row.actor_target_id,
            actor_display=(
                definition.display_name if definition is not None else row.actor_target_id
            ),
            operation_type=row.operation_type,
            request_id=row.request_id,
            note_id=row.note_id,
            result_count=row.result_count,
            handling_mode=row.handling_mode,
            status=row.status,
            failure_reason=row.failure_reason,
            is_duplicate=row.is_duplicate,
            entered_confirmation=row.entered_confirmation,
        )


def _short_text(value: object, max_length: int = 180) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:max_length]


def _safe_actor_id(value: object) -> str:
    actor_id = _short_text(value)
    return actor_id if target_definition(actor_id) is not None else "unknown"
