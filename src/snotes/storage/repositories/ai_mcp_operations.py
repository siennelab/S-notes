"""Atomic SQLite persistence for R2 local-MCP write operations."""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

from src.snotes.application.ports import (
    AiMcpOperationRepository,
    AiTargetCatalogEntry,
)
from src.snotes.domain.models import NoteLocation
from src.snotes.storage.ai_metadata import (
    replace_note_ai_targets,
    sync_ai_target_catalog,
)
from src.snotes.storage.connection import SQLiteConnectionFactory
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.support import next_note_updated_at, timestamp_seconds


CREATE_CONFIRMATION_KEY = "ai_create_requires_confirmation"
MODIFY_CONFIRMATION_KEY = "ai_modify_requires_confirmation"
DEFAULT_CONFIRMATION = {
    CREATE_CONFIRMATION_KEY: False,
    MODIFY_CONFIRMATION_KEY: True,
}


class SQLiteAiMcpOperationRepository(AiMcpOperationRepository):
    """Keep idempotency, note mutation, history, and receipts transactional."""

    def __init__(
        self,
        connections: SQLiteConnectionFactory,
        tags: SQLiteTagsRepository,
    ) -> None:
        self.connections = connections
        self.tags = tags

    def confirmation_required(self, operation_type: str) -> bool:
        key = (
            CREATE_CONFIRMATION_KEY
            if operation_type == "create"
            else MODIFY_CONFIRMATION_KEY
        )
        with self.connections.read() as connection:
            row = connection.execute(
                "SELECT setting_value FROM app_settings WHERE setting_key = ?",
                (key,),
            ).fetchone()
        return DEFAULT_CONFIRMATION[key] if row is None else row["setting_value"] == "1"

    def get_operation(
        self,
        request_id: str,
        actor_target_id: str | None = None,
    ) -> dict | None:
        parameters: list[object] = [request_id]
        actor_clause = ""
        if actor_target_id is not None:
            actor_clause = " AND actor_target_id = ?"
            parameters.append(actor_target_id)
        with self.connections.read() as connection:
            row = connection.execute(
                """
                SELECT operation_type, result_json, created_at, updated_at
                FROM ai_operations WHERE request_id = ?
                """
                + actor_clause,
                parameters,
            ).fetchone()
        return self._operation_result(row)

    def store_terminal(
        self,
        actor_target_id: str,
        operation_type: str,
        result: dict,
    ) -> dict:
        with self.connections.write() as connection:
            existing = self._operation_from_connection(
                connection, result["request_id"]
            )
            if existing is not None:
                return _already_processed(existing)
            self._store_operation(
                connection,
                actor_target_id,
                operation_type,
                result,
            )
        return result

    def create_direct(
        self,
        *,
        actor_target_id: str,
        request_id: str,
        title: str,
        content_html: str,
        directory_id: int,
        location: NoteLocation,
        tags: Sequence[str],
        visibility: str,
        target_ids: Sequence[str],
        target_catalog: Sequence[AiTargetCatalogEntry],
    ) -> dict:
        timestamp = timestamp_seconds()
        with self.connections.write() as connection:
            existing = self._operation_from_connection(connection, request_id)
            if existing is not None:
                return _already_processed(existing)
            directory = connection.execute(
                "SELECT id FROM directories WHERE id = ? AND is_deleted = 0",
                (directory_id,),
            ).fetchone()
            if directory is None:
                result = _result(
                    "failed",
                    request_id,
                    None,
                    title,
                    "direct",
                    False,
                    False,
                    "目标目录不存在，AI 不能创建目录。",
                )
                self._store_operation(connection, actor_target_id, "create", result)
                return result
            main, sub = location.values()
            note_uuid = str(uuid.uuid4())
            cursor = connection.execute(
                """
                INSERT INTO notes (
                    note_uuid, revision, directory_id, title, body_rich_text,
                    content_html, main_category, sub_category,
                    tags, ai_visibility, is_pinned, is_deleted, created_at,
                    updated_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, '', ?, 0, 0, ?, ?)
                """,
                (
                    note_uuid,
                    directory_id,
                    title,
                    content_html,
                    content_html,
                    main,
                    sub,
                    visibility,
                    timestamp,
                    timestamp,
                ),
            )
            note_id = int(cursor.lastrowid)
            self.tags.set_for_new_note_in_connection(
                connection, note_id, tags, timestamp
            )
            sync_ai_target_catalog(connection, target_catalog, timestamp)
            replace_note_ai_targets(connection, note_id, target_ids, timestamp)
            result = _result(
                "created", request_id, note_id, title, "direct", True, False, ""
            )
            self._store_operation(connection, actor_target_id, "create", result)
        return result

    def modify_direct(
        self,
        *,
        actor_target_id: str,
        request_id: str,
        note_id: int,
        operation_type: str,
        expected_revision: int,
        expected_updated_at: str,
        addition_html: str = "",
        title: str | None = None,
        directory_id: int | None = None,
        location: NoteLocation | None = None,
        tags: Sequence[str] | None = None,
    ) -> dict:
        with self.connections.write() as connection:
            existing = self._operation_from_connection(connection, request_id)
            if existing is not None:
                return _already_processed(existing)
            note = connection.execute(
                "SELECT * FROM notes WHERE id = ? AND is_deleted = 0",
                (note_id,),
            ).fetchone()
            if note is None:
                result = _result(
                    "not_found", request_id, note_id, "", "direct", False, False,
                    "未找到笔记。",
                )
                self._store_operation(
                    connection, actor_target_id, operation_type, result
                )
                return result
            if (
                int(note["revision"]) != int(expected_revision)
                or str(note["updated_at"]) != expected_updated_at
            ):
                result = _result(
                    "conflict", request_id, note_id, str(note["title"]),
                    "direct", False, False, "笔记已被更新。",
                )
                self._store_operation(
                    connection, actor_target_id, operation_type, result
                )
                return result

            timestamp = next_note_updated_at(str(note["updated_at"]))
            self._insert_history(
                connection,
                note,
                actor_target_id,
                request_id,
                operation_type,
                timestamp,
            )
            result_title = str(note["title"])
            if operation_type in {"prepend", "append"}:
                content_html = (
                    addition_html + str(note["content_html"])
                    if operation_type == "prepend"
                    else str(note["content_html"]) + addition_html
                )
                connection.execute(
                    """
                    UPDATE notes
                    SET content_html = ?, body_rich_text = ?,
                        revision = revision + 1, updated_at = ?
                    WHERE id = ? AND revision = ?
                    """,
                    (
                        content_html,
                        content_html,
                        timestamp,
                        note_id,
                        expected_revision,
                    ),
                )
            elif operation_type == "metadata":
                result_title = title if title is not None else str(note["title"])
                new_directory_id = (
                    directory_id if directory_id is not None else note["directory_id"]
                )
                new_location = location or NoteLocation(
                    str(note["main_category"]),
                    str(note["sub_category"]),
                )
                main, sub = new_location.values()
                connection.execute(
                    """
                    UPDATE notes
                    SET title = ?, directory_id = ?, main_category = ?,
                        sub_category = ?, revision = revision + 1, updated_at = ?
                    WHERE id = ? AND revision = ?
                    """,
                    (
                        result_title,
                        new_directory_id,
                        main,
                        sub,
                        timestamp,
                        note_id,
                        expected_revision,
                    ),
                )
                if tags is not None:
                    self.tags.set_for_new_note_in_connection(
                        connection, note_id, tags, timestamp
                    )
            else:
                raise ValueError(f"不支持的 AI 修改类型：{operation_type}")
            connection.execute(
                "UPDATE ai_note_history SET after_updated_at = ? WHERE request_id = ?",
                (timestamp, request_id),
            )
            result = _result(
                "updated",
                request_id,
                note_id,
                result_title,
                "direct",
                True,
                False,
                "",
            )
            self._store_operation(
                connection, actor_target_id, operation_type, result
            )
        return result

    def create_pending(
        self,
        *,
        actor_target_id: str,
        actor_display: str,
        request_id: str,
        operation_type: str,
        title: str,
        note_id: int | None,
        expected_updated_at: str | None,
        payload: dict,
        location: NoteLocation,
        tags: Sequence[str],
        body: str,
    ) -> dict:
        timestamp = timestamp_seconds()
        action = {
            "create": "新建",
            "prepend": "开头追加",
            "append": "末尾追加",
            "metadata": "修改元数据",
        }[operation_type]
        with self.connections.write() as connection:
            existing = self._operation_from_connection(connection, request_id)
            if existing is not None:
                return _already_processed(existing)
            main, sub = location.values()
            cursor = connection.execute(
                """
                INSERT INTO ai_receipt_inbox (
                    raw_text, parse_status, status, source, action, title,
                    main_category, sub_category, tags,
                    ai_visibility, ai_targets, body, note_id, created_at,
                    updated_at, parse_error, request_id, actor_target_id,
                    operation_type, target_note_id, expected_updated_at,
                    operation_payload, result_status
                ) VALUES (
                    '', 'parsed', 'pending', ?, ?, ?, ?, ?, ?, 'full', ?, ?,
                    NULL, ?, ?, '', ?, ?, ?, ?, ?, ?, 'pending_confirmation'
                )
                """,
                (
                    actor_display,
                    action,
                    title,
                    main,
                    sub,
                    ", ".join(tags),
                    actor_display,
                    body,
                    timestamp,
                    timestamp,
                    request_id,
                    actor_target_id,
                    operation_type,
                    note_id,
                    expected_updated_at,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
            result = _result(
                "pending_confirmation",
                request_id,
                note_id,
                title,
                "pending",
                False,
                True,
                "",
            )
            result["proposal_id"] = int(cursor.lastrowid)
            self._store_operation(
                connection, actor_target_id, operation_type, result
            )
        return result

    @staticmethod
    def _operation_result(row) -> dict | None:
        if row is None:
            return None
        return {
            **json.loads(row["result_json"]),
            "operation_type": str(row["operation_type"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def _operation_from_connection(self, connection, request_id: str) -> dict | None:
        row = connection.execute(
            """
            SELECT operation_type, result_json, created_at, updated_at
            FROM ai_operations WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()
        return self._operation_result(row)

    @staticmethod
    def _store_operation(
        connection,
        actor_target_id: str,
        operation_type: str,
        result: dict,
    ) -> None:
        timestamp = timestamp_seconds()
        connection.execute(
            """
            INSERT INTO ai_operations (
                request_id, actor_target_id, operation_type, status,
                result_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result["request_id"],
                actor_target_id,
                operation_type,
                result["status"],
                json.dumps(result, ensure_ascii=False),
                timestamp,
                timestamp,
            ),
        )

    @staticmethod
    def _insert_history(
        connection,
        note,
        actor_target_id: str,
        request_id: str,
        operation_type: str,
        timestamp: str,
    ) -> None:
        snapshot = {
            "title": note["title"],
            "content_html": note["content_html"],
            "body_rich_text": note["body_rich_text"],
            "main_category": note["main_category"],
            "sub_category": note["sub_category"],
            "tags": note["tags"],
            "ai_visibility": note["ai_visibility"],
        }
        connection.execute(
            """
            INSERT INTO ai_note_history (
                note_id, note_title, modification_type, actor_target_id,
                request_id, modified_at, before_snapshot, after_updated_at,
                handling_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'direct')
            """,
            (
                note["id"],
                note["title"],
                operation_type,
                actor_target_id,
                request_id,
                timestamp,
                json.dumps(snapshot, ensure_ascii=False),
                timestamp,
            ),
        )


def _result(
    status: str,
    request_id: str,
    note_id: int | None,
    title: str,
    handling_mode: str,
    changed: bool,
    pending: bool,
    reason: str,
) -> dict:
    return {
        "status": status,
        "request_id": request_id,
        "note_id": note_id,
        "title": title,
        "handling_mode": handling_mode,
        "modified_formal_note": changed,
        "entered_confirmation": pending,
        "reason": reason,
    }


def _already_processed(existing: dict) -> dict:
    return {
        **existing,
        "status": "already_processed",
        "previous_status": existing.get("status"),
    }
