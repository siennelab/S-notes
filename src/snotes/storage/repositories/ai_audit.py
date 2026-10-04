"""SQLite adapter for metadata-only AI call records."""

from __future__ import annotations

import json

from src.snotes.application.ports import AiAuditRepository
from src.snotes.domain.ai_audit import AiAuditEvent, AiCallLog
from src.snotes.storage.connection import SQLiteConnectionFactory


AI_CALL_LOG_RETENTION_THRESHOLD = 12_000
AI_CALL_LOG_RETENTION_LIMIT = 10_000


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

    def prune_call_logs(self) -> int:
        """Trim old call logs after the startup maintenance threshold is crossed."""
        with self.connections.write() as connection:
            count = int(
                connection.execute("SELECT COUNT(*) FROM ai_call_logs").fetchone()[0]
            )
            if count <= AI_CALL_LOG_RETENTION_THRESHOLD:
                return 0
            cursor = connection.execute(
                """
                DELETE FROM ai_call_logs
                WHERE id NOT IN (
                    SELECT id
                    FROM ai_call_logs
                    ORDER BY called_at DESC, id DESC
                    LIMIT ?
                )
                """,
                (AI_CALL_LOG_RETENTION_LIMIT,),
            )
            return int(cursor.rowcount)

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
        return [self._call_log(row) for row in rows]

    def list_page(
        self,
        operation_type: str | None = None,
        *,
        offset: int = 0,
        limit: int = 100,
    ) -> list[AiCallLog]:
        if offset < 0 or limit < 1:
            raise ValueError("Invalid call-log page bounds")
        clause = "" if operation_type is None else "WHERE operation_type = ?"
        parameters = (() if operation_type is None else (operation_type,)) + (limit, offset)
        with self.connections.read() as connection:
            rows = connection.execute(
                f"""
                SELECT log.*, operation.result_json AS operation_result
                FROM (
                    SELECT id, called_at, actor_target_id, operation_type,
                           request_id, note_id, summary, result_count,
                           handling_mode, status, failure_reason, is_duplicate,
                           entered_confirmation
                    FROM ai_call_logs
                    {clause}
                    ORDER BY called_at DESC, id DESC
                    LIMIT ? OFFSET ?
                ) AS log
                LEFT JOIN ai_operations AS operation
                    ON log.request_id <> ''
                    AND operation.request_id = log.request_id
                    AND operation.actor_target_id = log.actor_target_id
                ORDER BY log.called_at DESC, log.id DESC
                """,
                parameters,
            ).fetchall()
        return [
            self._call_log(
                row,
                # Match get_operation(): the displayed status comes from its
                # stored result, not the separate lifecycle status column.
                operation_status=(
                    json.loads(row["operation_result"]).get("status")
                    if row["operation_result"] is not None
                    else None
                ),
            )
            for row in rows
        ]

    @staticmethod
    def _call_log(row, *, operation_status: str | None = None) -> AiCallLog:
        return AiCallLog(
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
            operation_status=operation_status,
        )
