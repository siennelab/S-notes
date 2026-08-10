"""SQLite adapter for metadata-only AI call records."""

from __future__ import annotations

from src.snotes.application.ports import AiAuditRepository
from src.snotes.domain.ai_audit import AiAuditEvent, AiCallLog
from src.snotes.storage.connection import SQLiteConnectionFactory


class SQLiteAiAuditRepository(AiAuditRepository):
    def __init__(self, connections: SQLiteConnectionFactory) -> None:
        self.connections = connections

    def record(self, event: AiAuditEvent) -> None:
        with self.connections.write() as connection:
            connection.execute(
                """
                INSERT INTO ai_call_logs (
                    called_at, actor_target_id, operation_type, request_id,
                    note_id, summary, result_count, handling_mode, status,
                    failure_reason, is_duplicate, entered_confirmation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.called_at,
                    event.actor_target_id,
                    event.operation_type,
                    event.request_id,
                    event.note_id,
                    event.summary,
                    event.result_count,
                    event.handling_mode,
                    event.status,
                    event.failure_reason,
                    int(event.is_duplicate),
                    int(event.entered_confirmation),
                ),
            )

    def list(self, operation_type: str | None = None) -> list[AiCallLog]:
        clause = "" if operation_type is None else "WHERE operation_type = ?"
        parameters = () if operation_type is None else (operation_type,)
        with self.connections.read() as connection:
            rows = connection.execute(
                f"""
                SELECT id, called_at, actor_target_id, operation_type,
                       request_id, note_id, summary, result_count,
                       handling_mode, status, failure_reason, is_duplicate,
                       entered_confirmation
                FROM ai_call_logs
                {clause}
                ORDER BY called_at DESC, id DESC
                """,
                parameters,
            ).fetchall()
        return [
            AiCallLog(
                id=int(row["id"]),
                called_at=str(row["called_at"]),
                actor_target_id=str(row["actor_target_id"]),
                operation_type=str(row["operation_type"]),
                request_id=str(row["request_id"]),
                note_id=(int(row["note_id"]) if row["note_id"] is not None else None),
                summary=str(row["summary"]),
                result_count=int(row["result_count"]),
                handling_mode=str(row["handling_mode"]),
                status=str(row["status"]),
                failure_reason=str(row["failure_reason"]),
                is_duplicate=bool(row["is_duplicate"]),
                entered_confirmation=bool(row["entered_confirmation"]),
            )
            for row in rows
        ]
