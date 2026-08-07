from __future__ import annotations

import hashlib
import html
import hmac
import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta
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
    AI_SCHEMA_VERSION,
    _connect,
    _ensure_directory_path,
    _normalize_note_tags,
    _plain_text_to_html,
    _set_note_ai_targets_with_connection,
    _set_note_tags_with_connection,
)
from src.redaction import (
    RedactionUnavailableError,
    apply_redactions,
    load_redaction_rules,
)
from src.search_query import SearchQuery, match_search_fields, parse_search_query
from src.services.ai_safe_materialization import materialize_ai_safe_note
from src.tag_manager import parse_tags
from src.time_utils import now_shanghai, now_shanghai_iso


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
    "no_change",
    "failed",
}

REQUIRED_AI_TABLES = {
    "notes",
    "directories",
    "tags",
    "note_tags",
    "ai_targets",
    "note_ai_targets",
    "ai_receipt_inbox",
    "app_settings",
    "ai_note_history",
    "ai_call_logs",
    "ai_operations",
    "schema_meta",
}


def inspect_ai_runtime(
    actor_target_id: str,
    *,
    database_path: Path = DATABASE_PATH,
) -> dict:
    """Perform the MCP startup health checks through a read-only connection."""
    database_path = Path(database_path)
    if not database_path.is_file():
        return {
            "status": "failed",
            "database_exists": False,
            "quick_check": "not_run",
            "schema_version": None,
            "schema_ok": False,
            "actor_ok": False,
            "foreign_keys": False,
            "reason": "database_missing",
        }

    actor = _validated_actor(actor_target_id)
    try:
        connection = sqlite3.connect(
            f"{database_path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=5.0,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        quick_check_row = connection.execute("PRAGMA quick_check").fetchone()
        quick_check = str(quick_check_row[0]) if quick_check_row else "failed"
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        schema_row = None
        if "schema_meta" in tables:
            schema_row = connection.execute(
                "SELECT meta_value FROM schema_meta WHERE meta_key='ai_foundation_version'"
            ).fetchone()
        try:
            schema_version = int(schema_row["meta_value"]) if schema_row else None
        except (TypeError, ValueError):
            schema_version = None
        actor_row = None
        if actor is not None and "ai_targets" in tables:
            actor_row = connection.execute(
                "SELECT is_enabled FROM ai_targets WHERE target_id=?", (actor,)
            ).fetchone()
        foreign_keys = bool(connection.execute("PRAGMA foreign_keys").fetchone()[0])
    except sqlite3.Error:
        return {
            "status": "failed",
            "database_exists": True,
            "quick_check": "failed",
            "schema_version": None,
            "schema_ok": False,
            "actor_ok": False,
            "foreign_keys": False,
            "reason": "database_unavailable",
        }
    finally:
        if "connection" in locals():
            connection.close()

    schema_ok = schema_version == AI_SCHEMA_VERSION and REQUIRED_AI_TABLES.issubset(tables)
    actor_ok = actor_row is not None and int(actor_row["is_enabled"]) == 1
    healthy = quick_check == "ok" and schema_ok and actor_ok and foreign_keys
    if quick_check != "ok":
        reason = "quick_check_failed"
    elif not schema_ok:
        reason = "schema_mismatch"
    elif not actor_ok:
        reason = "actor_unavailable"
    elif not foreign_keys:
        reason = "foreign_keys_disabled"
    else:
        reason = ""
    return {
        "status": "ok" if healthy else "failed",
        "database_exists": True,
        "quick_check": quick_check,
        "schema_version": schema_version,
        "schema_ok": schema_ok,
        "actor_ok": actor_ok,
        "foreign_keys": foreign_keys,
        "reason": reason,
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
    directory_id: int | None = None,
    tag_id: int | None = None,
    tags: list[str] | None = None,
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
            summary=_query_summary(query), status="failed", failure_reason="unknown_actor",
        )
        return {"items": [], "total_count": 0, "limit": clean_limit, "offset": clean_offset}

    with _connect(database_path) as connection:
        rows = _authorized_rows(connection, actor)
        rows, replacements, redaction_warning = _available_rows_and_replacements(
            rows, redaction_rules_path
        )
        safe_notes = [_safe_note(row, replacements, connection) for row in rows]

    parsed_query = parse_search_query(query)
    requested_tags = {
        str(value or "").strip().casefold() for value in (tags or []) if str(value or "").strip()
    }
    results: list[dict] = []
    for safe in safe_notes:
        if directory_id is not None and safe["directory_id"] != int(directory_id):
            continue
        if tag_id is not None and int(tag_id) not in safe["tag_ids"]:
            continue
        if requested_tags and not requested_tags.issubset(
            {tag.casefold() for tag in safe["tags"]}
        ):
            continue
        match = _match_note(safe, parsed_query)
        if match is None:
            continue
        results.append({**_public_search_note(safe), **match})
    results.sort(key=lambda item: (item["updated_at"], item["note_id"]), reverse=True)
    results.sort(key=lambda item: item["relevance"]["score"], reverse=True)
    page = results[clean_offset : clean_offset + clean_limit]
    _write_log(
        database_path, actor, "search", request_id,
        summary=_query_summary(query), result_count=len(page), status="success",
    )
    return {
        "items": page,
        "total_count": len(results),
        "limit": clean_limit,
        "offset": clean_offset,
        "reason": redaction_warning,
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
                replacements = _redaction_replacements_for_rows(
                    [row], redaction_rules_path
                )
                note = _public_full_note(_safe_note(row, replacements, connection))
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


def safe_list_directory_records(
    actor_target_id: str,
    *,
    request_id: str | None = None,
    database_path: Path = DATABASE_PATH,
    redaction_rules_path: Path = REDACTION_RULES_PATH,
) -> list[dict]:
    request_id = request_id or uuid.uuid4().hex
    actor = _validated_actor(actor_target_id)
    records: dict[tuple[int, str], dict] = {}
    if actor is not None:
        with _connect(database_path) as connection:
            rows = _authorized_rows(connection, actor)
            rows, replacements, _warning = _available_rows_and_replacements(
                rows, redaction_rules_path
            )
            for row in rows:
                note = _safe_note(row, replacements, connection)
                key = (int(note["directory_id"]), note["directory"])
                records[key] = {
                    "directory_id": key[0],
                    "directory": key[1],
                    "writable": True,
                }
    result = sorted(records.values(), key=lambda item: (item["directory"], item["directory_id"]))
    _write_log(
        database_path, actor or str(actor_target_id), "list_directories", request_id,
        result_count=len(result), status="success" if actor else "failed",
        failure_reason="" if actor else "unknown_actor",
    )
    return result


def safe_list_tag_records(
    actor_target_id: str,
    *,
    request_id: str | None = None,
    database_path: Path = DATABASE_PATH,
    redaction_rules_path: Path = REDACTION_RULES_PATH,
) -> list[dict]:
    request_id = request_id or uuid.uuid4().hex
    actor = _validated_actor(actor_target_id)
    records: dict[tuple[int, str], dict] = {}
    if actor is not None:
        with _connect(database_path) as connection:
            rows = _authorized_rows(connection, actor)
            rows, replacements, _warning = _available_rows_and_replacements(
                rows, redaction_rules_path
            )
            for row in rows:
                note = _safe_note(row, replacements, connection)
                for tag_record in note["tag_records"]:
                    key = (int(tag_record["tag_id"]), tag_record["name"])
                    records[key] = dict(tag_record)
    result = sorted(records.values(), key=lambda item: (item["name"], item["tag_id"]))
    _write_log(
        database_path, actor or str(actor_target_id), "list_tags", request_id,
        result_count=len(result), status="success" if actor else "failed",
        failure_reason="" if actor else "unknown_actor",
    )
    return result


def ai_create_note(
    actor_target_id: str,
    request_id: str,
    *,
    title: str,
    body: str,
    tags: list[str] | None = None,
    directory_id: int | None = None,
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
    if directory_id is not None:
        with _connect(database_path) as connection:
            selected_directory = _visible_directory_path_by_id(
                connection, actor, int(directory_id)
            )
        if selected_directory is None:
            result = _result(
                "rejected", request_id, None, "", "none", False, False,
                "目标目录不可用；只能选择当前身份可见的已有目录。",
            )
            _persist_terminal(database_path, actor, "create", result, "rejected")
            return result
        main_category, sub_category, sub_sub_category = selected_directory
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
    expected_updated_at: str | None = None,
    expected_version: str | None = None,
    database_path: Path = DATABASE_PATH,
) -> dict:
    return _request_modify(
        database_path, actor_target_id, request_id, int(note_id), "prepend",
        {"content": str(content or "")}, expected_updated_at, expected_version,
    )


def ai_append_note(
    actor_target_id: str,
    request_id: str,
    note_id: int,
    content: str,
    *,
    expected_updated_at: str | None = None,
    expected_version: str | None = None,
    database_path: Path = DATABASE_PATH,
) -> dict:
    return _request_modify(
        database_path, actor_target_id, request_id, int(note_id), "append",
        {"content": str(content or "")}, expected_updated_at, expected_version,
    )


def ai_update_note_metadata(
    actor_target_id: str,
    request_id: str,
    note_id: int,
    *,
    expected_updated_at: str | None = None,
    expected_version: str | None = None,
    title: str | None = None,
    tags: list[str] | None = None,
    directory: tuple[str, str, str] | None = None,
    directory_id: int | None = None,
    add_tags: list[str] | None = None,
    remove_tags: list[str] | None = None,
    database_path: Path = DATABASE_PATH,
) -> dict:
    payload: dict = {}
    if title is not None:
        payload["title"] = str(title).strip() or "未命名笔记"
    if tags is not None:
        payload["tags"] = _normalize_note_tags(tags)
    if add_tags is not None:
        payload["add_tags"] = _normalize_note_tags(add_tags)
    if remove_tags is not None:
        payload["remove_tags"] = _normalize_note_tags(remove_tags)
    if tags is not None and (add_tags is not None or remove_tags is not None):
        payload["invalid_tag_modes"] = True
    if directory is not None:
        if len(directory) != 3:
            raise ValueError("目录必须包含主分类、子分类、子子分类。")
        payload["directory"] = [str(value) for value in directory]
    if directory_id is not None:
        payload["directory_id"] = int(directory_id)
    return _request_modify(
        database_path, actor_target_id, request_id, int(note_id), "metadata",
        payload, expected_updated_at, expected_version,
    )


def get_ai_operation_status(
    request_id: str, database_path: Path = DATABASE_PATH
) -> dict | None:
    with _connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT operation_type, result_json, created_at, updated_at
            FROM ai_operations WHERE request_id=?
            """,
            (request_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        **json.loads(row["result_json"]),
        "operation_type": row["operation_type"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def safe_get_ai_operation_status(
    request_id: str,
    actor_target_id: str,
    *,
    log_request_id: str | None = None,
    database_path: Path = DATABASE_PATH,
) -> dict | None:
    actor = _validated_actor(actor_target_id)
    result = None
    if actor is not None:
        with _connect(database_path) as connection:
            row = connection.execute(
                """
                SELECT operation_type, result_json, created_at, updated_at
                FROM ai_operations
                WHERE request_id=? AND actor_target_id=?
                """,
                (request_id, actor),
            ).fetchone()
        if row is not None:
            result = {
                **json.loads(row["result_json"]),
                "operation_type": row["operation_type"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
    _write_log(
        database_path,
        actor or str(actor_target_id),
        "operation_status",
        log_request_id or uuid.uuid4().hex,
        result_count=1 if result else 0,
        status="success" if actor else "failed",
        failure_reason="" if actor else "unknown_actor",
    )
    return result


def record_ai_rejected_operation(
    actor_target_id: str,
    request_id: str,
    operation_type: str,
    reason: str,
    *,
    note_id: int | None = None,
    database_path: Path = DATABASE_PATH,
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
    result = _result(
        "rejected", request_id, note_id, "", "none", False, False, reason
    )
    _persist_terminal(database_path, actor, operation_type, result, "rejected")
    return result


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
        with _connect(database_path) as connection:
            rows = _authorized_rows(connection, actor)
            rows, replacements, _warning = _available_rows_and_replacements(
                rows, redaction_rules_path
            )
            for row in rows:
                note = _safe_note(row, replacements, connection)
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
    expected_updated_at: str | None,
    expected_version: str | None,
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
        if note is not None:
            if not _credential_supplied(expected_updated_at, expected_version):
                result = _result(
                    "rejected", request_id, note_id, note["title"], "none", False, False,
                    "缺少版本凭证；请先重新读取笔记。",
                )
                _persist_terminal(database_path, actor, operation_type, result, "rejected")
                return result
            if not _version_credentials_match(
                int(note["id"]), str(note["updated_at"]),
                expected_updated_at=expected_updated_at,
                expected_version=expected_version,
            ):
                result = _result(
                    "conflict", request_id, note_id, note["title"], "none", False, False,
                    "笔记已被更新。",
                )
                _persist_terminal(database_path, actor, operation_type, result, "conflict")
                return result
            # Only the canonical value read inside this authorization check is carried
            # into the proposal or transaction-level race check.
            expected_updated_at = str(note["updated_at"])

        if note is not None and operation_type == "metadata":
            if payload.pop("invalid_tag_modes", False):
                result = _result(
                    "rejected", request_id, note_id, note["title"], "none", False, False,
                    "不能同时提交完整标签列表和标签增删操作。",
                )
                _persist_terminal(database_path, actor, operation_type, result, "rejected")
                return result
            if "directory_id" in payload:
                selected_directory = _visible_directory_path_by_id(
                    connection, actor, int(payload.pop("directory_id"))
                )
                if selected_directory is None:
                    result = _result(
                        "rejected", request_id, note_id, note["title"], "none", False, False,
                        "目标目录不可用；只能选择当前身份可见的已有目录。",
                    )
                    _persist_terminal(database_path, actor, operation_type, result, "rejected")
                    return result
                payload["directory"] = list(selected_directory)

            add_tags = set(payload.pop("add_tags", []))
            remove_tags = set(payload.pop("remove_tags", []))
            if add_tags.intersection(remove_tags):
                result = _result(
                    "rejected", request_id, note_id, note["title"], "none", False, False,
                    "同一标签不能同时添加和移除。",
                )
                _persist_terminal(database_path, actor, operation_type, result, "rejected")
                return result
            if add_tags or remove_tags:
                current_tags = _note_tag_names(connection, note_id)
                desired_tags = [tag for tag in current_tags if tag not in remove_tags]
                desired_tags.extend(tag for tag in sorted(add_tags) if tag not in desired_tags)
                payload["tags"] = desired_tags

            if not _metadata_changes_note(connection, note, payload):
                result = _result(
                    "no_change", request_id, note_id, note["title"], "none", False, False,
                    "请求没有产生实际变化。",
                )
                _persist_terminal(database_path, actor, operation_type, result, "no_change")
                return result
    if note is None:
        result = _result("not_found", request_id, note_id, "", "none", False, False, "未找到笔记。")
        _persist_terminal(database_path, actor, operation_type, result, "not_found")
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
        directory_id = _existing_directory_path_id(
            connection, payload["main_category"], payload["sub_category"], payload["sub_sub_category"]
        )
        if directory_id is None:
            result = _result(
                "failed", request_id, None, payload["title"], handling_mode,
                False, False, "目标目录不存在，AI 不能创建目录。",
            )
            _store_operation(connection, request_id, actor, "create", result)
            _insert_log(
                connection, actor, "create", request_id, None, payload["title"], 0,
                handling_mode, "failed", result["reason"], False,
                handling_mode == "human_approved",
            )
            return result
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
        if not _timestamp_credentials_match(str(note["updated_at"]), expected_updated_at):
            result = _result("conflict", request_id, note_id, note["title"], handling_mode, False, False, "笔记已被更新。")
            _store_operation(connection, request_id, actor, operation_type, result)
            _insert_log(connection, actor, operation_type, request_id, note_id, note["title"], 0, handling_mode, "conflict", result["reason"], False, handling_mode == "human_approved")
            return result
        timestamp = _next_note_updated_at(note["updated_at"])
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
                """
                UPDATE notes SET content_html=?, body_rich_text=?,
                    revision=revision+1, updated_at=? WHERE id=?
                """,
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
                    sub_category=?, sub_sub_category=?, revision=revision+1,
                    updated_at=? WHERE id=?
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
        cursor = connection.execute(
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
        result["proposal_id"] = int(cursor.lastrowid)
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


def _safe_note(
    row,
    replacements: list[tuple[str, str]],
    connection=None,
    *,
    body_renderer=None,
    redaction_transform=None,
) -> dict:
    if connection is None:
        tag_records = [
            {"tag_id": 0, "name": tag}
            for tag in parse_tags(row["tags"] or "")
        ]
    else:
        tag_records = _note_tag_records(connection, int(row["id"]))
    values = materialize_ai_safe_note(
        row,
        authorized=True,
        replacements=replacements,
        body_renderer=body_renderer or _html_to_text,
        redaction_transform=redaction_transform or apply_redactions,
        tag_records=tag_records,
    )
    if values is None:
        raise ValueError("已授权查询返回了不可供 AI 使用的笔记。")
    values["version"] = note_version_token(
        int(row["id"]),
        str(row["updated_at"]),
    )
    return values


def materialize_ai_safe_notes_for_target(
    target: str,
    *,
    database_path: Path = DATABASE_PATH,
    redaction_rules_path: Path = REDACTION_RULES_PATH,
    body_renderer=None,
    redaction_transform=None,
    source_read_only: bool = False,
) -> tuple[list[dict], str]:
    """Read authorized notes and derive safe records without logs or writes."""
    actor = normalize_target_ids([target])[0]
    connection = _open_ai_source_connection(
        database_path,
        read_only=source_read_only,
    )
    try:
        rows = _authorized_rows(connection, actor)
        rows = sorted(
            rows,
            key=lambda row: (
                str(row["updated_at"] or ""),
                str(row["created_at"] or ""),
                int(row["id"]),
            ),
            reverse=True,
        )
        rows, replacements, redaction_warning = _available_rows_and_replacements(
            rows, redaction_rules_path
        )
        safe_notes = [
            _safe_note(
                row,
                replacements,
                connection,
                body_renderer=body_renderer,
                redaction_transform=redaction_transform,
            )
            for row in rows
        ]
    finally:
        connection.close()
    return safe_notes, redaction_warning


def _available_rows_and_replacements(
    rows,
    redaction_rules_path: Path,
):
    try:
        replacements = _redaction_replacements_for_rows(
            rows, redaction_rules_path
        )
    except RedactionUnavailableError as error:
        available_rows = [
            row
            for row in rows
            if normalize_ai_visibility(row["ai_visibility"])
            != AI_VISIBILITY_REDACTED
        ]
        return available_rows, [], str(error)
    return rows, replacements, ""


def _redaction_replacements_for_rows(
    rows,
    redaction_rules_path: Path,
) -> list[tuple[str, str]]:
    has_redacted_notes = any(
        normalize_ai_visibility(row["ai_visibility"])
        == AI_VISIBILITY_REDACTED
        for row in rows
    )
    if not has_redacted_notes:
        return []
    return load_redaction_rules(redaction_rules_path).replacements


def _open_ai_source_connection(
    database_path: Path,
    *,
    read_only: bool,
) -> sqlite3.Connection:
    if not read_only:
        return _connect(database_path)

    source_path = Path(database_path)
    if not source_path.is_file():
        raise FileNotFoundError("AI source database is unavailable.")
    connection = sqlite3.connect(
        f"{source_path.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=5.0,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _match_note(note: dict, query: SearchQuery) -> dict | None:
    if query.is_blank:
        return {
            "best_safe_snippet": _snippet(note["body"], ""),
            "matched_in": "all",
            "relevance": {"score": 1, "reason": "recent_safe_note"},
        }
    match = match_search_fields(
        query,
        title=note["title"],
        tags=note["tags"],
        directory=note["directory"],
        body=note["body"],
    )
    if match is None:
        return None
    first_token = query.tokens[0]
    if match.matched_in == "title":
        snippet = _snippet(note["title"], first_token)
    elif match.matched_in in {"tags", "title_tags"}:
        snippet = " | ".join(
            part for part in (note["title"], ", ".join(note["tags"])) if part
        )
    elif match.matched_in == "directory":
        snippet = note["directory"]
    else:
        snippet = _snippet(note["body"], first_token)
    return {
        "best_safe_snippet": snippet,
        "matched_in": match.matched_in,
        "relevance": {"score": match.score, "reason": match.reason},
    }


def _public_search_note(note: dict) -> dict:
    return {key: note[key] for key in ("note_id", "title", "directory", "tags", "updated_at")}


def _public_full_note(note: dict) -> dict:
    return {
        key: note[key]
        for key in ("note_id", "title", "directory", "tags", "body", "updated_at", "version")
    }


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
            revision=revision+1, updated_at=? WHERE id=? AND is_deleted=0
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


def _visible_directory_path_by_id(
    connection,
    actor: str,
    directory_id: int,
) -> tuple[str, str, str] | None:
    row = connection.execute(
        """
        SELECT DISTINCT notes.main_category, notes.sub_category, notes.sub_sub_category
        FROM notes JOIN note_ai_targets ON note_ai_targets.note_id=notes.id
        WHERE notes.directory_id=? AND notes.is_deleted=0
          AND notes.ai_visibility IN (
              'full', 'redacted', '可直接给 AI', '脱敏后给 AI',
              'AI完整可见', 'AI脱敏后可见'
          )
          AND note_ai_targets.target_id IN (?, ?)
        LIMIT 1
        """,
        (directory_id, AI_TARGET_COMMON_ID, actor),
    ).fetchone()
    if row is None:
        return None
    return (row["main_category"], row["sub_category"], row["sub_sub_category"])


def _note_tag_records(connection, note_id: int) -> list[dict]:
    rows = connection.execute(
        """
        SELECT tags.id AS tag_id, tags.name
        FROM tags JOIN note_tags ON note_tags.tag_id=tags.id
        WHERE note_tags.note_id=?
        ORDER BY tags.name, tags.id
        """,
        (note_id,),
    ).fetchall()
    return [{"tag_id": int(row["tag_id"]), "name": str(row["name"])} for row in rows]


def _note_tag_names(connection, note_id: int) -> list[str]:
    return [item["name"] for item in _note_tag_records(connection, note_id)]


def _metadata_changes_note(connection, note, payload: dict) -> bool:
    if "title" in payload and payload["title"] != note["title"]:
        return True
    if "directory" in payload and tuple(payload["directory"]) != (
        note["main_category"], note["sub_category"], note["sub_sub_category"]
    ):
        return True
    if "tags" in payload and set(payload["tags"]) != set(
        _note_tag_names(connection, int(note["id"]))
    ):
        return True
    return False


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


def _query_summary(value: object) -> str:
    return f"query:{len(str(value or ''))} chars"


def _next_note_updated_at(previous: str) -> str:
    now = now_shanghai()
    try:
        prior = datetime.fromisoformat(previous)
        if prior.tzinfo is None:
            prior = prior.replace(tzinfo=now.tzinfo)
        if now <= prior:
            now = prior + timedelta(microseconds=1)
    except (TypeError, ValueError):
        pass
    return now.isoformat(timespec="microseconds")


def note_version_token(note_id: int, updated_at: str) -> str:
    """Return the stable opaque version credential for one exact note revision."""
    material = f"{int(note_id)}\0{updated_at}".encode("utf-8")
    return "snotes-v1-" + hashlib.sha256(material).hexdigest()


def _credential_supplied(
    expected_updated_at: str | None,
    expected_version: str | None,
) -> bool:
    return bool(str(expected_updated_at or "").strip() or str(expected_version or "").strip())


def _version_credentials_match(
    note_id: int,
    current_updated_at: str,
    *,
    expected_updated_at: str | None,
    expected_version: str | None,
) -> bool:
    """Validate every supplied credential; supplying both requires both to match."""
    if expected_version is not None:
        supplied_version = str(expected_version).strip()
        if not supplied_version or not hmac.compare_digest(
            note_version_token(note_id, current_updated_at), supplied_version
        ):
            return False
    if expected_updated_at is not None:
        supplied_timestamp = str(expected_updated_at)
        if not supplied_timestamp.strip() or not _timestamp_credentials_match(
            current_updated_at, supplied_timestamp
        ):
            return False
    return _credential_supplied(expected_updated_at, expected_version)


def _timestamp_credentials_match(current: str, expected: str) -> bool:
    current_value = _parse_iso_timestamp(current)
    expected_value = _parse_iso_timestamp(expected)
    if current_value is None or expected_value is None:
        return False
    if (current_value.tzinfo is None) != (expected_value.tzinfo is None):
        return False
    return current_value == expected_value


def _parse_iso_timestamp(value: object) -> datetime | None:
    text = _clean_timestamp_credential(value)
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def _clean_timestamp_credential(value: object) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return text


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
