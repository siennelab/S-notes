"""SQLite transaction owner for the frozen-R1 AI receipt inbox lifecycle."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Sequence

from src.snotes.application.ports import (
    AiReceiptInboxRepository,
    AiTargetCatalogEntry,
)
from src.snotes.domain.ai_receipts import (
    RECEIPT_STATUS_ACCEPTED,
    RECEIPT_STATUS_DISCARDED,
    RECEIPT_STATUS_PENDING,
    AiReceiptAcceptCommand,
    AiReceiptAcceptPersistence,
    AiReceiptDiscardPersistence,
    AiReceiptInboxItem,
    GaleReceiptImport,
)
from src.snotes.domain.models import DEFAULT_SUB_CATEGORY
from src.snotes.storage.ai_metadata import (
    replace_note_ai_targets,
    sync_ai_target_catalog,
)
from src.snotes.storage.connection import SQLiteConnectionFactory
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.support import Clock, now_shanghai, timestamp_seconds


RECEIPT_SELECT = """
SELECT id, parse_status, status, source, action, title,
       main_category, sub_category, tags,
       ai_visibility, ai_targets, body, note_id, created_at,
       updated_at, parse_error, request_id, actor_target_id,
       operation_type, external_submission_id, idempotency_key
FROM ai_receipt_inbox
"""

RECEIPT_ORDER = """
ORDER BY
    CASE status
        WHEN 'pending' THEN 0
        WHEN 'accepted' THEN 1
        WHEN 'discarded' THEN 2
        WHEN 'rejected' THEN 2
        ELSE 3
    END,
    updated_at DESC,
    id DESC
"""
GALE_IMPORT_SQL = """INSERT INTO ai_receipt_inbox (
    raw_text, parse_status, status, source, action, title, main_category,
    sub_category, tags, ai_visibility, ai_targets, body,
    created_at, updated_at, external_submission_id, idempotency_key
) VALUES ('', 'parsed', 'pending', 'Gale', '新增', ?, ?, ?, ?,
          ?, ?, ?, ?, ?, ?, ?)"""


class SQLiteAiReceiptInboxRepository(AiReceiptInboxRepository):
    def __init__(
        self,
        connections: SQLiteConnectionFactory,
        directories: SQLiteDirectoriesRepository,
        tags: SQLiteTagsRepository,
        *,
        clock: Clock = now_shanghai,
        uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    ) -> None:
        self.connections = connections
        self.directories = directories
        self.tags = tags
        self.clock = clock
        self.uuid_factory = uuid_factory

    def list_pending(self) -> list[AiReceiptInboxItem]:
        return self._list("status = ?", (RECEIPT_STATUS_PENDING,))

    def list_processed(self) -> list[AiReceiptInboxItem]:
        return self._list("status <> ?", (RECEIPT_STATUS_PENDING,))

    def get(self, receipt_id: int) -> AiReceiptInboxItem | None:
        with self.connections.read() as connection:
            row = connection.execute(
                RECEIPT_SELECT + " WHERE id = ?",
                (receipt_id,),
            ).fetchone()
        return _receipt_from_row(row) if row is not None else None

    def import_gale_pending(
        self, submission: GaleReceiptImport
    ) -> tuple[AiReceiptInboxItem, bool]:
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            existing = connection.execute(
                RECEIPT_SELECT
                + " WHERE source = ? AND (external_submission_id = ? OR idempotency_key = ?)",
                ("Gale", submission.external_submission_id, submission.idempotency_key),
            ).fetchone()
            if existing is not None:
                return _receipt_from_row(existing), False
            main, sub = submission.location.values()
            values = (
                submission.title,
                main,
                sub,
                ", ".join(submission.tags),
                submission.ai_visibility,
                ", ".join(submission.ai_targets),
                submission.body,
                timestamp,
                timestamp,
                submission.external_submission_id,
                submission.idempotency_key,
            )
            cursor = connection.execute(GALE_IMPORT_SQL, values)
            row = connection.execute(
                RECEIPT_SELECT + " WHERE id = ?", (int(cursor.lastrowid),)
            ).fetchone()
            if row is None:
                raise RuntimeError("Gale 投稿写入后读取失败。")
            return _receipt_from_row(row), True

    def accept_pending(
        self,
        receipt_id: int,
        command: AiReceiptAcceptCommand,
        target_catalog: Sequence[AiTargetCatalogEntry],
    ) -> AiReceiptAcceptPersistence:
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            row = connection.execute(
                RECEIPT_SELECT + " WHERE id = ?",
                (receipt_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"AI 回执不存在: {receipt_id}")
            receipt = _receipt_from_row(row)
            if receipt.status == RECEIPT_STATUS_ACCEPTED and receipt.note_id is not None:
                return AiReceiptAcceptPersistence(receipt, receipt.note_id, False)
            if not receipt.pending:
                raise ValueError("该回执已处理，不能重复接受。")

            existing_note_id = self._completed_operation_note_id(
                connection,
                receipt.request_id,
            )
            if existing_note_id is not None:
                linked = self._mark_accepted(
                    connection,
                    receipt,
                    command,
                    existing_note_id,
                    self._note_uuid(connection, existing_note_id),
                    timestamp,
                    result_status="created",
                )
                return AiReceiptAcceptPersistence(linked, existing_note_id, False)

            if command.allow_directory_creation:
                directory_id = self.directories.ensure_location(
                    connection,
                    command.location,
                )
            else:
                directory_id = self._existing_location_id(
                    connection,
                    command.location,
                )
                if directory_id is None:
                    raise ValueError("目标目录不存在，AI 不能创建目录。")

            note_uuid = str(self.uuid_factory())
            main, sub = command.location.values()
            cursor = connection.execute(
                """
                INSERT INTO notes (
                    note_uuid, revision, directory_id, title,
                    content_markdown, main_category, sub_category,
                    tags, ai_visibility, is_pinned, is_deleted, created_at,
                    updated_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?, '', ?, 0, 0, ?, ?)
                """,
                (
                    note_uuid,
                    directory_id,
                    command.title,
                    command.content_markdown,
                    main,
                    sub,
                    command.visibility,
                    timestamp,
                    timestamp,
                ),
            )
            note_id = int(cursor.lastrowid)
            self.tags.set_for_new_note_in_connection(
                connection,
                note_id,
                command.tags,
                timestamp,
            )
            sync_ai_target_catalog(connection, target_catalog, timestamp)
            replace_note_ai_targets(
                connection,
                note_id,
                command.target_ids,
                timestamp,
            )
            linked = self._mark_accepted(
                connection,
                receipt,
                command,
                note_id,
                note_uuid,
                timestamp,
                result_status="created",
            )
            if receipt.request_id:
                self._store_operation(
                    connection,
                    receipt,
                    note_id,
                    command.title,
                    timestamp,
                    status="created",
                    reason="",
                )
            return AiReceiptAcceptPersistence(linked, note_id, True)

    def discard_pending(self, receipt_id: int) -> AiReceiptDiscardPersistence:
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            row = connection.execute(
                RECEIPT_SELECT + " WHERE id = ?",
                (receipt_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"AI 回执不存在: {receipt_id}")
            receipt = _receipt_from_row(row)
            if receipt.status == RECEIPT_STATUS_DISCARDED:
                return AiReceiptDiscardPersistence(receipt, False)
            if not receipt.pending:
                raise ValueError("该回执已处理，不能重复丢弃。")
            cursor = connection.execute(
                """
                UPDATE ai_receipt_inbox
                SET status = ?, result_status = ?, updated_at = ?, decided_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    RECEIPT_STATUS_DISCARDED,
                    RECEIPT_STATUS_DISCARDED,
                    timestamp,
                    timestamp,
                    receipt_id,
                    RECEIPT_STATUS_PENDING,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("AI 回执状态已改变，请刷新后重试。")
            if receipt.request_id:
                self._store_operation(
                    connection,
                    receipt,
                    receipt.note_id,
                    receipt.title,
                    timestamp,
                    status="rejected",
                    reason="用户已丢弃。",
                )
            updated = self._read_from_connection(connection, receipt_id)
            return AiReceiptDiscardPersistence(updated, True)

    def delete_if_unchanged(
        self,
        receipt_id: int,
        *,
        expected_status: str,
        expected_note_id: int | None,
    ) -> bool:
        with self.connections.write() as connection:
            cursor = connection.execute(
                """
                DELETE FROM ai_receipt_inbox
                WHERE id = ? AND status = ?
                  AND (
                      note_id = ?
                      OR (note_id IS NULL AND ? IS NULL)
                  )
                """,
                (
                    receipt_id,
                    expected_status,
                    expected_note_id,
                    expected_note_id,
                ),
            )
            return cursor.rowcount == 1

    def _list(
        self,
        where_clause: str,
        parameters: tuple[str, ...],
    ) -> list[AiReceiptInboxItem]:
        with self.connections.read() as connection:
            rows = connection.execute(
                RECEIPT_SELECT + f" WHERE {where_clause} " + RECEIPT_ORDER,
                parameters,
            ).fetchall()
        return [_receipt_from_row(row) for row in rows]

    def _mark_accepted(
        self,
        connection,
        receipt: AiReceiptInboxItem,
        command: AiReceiptAcceptCommand,
        note_id: int,
        note_uuid: str,
        timestamp: str,
        *,
        result_status: str,
    ) -> AiReceiptInboxItem:
        main, sub = command.location.values()
        cursor = connection.execute(
            """
            UPDATE ai_receipt_inbox
            SET status = ?, title = ?, main_category = ?, sub_category = ?,
                tags = ?, ai_visibility = ?, ai_targets = ?, body = ?,
                note_id = ?, accepted_note_uuid = ?,
                result_status = ?, updated_at = ?, decided_at = ?
            WHERE id = ? AND status = ?
            """,
            (
                RECEIPT_STATUS_ACCEPTED,
                command.title,
                main,
                sub,
                ", ".join(command.tags),
                command.visibility,
                ", ".join(command.target_ids),
                command.content_markdown,
                note_id,
                note_uuid,
                result_status,
                timestamp,
                timestamp,
                receipt.id,
                RECEIPT_STATUS_PENDING,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("AI 回执状态已改变，请刷新后重试。")
        return self._read_from_connection(connection, receipt.id)

    def _existing_location_id(self, connection, location) -> int | None:
        main, sub = location.values()
        main_id = self.directories.find_active(connection, main, None)
        if main_id is None:
            return None
        if sub == DEFAULT_SUB_CATEGORY:
            return main_id
        sub_id = self.directories.find_active(connection, sub, main_id)
        if sub_id is None:
            return None
        return sub_id

    @staticmethod
    def _completed_operation_note_id(connection, request_id: str | None) -> int | None:
        if not request_id:
            return None
        row = connection.execute(
            "SELECT status, result_json FROM ai_operations WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        if row is None or row["status"] not in {"created", "accepted"}:
            return None
        try:
            note_id = int(json.loads(row["result_json"])["note_id"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        exists = connection.execute(
            "SELECT id FROM notes WHERE id = ? AND is_deleted = 0",
            (note_id,),
        ).fetchone()
        return note_id if exists is not None else None

    @staticmethod
    def _note_uuid(connection, note_id: int) -> str:
        row = connection.execute(
            "SELECT note_uuid FROM notes WHERE id = ? AND is_deleted = 0",
            (note_id,),
        ).fetchone()
        if row is None:
            raise ValueError("幂等记录关联的正式笔记不存在。")
        return str(row["note_uuid"] or "")

    @staticmethod
    def _store_operation(
        connection,
        receipt: AiReceiptInboxItem,
        note_id: int | None,
        title: str,
        timestamp: str,
        *,
        status: str,
        reason: str,
    ) -> None:
        if not receipt.request_id:
            return
        result = {
            "status": status,
            "request_id": receipt.request_id,
            "note_id": note_id,
            "title": title,
            "handling_mode": "human_approved" if status == "created" else "human_rejected",
            "changed": status == "created",
            "pending_confirmation": False,
            "reason": reason,
        }
        connection.execute(
            """
            INSERT INTO ai_operations (
                request_id, actor_target_id, operation_type, status,
                result_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(request_id) DO UPDATE SET
                actor_target_id = excluded.actor_target_id,
                operation_type = excluded.operation_type,
                status = excluded.status,
                result_json = excluded.result_json,
                updated_at = excluded.updated_at
            """,
            (
                receipt.request_id,
                receipt.actor_target_id or "",
                receipt.operation_type or "create",
                status,
                json.dumps(result, ensure_ascii=False, sort_keys=True),
                timestamp,
                timestamp,
            ),
        )

    @staticmethod
    def _read_from_connection(connection, receipt_id: int) -> AiReceiptInboxItem:
        row = connection.execute(
            RECEIPT_SELECT + " WHERE id = ?",
            (receipt_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("写入 AI 回执后读取失败。")
        return _receipt_from_row(row)


def _receipt_from_row(row) -> AiReceiptInboxItem:
    return AiReceiptInboxItem(
        id=int(row["id"]),
        parse_status=str(row["parse_status"]),
        status=str(row["status"]),
        source=str(row["source"]),
        action=str(row["action"]),
        title=str(row["title"]),
        main_category=str(row["main_category"]),
        sub_category=str(row["sub_category"]),
        tags=str(row["tags"]),
        ai_visibility=str(row["ai_visibility"]),
        ai_targets=str(row["ai_targets"]),
        body=str(row["body"]),
        note_id=int(row["note_id"]) if row["note_id"] is not None else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        parse_error=(
            str(row["parse_error"]) if row["parse_error"] is not None else None
        ),
        request_id=(
            str(row["request_id"]) if row["request_id"] is not None else None
        ),
        actor_target_id=(
            str(row["actor_target_id"])
            if row["actor_target_id"] is not None
            else None
        ),
        operation_type=(
            str(row["operation_type"])
            if row["operation_type"] is not None
            else None
        ),
        external_submission_id=(
            str(row["external_submission_id"])
            if row["external_submission_id"] is not None
            else None
        ),
        idempotency_key=(
            str(row["idempotency_key"])
            if row["idempotency_key"] is not None
            else None
        ),
    )
