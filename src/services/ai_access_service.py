from __future__ import annotations

import html
import json
import re
import uuid
from pathlib import Path

from src.ai_targets import (
    AI_TARGET_COMMON_ID,
    AI_TARGET_DISPLAY_BY_ID,
    AI_TARGET_IDS,
    AI_VISIBILITY_FULL,
    AI_VISIBILITY_HIDDEN,
    AI_VISIBILITY_REDACTED,
    normalize_ai_visibility,
    normalize_target_ids,
)
from src.app_paths import DATABASE_PATH, REDACTION_RULES_PATH
from src.database import (
    DEFAULT_MAIN_CATEGORY,
    DEFAULT_SUB_CATEGORY,
    DEFAULT_SUB_SUB_CATEGORY,
    _connect,
    _ensure_directory_path,
    _normalize_note_tags,
    _plain_text_to_html,
    _set_note_ai_targets_with_connection,
    _set_note_tags_with_connection,
)
from src.redaction import apply_redactions, load_redaction_rules
from src.tag_manager import parse_tags
from src.time_utils import now_shanghai_iso


CREATE_CONFIRMATION_KEY = "ai_create_requires_confirmation"
MODIFY_CONFIRMATION_KEY = "ai_modify_requires_confirmation"
DEFAULT_SETTINGS = {
    CREATE_CONFIRMATION_KEY: False,
    MODIFY_CONFIRMATION_KEY: True,
}
WRITE_STATUSES = {
    "created",
    "updated",
    "pending_confirmation",
    "already_processed",
    "conflict",
    "not_found",
    "rejected",
    "failed",
}


def get_ai_settings(database_path: Path = DATABASE_PATH) -> dict[str, bool]:
    with _connect(database_path) as connection:
        rows = connection.execute(
            "SELECT setting_key, setting_value FROM app_settings WHERE setting_key IN (?, ?)",
            (CREATE_CONFIRMATION_KEY, MODIFY_CONFIRMATION_KEY),
        ).fetchall()
    values = dict(DEFAULT_SETTINGS)
    for row in rows:
        values[row["setting_key"]] = row["setting_value"] == "1"
    return values


def set_ai_setting(
    setting_key: str, enabled: bool, database_path: Path = DATABASE_PATH
) -> bool:
    if setting_key not in DEFAULT_SETTINGS:
        raise ValueError(f"未知 AI 设置：{setting_key}")
    timestamp = now_shanghai_iso()
    with _connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO app_settings(setting_key, setting_value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value=excluded.setting_value,
                updated_at=excluded.updated_at
            """,
            (setting_key, "1" if enabled else "0", timestamp),
        )
    return bool(enabled)


def safe_search_notes(
    query: str,
    actor_target_id: str,
    *,
    request_id: str | None = None,
    limit: int = 10,
    offset: int = 0,
    database_path: Path = DATABASE_PATH,
    redaction_rules_path: Path = REDACTION_RULES_PATH,
) -> dict:
    request_id = request_id or uuid.uuid4().hex
    clean_limit = max(1, min(int(limit), 20))
    clean_offset = max(0, int(offset))
    actor = _validated_actor(actor_target_id)
    if actor is None:
        _write_log(
            database_path, actor_target_id, "search", request_id,
            summary=_short_summary(query), status="failed", failure_reason="unknown_actor",
        )
        return {"items": [], "total_count": 0, "limit": clean_limit, "offset": clean_offset}

    rules = load_redaction_rules(redaction_rules_path)
    with _connect(database_path) as connection:
        rows = _authorized_rows(connection, actor)

    needle = (query or "").strip().casefold()
    results: list[dict] = []
    for row in rows:
        safe = _safe_note(row, rules.replacements)
        match = _match_note(safe, needle)
        if match is None:
            continue
        results.append({**_public_search_note(safe), **match})
    results.sort(key=lambda item: (item["updated_at"], item["note_id"]), reverse=True)
    results.sort(key=lambda item: item["relevance"]["score"], reverse=True)
    page = results[clean_offset : clean_offset + clean_limit]
    _write_log(
        database_path, actor, "search", request_id,
        summary=_short_summary(query), result_count=len(page), status="success",
    )
    return {
        "items": page,
        "total_count": len(results),
        "limit": clean_limit,
        "offset": clean_offset,
    }


def safe_get_note_by_id(
    note_id: int,
    actor_target_id: str,
    *,
    request_id: str | None = None,
    database_path: Path = DATABASE_PATH,
    redaction_rules_path: Path = REDACTION_RULES_PATH,
) -> dict | None:
    request_id = request_id or uuid.uuid4().hex
    actor = _validated_actor(actor_target_id)
    note = None
    if actor is not None:
        with _connect(database_path) as connection:
            row = _authorized_note(connection, int(note_id), actor)
        if row is not None:
            rules = load_redaction_rules(redaction_rules_path)
            note = _public_full_note(_safe_note(row, rules.replacements))
    _write_log(
        database_path, actor or str(actor_target_id), "get_note", request_id,
        note_id=int(note_id), result_count=1 if note else 0,
        status="success" if actor is not None else "failed",
        failure_reason="" if actor is not None else "unknown_actor",
    )
    return note


def safe_list_directories(
    actor_target_id: str,
    *,
    request_id: str | None = None,
    database_path: Path = DATABASE_PATH,
    redaction_rules_path: Path = REDACTION_RULES_PATH,
) -> list[str]:
    return _safe_list_facets(
        "directories", actor_target_id, request_id, database_path, redaction_rules_path
    )


def safe_list_tags(
    actor_target_id: str,
    *,
    request_id: str | None = None,
    database_path: Path = DATABASE_PATH,
    redaction_rules_path: Path = REDACTION_RULES_PATH,
) -> list[str]:
    return _safe_list_facets(
        "tags", actor_target_id, request_id, database_path, redaction_rules_path
    )


def ai_create_note(
    actor_target_id: str,
    request_id: str,
    *,
    title: str,
    body: str,
    tags: list[str] | None = None,
    main_category: str = DEFAULT_MAIN_CATEGORY,
    sub_category: str = DEFAULT_SUB_CATEGORY,
    sub_sub_category: str = DEFAULT_SUB_SUB_CATEGORY,
    database_path: Path = DATABASE_PATH,
) -> dict:
    actor = _validated_actor(actor_target_id)
    if actor is None:
        return _failed_public_request(
            database_path, str(actor_target_id), request_id, "create",
            "未知或不可用的 AI 身份。",
        )
    duplicate = _duplicate_result(database_path, request_id)
    if duplicate:
        _log_duplicate(database_path, actor, "create", request_id, duplicate.get("note_id"))
        return duplicate
    payload = {
        "title": str(title or "").strip() or "未命名 AI 笔记",
        "body": str(body or ""),
        "tags": _normalize_note_tags(tags or []),
        "main_category": str(main_category or DEFAULT_MAIN_CATEGORY),
        "sub_category": str(sub_category or DEFAULT_SUB_CATEGORY),
        "sub_sub_category": str(sub_sub_category or DEFAULT_SUB_SUB_CATEGORY),
    }
    if get_ai_settings(database_path)[CREATE_CONFIRMATION_KEY]:
        return _create_pending_proposal(
            database_path, actor, request_id, "create", payload, title=payload["title"]
        )
    return _execute_create(database_path, actor, request_id, payload, "direct")


def ai_prepend_note(
    actor_target_id: str,
    request_id: str,
    note_id: int,
    content: str,
    *,
    expected_updated_at: str,
    database_path: Path = DATABASE_PATH,
) -> dict:
    return _request_modify(
        database_path, actor_target_id, request_id, int(note_id), "prepend",
        {"content": str(content or "")}, expected_updated_at,
    )


def ai_append_note(
    actor_target_id: str,
    request_id: str,
    note_id: int,
    content: str,
    *,
    expected_updated_at: str,
    database_path: Path = DATABASE_PATH,
) -> dict:
    return _request_modify(
        database_path, actor_target_id, request_id, int(note_id), "append",
        {"content": str(content or "")}, expected_updated_at,
    )


def ai_update_note_metadata(
    actor_target_id: str,
    request_id: str,
    note_id: int,
    *,
    expected_updated_at: str,
    title: str | None = None,
    tags: list[str] | None = None,
    directory: tuple[str, str, str] | None = None,
    database_path: Path = DATABASE_PATH,
) -> dict:
    if title is None and tags is None and directory is None:
        raise ValueError("至少需要一项元数据修改。")
    payload: dict = {}
    if title is not None:
        payload["title"] = str(title).strip() or "未命名笔记"
    if tags is not None:
        payload["tags"] = _normalize_note_tags(tags)
    if directory is not None:
        if len(directory) != 3:
            raise ValueError("目录必须包含主分类、子分类、子子分类。")
        payload["directory"] = [str(value) for value in directory]
    return _request_modify(
        database_path, actor_target_id, request_id, int(note_id), "metadata",
        payload, expected_updated_at,
    )


def get_ai_operation_status(
    request_id: str, database_path: Path = DATABASE_PATH
) -> dict | None:
    with _connect(database_path) as connection:
        row = connection.execute(
            "SELECT result_json FROM ai_operations WHERE request_id=?", (request_id,)
        ).fetchone()
    return json.loads(row["result_json"]) if row else None


def accept_ai_proposal(
    inbox_id: int,
    fields: dict | None = None,
    database_path: Path = DATABASE_PATH,
) -> dict:
    with _connect(database_path) as connection:
        proposal = connection.execute(
            "SELECT * FROM ai_receipt_inbox WHERE id=?", (int(inbox_id),)
        ).fetchone()
    if proposal is None or not proposal["request_id"]:
        raise ValueError("这不是 AI 业务层 proposal。")
    if proposal["status"] != "pending":
        return _already_processed(json.loads(_operation_row(database_path, proposal["request_id"])["result_json"]))
    actor = _require_actor(proposal["actor_target_id"])
    payload = json.loads(proposal["operation_payload"] or "{}")
    if fields:
        if proposal["operation_type"] == "create":
            payload.update(
                title=str(fields.get("title") or payload.get("title") or "未命名 AI 笔记"),
                body=str(fields.get("body") or ""),
                tags=_normalize_note_tags(parse_tags(fields.get("tags", ""))),
                main_category=str(fields.get("main_category") or DEFAULT_MAIN_CATEGORY),
                sub_category=str(fields.get("sub_category") or DEFAULT_SUB_CATEGORY),
                sub_sub_category=str(fields.get("sub_sub_category") or DEFAULT_SUB_SUB_CATEGORY),
            )
        elif proposal["operation_type"] in {"prepend", "append"}:
            payload["content"] = str(fields.get("body") or "")
        elif proposal["operation_type"] == "metadata":
            if "title" in payload:
                payload["title"] = str(fields.get("title") or "未命名笔记")
            if "tags" in payload:
                payload["tags"] = _normalize_note_tags(parse_tags(fields.get("tags", "")))
            if "directory" in payload:
                payload["directory"] = [
                    str(fields.get("main_category") or DEFAULT_MAIN_CATEGORY),
                    str(fields.get("sub_category") or DEFAULT_SUB_CATEGORY),
                    str(fields.get("sub_sub_category") or DEFAULT_SUB_SUB_CATEGORY),
                ]
    if proposal["operation_type"] == "create":
        result = _execute_create(
            database_path, actor, proposal["request_id"], payload,
            "human_approved", replace_pending=True,
        )
    else:
        result = _execute_modify(
            database_path, actor, proposal["request_id"],
            int(proposal["target_note_id"]), proposal["operation_type"], payload,
            str(proposal["expected_updated_at"] or ""), "human_approved",
            replace_pending=True,
        )
    timestamp = now_shanghai_iso()
    receipt_status = "accepted" if result["status"] in {"created", "updated"} else result["status"]
    with _connect(database_path) as connection:
        connection.execute(
            """
            UPDATE ai_receipt_inbox
            SET status=?, note_id=?, result_status=?, updated_at=?, decided_at=?
            WHERE id=? AND status='pending'
            """,
            (receipt_status, result.get("note_id"), result["status"], timestamp, timestamp, int(inbox_id)),
        )
    return result


def reject_ai_proposal(inbox_id: int, database_path: Path = DATABASE_PATH) -> dict:
    timestamp = now_shanghai_iso()
    with _connect(database_path) as connection:
        proposal = connection.execute(
            "SELECT * FROM ai_receipt_inbox WHERE id=?", (int(inbox_id),)
        ).fetchone()
        if proposal is None or not proposal["request_id"]:
            raise ValueError("这不是 AI 业务层 proposal。")
        if proposal["status"] != "pending":
            existing = json.loads(_operation_row(database_path, proposal["request_id"])["result_json"])
            return _already_processed(existing)
        result = _result(
            "rejected", proposal["request_id"], proposal["target_note_id"],
            proposal["title"], "human_rejected", False, False, "用户已拒绝。",
        )
        connection.execute(
            """
            UPDATE ai_receipt_inbox
            SET status='rejected', result_status='rejected', updated_at=?, decided_at=?
            WHERE id=? AND status='pending'
            """,
            (timestamp, timestamp, int(inbox_id)),
        )
        _store_operation(connection, proposal["request_id"], proposal["actor_target_id"], proposal["operation_type"], result)
        _insert_log(connection, proposal["actor_target_id"], proposal["operation_type"], proposal["request_id"], proposal["target_note_id"], "", 0, "human_rejected", "rejected", "用户已拒绝。", False, True)
    return result


def list_ai_note_history(database_path: Path = DATABASE_PATH) -> list[dict]:
    with _connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, note_id, note_title, modification_type, actor_target_id,
                   request_id, modified_at, before_snapshot, after_updated_at,
                   handling_mode
            FROM ai_note_history ORDER BY modified_at DESC, id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def list_ai_call_logs(
    *, operation_type: str | None = None, database_path: Path = DATABASE_PATH
) -> list[dict]:
    clause = "" if not operation_type else "WHERE operation_type=?"
    params = () if not operation_type else (operation_type,)
    with _connect(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT id, called_at, actor_target_id, operation_type, request_id,
                   note_id, summary, result_count, handling_mode, status,
                   failure_reason, is_duplicate, entered_confirmation
            FROM ai_call_logs {clause}
            ORDER BY called_at DESC, id DESC
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def restore_ai_note_history(
    history_id: int,
    *,
    request_id: str | None = None,
    database_path: Path = DATABASE_PATH,
) -> dict:
    request_id = request_id or f"restore-{uuid.uuid4().hex}"
    timestamp = now_shanghai_iso()
    with _connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        history = connection.execute(
            "SELECT * FROM ai_note_history WHERE id=?", (int(history_id),)
        ).fetchone()
        if history is None:
            raise ValueError("历史记录不存在。")
        current = _note_snapshot_row(connection, int(history["note_id"]))
        if current is None:
            raise ValueError("笔记不存在或已删除。")
        current_snapshot = _snapshot(connection, current)
        target_snapshot = json.loads(history["before_snapshot"])
        _insert_history(
            connection, current, "restore", "local-user", request_id,
            current_snapshot, timestamp, "restore",
        )
        _restore_snapshot(connection, int(history["note_id"]), target_snapshot, timestamp)
        connection.execute(
            "UPDATE ai_note_history SET after_updated_at=? WHERE request_id=?",
            (timestamp, request_id),
        )
        _insert_log(connection, "local-user", "restore", request_id, int(history["note_id"]), f"history:{history_id}", 1, "restore", "updated", "", False, False)
    return _result(
        "updated", request_id, int(history["note_id"]), target_snapshot.get("title", ""),
        "restore", True, False, "",
    )


def _safe_list_facets(
    facet: str,
    actor_target_id: str,
    request_id: str | None,
    database_path: Path,
    redaction_rules_path: Path,
) -> list[str]:
    request_id = request_id or uuid.uuid4().hex
    actor = _validated_actor(actor_target_id)
    values: set[str] = set()
    if actor is not None:
        rules = load_redaction_rules(redaction_rules_path)
        with _connect(database_path) as connection:
            rows = _authorized_rows(connection, actor)
        for row in rows:
            note = _safe_note(row, rules.replacements)
            if facet == "tags":
                values.update(note["tags"])
            else:
                values.add(note["directory"])
    result = sorted(value for value in values if value)
    _write_log(
        database_path, actor or str(actor_target_id), f"list_{facet}", request_id,
        result_count=len(result), status="success" if actor else "failed",
        failure_reason="" if actor else "unknown_actor",
    )
    return result


def _request_modify(
    database_path: Path,
    actor_target_id: str,
    request_id: str,
    note_id: int,
    operation_type: str,
    payload: dict,
    expected_updated_at: str,
) -> dict:
    actor = _validated_actor(actor_target_id)
    if actor is None:
        return _failed_public_request(
            database_path, str(actor_target_id), request_id, operation_type,
            "未知或不可用的 AI 身份。", note_id,
        )
    duplicate = _duplicate_result(database_path, request_id)
    if duplicate:
        _log_duplicate(database_path, actor, operation_type, request_id, note_id)
        return duplicate
    with _connect(database_path) as connection:
        note = _authorized_note(connection, note_id, actor)
    if note is None:
        result = _result("not_found", request_id, note_id, "", "none", False, False, "未找到笔记。")
        _persist_terminal(database_path, actor, operation_type, result, "not_found")
        return result
    if note["updated_at"] != expected_updated_at:
        result = _result("conflict", request_id, note_id, note["title"], "none", False, False, "笔记已被更新。")
        _persist_terminal(database_path, actor, operation_type, result, "conflict")
        return result
    if operation_type == "metadata" and "directory" in payload:
        with _connect(database_path) as connection:
            directory_id = _existing_directory_path_id(connection, *payload["directory"])
        if directory_id is None:
            result = _result(
                "failed", request_id, note_id, note["title"], "none", False, False,
                "目标目录不存在，AI 不能创建目录。",
            )
            _persist_terminal(database_path, actor, operation_type, result, "failed")
            return result
    if operation_type in {"prepend", "append"} and not payload.get("content"):
        result = _result("failed", request_id, note_id, note["title"], "none", False, False, "追加内容不能为空。")
        _persist_terminal(database_path, actor, operation_type, result, "failed")
        return result
    if get_ai_settings(database_path)[MODIFY_CONFIRMATION_KEY]:
        return _create_pending_proposal(
            database_path, actor, request_id, operation_type, payload,
            note_id=note_id, title=note["title"], expected_updated_at=expected_updated_at,
        )
    return _execute_modify(
        database_path, actor, request_id, note_id, operation_type, payload,
        expected_updated_at, "direct",
    )


def _execute_create(
    database_path: Path,
    actor: str,
    request_id: str,
    payload: dict,
    handling_mode: str,
    *,
    replace_pending: bool = False,
) -> dict:
    timestamp = now_shanghai_iso()
    with _connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if not replace_pending and connection.execute(
            "SELECT 1 FROM ai_operations WHERE request_id=?", (request_id,)
        ).fetchone():
            existing = json.loads(_operation_row_from_connection(connection, request_id)["result_json"])
            return _already_processed(existing)
        directory_id = _ensure_directory_path(
            connection, payload["main_category"], payload["sub_category"], payload["sub_sub_category"]
        )
        content_html = _plain_text_to_html(payload["body"])
        cursor = connection.execute(
            """
            INSERT INTO notes (
                directory_id, title, body_rich_text, content_html,
                main_category, sub_category, sub_sub_category, tags,
                ai_visibility, is_pinned, is_deleted, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', 'full', 0, 0, ?, ?)
            """,
            (
                directory_id, payload["title"], content_html, content_html,
                payload["main_category"], payload["sub_category"], payload["sub_sub_category"],
                timestamp, timestamp,
            ),
        )
        note_id = int(cursor.lastrowid)
        _set_note_tags_with_connection(connection, note_id, payload.get("tags", []), timestamp)
        _set_note_ai_targets_with_connection(connection, note_id, [actor], timestamp)
        result = _result("created", request_id, note_id, payload["title"], handling_mode, True, False, "")
        _store_operation(connection, request_id, actor, "create", result)
        _insert_log(connection, actor, "create", request_id, note_id, payload["title"], 1, handling_mode, "created", "", False, handling_mode == "human_approved")
    return result


def _execute_modify(
    database_path: Path,
    actor: str,
    request_id: str,
    note_id: int,
    operation_type: str,
    payload: dict,
    expected_updated_at: str,
    handling_mode: str,
    *,
    replace_pending: bool = False,
) -> dict:
    timestamp = now_shanghai_iso()
    with _connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if not replace_pending and connection.execute(
            "SELECT 1 FROM ai_operations WHERE request_id=?", (request_id,)
        ).fetchone():
            existing = json.loads(_operation_row_from_connection(connection, request_id)["result_json"])
            return _already_processed(existing)
        note = _authorized_note(connection, note_id, actor)
        if note is None:
            result = _result("not_found", request_id, note_id, "", handling_mode, False, False, "未找到笔记。")
            _store_operation(connection, request_id, actor, operation_type, result)
            _insert_log(connection, actor, operation_type, request_id, note_id, "", 0, handling_mode, "not_found", result["reason"], False, handling_mode == "human_approved")
            return result
        if note["updated_at"] != expected_updated_at:
            result = _result("conflict", request_id, note_id, note["title"], handling_mode, False, False, "笔记已被更新。")
            _store_operation(connection, request_id, actor, operation_type, result)
            _insert_log(connection, actor, operation_type, request_id, note_id, note["title"], 0, handling_mode, "conflict", result["reason"], False, handling_mode == "human_approved")
            return result
        snapshot = _snapshot(connection, note)
        _insert_history(connection, note, operation_type, actor, request_id, snapshot, timestamp, handling_mode)
        if operation_type in {"prepend", "append"}:
            addition = _plain_text_to_html(payload["content"])
            content_html = (
                addition + note["content_html"]
                if operation_type == "prepend"
                else note["content_html"] + addition
            )
            connection.execute(
                "UPDATE notes SET content_html=?, body_rich_text=?, updated_at=? WHERE id=?",
                (content_html, content_html, timestamp, note_id),
            )
        elif operation_type == "metadata":
            title = payload.get("title", note["title"])
            main, sub, sub_sub = note["main_category"], note["sub_category"], note["sub_sub_category"]
            directory_id = note["directory_id"]
            if "directory" in payload:
                main, sub, sub_sub = payload["directory"]
                directory_id = _existing_directory_path_id(connection, main, sub, sub_sub)
                if directory_id is None:
                    raise ValueError("目标目录不存在，AI 不能创建目录。")
            connection.execute(
                """
                UPDATE notes SET title=?, directory_id=?, main_category=?,
                    sub_category=?, sub_sub_category=?, updated_at=? WHERE id=?
                """,
                (title, directory_id, main, sub, sub_sub, timestamp, note_id),
            )
            if "tags" in payload:
                _set_note_tags_with_connection(connection, note_id, payload["tags"], timestamp)
        else:
            raise ValueError(f"不支持的 AI 修改类型：{operation_type}")
        connection.execute(
            "UPDATE ai_note_history SET after_updated_at=? WHERE request_id=?",
            (timestamp, request_id),
        )
        result = _result("updated", request_id, note_id, payload.get("title", note["title"]), handling_mode, True, False, "")
        _store_operation(connection, request_id, actor, operation_type, result)
        _insert_log(connection, actor, operation_type, request_id, note_id, _operation_summary(operation_type, payload), 1, handling_mode, "updated", "", False, handling_mode == "human_approved")
    return result


def _create_pending_proposal(
    database_path: Path,
    actor: str,
    request_id: str,
    operation_type: str,
    payload: dict,
    *,
    note_id: int | None = None,
    title: str = "",
    expected_updated_at: str | None = None,
) -> dict:
    timestamp = now_shanghai_iso()
    result = _result("pending_confirmation", request_id, note_id, title, "pending", False, True, "")
    action_labels = {"create": "新建", "prepend": "开头追加", "append": "末尾追加", "metadata": "修改元数据"}
    with _connect(database_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("SELECT 1 FROM ai_operations WHERE request_id=?", (request_id,)).fetchone():
            existing = json.loads(_operation_row_from_connection(connection, request_id)["result_json"])
            return _already_processed(existing)
        connection.execute(
            """
            INSERT INTO ai_receipt_inbox (
                raw_text, parse_status, status, source, action, title,
                main_category, sub_category, sub_sub_category, tags,
                ai_visibility, ai_targets, body, note_id, created_at, updated_at,
                parse_error, request_id, actor_target_id, operation_type,
                target_note_id, expected_updated_at, operation_payload, result_status
            ) VALUES ('', 'parsed', 'pending', ?, ?, ?, ?, ?, ?, ?, 'full', ?, ?,
                      NULL, ?, ?, '', ?, ?, ?, ?, ?, ?, 'pending_confirmation')
            """,
            (
                AI_TARGET_DISPLAY_BY_ID[actor], action_labels[operation_type], title,
                payload.get("main_category", DEFAULT_MAIN_CATEGORY),
                payload.get("sub_category", DEFAULT_SUB_CATEGORY),
                payload.get("sub_sub_category", DEFAULT_SUB_SUB_CATEGORY),
                ", ".join(payload.get("tags", [])), AI_TARGET_DISPLAY_BY_ID[actor],
                payload.get("body", payload.get("content", "")), timestamp, timestamp,
                request_id, actor, operation_type, note_id, expected_updated_at,
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        _store_operation(connection, request_id, actor, operation_type, result)
        _insert_log(connection, actor, operation_type, request_id, note_id, _operation_summary(operation_type, payload), 0, "pending", "pending_confirmation", "", False, True)
    return result


def _authorized_rows(connection, actor: str) -> list:
    return connection.execute(
        """
        SELECT DISTINCT notes.*
        FROM notes JOIN note_ai_targets ON note_ai_targets.note_id=notes.id
        WHERE notes.is_deleted=0
          AND notes.ai_visibility IN ('full', 'redacted', '可直接给 AI', '脱敏后给 AI', 'AI完整可见', 'AI脱敏后可见')
          AND note_ai_targets.target_id IN (?, ?)
        ORDER BY notes.id
        """,
        (AI_TARGET_COMMON_ID, actor),
    ).fetchall()


def _authorized_note(connection, note_id: int, actor: str):
    return connection.execute(
        """
        SELECT DISTINCT notes.*
        FROM notes JOIN note_ai_targets ON note_ai_targets.note_id=notes.id
        WHERE notes.id=? AND notes.is_deleted=0
          AND notes.ai_visibility IN ('full', 'redacted', '可直接给 AI', '脱敏后给 AI', 'AI完整可见', 'AI脱敏后可见')
          AND note_ai_targets.target_id IN (?, ?)
        """,
        (note_id, AI_TARGET_COMMON_ID, actor),
    ).fetchone()


def _safe_note(row, replacements: list[tuple[str, str]]) -> dict:
    visibility = normalize_ai_visibility(row["ai_visibility"])
    values = {
        "note_id": row["id"],
        "title": str(row["title"] or ""),
        "directory": " / ".join(
            str(row[key] or "") for key in ("main_category", "sub_category", "sub_sub_category")
        ),
        "tags": parse_tags(row["tags"] or ""),
        "body": _html_to_text(row["content_html"] or ""),
        "updated_at": row["updated_at"],
        "visibility": visibility,
    }
    if visibility == AI_VISIBILITY_REDACTED:
        for key in ("title", "directory", "body"):
            values[key] = _apply_safe_redactions(values[key], replacements)
        values["tags"] = [_apply_safe_redactions(tag, replacements) for tag in values["tags"]]
    return values


def _match_note(note: dict, needle: str) -> dict | None:
    if not needle:
        return {
            "best_safe_snippet": _snippet(note["body"], ""),
            "matched_in": "all",
            "relevance": {"score": 1, "reason": "recent_safe_note"},
        }
    title = note["title"].casefold()
    tags = [tag.casefold() for tag in note["tags"]]
    directory = note["directory"].casefold()
    body = note["body"].casefold()
    if title == needle:
        return {"best_safe_snippet": note["title"], "matched_in": "title", "relevance": {"score": 400, "reason": "exact_title"}}
    if needle in title:
        return {"best_safe_snippet": _snippet(note["title"], needle), "matched_in": "title", "relevance": {"score": 350, "reason": "title"}}
    if any(needle in tag for tag in tags):
        return {"best_safe_snippet": ", ".join(note["tags"]), "matched_in": "tags", "relevance": {"score": 300, "reason": "tag"}}
    if needle in directory:
        return {"best_safe_snippet": note["directory"], "matched_in": "directory", "relevance": {"score": 200, "reason": "directory"}}
    if needle in body:
        return {"best_safe_snippet": _snippet(note["body"], needle), "matched_in": "body", "relevance": {"score": 100, "reason": "body"}}
    return None


def _public_search_note(note: dict) -> dict:
    return {key: note[key] for key in ("note_id", "title", "directory", "tags", "updated_at")}


def _public_full_note(note: dict) -> dict:
    return {key: note[key] for key in ("note_id", "title", "directory", "tags", "body", "updated_at")}


def _snapshot(connection, note) -> dict:
    tags = [row["name"] for row in connection.execute(
        "SELECT tags.name FROM tags JOIN note_tags ON note_tags.tag_id=tags.id WHERE note_tags.note_id=? ORDER BY tags.name",
        (note["id"],),
    ).fetchall()]
    targets = [row["target_id"] for row in connection.execute(
        "SELECT target_id FROM note_ai_targets WHERE note_id=? ORDER BY target_id", (note["id"],)
    ).fetchall()]
    return {
        "title": note["title"], "content_html": note["content_html"],
        "body_rich_text": note["body_rich_text"], "main_category": note["main_category"],
        "sub_category": note["sub_category"], "sub_sub_category": note["sub_sub_category"],
        "tags": tags, "ai_visibility": normalize_ai_visibility(note["ai_visibility"]),
        "target_ids": targets,
    }


def _insert_history(connection, note, operation_type: str, actor: str, request_id: str, snapshot: dict, timestamp: str, handling_mode: str) -> None:
    connection.execute(
        """
        INSERT INTO ai_note_history (
            note_id, note_title, modification_type, actor_target_id,
            request_id, modified_at, before_snapshot, after_updated_at, handling_mode
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (note["id"], note["title"], operation_type, actor, request_id, timestamp, json.dumps(snapshot, ensure_ascii=False), timestamp, handling_mode),
    )


def _restore_snapshot(connection, note_id: int, snapshot: dict, timestamp: str) -> None:
    directory_id = _ensure_directory_path(
        connection, snapshot["main_category"], snapshot["sub_category"], snapshot["sub_sub_category"]
    )
    connection.execute(
        """
        UPDATE notes SET directory_id=?, title=?, content_html=?, body_rich_text=?,
            main_category=?, sub_category=?, sub_sub_category=?, ai_visibility=?,
            updated_at=? WHERE id=? AND is_deleted=0
        """,
        (directory_id, snapshot["title"], snapshot["content_html"], snapshot.get("body_rich_text", snapshot["content_html"]), snapshot["main_category"], snapshot["sub_category"], snapshot["sub_sub_category"], normalize_ai_visibility(snapshot["ai_visibility"]), timestamp, note_id),
    )
    _set_note_tags_with_connection(connection, note_id, snapshot.get("tags", []), timestamp)
    _set_note_ai_targets_with_connection(connection, note_id, normalize_target_ids(snapshot.get("target_ids", [])), timestamp)


def _note_snapshot_row(connection, note_id: int):
    return connection.execute("SELECT * FROM notes WHERE id=? AND is_deleted=0", (note_id,)).fetchone()


def _existing_directory_path_id(connection, main: str, sub: str, sub_sub: str) -> int | None:
    rows = connection.execute("SELECT id, name, parent_id FROM directories WHERE is_deleted=0").fetchall()
    by_parent: dict[int | None, dict[str, int]] = {}
    for row in rows:
        by_parent.setdefault(row["parent_id"], {})[row["name"]] = row["id"]
    main_id = by_parent.get(None, {}).get(main)
    if main_id is None:
        return None
    if sub == DEFAULT_SUB_CATEGORY and sub_sub == DEFAULT_SUB_SUB_CATEGORY:
        return main_id
    sub_id = by_parent.get(main_id, {}).get(sub)
    if sub_id is None:
        return None
    if sub_sub == DEFAULT_SUB_SUB_CATEGORY:
        return sub_id
    return by_parent.get(sub_id, {}).get(sub_sub)


def _validated_actor(value: object) -> str | None:
    try:
        actor = normalize_target_ids([str(value)])[0]
    except ValueError:
        return None
    return actor if actor in AI_TARGET_IDS and actor != AI_TARGET_COMMON_ID else None


def _require_actor(value: object) -> str:
    actor = _validated_actor(value)
    if actor is None:
        raise ValueError("未知或不可用的 AI 身份。")
    return actor


def _result(status: str, request_id: str, note_id: int | None, title: str, handling_mode: str, modified: bool, pending: bool, reason: str) -> dict:
    if status not in WRITE_STATUSES:
        raise ValueError(f"未知写入状态：{status}")
    return {
        "status": status, "request_id": request_id, "note_id": note_id,
        "title": title, "handling_mode": handling_mode,
        "modified_formal_note": modified, "entered_confirmation": pending,
        "reason": reason,
    }


def _already_processed(existing: dict) -> dict:
    return {**existing, "status": "already_processed", "previous_status": existing.get("status")}


def _duplicate_result(database_path: Path, request_id: str) -> dict | None:
    row = _operation_row(database_path, request_id)
    return _already_processed(json.loads(row["result_json"])) if row else None


def _operation_row(database_path: Path, request_id: str):
    with _connect(database_path) as connection:
        return _operation_row_from_connection(connection, request_id)


def _operation_row_from_connection(connection, request_id: str):
    return connection.execute("SELECT * FROM ai_operations WHERE request_id=?", (request_id,)).fetchone()


def _store_operation(connection, request_id: str, actor: str, operation_type: str, result: dict) -> None:
    timestamp = now_shanghai_iso()
    connection.execute(
        """
        INSERT INTO ai_operations(request_id, actor_target_id, operation_type, status, result_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(request_id) DO UPDATE SET status=excluded.status,
            result_json=excluded.result_json, updated_at=excluded.updated_at
        """,
        (request_id, actor, operation_type, result["status"], json.dumps(result, ensure_ascii=False), timestamp, timestamp),
    )


def _persist_terminal(database_path: Path, actor: str, operation_type: str, result: dict, status: str) -> None:
    with _connect(database_path) as connection:
        _store_operation(connection, result["request_id"], actor, operation_type, result)
        _insert_log(connection, actor, operation_type, result["request_id"], result.get("note_id"), "", 0, "none", status, result.get("reason", ""), False, False)


def _failed_public_request(
    database_path: Path,
    actor: str,
    request_id: str,
    operation_type: str,
    reason: str,
    note_id: int | None = None,
) -> dict:
    duplicate = _duplicate_result(database_path, request_id)
    if duplicate:
        _log_duplicate(database_path, actor, operation_type, request_id, note_id)
        return duplicate
    result = _result("failed", request_id, note_id, "", "none", False, False, reason)
    _persist_terminal(database_path, actor, operation_type, result, "failed")
    return result


def _write_log(database_path: Path, actor: str, operation_type: str, request_id: str, *, note_id: int | None = None, summary: str = "", result_count: int = 0, handling_mode: str = "read", status: str, failure_reason: str = "") -> None:
    with _connect(database_path) as connection:
        _insert_log(connection, actor, operation_type, request_id, note_id, summary, result_count, handling_mode, status, failure_reason, False, False)


def _log_duplicate(database_path: Path, actor: str, operation_type: str, request_id: str, note_id: int | None) -> None:
    with _connect(database_path) as connection:
        _insert_log(connection, actor, operation_type, request_id, note_id, "", 0, "duplicate", "already_processed", "", True, False)


def _insert_log(connection, actor: str, operation_type: str, request_id: str, note_id: int | None, summary: str, result_count: int, handling_mode: str, status: str, failure_reason: str, duplicate: bool, entered_confirmation: bool) -> None:
    connection.execute(
        """
        INSERT INTO ai_call_logs (
            called_at, actor_target_id, operation_type, request_id, note_id,
            summary, result_count, handling_mode, status, failure_reason,
            is_duplicate, entered_confirmation
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (now_shanghai_iso(), actor, operation_type, request_id, note_id, _short_summary(summary), int(result_count), handling_mode, status, _short_summary(failure_reason), 1 if duplicate else 0, 1 if entered_confirmation else 0),
    )


def _operation_summary(operation_type: str, payload: dict) -> str:
    if operation_type in {"prepend", "append"}:
        return f"{operation_type}:{len(payload.get('content', ''))} chars"
    if operation_type == "metadata":
        return "metadata:" + ",".join(sorted(payload))
    return str(payload.get("title", ""))


def _short_summary(value: object, max_length: int = 180) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:max_length]


def _apply_safe_redactions(text: str, replacements: list[tuple[str, str]]) -> str:
    redacted = text
    for source, target in replacements:
        if source:
            redacted = re.sub(re.escape(source), lambda _match: target, redacted, flags=re.IGNORECASE)
    return redacted


def _html_to_text(value: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>", "\n", value or "")
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def _snippet(text: str, needle: str, radius: int = 80) -> str:
    if not text:
        return ""
    index = text.casefold().find(needle) if needle else 0
    index = max(index, 0)
    start = max(0, index - radius)
    end = min(len(text), index + max(len(needle), 1) + radius)
    prefix = "…" if start else ""
    suffix = "…" if end < len(text) else ""
    return prefix + text[start:end] + suffix
