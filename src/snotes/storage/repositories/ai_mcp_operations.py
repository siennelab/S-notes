"""Atomic SQLite persistence for R2 local-MCP write operations."""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence

from src.snotes.application.ports import (
    AiMcpOperationRepository,
    AiTargetCatalogEntry,
)
from src.snotes.application.ai_actor_registry import resolve_target_id
from src.snotes.application.ai_pending_metadata import parse_pending_metadata_payload
from src.snotes.application.note_canonical_candidate import (
    apply_exact_markdown_edits,
    build_canonical_edit_candidate,
)
from src.snotes.application.ai_safe_read_service import (
    AI_VISIBILITY_FULL,
    AI_VISIBILITY_HIDDEN,
    is_actor_authorized,
    normalize_ai_visibility,
)
from src.snotes.domain.ai_receipts import AiReceiptInboxItem
from src.snotes.domain.models import DEFAULT_SUB_CATEGORY, NoteLocation
from src.snotes.storage.ai_metadata import (
    replace_note_ai_targets,
    sync_ai_target_catalog,
)
from src.snotes.storage.connection import SQLiteConnectionFactory
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
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
        addition_markdown: str = "",
        canonical_markdown: str | None = None,
        expected_content_markdown: str | None = None,
        raw_candidate_markdown: str | None = None,
        edits: list[dict[str, str]] | None = None,
        title: str | None = None,
        directory_id: int | None = None,
        location: NoteLocation | None = None,
        tags: Sequence[str] | None = None,
    ) -> dict:
        if operation_type in {"append", "prepend", "edit"} and (
            canonical_markdown is None or expected_content_markdown is None
        ):
            raise ValueError("正文写入缺少 canonical 候选或原正文校验。")
        if operation_type == "edit" and (edits is None or raw_candidate_markdown is None):
            raise ValueError("正文修改缺少精确替换候选。")
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
            if not _note_version_matches(
                note,
                expected_revision=expected_revision,
                expected_updated_at=expected_updated_at,
            ) or (
                operation_type in {"append", "prepend", "edit"}
                and str(note["content_markdown"]) != expected_content_markdown
            ):
                result = _result(
                    "conflict", request_id, note_id, str(note["title"]),
                    "direct", False, False, "笔记已被更新。",
                )
                self._store_operation(
                    connection, actor_target_id, operation_type, result
                )
                return result

            if operation_type == "edit":
                if not self._actor_can_edit_note(connection, note, actor_target_id):
                    result = _result(
                        "not_found", request_id, note_id, "", "direct", False,
                        False, "未找到笔记。",
                    )
                    self._store_operation(connection, actor_target_id, operation_type, result)
                    return result
                try:
                    current_candidate = apply_exact_markdown_edits(
                        str(note["content_markdown"]), edits
                    )
                except (TypeError, ValueError) as error:
                    result = _result(
                        "rejected", request_id, note_id, str(note["title"]),
                        "direct", False, False, str(error),
                    )
                    self._store_operation(connection, actor_target_id, operation_type, result)
                    return result
                if current_candidate != raw_candidate_markdown:
                    result = _result(
                        "conflict", request_id, note_id, str(note["title"]),
                        "direct", False, False, "笔记已被更新。",
                    )
                    self._store_operation(connection, actor_target_id, operation_type, result)
                    return result

            result_title, _timestamp = self._apply_modification_in_connection(
                connection, note, actor_target_id=actor_target_id,
                request_id=request_id, operation_type=operation_type,
                expected_revision=expected_revision,
                canonical_markdown=canonical_markdown, title=title,
                directory_id=directory_id, location=location, tags=tags,
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

    def accept_pending_append(
        self,
        *,
        receipt_id: int,
        addition_markdown: str,
        accepted_body: str,
        canonical_markdown: str,
        expected_revision: int,
        expected_content_markdown: str,
    ) -> dict:
        """Compatibility entry point; use the checked acceptance transaction."""
        from types import SimpleNamespace

        with self.connections.read() as connection:
            row = connection.execute(
                """
                SELECT request_id, actor_target_id, operation_type,
                       target_note_id, expected_updated_at, title, body
                FROM ai_receipt_inbox WHERE id = ?
                """,
                (receipt_id,),
            ).fetchone()
        if row is None:
            raise ValueError(f"AI 回执不存在: {receipt_id}")
        if row["operation_type"] != "append":
            raise ValueError("该回执不是末尾追加提案。")
        expected = SimpleNamespace(**dict(row))
        return self.accept_pending_modification(
            receipt_id=receipt_id,
            expected_receipt=expected,
            expected_payload=self.pending_operation_payload(receipt_id),
            accepted_body=accepted_body,
            canonical_markdown=canonical_markdown,
            expected_revision=expected_revision,
            expected_content_markdown=expected_content_markdown,
        )

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
        raw_candidate_markdown: str | None = None,
        expected_revision: int | None = None,
        expected_content_markdown: str | None = None,
        failure_reason: str | None = None,
    ) -> dict:
        """Recheck the proposal and current grants in the write transaction."""
        with self.connections.write() as connection:
            receipt = connection.execute(
                """
                SELECT id, status, parse_status, request_id, actor_target_id,
                       operation_type, target_note_id, expected_updated_at,
                       title, body, operation_payload
                FROM ai_receipt_inbox WHERE id = ?
                """,
                (receipt_id,),
            ).fetchone()
            if receipt is None:
                raise ValueError(f"AI 回执不存在: {receipt_id}")
            if receipt["status"] != "pending":
                raise ValueError("该回执已处理，不能重复接受。")
            request_id = str(receipt["request_id"] or "")
            actor_target_id = str(receipt["actor_target_id"] or "")
            operation_type = str(receipt["operation_type"] or "")
            note_id = (
                int(receipt["target_note_id"])
                if receipt["target_note_id"] is not None else None
            )
            title = str(receipt["title"] or "")

            def fail(status: str, reason: str) -> dict:
                return self._finish_pending_modification(
                    connection, receipt, status=status, reason=reason,
                    title=title, note_id=note_id,
                )

            if operation_type not in {"append", "prepend", "edit", "metadata"}:
                return fail("rejected", "不支持的 AI 修改类型。")
            if (
                receipt["parse_status"] != "parsed"
                or request_id != expected_receipt.request_id
                or actor_target_id != expected_receipt.actor_target_id
                or operation_type != expected_receipt.operation_type
                or note_id != expected_receipt.target_note_id
                or receipt["expected_updated_at"] != expected_receipt.expected_updated_at
                or title != expected_receipt.title
                or str(receipt["body"] or "") != expected_receipt.body
                or receipt["operation_payload"] != expected_payload
            ):
                return fail("conflict", "提案已被更新，请刷新后重试。")
            if not request_id or not actor_target_id or note_id is None or not receipt["expected_updated_at"]:
                return fail("rejected", "提案缺少必要的身份、目标或版本信息。")

            operation = connection.execute(
                """
                SELECT actor_target_id, operation_type, status, result_json
                FROM ai_operations WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()
            if operation is None:
                return fail("rejected", "提案的操作记录不存在。")
            try:
                stored_result = json.loads(operation["result_json"])
            except (TypeError, ValueError):
                stored_result = None
            if (
                operation["status"] != "pending_confirmation"
                or operation["actor_target_id"] != actor_target_id
                or operation["operation_type"] != operation_type
                or not isinstance(stored_result, dict)
                or stored_result.get("proposal_id") != receipt_id
                or stored_result.get("status") != "pending_confirmation"
            ):
                return fail("conflict", "提案的操作记录已改变。")

            note = connection.execute(
                "SELECT * FROM notes WHERE id = ? AND is_deleted = 0",
                (note_id,),
            ).fetchone()
            if note is None:
                return fail("rejected", "目标笔记不存在。")
            can_modify = (
                self._actor_can_edit_note(connection, note, actor_target_id)
                if operation_type == "edit"
                else self._actor_can_modify_note(connection, note, actor_target_id)
            )
            if not can_modify:
                return fail("rejected", "当前 AI 身份已无权修改此笔记。")
            if str(note["updated_at"]) != str(receipt["expected_updated_at"]):
                return fail("conflict", "笔记已被更新。")
            if expected_revision is not None and int(note["revision"]) != expected_revision:
                return fail("conflict", "笔记已被更新。")
            if expected_content_markdown is not None and str(note["content_markdown"]) != expected_content_markdown:
                return fail("conflict", "笔记已被更新。")

            desired_title: str | None = None
            directory_id: int | None = None
            location: NoteLocation | None = None
            tags: tuple[str, ...] | None = None
            if operation_type == "metadata":
                if expected_payload is None:
                    return fail("rejected", "元数据提案内容不存在。")
                try:
                    changes = parse_pending_metadata_payload(expected_payload)
                except ValueError as error:
                    return fail("rejected", str(error))
                desired_title = changes.get("title")
                tags = changes.get("tags")
                if "directory" in changes:
                    location = NoteLocation(*changes["directory"])
                    directory_id = self._active_location_id(connection, location)
                    if directory_id is None or not self._actor_can_use_directory(
                        connection, directory_id, actor_target_id
                    ):
                        return fail("rejected", "目标目录不可用；只能选择当前身份可见的已有目录。")
            else:
                if expected_payload is None:
                    return fail("rejected", "正文提案内容不存在。")
                try:
                    payload = json.loads(expected_payload)
                except (TypeError, ValueError):
                    payload = None
                if operation_type == "edit":
                    if not isinstance(payload, dict) or set(payload) != {"edits"}:
                        return fail("rejected", "正文修改提案内容无效。")
                    try:
                        current_candidate = apply_exact_markdown_edits(
                            str(note["content_markdown"]), payload["edits"]
                        )
                    except (TypeError, ValueError) as error:
                        return fail("rejected", str(error))
                    if (
                        raw_candidate_markdown is not None
                        and current_candidate != raw_candidate_markdown
                    ):
                        return fail("conflict", "笔记已被更新。")
                    if failure_reason:
                        return fail("rejected", failure_reason)
                    from src.snotes.ui.markdown_canonicalizer import CanonicalizationError

                    try:
                        transaction_candidate = build_canonical_edit_candidate(
                            str(note["content_markdown"]), payload["edits"]
                        )
                    except (CanonicalizationError, TypeError, ValueError):
                        return fail("rejected", "无法生成稳定的 Markdown 正文。")
                    if (
                        canonical_markdown is not None
                        and transaction_candidate.final_markdown != canonical_markdown
                    ):
                        return fail("conflict", "提案预览已改变，请刷新后重试。")
                    canonical_markdown = transaction_candidate.final_markdown
                    current_note = connection.execute(
                        "SELECT * FROM notes WHERE id = ? AND is_deleted = 0",
                        (note_id,),
                    ).fetchone()
                    current_receipt = connection.execute(
                        "SELECT status, operation_payload FROM ai_receipt_inbox WHERE id = ?",
                        (receipt_id,),
                    ).fetchone()
                    if (
                        current_note is None
                        or not _note_version_matches(
                            current_note,
                            expected_revision=int(note["revision"]),
                            expected_updated_at=str(note["updated_at"]),
                        )
                        or str(current_note["content_markdown"]) != str(note["content_markdown"])
                        or not self._actor_can_edit_note(
                            connection, current_note, actor_target_id
                        )
                        or current_receipt is None
                        or current_receipt["status"] != "pending"
                        or current_receipt["operation_payload"] != expected_payload
                    ):
                        return fail("conflict", "提案或笔记已被更新，请刷新后重试。")
                elif (
                    not isinstance(payload, dict)
                    or set(payload) != {"content"}
                    or not isinstance(payload["content"], str)
                    or not payload["content"]
                    or not isinstance(accepted_body, str)
                    or not accepted_body
                ):
                    return fail("rejected", "正文提案内容无效。")
                if failure_reason or not isinstance(canonical_markdown, str) or not canonical_markdown.strip():
                    return fail("rejected", failure_reason or "无法生成稳定的 Markdown 正文。")
                if expected_revision is None or expected_content_markdown is None:
                    return fail("conflict", "笔记已被更新。")

            result_title, timestamp = self._apply_modification_in_connection(
                connection, note, actor_target_id=actor_target_id,
                request_id=request_id, operation_type=operation_type,
                expected_revision=int(note["revision"]),
                canonical_markdown=canonical_markdown,
                title=desired_title, directory_id=directory_id,
                location=location, tags=tags,
                handling_mode="human_approved",
            )
            result = _result(
                "updated", request_id, note_id, result_title,
                "human_approved", True, False, "",
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
            cursor = connection.execute(
                """
                UPDATE ai_operations
                SET status = 'updated', result_json = ?, updated_at = ?
                WHERE request_id = ? AND actor_target_id = ?
                  AND operation_type = ? AND status = 'pending_confirmation'
                """,
                (json.dumps(result, ensure_ascii=False), timestamp,
                 request_id, actor_target_id, operation_type),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("提案的操作记录已改变，请刷新后重试。")
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
        handling_mode: str = "direct",
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
    def _actor_can_modify_note(connection, note, actor_target_id: str) -> bool:
        actor_id = resolve_target_id(actor_target_id)
        targets = tuple(
            str(row["target_id"])
            for row in connection.execute(
                "SELECT target_id FROM note_ai_targets WHERE note_id = ?",
                (note["id"],),
            ).fetchall()
        )
        return (
            normalize_ai_visibility(note["ai_visibility"]) != AI_VISIBILITY_HIDDEN
            and is_actor_authorized(actor_id, targets)
        )

    @classmethod
    def _actor_can_edit_note(cls, connection, note, actor_target_id: str) -> bool:
        return (
            normalize_ai_visibility(note["ai_visibility"]) == AI_VISIBILITY_FULL
            and cls._actor_can_modify_note(connection, note, actor_target_id)
        )

    @classmethod
    def _actor_can_use_directory(
        cls, connection, directory_id: int, actor_target_id: str
    ) -> bool:
        rows = connection.execute(
            "SELECT * FROM notes WHERE directory_id = ? AND is_deleted = 0",
            (directory_id,),
        ).fetchall()
        return any(
            cls._actor_can_modify_note(connection, row, actor_target_id)
            for row in rows
        )

    def _active_location_id(self, connection, location: NoteLocation) -> int | None:
        directories = SQLiteDirectoriesRepository(self.connections)
        main, sub = location.values()
        main_id = directories.find_active(connection, main, None)
        if main_id is None:
            return None
        return main_id if sub == DEFAULT_SUB_CATEGORY else directories.find_active(connection, sub, main_id)

    @staticmethod
    def _finish_pending_modification(
        connection, receipt, *, status: str, reason: str,
        title: str, note_id: int | None,
    ) -> dict:
        request_id = str(receipt["request_id"] or "")
        actor_target_id = str(receipt["actor_target_id"] or "")
        operation_type = str(receipt["operation_type"] or "")
        timestamp = timestamp_seconds()
        result = _result(
            status, request_id, note_id, title, "human_rejected", False, False, reason
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
                operation["status"] == "pending_confirmation"
                and operation["actor_target_id"] == actor_target_id
                and operation["operation_type"] == operation_type
                and isinstance(previous, dict)
                and previous.get("proposal_id") == receipt["id"]
            ):
                connection.execute(
                    """
                    UPDATE ai_operations
                    SET status = ?, result_json = ?, updated_at = ?
                    WHERE request_id = ? AND actor_target_id = ?
                      AND operation_type = ? AND status = 'pending_confirmation'
                    """,
                    (status, json.dumps(result, ensure_ascii=False), timestamp,
                     request_id, actor_target_id, operation_type),
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
        *,
        handling_mode: str = "direct",
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
