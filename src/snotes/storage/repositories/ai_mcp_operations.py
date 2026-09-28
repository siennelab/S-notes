"""Atomic SQLite persistence for R2 local-MCP write operations."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence

from src.snotes.application.ports import (
    AiMcpOperationRepository,
    AcceptanceCommitFact,
    AcceptanceCommitResult,
    AiNoteFingerprint,
    AiTargetCatalogEntry,
)
from src.snotes.domain.notes_operation_result import (
    OperationStatus, HandlingMode,
    deserialize_operation_result_json,
    notes_operation_result as _result,
    serialize_notes_operation_result,
)
from src.snotes.domain.ai_receipts import AiReceiptInboxItem
from src.snotes.domain.models import Directory, NoteLocation
from src.snotes.storage.ai_metadata import (
    replace_note_ai_targets,
    sync_ai_target_catalog,
)
from src.snotes.storage.connection import SQLiteConnectionFactory
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.support import next_note_updated_at, timestamp_seconds


class SQLiteAiMcpOperationRepository(AiMcpOperationRepository):
    """Keep idempotency, note mutation, history, and receipts transactional."""

    def __init__(
        self,
        connections: SQLiteConnectionFactory,
        tags: SQLiteTagsRepository,
    ) -> None:
        self.connections = connections
        self.tags = tags

    def confirmation_setting(self, setting_key: str) -> bool | None:
        with self.connections.read() as connection:
            row = connection.execute(
                "SELECT setting_value FROM app_settings WHERE setting_key = ?",
                (setting_key,),
            ).fetchone()
        return None if row is None else row["setting_value"] == "1"

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
        content_markdown: str,
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
                    OperationStatus.FAILED,
                    request_id,
                    None,
                    title,
                    HandlingMode.DIRECT,
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
                    note_uuid, revision, directory_id, title,
                    content_markdown, main_category, sub_category,
                    tags, ai_visibility, is_pinned, is_deleted, created_at,
                    updated_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?, '', ?, 0, 0, ?, ?)
                """,
                (
                    note_uuid,
                    directory_id,
                    title,
                    content_markdown,
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
                OperationStatus.CREATED, request_id, note_id, title, HandlingMode.DIRECT, True, False, ""
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
        addition_markdown: str = "",
        canonical_markdown: str | None = None,
        expected_content_markdown: str | None = None,
        raw_candidate_markdown: str | None = None,
        edits: list[dict[str, str]] | None = None,
        title: str | None = None,
        directory_id: int | None = None,
        location: NoteLocation | None = None,
        tags: Sequence[str] | None = None,
        expected_note_fingerprint: AiNoteFingerprint | None = None,
        terminal_results: Mapping[AcceptanceCommitFact, dict],
    ) -> dict:
        if operation_type in {"append", "prepend", "edit"} and (
            canonical_markdown is None or expected_content_markdown is None
        ):
            raise ValueError("正文写入缺少 canonical 候选或原正文校验。")
        with self.connections.write() as connection:
            existing = self._operation_from_connection(connection, request_id)
            if existing is not None:
                return _already_processed(existing)
            note = connection.execute(
                "SELECT * FROM notes WHERE id = ? AND is_deleted = 0",
                (note_id,),
            ).fetchone()
            if note is None:
                result = dict(terminal_results[AcceptanceCommitFact.NOTE_MISSING])
                self._store_operation(
                    connection, actor_target_id, operation_type, result
                )
                return result
            if not _note_version_matches(
                note,
                expected_revision=expected_revision,
                expected_updated_at=expected_updated_at,
            ) or (
                operation_type in {"append", "prepend", "edit"}
                and str(note["content_markdown"]) != expected_content_markdown
            ) or (
                expected_note_fingerprint is not None
                and not self._fingerprint_matches(
                    connection, expected_note_fingerprint, note
                )
            ):
                result = {
                    **terminal_results[AcceptanceCommitFact.NOTE_CHANGED],
                    "title": str(note["title"]),
                }
                self._store_operation(
                    connection, actor_target_id, operation_type, result
                )
                return result

            result_title, _timestamp = self._apply_modification_in_connection(
                connection, note, actor_target_id=actor_target_id,
                request_id=request_id, operation_type=operation_type,
                expected_revision=expected_revision,
                canonical_markdown=canonical_markdown, title=title,
                directory_id=directory_id, location=location, tags=tags,
            )
            result = _result(
                OperationStatus.UPDATED,
                request_id,
                note_id,
                result_title,
                HandlingMode.DIRECT,
                True,
                False,
                "",
            )
            self._store_operation(
                connection, actor_target_id, operation_type, result
            )
        return result

    def pending_operation_payload(self, receipt_id: int) -> str | None:
        with self.connections.read() as connection:
            row = connection.execute(
                "SELECT operation_payload FROM ai_receipt_inbox WHERE id = ?",
                (receipt_id,),
            ).fetchone()
        return str(row["operation_payload"]) if row is not None and row["operation_payload"] is not None else None

    def accept_pending_modification(
        self,
        *,
        receipt_id: int,
        expected_receipt: AiReceiptInboxItem,
        expected_payload: str | None,
        accepted_body: str | None = None,
        canonical_markdown: str | None = None,
        expected_note_fingerprint: AiNoteFingerprint | None = None,
        expected_directory_id: int | None = None,
        expected_directory_snapshot: Sequence[Directory] = (),
        terminal_results: Mapping[AcceptanceCommitFact, dict],
        desired_title: str | None = None,
        location: NoteLocation | None = None,
        tags: Sequence[str] | None = None,
        apply_modification: bool = True,
    ) -> AcceptanceCommitResult:
        """Atomically compare prepared snapshots and persist one decision."""
        with self.connections.write() as connection:
            receipt = connection.execute(
                """
                SELECT id, status, parse_status, request_id, actor_target_id,
                       operation_type, target_note_id, expected_updated_at,
                       title, body, operation_payload, updated_at
                FROM ai_receipt_inbox WHERE id = ?
                """,
                (receipt_id,),
            ).fetchone()
            if receipt is None or receipt["status"] != "pending":
                fact = AcceptanceCommitFact.RECEIPT_CHANGED
                return AcceptanceCommitResult(fact, terminal_results[fact])
            request_id = str(receipt["request_id"] or "")
            actor_target_id = str(receipt["actor_target_id"] or "")
            operation_type = str(receipt["operation_type"] or "")
            note_id = (
                int(receipt["target_note_id"])
                if receipt["target_note_id"] is not None else None
            )
            receipt_title = str(receipt["title"] or "")

            def fail(fact: AcceptanceCommitFact) -> AcceptanceCommitResult:
                decision = terminal_results[fact]
                operation = self._finish_pending_modification(
                    connection, receipt, status=decision["status"],
                    reason=decision["reason"], handling_mode=decision["handling_mode"],
                    title=receipt_title, note_id=note_id,
                )
                return AcceptanceCommitResult(fact, operation)

            if (
                request_id != expected_receipt.request_id
                or receipt["parse_status"] != expected_receipt.parse_status
                or actor_target_id != expected_receipt.actor_target_id
                or operation_type != expected_receipt.operation_type
                or note_id != expected_receipt.target_note_id
                or receipt["expected_updated_at"] != expected_receipt.expected_updated_at
                or receipt_title != expected_receipt.title
                or str(receipt["body"] or "") != expected_receipt.body
                or receipt["operation_payload"] != expected_payload
                or receipt["updated_at"] != expected_receipt.updated_at
            ):
                return fail(AcceptanceCommitFact.RECEIPT_CHANGED)
            operation = connection.execute(
                """
                SELECT actor_target_id, operation_type, status, result_json
                FROM ai_operations WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()
            if operation is None:
                return fail(AcceptanceCommitFact.OPERATION_MISSING)
            try:
                stored_result = json.loads(operation["result_json"])
            except (TypeError, ValueError):
                stored_result = None
            if (
                operation["status"] != OperationStatus.PENDING_CONFIRMATION
                or operation["actor_target_id"] != actor_target_id
                or operation["operation_type"] != operation_type
                or not isinstance(stored_result, dict)
                or stored_result.get("proposal_id") != receipt_id
                or stored_result.get("status") != OperationStatus.PENDING_CONFIRMATION
            ):
                return fail(AcceptanceCommitFact.OPERATION_CHANGED)

            if not apply_modification:
                return fail(AcceptanceCommitFact.PREPARED_DECISION)

            note = connection.execute(
                "SELECT * FROM notes WHERE id = ? AND is_deleted = 0",
                (note_id,),
            ).fetchone()
            if note is None:
                return fail(AcceptanceCommitFact.NOTE_MISSING)
            if str(note["updated_at"]) != str(receipt["expected_updated_at"]):
                return fail(AcceptanceCommitFact.NOTE_CHANGED)
            if (
                expected_note_fingerprint is None
                or not self._fingerprint_matches(
                    connection, expected_note_fingerprint, note
                )
            ):
                return fail(AcceptanceCommitFact.NOTE_CHANGED)

            if location is not None:
                # Compare the actual directory rows selected by application,
                # including the parent, instead of unrelated note grants.
                if not expected_directory_snapshot:
                    return fail(AcceptanceCommitFact.DIRECTORY_CHANGED)
                for expected in expected_directory_snapshot:
                    directory = connection.execute(
                        "SELECT id, name, parent_id, updated_at FROM directories "
                        "WHERE id = ? AND is_deleted = 0", (expected.id,),
                    ).fetchone()
                    if directory is None or (
                        directory["name"], directory["parent_id"], directory["updated_at"]
                    ) != (expected.name, expected.parent_id, expected.updated_at):
                        return fail(AcceptanceCommitFact.DIRECTORY_CHANGED)

            result_title, timestamp = self._apply_modification_in_connection(
                connection, note, actor_target_id=actor_target_id,
                request_id=request_id, operation_type=operation_type,
                expected_revision=int(note["revision"]),
                canonical_markdown=canonical_markdown,
                title=desired_title, directory_id=expected_directory_id,
                location=location, tags=tags,
                handling_mode=HandlingMode.HUMAN_APPROVED,
            )
            result = _result(
                OperationStatus.UPDATED, request_id, note_id, result_title,
                HandlingMode.HUMAN_APPROVED, True, False, "",
            )
            result["proposal_id"] = receipt_id
            note_uuid = str(note["note_uuid"] or "")
            cursor = connection.execute(
                """
                UPDATE ai_receipt_inbox
                SET status = 'accepted', note_id = ?, accepted_note_uuid = ?,
                    body = ?, result_status = 'updated', updated_at = ?, decided_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (
                    note_id, note_uuid,
                    accepted_body if accepted_body is not None else receipt["body"],
                    timestamp, timestamp, receipt_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("AI 回执状态已改变，请刷新后重试。")
            rowcount = replace_pending_notes_operation_result(
                connection,
                actor_target_id=actor_target_id,
                operation_type=operation_type,
                result=result,
                timestamp=timestamp,
            )
            if rowcount != 1:
                raise RuntimeError("提案的操作记录已改变，请刷新后重试。")
            return AcceptanceCommitResult(AcceptanceCommitFact.COMMITTED, result)

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
        visibility: str,
        target_ids: Sequence[str],
    ) -> dict:
        timestamp = timestamp_seconds()
        action = {
            "create": "新建",
            "prepend": "开头追加",
            "append": "末尾追加",
            "edit": "精确修改",
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
                    '', 'parsed', 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?,
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
                    visibility,
                    ", ".join(target_ids),
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
                OperationStatus.PENDING_CONFIRMATION,
                request_id,
                note_id,
                title,
                HandlingMode.PENDING,
                False,
                True,
                "",
            )
            result["proposal_id"] = int(cursor.lastrowid)
            self._store_operation(
                connection, actor_target_id, operation_type, result
            )
        return result

    def _apply_modification_in_connection(
        self,
        connection,
        note,
        *,
        actor_target_id: str,
        request_id: str,
        operation_type: str,
        expected_revision: int,
        canonical_markdown: str | None = None,
        title: str | None = None,
        directory_id: int | None = None,
        location: NoteLocation | None = None,
        tags: Sequence[str] | None = None,
        handling_mode: str = HandlingMode.DIRECT,
    ) -> tuple[str, str]:
        timestamp = next_note_updated_at(str(note["updated_at"]))
        self._insert_history(
            connection, note, actor_target_id, request_id,
            operation_type, timestamp, handling_mode=handling_mode,
        )
        result_title = str(note["title"])
        if operation_type in {"append", "prepend", "edit"}:
            if canonical_markdown is None:
                raise ValueError("正文写入缺少 canonical 候选。")
            self._write_content_in_connection(
                connection, note, canonical_markdown=canonical_markdown,
                timestamp=timestamp, expected_revision=expected_revision,
            )
        elif operation_type == "metadata":
            result_title = title if title is not None else str(note["title"])
            new_directory_id = (
                directory_id if directory_id is not None else note["directory_id"]
            )
            new_location = location or NoteLocation(
                str(note["main_category"]), str(note["sub_category"]),
            )
            main, sub = new_location.values()
            cursor = connection.execute(
                """
                UPDATE notes
                SET title = ?, directory_id = ?, main_category = ?,
                    sub_category = ?, revision = revision + 1, updated_at = ?
                WHERE id = ? AND revision = ?
                """,
                (result_title, new_directory_id, main, sub, timestamp,
                 note["id"], expected_revision),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("笔记已被更新。")
            if tags is not None:
                self.tags.set_for_new_note_in_connection(
                    connection, int(note["id"]), tags, timestamp,
                )
        else:
            raise ValueError(f"不支持的 AI 修改类型：{operation_type}")
        connection.execute(
            "UPDATE ai_note_history SET after_updated_at = ? WHERE request_id = ?",
            (timestamp, request_id),
        )
        return result_title, timestamp

    @staticmethod
    def _fingerprint_matches(
        connection,
        expected: AiNoteFingerprint,
        note,
    ) -> bool:
        target_ids = tuple(
            sorted(
                str(row["target_id"])
                for row in connection.execute(
                    "SELECT target_id FROM note_ai_targets WHERE note_id = ?",
                    (expected.note_id,),
                ).fetchall()
            )
        )
        return (
            int(note["id"]) == expected.note_id
            and int(note["revision"]) == expected.revision
            and str(note["updated_at"]) == expected.updated_at
            and str(note["content_markdown"]) == expected.content_markdown
            and str(note["ai_visibility"]) == expected.ai_visibility
            and target_ids == expected.target_ids
            and note["directory_id"] == expected.directory_id
        )

    @staticmethod
    def _finish_pending_modification(
        connection, receipt, *, status: str, reason: str, handling_mode: str,
        title: str, note_id: int | None,
    ) -> dict:
        request_id = str(receipt["request_id"] or "")
        actor_target_id = str(receipt["actor_target_id"] or "")
        operation_type = str(receipt["operation_type"] or "")
        timestamp = timestamp_seconds()
        result = _result(
            status, request_id, note_id, title, handling_mode, False, False, reason
        )
        result["proposal_id"] = int(receipt["id"])
        connection.execute(
            """
            UPDATE ai_receipt_inbox
            SET status = 'rejected', result_status = ?,
                updated_at = ?, decided_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (status, timestamp, timestamp, receipt["id"]),
        )
        operation = connection.execute(
            """
            SELECT actor_target_id, operation_type, status, result_json
            FROM ai_operations WHERE request_id = ?
            """,
            (request_id,),
        ).fetchone()
        if operation is not None:
            try:
                previous = json.loads(operation["result_json"])
            except (TypeError, ValueError):
                previous = None
            if (
                operation["status"] == OperationStatus.PENDING_CONFIRMATION
                and operation["actor_target_id"] == actor_target_id
                and operation["operation_type"] == operation_type
                and isinstance(previous, dict)
                and previous.get("proposal_id") == receipt["id"]
            ):
                replace_pending_notes_operation_result(
                    connection,
                    actor_target_id=actor_target_id,
                    operation_type=operation_type,
                    result=result,
                    timestamp=timestamp,
                )
        return result

    @staticmethod
    def _operation_result(row) -> dict | None:
        return read_operation_result_row(row)

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
        insert_notes_operation_result(
            connection,
            actor_target_id=actor_target_id,
            operation_type=operation_type,
            result=result,
            timestamp=timestamp,
        )

    @staticmethod
    def _insert_history(
        connection,
        note,
        actor_target_id: str,
        request_id: str,
        operation_type: str,
        timestamp: str,
        *,
        handling_mode: str = HandlingMode.DIRECT,
    ) -> None:
        snapshot = {
            "title": note["title"],
            "content_markdown": note["content_markdown"],
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                handling_mode,
            ),
        )

    @staticmethod
    def _write_content_in_connection(
        connection,
        note,
        *,
        canonical_markdown: str,
        timestamp: str,
        expected_revision: int,
    ) -> None:
        cursor = connection.execute(
            """
            UPDATE notes
            SET content_markdown = ?,
                revision = revision + 1, updated_at = ?
            WHERE id = ? AND revision = ?
            """,
            (
                canonical_markdown,
                timestamp,
                note["id"],
                expected_revision,
            ),
        )
        if cursor.rowcount != 1:
            raise RuntimeError("笔记已被更新。")


def _note_version_matches(
    note,
    *,
    expected_revision: int,
    expected_updated_at: str,
) -> bool:
    return (
        int(note["revision"]) == int(expected_revision)
        and str(note["updated_at"]) == expected_updated_at
    )


def read_operation_result_row(row) -> dict[str, object] | None:
    """Compatibility reader shared by Notes operation status lookups."""

    if row is None:
        return None
    return {
        **deserialize_operation_result_json(str(row["result_json"])),
        "operation_type": str(row["operation_type"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def insert_notes_operation_result(
    connection,
    *,
    actor_target_id: str,
    operation_type: str,
    result: Mapping[str, object],
    timestamp: str,
) -> None:
    """Persist a fresh Notes result using the canonical serializer."""

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
            serialize_notes_operation_result(result),
            timestamp,
            timestamp,
        ),
    )


def upsert_notes_operation_result(
    connection,
    *,
    actor_target_id: str,
    operation_type: str,
    result: Mapping[str, object],
    timestamp: str,
) -> None:
    """Persist a receipt decision without changing its original creation time."""

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
            result["request_id"],
            actor_target_id,
            operation_type,
            result["status"],
            serialize_notes_operation_result(result),
            timestamp,
            timestamp,
        ),
    )


def store_receipt_decision_operation_result(
    connection,
    *,
    receipt: AiReceiptInboxItem,
    note_id: int | None,
    title: str,
    timestamp: str,
    status: str,
    reason: str,
) -> None:
    """Own the business result and persistence for a Notes receipt decision."""

    if not receipt.request_id:
        return
    result = _result(
        status,
        receipt.request_id,
        note_id,
        title,
        HandlingMode.HUMAN_APPROVED if status == OperationStatus.CREATED else HandlingMode.HUMAN_REJECTED,
        status == OperationStatus.CREATED,
        False,
        reason,
        proposal_id=receipt.id,
    )
    upsert_notes_operation_result(
        connection,
        actor_target_id=receipt.actor_target_id or "",
        operation_type=receipt.operation_type or "create",
        result=result,
        timestamp=timestamp,
    )


def replace_pending_notes_operation_result(
    connection,
    *,
    actor_target_id: str,
    operation_type: str,
    result: Mapping[str, object],
    timestamp: str,
) -> int:
    """Replace exactly the pending Notes result selected by the transaction."""

    cursor = connection.execute(
        """
        UPDATE ai_operations
        SET status = ?, result_json = ?, updated_at = ?
        WHERE request_id = ? AND actor_target_id = ?
          AND operation_type = ? AND status = 'pending_confirmation'
        """,
        (
            result["status"],
            serialize_notes_operation_result(result),
            timestamp,
            result["request_id"],
            actor_target_id,
            operation_type,
        ),
    )
    return int(cursor.rowcount)


def _already_processed(existing: dict) -> dict:
    return {
        **existing,
        "status": OperationStatus.ALREADY_PROCESSED,
        "previous_status": existing.get("status"),
    }
