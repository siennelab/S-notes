"""SQLite transaction owner for the frozen-R1 AI receipt inbox lifecycle."""

from __future__ import annotations

import json
import hashlib
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
    GreenCloudReceiptImport,
)
from src.snotes.domain.greencloud import (
    GREEN_CLOUD_SOURCE,
    GreenCloudTerminalReceipt,
)
from src.snotes.domain.notes_operation_result import (
    HandlingMode,
    OperationStatus,
    notes_operation_result,
)
from src.snotes.domain.models import DEFAULT_SUB_CATEGORY
from src.snotes.storage.ai_metadata import (
    replace_note_ai_targets,
    sync_ai_target_catalog,
)
from src.snotes.storage.connection import SQLiteConnectionFactory
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.ai_mcp_operations import (
    insert_notes_operation_result,
    store_receipt_decision_operation_result,
)
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.support import Clock, now_shanghai, timestamp_seconds


RECEIPT_SELECT = """
SELECT id, parse_status, status, source, action, title,
       main_category, sub_category, tags,
       ai_visibility, ai_targets, body, note_id, created_at,
       updated_at, parse_error, request_id, actor_target_id,
       operation_type, external_submission_id, idempotency_key,
       target_note_id, expected_updated_at
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

GREEN_CLOUD_ACTIONS = {
    "create": "新建",
    "append": "末尾追加",
    "edit": "精确修改",
}
GREEN_CLOUD_ENVELOPE_SCHEMA = 1


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

    def import_greencloud_pending(
        self, submission: GreenCloudReceiptImport
    ) -> tuple[AiReceiptInboxItem, bool]:
        """Import one Cloud proposal and its pending operation atomically."""

        timestamp = timestamp_seconds(self.clock)
        payload_json = json.dumps(
            submission.operation_payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        envelope = _greencloud_envelope(
            submission.source_actor,
            submission.cloud_request_id,
            submission.cloud_expected_version,
        )
        with self.connections.write() as connection:
            existing = connection.execute(
                """
                SELECT * FROM ai_receipt_inbox
                WHERE source = ?
                  AND (external_submission_id = ? OR idempotency_key = ?)
                """,
                (
                    GREEN_CLOUD_SOURCE,
                    submission.submission_uuid,
                    submission.local_idempotency_key,
                ),
            ).fetchone()
            if existing is not None:
                if not _same_greencloud_import(
                    existing,
                    submission,
                    payload_json=payload_json,
                ):
                    raise ValueError("GreenCloud 投稿幂等身份冲突。")
                return _receipt_from_row(existing), False

            operation = connection.execute(
                "SELECT request_id FROM ai_operations WHERE request_id = ?",
                (submission.local_request_id,),
            ).fetchone()
            if operation is not None:
                raise ValueError("GreenCloud 本地 request_id 冲突。")

            main, sub = submission.location.values()
            cursor = connection.execute(
                """
                INSERT INTO ai_receipt_inbox (
                    raw_text, parse_status, status, source, action, title,
                    main_category, sub_category, tags,
                    ai_visibility, ai_targets, body, note_id,
                    created_at, updated_at, parse_error,
                    request_id, actor_target_id, operation_type,
                    target_note_id, expected_updated_at, operation_payload,
                    result_status, external_submission_id, idempotency_key
                ) VALUES (
                    ?, 'parsed', 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL,
                    ?, ?, '', ?, ?, ?, ?, ?, ?, 'pending_confirmation', ?, ?
                )
                """,
                (
                    json.dumps(
                        envelope,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    GREEN_CLOUD_SOURCE,
                    GREEN_CLOUD_ACTIONS[submission.operation_type],
                    submission.title,
                    main,
                    sub,
                    ", ".join(submission.tags),
                    submission.ai_visibility,
                    ", ".join(submission.ai_targets),
                    submission.body,
                    timestamp,
                    timestamp,
                    submission.local_request_id,
                    submission.source_actor,
                    submission.operation_type,
                    submission.target_note_id,
                    submission.expected_updated_at,
                    payload_json,
                    submission.submission_uuid,
                    submission.local_idempotency_key,
                ),
            )
            receipt_id = int(cursor.lastrowid)
            pending = notes_operation_result(
                OperationStatus.PENDING_CONFIRMATION,
                submission.local_request_id,
                submission.target_note_id,
                submission.title,
                HandlingMode.PENDING,
                False,
                True,
                "",
                proposal_id=receipt_id,
            )
            insert_notes_operation_result(
                connection,
                actor_target_id=submission.source_actor,
                operation_type=submission.operation_type,
                result=pending,
                timestamp=timestamp,
            )
            row = connection.execute(
                RECEIPT_SELECT + " WHERE id = ?", (receipt_id,)
            ).fetchone()
            if row is None:
                raise RuntimeError("GreenCloud 投稿写入后读取失败。")
            return _receipt_from_row(row), True

    def reject_pending_decision(
        self,
        receipt_id: int,
        *,
        result_status: str,
        reason: str,
    ) -> AiReceiptInboxItem:
        """Persist a failed human acceptance as a terminal decision."""

        if result_status not in {OperationStatus.REJECTED, OperationStatus.CONFLICT}:
            raise ValueError("无效的 AI 回执终态。")
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            row = connection.execute(
                RECEIPT_SELECT + " WHERE id = ?", (receipt_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"AI 回执不存在: {receipt_id}")
            receipt = _receipt_from_row(row)
            if not receipt.pending:
                return receipt
            cursor = connection.execute(
                """
                UPDATE ai_receipt_inbox
                SET status = 'rejected', result_status = ?,
                    updated_at = ?, decided_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (result_status, timestamp, timestamp, receipt_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("AI 回执状态已改变，请刷新后重试。")
            store_receipt_decision_operation_result(
                connection,
                receipt=receipt,
                note_id=receipt.target_note_id,
                title=receipt.title,
                timestamp=timestamp,
                status=result_status,
                reason=reason,
            )
            return self._read_from_connection(connection, receipt_id)

    def reject_pending_by_actor(
        self,
        *,
        source: str,
        actor_target_id: str,
    ) -> tuple[int, ...]:
        """Reject one actor's pending receipts without crossing source."""

        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            rows = connection.execute(
                RECEIPT_SELECT
                + " WHERE status = ? AND source = ? AND actor_target_id = ? "
                + " ORDER BY id",
                (RECEIPT_STATUS_PENDING, source, actor_target_id),
            ).fetchall()
            receipts = tuple(_receipt_from_row(row) for row in rows)
            for receipt in receipts:
                cursor = connection.execute(
                    """
                    UPDATE ai_receipt_inbox
                    SET status = ?, result_status = ?,
                        updated_at = ?, decided_at = ?
                    WHERE id = ? AND status = ? AND source = ?
                      AND actor_target_id = ?
                    """,
                    (
                        RECEIPT_STATUS_DISCARDED,
                        OperationStatus.REJECTED,
                        timestamp,
                        timestamp,
                        receipt.id,
                        RECEIPT_STATUS_PENDING,
                        source,
                        actor_target_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("AI 回执状态已改变，请刷新后重试。")
                store_receipt_decision_operation_result(
                    connection,
                    receipt=receipt,
                    note_id=receipt.target_note_id,
                    title=receipt.title,
                    timestamp=timestamp,
                    status=OperationStatus.REJECTED,
                    reason="用户已按 actor 批量拒绝。",
                )
            return tuple(receipt.id for receipt in receipts)

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
                    result_status=OperationStatus.CREATED,
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
                result_status=OperationStatus.CREATED,
            )
            if receipt.request_id:
                store_receipt_decision_operation_result(
                    connection,
                    receipt=receipt,
                    note_id=note_id,
                    title=command.title,
                    timestamp=timestamp,
                    status=OperationStatus.CREATED,
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
                store_receipt_decision_operation_result(
                    connection,
                    receipt=receipt,
                    note_id=(
                        receipt.target_note_id
                        if receipt.target_note_id is not None
                        else receipt.note_id
                    ),
                    title=receipt.title,
                    timestamp=timestamp,
                    status=OperationStatus.REJECTED,
                    reason="用户已丢弃。",
                )
            updated = self._read_from_connection(connection, receipt_id)
            return AiReceiptDiscardPersistence(updated, True)

    def pending_greencloud_terminal_receipts(
        self,
    ) -> tuple[GreenCloudTerminalReceipt, ...]:
        with self.connections.read() as connection:
            rows = connection.execute(
                """
                SELECT r.*, o.result_json
                FROM ai_receipt_inbox AS r
                LEFT JOIN ai_operations AS o ON o.request_id = r.request_id
                WHERE r.source = ? AND r.status <> 'pending'
                ORDER BY r.id
                """,
                (GREEN_CLOUD_SOURCE,),
            ).fetchall()
        pending: list[GreenCloudTerminalReceipt] = []
        for row in rows:
            terminal = _greencloud_terminal_from_row(row)
            if terminal is not None:
                pending.append(terminal)
        return tuple(pending)

    def mark_greencloud_receipt_sent(
        self,
        receipt_id: int,
        fingerprint: str,
    ) -> bool:
        with self.connections.write() as connection:
            row = connection.execute(
                """
                SELECT r.*, o.result_json
                FROM ai_receipt_inbox AS r
                LEFT JOIN ai_operations AS o ON o.request_id = r.request_id
                WHERE r.id = ? AND r.source = ?
                """,
                (receipt_id, GREEN_CLOUD_SOURCE),
            ).fetchone()
            if row is None:
                raise ValueError("GreenCloud 回执不存在。")
            terminal = _greencloud_terminal_from_row(row, include_sent=True)
            if terminal is None or terminal.fingerprint != fingerprint:
                raise ValueError("GreenCloud 回执终态已改变。")
            envelope = _parse_greencloud_envelope(row["raw_text"])
            sent = envelope.get("terminal_sent_fingerprint")
            if sent == fingerprint:
                return False
            if sent is not None:
                raise ValueError("GreenCloud 回执已记录其他终态。")
            if terminal.terminal_state == "accepted" and not terminal.mirror_published:
                raise ValueError("GreenCloud accepted 回执必须先发布 actor mirror。")
            envelope["terminal_sent_fingerprint"] = fingerprint
            cursor = connection.execute(
                "UPDATE ai_receipt_inbox SET raw_text = ? WHERE id = ? AND raw_text = ?",
                (
                    json.dumps(
                        envelope,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    receipt_id,
                    row["raw_text"],
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("GreenCloud 回执状态已改变。")
            return True

    def mark_greencloud_mirror_published(
        self,
        receipt_id: int,
        fingerprint: str,
    ) -> bool:
        with self.connections.write() as connection:
            row = connection.execute(
                """
                SELECT r.*, o.result_json
                FROM ai_receipt_inbox AS r
                LEFT JOIN ai_operations AS o ON o.request_id = r.request_id
                WHERE r.id = ? AND r.source = ?
                """,
                (receipt_id, GREEN_CLOUD_SOURCE),
            ).fetchone()
            if row is None:
                raise ValueError("GreenCloud 回执不存在。")
            terminal = _greencloud_terminal_from_row(row, include_sent=True)
            if (
                terminal is None
                or terminal.terminal_state != "accepted"
                or terminal.fingerprint != fingerprint
            ):
                raise ValueError("GreenCloud mirror 标记与 accepted 终态不匹配。")
            envelope = _parse_greencloud_envelope(row["raw_text"])
            if envelope.get("mirror_published") is True:
                return False
            envelope["mirror_published"] = True
            cursor = connection.execute(
                "UPDATE ai_receipt_inbox SET raw_text = ? WHERE id = ? AND raw_text = ?",
                (
                    json.dumps(
                        envelope,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                    receipt_id,
                    row["raw_text"],
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("GreenCloud mirror 标记状态已改变。")
            return True

    def delete_if_unchanged(
        self,
        receipt_id: int,
        *,
        expected_status: str,
        expected_note_id: int | None,
    ) -> bool:
        with self.connections.write() as connection:
            row = connection.execute(
                "SELECT r.*, o.result_json FROM ai_receipt_inbox AS r "
                "LEFT JOIN ai_operations AS o ON o.request_id = r.request_id "
                "WHERE r.id = ? AND r.source = ?",
                (receipt_id, GREEN_CLOUD_SOURCE),
            ).fetchone()
            if row is not None and _greencloud_terminal_from_row(row) is not None:
                return False
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
        if row is None or row["status"] not in {OperationStatus.CREATED, OperationStatus.ACCEPTED}:
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
    def _read_from_connection(connection, receipt_id: int) -> AiReceiptInboxItem:
        row = connection.execute(
            RECEIPT_SELECT + " WHERE id = ?",
            (receipt_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("写入 AI 回执后读取失败。")
        return _receipt_from_row(row)


def _greencloud_envelope(
    source_actor: str, cloud_request_id: str, expected_version: str | None,
) -> dict[str, object]:
    return {
        "schema": GREEN_CLOUD_ENVELOPE_SCHEMA,
        "source_actor": source_actor,
        "cloud_request_id": cloud_request_id,
        "cloud_expected_version": expected_version,
        "mirror_published": False,
        "terminal_sent_fingerprint": None,
    }


def _parse_greencloud_envelope(raw: object) -> dict[str, object]:
    try:
        value = json.loads(str(raw or ""))
    except (TypeError, ValueError) as error:
        raise ValueError("GreenCloud transport envelope 无效。") from error
    if (
        not isinstance(value, dict)
        or value.get("schema") != GREEN_CLOUD_ENVELOPE_SCHEMA
        or not isinstance(value.get("source_actor"), str)
        or not isinstance(value.get("cloud_request_id"), str)
        or not value["cloud_request_id"]
        or type(value.get("mirror_published")) is not bool
        or (
            value.get("terminal_sent_fingerprint") is not None
            and not isinstance(value.get("terminal_sent_fingerprint"), str)
        )
    ):
        raise ValueError("GreenCloud transport envelope 无效。")
    return value


def _same_greencloud_import(
    row,
    submission: GreenCloudReceiptImport,
    *,
    payload_json: str,
) -> bool:
    try:
        envelope = _parse_greencloud_envelope(row["raw_text"])
    except ValueError:
        return False
    return (
        str(row["source"]) == GREEN_CLOUD_SOURCE
        and str(row["external_submission_id"] or "") == submission.submission_uuid
        and str(row["idempotency_key"] or "") == submission.local_idempotency_key
        and str(row["request_id"] or "") == submission.local_request_id
        and str(row["actor_target_id"] or "") == submission.source_actor
        and str(row["operation_type"] or "") == submission.operation_type
        and row["target_note_id"] == submission.target_note_id
        and str(row["operation_payload"] or "") == payload_json
        and envelope["source_actor"] == submission.source_actor
        and envelope["cloud_request_id"] == submission.cloud_request_id
        and envelope.get("cloud_expected_version") == submission.cloud_expected_version
    )


def _greencloud_terminal_from_row(
    row,
    *,
    include_sent: bool = False,
) -> GreenCloudTerminalReceipt | None:
    status = str(row["status"] or "")
    if status == RECEIPT_STATUS_PENDING:
        return None
    if status == RECEIPT_STATUS_ACCEPTED:
        terminal_state = "accepted"
    elif str(row["result_status"] or "") == OperationStatus.CONFLICT:
        terminal_state = "conflict"
    elif status in {RECEIPT_STATUS_DISCARDED, "rejected"}:
        terminal_state = "rejected"
    else:
        return None

    envelope = _parse_greencloud_envelope(row["raw_text"])
    operation: dict[str, object] = {}
    if row["result_json"] is not None:
        try:
            parsed = json.loads(str(row["result_json"]))
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            operation = parsed
    note_id = operation.get("note_id")
    if note_id is None:
        note_id = row["note_id"] if row["note_id"] is not None else row["target_note_id"]
    receipt_payload: dict[str, object] = {
        "local_receipt_id": str(row["id"]),
        "result_status": str(row["result_status"] or terminal_state),
    }
    if note_id is not None:
        receipt_payload["note_id"] = int(note_id)
    reason = operation.get("reason")
    if isinstance(reason, str) and reason:
        receipt_payload["reason"] = reason
    fingerprint_material = {
        "receipt_id": int(row["id"]),
        "source_actor": envelope["source_actor"],
        "cloud_request_id": envelope["cloud_request_id"],
        "terminal_state": terminal_state,
        "receipt_payload": receipt_payload,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_material,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    if not include_sent and envelope.get("terminal_sent_fingerprint") == fingerprint:
        return None
    return GreenCloudTerminalReceipt(
        receipt_id=int(row["id"]),
        source_actor=str(envelope["source_actor"]),
        cloud_request_id=str(envelope["cloud_request_id"]),
        terminal_state=terminal_state,
        receipt_payload=receipt_payload,
        fingerprint=fingerprint,
        mirror_published=bool(envelope["mirror_published"]),
    )


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
        target_note_id=(
            int(row["target_note_id"])
            if row["target_note_id"] is not None
            else None
        ),
        expected_updated_at=(
            str(row["expected_updated_at"])
            if row["expected_updated_at"] is not None
            else None
        ),
    )
