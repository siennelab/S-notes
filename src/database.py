from __future__ import annotations

import html
import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from src.app_paths import DATABASE_PATH
from src.ai_targets import (
    AI_TARGET_COMMON,
    AI_TARGET_COMMON_ID,
    AI_TARGET_DEFINITIONS,
    AI_TARGET_DISPLAY_BY_ID,
    AI_TARGETS,
    AI_VISIBILITY_NO_EXPORT,
    LEGACY_AI_TARGET_IDS,
    normalize_ai_targets,
    normalize_ai_visibility,
    normalize_target_ids,
    resolve_target_id,
    target_display_name,
    validate_ai_visibility,
)
from src.tag_manager import normalize_tag, parse_tags
from src.time_utils import now_shanghai, now_shanghai_iso


DEFAULT_MAIN_CATEGORY = "临时"
DEFAULT_SUB_CATEGORY = "无"
DEFAULT_SUB_SUB_CATEGORY = "无"
DEFAULT_AI_VISIBILITY = AI_VISIBILITY_NO_EXPORT
AI_SCHEMA_VERSION = 1
INITIAL_MAIN_CATEGORIES = ("项目", "设备", "账号", "待办", "临时", "备忘", "终端指令")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS directories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    parent_id INTEGER REFERENCES directories(id),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    is_deleted INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT,
    trash_group_id TEXT
);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY,
    directory_id INTEGER REFERENCES directories(id),
    title TEXT NOT NULL DEFAULT '',
    body_rich_text TEXT NOT NULL DEFAULT '',
    content_html TEXT NOT NULL DEFAULT '',
    main_category TEXT NOT NULL DEFAULT '临时',
    sub_category TEXT NOT NULL DEFAULT '无',
    sub_sub_category TEXT NOT NULL DEFAULT '无',
    tags TEXT NOT NULL DEFAULT '',
    ai_visibility TEXT NOT NULL DEFAULT 'hidden',
    is_pinned INTEGER NOT NULL DEFAULT 0,
    is_deleted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,
    trash_group_id TEXT
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS note_tags (
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (note_id, tag_id)
);

CREATE TABLE IF NOT EXISTS ai_targets (
    target_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL UNIQUE,
    is_system INTEGER NOT NULL DEFAULT 0,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS note_ai_targets (
    id INTEGER PRIMARY KEY,
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    target_id TEXT NOT NULL REFERENCES ai_targets(target_id),
    created_at TEXT NOT NULL,
    UNIQUE(note_id, target_id)
);

CREATE TABLE IF NOT EXISTS memory_receipts (
    id INTEGER PRIMARY KEY,
    note_id INTEGER REFERENCES notes(id) ON DELETE SET NULL,
    receipt_text TEXT NOT NULL,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_receipt_inbox (
    id INTEGER PRIMARY KEY,
    raw_text TEXT NOT NULL,
    parse_status TEXT NOT NULL DEFAULT 'failed',
    status TEXT NOT NULL DEFAULT 'pending',
    source TEXT NOT NULL DEFAULT '未知',
    action TEXT NOT NULL DEFAULT '新增',
    title TEXT NOT NULL DEFAULT '',
    main_category TEXT NOT NULL DEFAULT '临时',
    sub_category TEXT NOT NULL DEFAULT '无',
    sub_sub_category TEXT NOT NULL DEFAULT '无',
    tags TEXT NOT NULL DEFAULT '',
    ai_visibility TEXT NOT NULL DEFAULT 'hidden',
    ai_targets TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    note_id INTEGER REFERENCES notes(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    decided_at TEXT,
    parse_error TEXT,
    request_id TEXT,
    actor_target_id TEXT,
    operation_type TEXT,
    target_note_id INTEGER REFERENCES notes(id) ON DELETE SET NULL,
    expected_updated_at TEXT,
    operation_payload TEXT NOT NULL DEFAULT '',
    result_status TEXT
);

CREATE TABLE IF NOT EXISTS app_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_note_history (
    id INTEGER PRIMARY KEY,
    note_id INTEGER NOT NULL,
    note_title TEXT NOT NULL,
    modification_type TEXT NOT NULL,
    actor_target_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    modified_at TEXT NOT NULL,
    before_snapshot TEXT NOT NULL,
    after_updated_at TEXT NOT NULL,
    handling_mode TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_call_logs (
    id INTEGER PRIMARY KEY,
    called_at TEXT NOT NULL,
    actor_target_id TEXT NOT NULL,
    operation_type TEXT NOT NULL,
    request_id TEXT NOT NULL,
    note_id INTEGER,
    summary TEXT NOT NULL DEFAULT '',
    result_count INTEGER NOT NULL DEFAULT 0,
    handling_mode TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    failure_reason TEXT NOT NULL DEFAULT '',
    is_duplicate INTEGER NOT NULL DEFAULT 0,
    entered_confirmation INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS ai_operations (
    request_id TEXT PRIMARY KEY,
    actor_target_id TEXT NOT NULL,
    operation_type TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_migration_issues (
    id INTEGER PRIMARY KEY,
    issue_type TEXT NOT NULL,
    source_value TEXT NOT NULL,
    note_id INTEGER,
    recorded_at TEXT NOT NULL,
    UNIQUE(issue_type, source_value, note_id)
);

CREATE TABLE IF NOT EXISTS schema_meta (
    meta_key TEXT PRIMARY KEY,
    meta_value TEXT NOT NULL
);
"""


NOTE_COLUMNS = {
    "content_html": "TEXT NOT NULL DEFAULT ''",
    "main_category": "TEXT NOT NULL DEFAULT '临时'",
    "sub_category": "TEXT NOT NULL DEFAULT '无'",
    "sub_sub_category": "TEXT NOT NULL DEFAULT '无'",
    "tags": "TEXT NOT NULL DEFAULT ''",
    "ai_visibility": "TEXT NOT NULL DEFAULT 'hidden'",
    "is_pinned": "INTEGER NOT NULL DEFAULT 0",
    "is_deleted": "INTEGER NOT NULL DEFAULT 0",
    "trash_group_id": "TEXT",
}

DIRECTORY_COLUMNS = {
    "is_deleted": "INTEGER NOT NULL DEFAULT 0",
    "deleted_at": "TEXT",
    "trash_group_id": "TEXT",
}

AI_RECEIPT_INBOX_COLUMNS = {
    "raw_text": "TEXT NOT NULL DEFAULT ''",
    "parse_status": "TEXT NOT NULL DEFAULT 'failed'",
    "status": "TEXT NOT NULL DEFAULT 'pending'",
    "source": "TEXT NOT NULL DEFAULT '未知'",
    "action": "TEXT NOT NULL DEFAULT '新增'",
    "title": "TEXT NOT NULL DEFAULT ''",
    "main_category": "TEXT NOT NULL DEFAULT '临时'",
    "sub_category": "TEXT NOT NULL DEFAULT '无'",
    "sub_sub_category": "TEXT NOT NULL DEFAULT '无'",
    "tags": "TEXT NOT NULL DEFAULT ''",
    "ai_visibility": "TEXT NOT NULL DEFAULT 'hidden'",
    "ai_targets": "TEXT NOT NULL DEFAULT ''",
    "body": "TEXT NOT NULL DEFAULT ''",
    "note_id": "INTEGER REFERENCES notes(id) ON DELETE SET NULL",
    "created_at": "TEXT NOT NULL DEFAULT ''",
    "updated_at": "TEXT NOT NULL DEFAULT ''",
    "decided_at": "TEXT",
    "parse_error": "TEXT",
    "request_id": "TEXT",
    "actor_target_id": "TEXT",
    "operation_type": "TEXT",
    "target_note_id": "INTEGER REFERENCES notes(id) ON DELETE SET NULL",
    "expected_updated_at": "TEXT",
    "operation_payload": "TEXT NOT NULL DEFAULT ''",
    "result_status": "TEXT",
}


def _connect(database_path: Path = DATABASE_PATH) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


LAST_MIGRATION_REPORT: dict | None = None


def initialize_database(database_path: Path = DATABASE_PATH) -> Path:
    global LAST_MIGRATION_REPORT
    database_path.parent.mkdir(parents=True, exist_ok=True)
    existed = database_path.exists() and database_path.stat().st_size > 0
    needs_foundation = existed and _database_needs_ai_foundation(database_path)
    backup_path = _backup_database_for_migration(database_path) if needs_foundation else None
    connection = _connect(database_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        _execute_schema(connection)
        _migrate_notes_table(connection)
        _migrate_directories_table(connection)
        _migrate_ai_receipt_inbox_table(connection)
        _seed_ai_targets(connection)
        before = _migration_counts(connection)
        unknown_visibilities = _migrate_ai_visibility_values(connection)
        unknown_targets = _migrate_note_ai_targets_table(connection)
        _seed_ai_settings(connection)
        _seed_initial_categories(connection)
        _ensure_directory_paths_for_notes(connection)
        after = _migration_counts(connection)
        report = {
            "schema_version": AI_SCHEMA_VERSION,
            "migrated_at": now_shanghai_iso(),
            "database_path": str(database_path),
            "backup_path": str(backup_path) if backup_path else "",
            "before": before,
            "after": after,
            "unknown_visibility_values": sorted(unknown_visibilities),
            "unknown_target_values": sorted(unknown_targets),
        }
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(meta_key, meta_value) VALUES (?, ?)",
            ("ai_foundation_version", str(AI_SCHEMA_VERSION)),
        )
        prior_report = connection.execute(
            "SELECT meta_value FROM schema_meta WHERE meta_key=?",
            ("task13a_last_migration_report",),
        ).fetchone()
        if needs_foundation or prior_report is None:
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(meta_key, meta_value) VALUES (?, ?)",
                ("task13a_last_migration_report", json.dumps(report, ensure_ascii=False)),
            )
            retained_report = report
        else:
            retained_report = json.loads(prior_report["meta_value"])
        connection.commit()
        LAST_MIGRATION_REPORT = retained_report
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return database_path


def get_last_migration_report(database_path: Path = DATABASE_PATH) -> dict | None:
    if LAST_MIGRATION_REPORT and LAST_MIGRATION_REPORT.get("database_path") == str(database_path):
        return dict(LAST_MIGRATION_REPORT)
    if not database_path.exists():
        return None
    with _connect(database_path) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_meta'"
        ).fetchone()
        if table is None:
            return None
        row = connection.execute(
            "SELECT meta_value FROM schema_meta WHERE meta_key = ?",
            ("task13a_last_migration_report",),
        ).fetchone()
    return json.loads(row["meta_value"]) if row else None


def _execute_schema(connection: sqlite3.Connection) -> None:
    for statement in SCHEMA_SQL.split(";"):
        if statement.strip():
            connection.execute(statement)


def _database_needs_ai_foundation(database_path: Path) -> bool:
    try:
        with _connect(database_path) as connection:
            meta_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_meta'"
            ).fetchone()
            if meta_exists is None:
                return True
            row = connection.execute(
                "SELECT meta_value FROM schema_meta WHERE meta_key='ai_foundation_version'"
            ).fetchone()
            return row is None or int(row["meta_value"]) < AI_SCHEMA_VERSION
    except (sqlite3.DatabaseError, ValueError):
        return True


def _backup_database_for_migration(database_path: Path) -> Path:
    backup_dir = database_path.parent / "backups" / "migrations"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_path = backup_dir / f"s_notes_pre_task13a_{timestamp}.db"
    source = _connect(database_path)
    destination = sqlite3.connect(backup_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return backup_path


def _migrate_notes_table(connection: sqlite3.Connection) -> None:
    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(notes)").fetchall()
    }
    for column_name, column_definition in NOTE_COLUMNS.items():
        if column_name not in existing_columns:
            connection.execute(
                f"ALTER TABLE notes ADD COLUMN {column_name} {column_definition}"
            )


def _migrate_directories_table(connection: sqlite3.Connection) -> None:
    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(directories)").fetchall()
    }
    for column_name, column_definition in DIRECTORY_COLUMNS.items():
        if column_name not in existing_columns:
            connection.execute(
                f"ALTER TABLE directories ADD COLUMN {column_name} {column_definition}"
            )


def _migrate_ai_receipt_inbox_table(connection: sqlite3.Connection) -> None:
    existing_columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(ai_receipt_inbox)").fetchall()
    }
    for column_name, column_definition in AI_RECEIPT_INBOX_COLUMNS.items():
        if column_name not in existing_columns:
            connection.execute(
                f"ALTER TABLE ai_receipt_inbox ADD COLUMN {column_name} {column_definition}"
            )


def _seed_ai_targets(connection: sqlite3.Connection) -> None:
    timestamp = now_shanghai_iso()
    for item in AI_TARGET_DEFINITIONS:
        connection.execute(
            """
            INSERT INTO ai_targets (
                target_id, display_name, slug, is_system, is_enabled,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(target_id) DO UPDATE SET
                display_name=excluded.display_name,
                slug=excluded.slug,
                is_system=excluded.is_system,
                updated_at=excluded.updated_at
            """,
            (
                item.target_id,
                item.display_name,
                item.slug,
                1 if item.system else 0,
                timestamp,
                timestamp,
            ),
        )


def _migrate_ai_visibility_values(connection: sqlite3.Connection) -> set[str]:
    unknown: set[str] = set()
    timestamp = now_shanghai_iso()
    rows = connection.execute("SELECT id, ai_visibility FROM notes").fetchall()
    for row in rows:
        raw = str(row["ai_visibility"] or "")
        normalized = normalize_ai_visibility(raw)
        if raw.strip() not in {
            "hidden", "full", "redacted", "AI不可见", "AI完整可见",
            "AI脱敏后可见", "不导出给 AI", "可直接给 AI", "脱敏后给 AI",
        }:
            unknown.add(raw)
            connection.execute(
                """
                INSERT OR IGNORE INTO ai_migration_issues
                    (issue_type, source_value, note_id, recorded_at)
                VALUES ('visibility', ?, ?, ?)
                """,
                (raw, row["id"], timestamp),
            )
        connection.execute(
            "UPDATE notes SET ai_visibility=? WHERE id=?",
            (normalized, row["id"]),
        )
    connection.execute(
        """
        UPDATE ai_receipt_inbox
        SET ai_visibility = CASE ai_visibility
            WHEN '可直接给 AI' THEN 'full'
            WHEN '脱敏后给 AI' THEN 'redacted'
            WHEN 'AI完整可见' THEN 'full'
            WHEN 'AI脱敏后可见' THEN 'redacted'
            WHEN 'full' THEN 'full'
            WHEN 'redacted' THEN 'redacted'
            ELSE 'hidden'
        END
        """
    )
    return unknown


def _migrate_note_ai_targets_table(connection: sqlite3.Connection) -> set[str]:
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(note_ai_targets)")
    }
    unknown: set[str] = set()
    if "target_name" not in columns:
        rows = connection.execute(
            "SELECT id, note_id, target_id FROM note_ai_targets"
        ).fetchall()
        for row in rows:
            if resolve_target_id(row["target_id"]) is None:
                unknown.add(str(row["target_id"] or ""))
                connection.execute("DELETE FROM note_ai_targets WHERE id=?", (row["id"],))
        return unknown

    legacy_rows = connection.execute(
        "SELECT id, note_id, target_name, created_at FROM note_ai_targets ORDER BY id"
    ).fetchall()
    connection.execute("ALTER TABLE note_ai_targets RENAME TO note_ai_targets_legacy")
    connection.execute(
        """
        CREATE TABLE note_ai_targets (
            id INTEGER PRIMARY KEY,
            note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
            target_id TEXT NOT NULL REFERENCES ai_targets(target_id),
            created_at TEXT NOT NULL,
            UNIQUE(note_id, target_id)
        )
        """
    )
    timestamp = now_shanghai_iso()
    for row in legacy_rows:
        target_id = resolve_target_id(row["target_name"])
        if target_id is None:
            value = str(row["target_name"] or "")
            unknown.add(value)
            connection.execute(
                """
                INSERT OR IGNORE INTO ai_migration_issues
                    (issue_type, source_value, note_id, recorded_at)
                VALUES ('target', ?, ?, ?)
                """,
                (value, row["note_id"], timestamp),
            )
            continue
        connection.execute(
            """
            INSERT OR IGNORE INTO note_ai_targets(note_id, target_id, created_at)
            VALUES (?, ?, ?)
            """,
            (row["note_id"], target_id, row["created_at"] or timestamp),
        )
    connection.execute("DROP TABLE note_ai_targets_legacy")
    return unknown


def _seed_ai_settings(connection: sqlite3.Connection) -> None:
    timestamp = now_shanghai_iso()
    for key, value in (
        ("ai_create_requires_confirmation", "0"),
        ("ai_modify_requires_confirmation", "1"),
    ):
        connection.execute(
            "INSERT OR IGNORE INTO app_settings(setting_key, setting_value, updated_at) VALUES (?, ?, ?)",
            (key, value, timestamp),
        )


def _migration_counts(connection: sqlite3.Connection) -> dict:
    visibility_rows = connection.execute(
        "SELECT ai_visibility, COUNT(*) AS count FROM notes GROUP BY ai_visibility"
    ).fetchall()
    target_count = connection.execute(
        "SELECT COUNT(*) FROM note_ai_targets"
    ).fetchone()[0]
    return {
        "notes": connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0],
        "visibility": {str(row["ai_visibility"]): row["count"] for row in visibility_rows},
        "target_relations": target_count,
    }


def _seed_initial_categories(connection: sqlite3.Connection) -> None:
    directory_count = connection.execute("SELECT COUNT(*) FROM directories").fetchone()[0]
    if directory_count:
        return

    timestamp = now_shanghai_iso()
    for sort_order, name in enumerate(INITIAL_MAIN_CATEGORIES):
        connection.execute(
            """
            INSERT INTO directories (name, parent_id, sort_order, created_at, updated_at)
            VALUES (?, NULL, ?, ?, ?)
            """,
            (name, sort_order, timestamp, timestamp),
        )


def _find_directory_id(
    connection: sqlite3.Connection, name: str, parent_id: int | None
) -> int | None:
    if parent_id is None:
        row = connection.execute(
            "SELECT id FROM directories WHERE parent_id IS NULL AND name = ? AND is_deleted = 0",
            (name,),
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT id FROM directories WHERE parent_id = ? AND name = ? AND is_deleted = 0",
            (parent_id, name),
        ).fetchone()
    return row["id"] if row else None


def _create_directory(
    connection: sqlite3.Connection, name: str, parent_id: int | None
) -> int:
    timestamp = now_shanghai_iso()
    cursor = connection.execute(
        """
        INSERT INTO directories (name, parent_id, sort_order, created_at, updated_at)
        VALUES (?, ?, 0, ?, ?)
        """,
        (name.strip(), parent_id, timestamp, timestamp),
    )
    return cursor.lastrowid


def _ensure_directory(
    connection: sqlite3.Connection, name: str, parent_id: int | None
) -> int:
    directory_id = _find_directory_id(connection, name, parent_id)
    if directory_id is not None:
        return directory_id
    return _create_directory(connection, name, parent_id)


def _ensure_directory_path(
    connection: sqlite3.Connection, main: str, sub: str, sub_sub: str
) -> int:
    main_id = _ensure_directory(connection, main, None)
    if sub == DEFAULT_SUB_CATEGORY and sub_sub == DEFAULT_SUB_SUB_CATEGORY:
        return main_id
    sub_id = _ensure_directory(connection, sub, main_id)
    if sub_sub == DEFAULT_SUB_SUB_CATEGORY:
        return sub_id
    return _ensure_directory(connection, sub_sub, sub_id)


def _ensure_default_directory_path(connection: sqlite3.Connection) -> int:
    return _ensure_directory_path(
        connection,
        DEFAULT_MAIN_CATEGORY,
        DEFAULT_SUB_CATEGORY,
        DEFAULT_SUB_SUB_CATEGORY,
    )


def _ensure_directory_paths_for_notes(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT DISTINCT main_category, sub_category, sub_sub_category
        FROM notes
        WHERE is_deleted = 0
        """
    ).fetchall()
    for row in rows:
        _ensure_directory_path(
            connection,
            row["main_category"] or DEFAULT_MAIN_CATEGORY,
            row["sub_category"] or DEFAULT_SUB_CATEGORY,
            row["sub_sub_category"] or DEFAULT_SUB_SUB_CATEGORY,
        )


def create_note(database_path: Path = DATABASE_PATH) -> dict:
    return create_note_in_directory(
        DEFAULT_MAIN_CATEGORY,
        DEFAULT_SUB_CATEGORY,
        DEFAULT_SUB_SUB_CATEGORY,
        database_path,
    )


def create_note_in_directory(
    main_category: str,
    sub_category: str = DEFAULT_SUB_CATEGORY,
    sub_sub_category: str = DEFAULT_SUB_SUB_CATEGORY,
    database_path: Path = DATABASE_PATH,
) -> dict:
    timestamp = now_shanghai_iso()
    title = f"未命名笔记 {now_shanghai().strftime('%Y-%m-%d %H:%M')}"
    with _connect(database_path) as connection:
        main = main_category or DEFAULT_MAIN_CATEGORY
        sub = sub_category or DEFAULT_SUB_CATEGORY
        sub_sub = sub_sub_category or DEFAULT_SUB_SUB_CATEGORY
        directory_id = _ensure_directory_path(connection, main, sub, sub_sub)
        cursor = connection.execute(
            """
            INSERT INTO notes (
                directory_id,
                title,
                body_rich_text,
                content_html,
                main_category,
                sub_category,
                sub_sub_category,
                tags,
                ai_visibility,
                is_pinned,
                is_deleted,
                created_at,
                updated_at
            )
            VALUES (?, ?, '', '', ?, ?, ?, '', ?, 0, 0, ?, ?)
            """,
            (
                directory_id,
                title,
                main,
                sub,
                sub_sub,
                DEFAULT_AI_VISIBILITY,
                timestamp,
                timestamp,
            ),
        )
        note_id = cursor.lastrowid
    note = get_note(note_id, database_path)
    if note is None:
        raise RuntimeError("新建笔记后读取失败")
    return note


def rename_note(
    note_id: int, new_title: str, database_path: Path = DATABASE_PATH
) -> dict:
    clean_title = new_title.strip() or "未命名笔记"
    timestamp = now_shanghai_iso()
    with _connect(database_path) as connection:
        connection.execute(
            """
            UPDATE notes
            SET title = ?, updated_at = ?
            WHERE id = ? AND is_deleted = 0
            """,
            (clean_title, timestamp, note_id),
        )
        changed = connection.total_changes
    if changed == 0:
        raise ValueError(f"笔记不存在或已删除: {note_id}")
    note = get_note(note_id, database_path)
    if note is None:
        raise RuntimeError(f"重命名后读取失败: {note_id}")
    return note


def soft_delete_note(note_id: int, database_path: Path = DATABASE_PATH) -> None:
    timestamp = now_shanghai_iso()
    trash_group_id = uuid.uuid4().hex
    with _connect(database_path) as connection:
        connection.execute(
            """
            UPDATE notes
            SET is_deleted = 1, deleted_at = ?, updated_at = ?, trash_group_id = ?
            WHERE id = ? AND is_deleted = 0
            """,
            (timestamp, timestamp, trash_group_id, note_id),
        )
        if connection.total_changes == 0:
            raise ValueError(f"笔记不存在或已删除: {note_id}")


def list_notes(database_path: Path = DATABASE_PATH) -> list[dict]:
    with _connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                title,
                main_category,
                sub_category,
                sub_sub_category,
                tags,
                ai_visibility,
                is_pinned,
                created_at,
                updated_at
            FROM notes
            WHERE is_deleted = 0
            ORDER BY updated_at DESC, created_at DESC, id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def list_notes_for_link_drawer(database_path: Path = DATABASE_PATH) -> list[dict]:
    with _connect(database_path) as connection:
        note_rows = connection.execute(
            """
            SELECT
                id,
                title,
                content_html,
                main_category,
                sub_category,
                sub_sub_category,
                tags,
                ai_visibility,
                is_pinned,
                created_at,
                updated_at
            FROM notes
            WHERE is_deleted = 0
            ORDER BY updated_at DESC, created_at DESC, id DESC
            """
        ).fetchall()
        tag_rows = connection.execute(
            """
            SELECT notes.id AS note_id, tags.name AS tag_name
            FROM notes
            JOIN note_tags ON note_tags.note_id = notes.id
            JOIN tags ON tags.id = note_tags.tag_id
            WHERE notes.is_deleted = 0
            ORDER BY tags.name COLLATE NOCASE ASC
            """
        ).fetchall()
        target_rows = connection.execute(
            """
            SELECT notes.id AS note_id, note_ai_targets.target_id
            FROM notes
            JOIN note_ai_targets ON note_ai_targets.note_id = notes.id
            WHERE notes.is_deleted = 0
            ORDER BY note_ai_targets.target_id COLLATE NOCASE ASC
            """
        ).fetchall()

    tags_by_note: dict[int, list[str]] = {}
    for row in tag_rows:
        tags_by_note.setdefault(row["note_id"], []).append(row["tag_name"])

    targets_by_note: dict[int, list[str]] = {}
    for row in target_rows:
        display_name = AI_TARGET_DISPLAY_BY_ID.get(row["target_id"])
        if display_name:
            targets_by_note.setdefault(row["note_id"], []).append(display_name)

    notes = [dict(row) for row in note_rows]
    for note in notes:
        note["tags"] = ", ".join(tags_by_note.get(note["id"], [])) or note["tags"]
        note["ai_targets"] = targets_by_note.get(note["id"], [])
    return notes


def get_note(
    note_id: int,
    database_path: Path = DATABASE_PATH,
    *,
    include_deleted: bool = False,
) -> dict | None:
    deleted_filter = "" if include_deleted else "AND is_deleted = 0"
    with _connect(database_path) as connection:
        row = connection.execute(
            f"""
            SELECT
                id,
                title,
                content_html,
                main_category,
                sub_category,
                sub_sub_category,
                tags,
                ai_visibility,
                is_pinned,
                is_deleted,
                created_at,
                updated_at
            FROM notes
            WHERE id = ? {deleted_filter}
            """,
            (note_id,),
        ).fetchone()
    return dict(row) if row else None


EXPORT_NOTE_SELECT = """
SELECT
    id,
    title,
    content_html,
    main_category,
    sub_category,
    sub_sub_category,
    tags,
    ai_visibility,
    is_pinned,
    created_at,
    updated_at
FROM notes
WHERE is_deleted = 0
"""


def get_note_for_export(
    note_id: int, database_path: Path = DATABASE_PATH
) -> dict | None:
    with _connect(database_path) as connection:
        row = connection.execute(
            EXPORT_NOTE_SELECT + " AND id = ?",
            (note_id,),
        ).fetchone()
    return dict(row) if row else None


def list_notes_for_export(database_path: Path = DATABASE_PATH) -> list[dict]:
    with _connect(database_path) as connection:
        rows = connection.execute(
            EXPORT_NOTE_SELECT + " ORDER BY updated_at DESC, created_at DESC, id DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def list_notes_by_ids_for_export(
    note_ids: list[int], database_path: Path = DATABASE_PATH
) -> list[dict]:
    if not note_ids:
        return []
    placeholders = ",".join("?" for _ in note_ids)
    with _connect(database_path) as connection:
        rows = connection.execute(
            EXPORT_NOTE_SELECT
            + f" AND id IN ({placeholders})"
            + " ORDER BY updated_at DESC, created_at DESC, id DESC",
            tuple(note_ids),
        ).fetchall()
    return [dict(row) for row in rows]


def list_notes_under_path_for_export(
    path: tuple[str, ...], database_path: Path = DATABASE_PATH
) -> list[dict]:
    if not path:
        return []
    clauses = ["main_category = ?"]
    params: list[str] = [path[0]]
    if len(path) >= 2:
        clauses.append("sub_category = ?")
        params.append(path[1])
    if len(path) >= 3:
        clauses.append("sub_sub_category = ?")
        params.append(path[2])
    with _connect(database_path) as connection:
        rows = connection.execute(
            EXPORT_NOTE_SELECT
            + " AND "
            + " AND ".join(clauses)
            + " ORDER BY updated_at DESC, created_at DESC, id DESC",
            tuple(params),
        ).fetchall()
    return [dict(row) for row in rows]


def update_note(
    note_id: int,
    *,
    title: str,
    content_html: str,
    main_category: str | None = None,
    sub_category: str | None = None,
    sub_sub_category: str | None = None,
    database_path: Path = DATABASE_PATH,
) -> dict:
    timestamp = now_shanghai_iso()
    with _connect(database_path) as connection:
        current = connection.execute(
            """
            SELECT main_category, sub_category, sub_sub_category
            FROM notes
            WHERE id = ? AND is_deleted = 0
            """,
            (note_id,),
        ).fetchone()
        if current is None:
            raise ValueError(f"笔记不存在或已删除: {note_id}")

        next_main = main_category or current["main_category"]
        next_sub = sub_category or current["sub_category"]
        next_sub_sub = sub_sub_category or current["sub_sub_category"]
        directory_id = _ensure_directory_path(connection, next_main, next_sub, next_sub_sub)
        connection.execute(
            """
            UPDATE notes
            SET title = ?,
                body_rich_text = ?,
                content_html = ?,
                directory_id = ?,
                main_category = ?,
                sub_category = ?,
                sub_sub_category = ?,
                updated_at = ?
            WHERE id = ? AND is_deleted = 0
            """,
            (
                title,
                content_html,
                content_html,
                directory_id,
                next_main,
                next_sub,
                next_sub_sub,
                timestamp,
                note_id,
            ),
        )
        changed = connection.total_changes
    if changed == 0:
        raise ValueError(f"笔记不存在或已删除: {note_id}")
    note = get_note(note_id, database_path)
    if note is None:
        raise RuntimeError(f"保存后读取失败: {note_id}")
    return note


def list_directories(database_path: Path = DATABASE_PATH) -> list[dict]:
    with _connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, name, parent_id, sort_order, created_at, updated_at,
                   is_deleted, deleted_at, trash_group_id
            FROM directories
            WHERE is_deleted = 0
            ORDER BY name COLLATE NOCASE ASC, id ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_directory_path(
    directory_id: int, database_path: Path = DATABASE_PATH
) -> tuple[str, ...]:
    with _connect(database_path) as connection:
        return _get_directory_path(connection, directory_id)


def _get_directory_path(
    connection: sqlite3.Connection, directory_id: int
) -> tuple[str, ...]:
    names: list[str] = []
    current_id = directory_id
    while current_id is not None:
        row = connection.execute(
            "SELECT id, name, parent_id, is_deleted FROM directories WHERE id = ?",
            (current_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"目录不存在: {directory_id}")
        names.append(row["name"])
        current_id = row["parent_id"]
    return tuple(reversed(names))


def create_directory(
    name: str, parent_id: int | None = None, database_path: Path = DATABASE_PATH
) -> dict:
    clean_name = name.strip()
    if not clean_name:
        raise ValueError("目录名称不能为空")
    with _connect(database_path) as connection:
        if parent_id is not None and _directory_depth(connection, parent_id) >= 3:
            raise ValueError("目录最多支持三层")
        existing_id = _find_directory_id(connection, clean_name, parent_id)
        if existing_id is not None:
            raise ValueError("同级目录已存在")
        directory_id = _create_directory(connection, clean_name, parent_id)
        row = connection.execute(
            "SELECT id, name, parent_id, sort_order, created_at, updated_at FROM directories WHERE id = ?",
            (directory_id,),
        ).fetchone()
    return dict(row)


def rename_directory(
    directory_id: int, new_name: str, database_path: Path = DATABASE_PATH
) -> None:
    clean_name = new_name.strip()
    if not clean_name:
        raise ValueError("目录名称不能为空")
    timestamp = now_shanghai_iso()
    with _connect(database_path) as connection:
        row = connection.execute(
            "SELECT id, name, parent_id FROM directories WHERE id = ? AND is_deleted = 0",
            (directory_id,),
        ).fetchone()
        if row is None:
            raise ValueError("目录不存在")
        existing_id = _find_directory_id(connection, clean_name, row["parent_id"])
        if existing_id is not None and existing_id != directory_id:
            raise ValueError("同级目录已存在")

        old_path = _get_directory_path(connection, directory_id)
        connection.execute(
            "UPDATE directories SET name = ?, updated_at = ? WHERE id = ?",
            (clean_name, timestamp, directory_id),
        )
        _rename_notes_for_directory_path(connection, old_path, clean_name, timestamp)


def delete_directory(directory_id: int, database_path: Path = DATABASE_PATH) -> None:
    soft_delete_directory_tree(directory_id, database_path)


def soft_delete_directory_tree(
    directory_id: int, database_path: Path = DATABASE_PATH
) -> str:
    timestamp = now_shanghai_iso()
    trash_group_id = uuid.uuid4().hex
    with _connect(database_path) as connection:
        path = _get_directory_path(connection, directory_id)
        _soft_delete_notes_under_path(connection, path, timestamp, trash_group_id)
        descendant_ids = _collect_descendant_directory_ids(connection, directory_id)
        connection.executemany(
            """
            UPDATE directories
            SET is_deleted = 1, deleted_at = ?, updated_at = ?, trash_group_id = ?
            WHERE id = ?
            """,
            [
                (timestamp, timestamp, trash_group_id, target_id)
                for target_id in descendant_ids
            ],
        )
    return trash_group_id


def soft_delete_notes_under_directory(
    directory_id: int, database_path: Path = DATABASE_PATH
) -> None:
    timestamp = now_shanghai_iso()
    trash_group_id = uuid.uuid4().hex
    with _connect(database_path) as connection:
        path = _get_directory_path(connection, directory_id)
        _soft_delete_notes_under_path(connection, path, timestamp, trash_group_id)


def restore_note(note_id: int, database_path: Path = DATABASE_PATH) -> dict:
    timestamp = now_shanghai_iso()
    with _connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT main_category, sub_category, sub_sub_category
            FROM notes
            WHERE id = ? AND is_deleted = 1
            """,
            (note_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"回收站中找不到笔记: {note_id}")
        directory_id = _ensure_directory_path(
            connection,
            row["main_category"] or DEFAULT_MAIN_CATEGORY,
            row["sub_category"] or DEFAULT_SUB_CATEGORY,
            row["sub_sub_category"] or DEFAULT_SUB_SUB_CATEGORY,
        )
        connection.execute(
            """
            UPDATE notes
            SET is_deleted = 0,
                deleted_at = NULL,
                trash_group_id = NULL,
                directory_id = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (directory_id, timestamp, note_id),
        )
    note = get_note(note_id, database_path)
    if note is None:
        raise RuntimeError(f"恢复后读取失败: {note_id}")
    return note


def permanently_delete_note(note_id: int, database_path: Path = DATABASE_PATH) -> None:
    with _connect(database_path) as connection:
        connection.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        if connection.total_changes == 0:
            raise ValueError(f"笔记不存在: {note_id}")


def restore_trash_note(note_id: int, database_path: Path = DATABASE_PATH) -> dict:
    return restore_note(note_id, database_path)


def restore_trash_directory(
    directory_id: int, database_path: Path = DATABASE_PATH
) -> None:
    timestamp = now_shanghai_iso()
    with _connect(database_path) as connection:
        _restore_trash_directories_with_connection(
            connection, [directory_id], timestamp
        )


def permanently_delete_trash_directory(
    directory_id: int, database_path: Path = DATABASE_PATH
) -> None:
    with _connect(database_path) as connection:
        directory_ids = _collect_descendant_directory_ids(connection, directory_id)
        paths = [_get_directory_path(connection, target_id) for target_id in directory_ids]
        trash_group_ids = _trash_group_ids_for_directories(connection, directory_ids)
        for path in paths:
            _permanently_delete_notes_under_path(connection, path, trash_group_ids)
        sorted_ids = sorted(
            directory_ids,
            key=lambda target_id: _directory_depth(connection, target_id),
            reverse=True,
        )
        for target_id in sorted_ids:
            connection.execute(
                "DELETE FROM directories WHERE id = ? AND is_deleted = 1",
                (target_id,),
            )


def list_trash_tree(database_path: Path = DATABASE_PATH) -> dict:
    with _connect(database_path) as connection:
        all_directory_rows = connection.execute(
            """
            SELECT id, name, parent_id, is_deleted
            FROM directories
            """
        ).fetchall()
        directory_lookup = {row["id"]: dict(row) for row in all_directory_rows}
        directory_rows = connection.execute(
            """
            SELECT id, name, parent_id, created_at, updated_at, deleted_at, trash_group_id
            FROM directories
            WHERE is_deleted = 1
            ORDER BY deleted_at DESC, name COLLATE NOCASE ASC, id ASC
            """
        ).fetchall()
        note_rows = connection.execute(
            """
            SELECT id, title, main_category, sub_category, sub_sub_category, tags,
                   ai_visibility, is_pinned, created_at, updated_at, deleted_at,
                   trash_group_id
            FROM notes
            WHERE is_deleted = 1
            ORDER BY updated_at DESC, deleted_at DESC, id DESC
            """
        ).fetchall()
    directories = [dict(row) for row in directory_rows]
    for directory in directories:
        path = _directory_path_from_lookup(directory_lookup, directory["id"])
        directory["path"] = path
        directory["source_path"] = path[:-1]
    notes = [dict(row) for row in note_rows]
    for note in notes:
        note["path"] = [
            note["main_category"],
            note["sub_category"],
            note["sub_sub_category"],
        ]
    return {
        "directories": directories,
        "notes": notes,
    }


def _directory_path_from_lookup(
    directory_lookup: dict[int, dict], directory_id: int
) -> list[str]:
    names = []
    current_id = directory_id
    while current_id is not None and current_id in directory_lookup:
        row = directory_lookup[current_id]
        names.append(row["name"])
        current_id = row["parent_id"]
    return list(reversed(names))


def set_note_pinned(
    note_id: int, pinned: bool, database_path: Path = DATABASE_PATH
) -> dict:
    timestamp = now_shanghai_iso()
    with _connect(database_path) as connection:
        connection.execute(
            """
            UPDATE notes
            SET is_pinned = ?, updated_at = ?
            WHERE id = ? AND is_deleted = 0
            """,
            (1 if pinned else 0, timestamp, note_id),
        )
        if connection.total_changes == 0:
            raise ValueError(f"笔记不存在或已删除: {note_id}")
    note = get_note(note_id, database_path)
    if note is None:
        raise RuntimeError(f"固定状态更新后读取失败: {note_id}")
    return note


def set_note_ai_visibility(
    note_id: int, visibility: str, database_path: Path = DATABASE_PATH
) -> dict:
    clean_visibility = validate_ai_visibility(visibility)
    timestamp = now_shanghai_iso()
    with _connect(database_path) as connection:
        connection.execute(
            """
            UPDATE notes
            SET ai_visibility = ?, updated_at = ?
            WHERE id = ? AND is_deleted = 0
            """,
            (clean_visibility, timestamp, note_id),
        )
        if connection.total_changes == 0:
            raise ValueError(f"笔记不存在或已删除: {note_id}")
    note = get_note(note_id, database_path)
    if note is None:
        raise RuntimeError(f"AI 可见性更新后读取失败: {note_id}")
    return note


def set_note_ai_targets(
    note_id: int, targets: list[str], database_path: Path = DATABASE_PATH
) -> list[str]:
    normalized_target_ids = normalize_target_ids(targets)
    timestamp = now_shanghai_iso()
    with _connect(database_path) as connection:
        note_exists = connection.execute(
            "SELECT id FROM notes WHERE id = ? AND is_deleted = 0",
            (note_id,),
        ).fetchone()
        if note_exists is None:
            raise ValueError(f"笔记不存在或已删除: {note_id}")
        _set_note_ai_targets_with_connection(
            connection, note_id, normalized_target_ids, timestamp
        )
    return [AI_TARGET_DISPLAY_BY_ID[target_id] for target_id in normalized_target_ids]


def _set_note_ai_targets_with_connection(
    connection: sqlite3.Connection,
    note_id: int,
    normalized_target_ids: list[str],
    timestamp: str,
) -> None:
    connection.execute("DELETE FROM note_ai_targets WHERE note_id = ?", (note_id,))
    for target_id in normalized_target_ids:
        connection.execute(
            """
            INSERT OR IGNORE INTO note_ai_targets (note_id, target_id, created_at)
            VALUES (?, ?, ?)
            """,
            (note_id, target_id, timestamp),
        )
    connection.execute(
        "UPDATE notes SET updated_at = ? WHERE id = ?",
        (timestamp, note_id),
    )


def get_note_ai_targets(
    note_id: int, database_path: Path = DATABASE_PATH
) -> list[str]:
    with _connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT target_id
            FROM note_ai_targets
            WHERE note_id = ?
            ORDER BY target_id COLLATE NOCASE ASC
            """,
            (note_id,),
        ).fetchall()
    target_set = {row["target_id"] for row in rows}
    return [
        item.display_name
        for item in AI_TARGET_DEFINITIONS
        if item.target_id in target_set
    ]


def create_ai_receipt_inbox_item(
    receipt: dict, database_path: Path = DATABASE_PATH
) -> dict:
    timestamp = now_shanghai_iso()
    with _connect(database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO ai_receipt_inbox (
                raw_text, parse_status, status, source, action, title,
                main_category, sub_category, sub_sub_category, tags,
                ai_visibility, ai_targets, body, created_at, updated_at,
                parse_error, request_id, actor_target_id, operation_type,
                target_note_id, expected_updated_at, operation_payload,
                result_status
            )
            VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                receipt.get("raw_text", ""),
                receipt.get("parse_status", "failed"),
                receipt.get("source", "未知"),
                receipt.get("action", "新增"),
                receipt.get("title", ""),
                receipt.get("main_category", DEFAULT_MAIN_CATEGORY),
                receipt.get("sub_category", DEFAULT_SUB_CATEGORY),
                receipt.get("sub_sub_category", DEFAULT_SUB_SUB_CATEGORY),
                receipt.get("tags", ""),
                receipt.get("ai_visibility", DEFAULT_AI_VISIBILITY),
                receipt.get("ai_targets", ""),
                receipt.get("body", ""),
                timestamp,
                timestamp,
                receipt.get("parse_error", ""),
                receipt.get("request_id"),
                receipt.get("actor_target_id"),
                receipt.get("operation_type"),
                receipt.get("target_note_id"),
                receipt.get("expected_updated_at"),
                receipt.get("operation_payload", ""),
                receipt.get("result_status"),
            ),
        )
        inbox_id = cursor.lastrowid
    inbox_item = get_ai_receipt_inbox_item(inbox_id, database_path)
    if inbox_item is None:
        raise RuntimeError("导入回执后读取失败")
    return inbox_item


def list_ai_receipt_inbox_items(
    status: str | None = "pending",
    database_path: Path = DATABASE_PATH,
) -> list[dict]:
    status_clause = ""
    params: tuple[str, ...] = ()
    if status is not None:
        status_clause = "WHERE status = ?"
        params = (status,)
    with _connect(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT id, parse_status, status, source, action, title,
                   main_category, sub_category, sub_sub_category, tags,
                   ai_visibility, ai_targets, note_id, created_at,
                   updated_at, decided_at, parse_error, request_id,
                   actor_target_id, operation_type, target_note_id,
                   expected_updated_at, result_status
            FROM ai_receipt_inbox
            {status_clause}
            ORDER BY
                CASE status
                    WHEN 'pending' THEN 0
                    WHEN 'accepted' THEN 1
                    WHEN 'rejected' THEN 2
                    ELSE 3
                END,
                updated_at DESC,
                id DESC
            """,
            params,
        ).fetchall()
    return [dict(row) for row in rows]


def get_ai_receipt_inbox_item(
    inbox_id: int, database_path: Path = DATABASE_PATH
) -> dict | None:
    with _connect(database_path) as connection:
        row = connection.execute(
            """
            SELECT id, raw_text, parse_status, status, source, action, title,
                   main_category, sub_category, sub_sub_category, tags,
                   ai_visibility, ai_targets, body, note_id, created_at,
                   updated_at, decided_at, parse_error, request_id,
                   actor_target_id, operation_type, target_note_id,
                   expected_updated_at, operation_payload, result_status
            FROM ai_receipt_inbox
            WHERE id = ?
            """,
            (inbox_id,),
        ).fetchone()
    return dict(row) if row else None


def update_ai_receipt_inbox_item(
    inbox_id: int, fields: dict, database_path: Path = DATABASE_PATH
) -> dict:
    current = get_ai_receipt_inbox_item(inbox_id, database_path)
    if current is None:
        raise ValueError(f"AI 回执不存在: {inbox_id}")
    if current["status"] != "pending":
        raise ValueError("该回执已处理，不能继续修改。")

    timestamp = now_shanghai_iso()
    allowed_fields = (
        "source",
        "action",
        "title",
        "main_category",
        "sub_category",
        "sub_sub_category",
        "tags",
        "ai_visibility",
        "ai_targets",
        "body",
    )
    values = {key: fields.get(key, current[key]) for key in allowed_fields}
    values["ai_visibility"] = validate_ai_visibility(values["ai_visibility"])
    values["ai_targets"] = ", ".join(
        normalize_ai_targets(_split_ai_target_text(values["ai_targets"]))
    )
    with _connect(database_path) as connection:
        connection.execute(
            """
            UPDATE ai_receipt_inbox
            SET source = ?, action = ?, title = ?, main_category = ?,
                sub_category = ?, sub_sub_category = ?, tags = ?,
                ai_visibility = ?, ai_targets = ?, body = ?, updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (
                values["source"],
                values["action"],
                values["title"],
                values["main_category"],
                values["sub_category"],
                values["sub_sub_category"],
                values["tags"],
                values["ai_visibility"],
                values["ai_targets"],
                values["body"],
                timestamp,
                inbox_id,
            ),
        )
        if connection.total_changes == 0:
            raise ValueError("该回执已处理，不能继续修改。")
    updated = get_ai_receipt_inbox_item(inbox_id, database_path)
    if updated is None:
        raise RuntimeError("保存回执后读取失败")
    return updated


def accept_ai_receipt_inbox_item(
    inbox_id: int,
    fields: dict,
    database_path: Path = DATABASE_PATH,
    *,
    allow_failed_parse: bool = False,
) -> dict:
    with _connect(database_path) as connection:
        receipt = connection.execute(
            "SELECT * FROM ai_receipt_inbox WHERE id = ?",
            (inbox_id,),
        ).fetchone()
        if receipt is None:
            raise ValueError(f"AI 回执不存在: {inbox_id}")
        if receipt["status"] != "pending":
            raise ValueError("该回执已处理，不能重复入库。")
        if receipt["parse_status"] != "parsed" and not allow_failed_parse:
            raise ValueError("该回执解析失败，需要人工确认后才能入库。")

        allowed_fields = (
            "source",
            "action",
            "title",
            "main_category",
            "sub_category",
            "sub_sub_category",
            "tags",
            "ai_visibility",
            "ai_targets",
            "body",
        )
        values = {
            key: str(fields.get(key, receipt[key]) or "") for key in allowed_fields
        }
        values["ai_visibility"] = validate_ai_visibility(values["ai_visibility"])
        normalized_target_ids = normalize_target_ids(
            _split_ai_target_text(values["ai_targets"])
        )
        normalized_tags = _normalize_note_tags(parse_tags(values["tags"]))
        values["ai_targets"] = ", ".join(
            AI_TARGET_DISPLAY_BY_ID[target_id] for target_id in normalized_target_ids
        )
        values["tags"] = ", ".join(normalized_tags)

        if receipt["parse_status"] != "parsed" and allow_failed_parse:
            if not values["title"].strip():
                raise ValueError("人工确认入库前，标题不能为空。")
            if not values["main_category"].strip():
                raise ValueError("人工确认入库前，主分类不能为空。")

        main = values["main_category"].strip() or DEFAULT_MAIN_CATEGORY
        sub = values["sub_category"].strip() or DEFAULT_SUB_CATEGORY
        sub_sub = values["sub_sub_category"].strip() or DEFAULT_SUB_SUB_CATEGORY
        title = values["title"].strip() or "未命名 AI 回执"
        timestamp = now_shanghai_iso()

        update_cursor = connection.execute(
            """
            UPDATE ai_receipt_inbox
            SET source = ?, action = ?, title = ?, main_category = ?,
                sub_category = ?, sub_sub_category = ?, tags = ?,
                ai_visibility = ?, ai_targets = ?, body = ?, updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (
                values["source"],
                values["action"],
                title,
                main,
                sub,
                sub_sub,
                values["tags"],
                values["ai_visibility"],
                values["ai_targets"],
                values["body"],
                timestamp,
                inbox_id,
            ),
        )
        if update_cursor.rowcount != 1:
            raise ValueError("该回执已处理，不能重复入库。")

        directory_id = _ensure_directory_path(connection, main, sub, sub_sub)
        content_html = _plain_text_to_html(values["body"])
        note_cursor = connection.execute(
            """
            INSERT INTO notes (
                directory_id, title, body_rich_text, content_html,
                main_category, sub_category, sub_sub_category, tags,
                ai_visibility, is_pinned, is_deleted, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, 0, 0, ?, ?)
            """,
            (
                directory_id,
                title,
                content_html,
                content_html,
                main,
                sub,
                sub_sub,
                values["ai_visibility"],
                timestamp,
                timestamp,
            ),
        )
        note_id = note_cursor.lastrowid
        _set_note_tags_with_connection(
            connection, note_id, normalized_tags, timestamp
        )
        _set_note_ai_targets_with_connection(
            connection, note_id, normalized_target_ids, timestamp
        )

        accepted_cursor = connection.execute(
            """
            UPDATE ai_receipt_inbox
            SET status = 'accepted', note_id = ?, updated_at = ?, decided_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (note_id, timestamp, timestamp, inbox_id),
        )
        if accepted_cursor.rowcount != 1:
            raise ValueError("该回执已处理，不能重复入库。")

    receipt_result = get_ai_receipt_inbox_item(inbox_id, database_path)
    note_result = get_note(note_id, database_path)
    if receipt_result is None or note_result is None:
        raise RuntimeError("确认回执入库后读取失败")
    return {
        "receipt": receipt_result,
        "note": note_result,
    }


def reject_ai_receipt_inbox_item(
    inbox_id: int, database_path: Path = DATABASE_PATH
) -> dict:
    current = get_ai_receipt_inbox_item(inbox_id, database_path)
    if current is None:
        raise ValueError(f"AI 回执不存在: {inbox_id}")
    if current["status"] != "pending":
        raise ValueError("该回执已处理，不能重复拒绝。")
    timestamp = now_shanghai_iso()
    with _connect(database_path) as connection:
        connection.execute(
            """
            UPDATE ai_receipt_inbox
            SET status = 'rejected', updated_at = ?, decided_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (timestamp, timestamp, inbox_id),
        )
        if connection.total_changes == 0:
            raise ValueError("该回执已处理，不能重复拒绝。")
    receipt = get_ai_receipt_inbox_item(inbox_id, database_path)
    if receipt is None:
        raise RuntimeError("拒绝回执后读取失败")
    return receipt


def delete_ai_receipt_inbox_item(
    inbox_id: int, database_path: Path = DATABASE_PATH
) -> None:
    current = get_ai_receipt_inbox_item(inbox_id, database_path)
    if current is None:
        raise ValueError(f"AI 回执不存在: {inbox_id}")
    if current["status"] == "accepted" and current.get("note_id"):
        raise ValueError("已确认入库的回执保留用于查账，不能在这里删除。")
    with _connect(database_path) as connection:
        connection.execute("DELETE FROM ai_receipt_inbox WHERE id = ?", (inbox_id,))
        if connection.total_changes == 0:
            raise ValueError(f"AI 回执不存在: {inbox_id}")


def _plain_text_to_html(value: str) -> str:
    escaped = html.escape(value or "")
    return "<p>" + escaped.replace("\n", "<br>") + "</p>"


def _split_ai_target_text(value: str) -> list[str]:
    if not value.strip() or value.strip().lower() in {"无", "none", "null"}:
        return []
    normalized = value.replace("，", ",").replace("、", ",")
    parts: list[str] = []
    for chunk in normalized.split(","):
        parts.extend(part for part in chunk.split() if part)
    return parts


def set_note_tags(
    note_id: int, tags: list[str], database_path: Path = DATABASE_PATH
) -> list[str]:
    normalized_tags = _normalize_note_tags(tags)
    timestamp = now_shanghai_iso()
    with _connect(database_path) as connection:
        note_exists = connection.execute(
            "SELECT id FROM notes WHERE id = ? AND is_deleted = 0",
            (note_id,),
        ).fetchone()
        if note_exists is None:
            raise ValueError(f"笔记不存在或已删除: {note_id}")
        _set_note_tags_with_connection(
            connection, note_id, normalized_tags, timestamp
        )
    return normalized_tags


def _normalize_note_tags(tags: list[str]) -> list[str]:
    normalized_tags: list[str] = []
    seen = set()
    for raw_tag in tags:
        tag = normalize_tag(raw_tag)
        if tag and tag not in seen:
            normalized_tags.append(tag)
            seen.add(tag)
    return normalized_tags


def _set_note_tags_with_connection(
    connection: sqlite3.Connection,
    note_id: int,
    normalized_tags: list[str],
    timestamp: str,
) -> None:
    connection.execute("DELETE FROM note_tags WHERE note_id = ?", (note_id,))
    for tag in normalized_tags:
        connection.execute(
            "INSERT OR IGNORE INTO tags (name, created_at) VALUES (?, ?)",
            (tag, timestamp),
        )
        tag_id = connection.execute(
            "SELECT id FROM tags WHERE name = ?",
            (tag,),
        ).fetchone()["id"]
        connection.execute(
            """
            INSERT OR IGNORE INTO note_tags (note_id, tag_id)
            VALUES (?, ?)
            """,
            (note_id, tag_id),
        )
    connection.execute(
        "UPDATE notes SET tags = ?, updated_at = ? WHERE id = ?",
        (", ".join(normalized_tags), timestamp, note_id),
    )


def get_note_tags(note_id: int, database_path: Path = DATABASE_PATH) -> list[str]:
    with _connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT tags.name
            FROM tags
            JOIN note_tags ON note_tags.tag_id = tags.id
            WHERE note_tags.note_id = ?
            ORDER BY tags.name COLLATE NOCASE ASC
            """,
            (note_id,),
        ).fetchall()
    return [row["name"] for row in rows]


def list_recent_or_common_tags(
    limit: int = 12, database_path: Path = DATABASE_PATH
) -> list[dict]:
    with _connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT tags.name, COUNT(note_tags.note_id) AS note_count, MAX(notes.updated_at) AS last_used
            FROM tags
            JOIN note_tags ON note_tags.tag_id = tags.id
            JOIN notes ON notes.id = note_tags.note_id
            WHERE notes.is_deleted = 0
            GROUP BY tags.id, tags.name
            ORDER BY note_count DESC, last_used DESC, tags.name COLLATE NOCASE ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_notes_by_tag(tag: str, database_path: Path = DATABASE_PATH) -> list[dict]:
    normalized_tag = normalize_tag(tag)
    with _connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT notes.id, notes.title, notes.main_category, notes.sub_category,
                   notes.sub_sub_category, notes.tags, notes.ai_visibility,
                   notes.is_pinned, notes.created_at, notes.updated_at
            FROM notes
            JOIN note_tags ON note_tags.note_id = notes.id
            JOIN tags ON tags.id = note_tags.tag_id
            WHERE notes.is_deleted = 0 AND tags.name = ?
            ORDER BY notes.updated_at DESC, notes.created_at DESC, notes.id DESC
            """,
            (normalized_tag,),
        ).fetchall()
    return [dict(row) for row in rows]


def search_notes(query: str, database_path: Path = DATABASE_PATH) -> list[dict]:
    term = query.strip()
    if not term:
        return []
    pattern = f"%{term}%"
    with _connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT notes.id, notes.title, notes.main_category, notes.sub_category,
                   notes.sub_sub_category, notes.tags, notes.ai_visibility,
                   notes.is_pinned, notes.created_at, notes.updated_at
            FROM notes
            LEFT JOIN note_tags ON note_tags.note_id = notes.id
            LEFT JOIN tags ON tags.id = note_tags.tag_id
            WHERE notes.is_deleted = 0
              AND (
                notes.title LIKE ?
                OR notes.content_html LIKE ?
                OR notes.main_category LIKE ?
                OR notes.sub_category LIKE ?
                OR notes.sub_sub_category LIKE ?
                OR notes.tags LIKE ?
                OR tags.name LIKE ?
              )
            ORDER BY notes.updated_at DESC, notes.created_at DESC, notes.id DESC
            """,
            (pattern, pattern, pattern, pattern, pattern, pattern, pattern),
        ).fetchall()
    return [dict(row) for row in rows]


def list_pinned_notes(database_path: Path = DATABASE_PATH) -> list[dict]:
    return _list_notes_where("is_deleted = 0 AND is_pinned = 1", database_path)


def list_recent_notes(
    limit: int = 10, database_path: Path = DATABASE_PATH
) -> list[dict]:
    with _connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, title, main_category, sub_category, sub_sub_category, tags,
                   ai_visibility, is_pinned, created_at, updated_at
            FROM notes
            WHERE is_deleted = 0
            ORDER BY updated_at DESC, created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_todo_notes(database_path: Path = DATABASE_PATH) -> list[dict]:
    with _connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT notes.id, notes.title, notes.main_category, notes.sub_category,
                   notes.sub_sub_category, notes.tags, notes.ai_visibility,
                   notes.is_pinned, notes.created_at, notes.updated_at
            FROM notes
            LEFT JOIN note_tags ON note_tags.note_id = notes.id
            LEFT JOIN tags ON tags.id = note_tags.tag_id
            WHERE notes.is_deleted = 0
              AND (
                notes.main_category = ?
                OR tags.name IN ('todo', '#todo')
                OR notes.tags LIKE '%todo%'
              )
            ORDER BY notes.updated_at DESC, notes.created_at DESC, notes.id DESC
            """,
            ("待办",),
        ).fetchall()
    return [dict(row) for row in rows]


def list_ai_visible_notes(database_path: Path = DATABASE_PATH) -> list[dict]:
    with _connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, title, main_category, sub_category, sub_sub_category, tags,
                   ai_visibility, is_pinned, created_at, updated_at
            FROM notes
            WHERE is_deleted = 0
              AND ai_visibility IN ('full', 'redacted', '可直接给 AI', '脱敏后给 AI', 'AI完整可见', 'AI脱敏后可见')
            ORDER BY updated_at DESC, created_at DESC, id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def list_ai_safe_notes_for_target(
    target_name: str, database_path: Path = DATABASE_PATH
) -> list[dict]:
    target = normalize_target_ids([target_name])[0]
    target_filter = [AI_TARGET_COMMON_ID] if target == AI_TARGET_COMMON_ID else [
        AI_TARGET_COMMON_ID,
        target,
    ]
    placeholders = ",".join("?" for _ in target_filter)
    with _connect(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT DISTINCT
                notes.id,
                notes.title,
                notes.content_html,
                notes.main_category,
                notes.sub_category,
                notes.sub_sub_category,
                notes.tags,
                notes.ai_visibility,
                notes.is_pinned,
                notes.created_at,
                notes.updated_at
            FROM notes
            JOIN note_ai_targets ON note_ai_targets.note_id = notes.id
            WHERE notes.is_deleted = 0
              AND notes.ai_visibility IN ('full', 'redacted', '可直接给 AI', '脱敏后给 AI', 'AI完整可见', 'AI脱敏后可见')
              AND note_ai_targets.target_id IN ({placeholders})
            ORDER BY notes.updated_at DESC, notes.created_at DESC, notes.id DESC
            """,
            tuple(target_filter),
        ).fetchall()
    return [dict(row) for row in rows]


def list_deleted_notes(database_path: Path = DATABASE_PATH) -> list[dict]:
    with _connect(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, title, main_category, sub_category, sub_sub_category, tags,
                   ai_visibility, is_pinned, created_at, updated_at, deleted_at
            FROM notes
            WHERE is_deleted = 1
            ORDER BY updated_at DESC, deleted_at DESC, id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _list_notes_where(where_clause: str, database_path: Path) -> list[dict]:
    with _connect(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT id, title, main_category, sub_category, sub_sub_category, tags,
                   ai_visibility, is_pinned, created_at, updated_at
            FROM notes
            WHERE {where_clause}
            ORDER BY updated_at DESC, created_at DESC, id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def update_note_directory(
    note_id: int,
    main_category: str,
    sub_category: str,
    sub_sub_category: str,
    database_path: Path = DATABASE_PATH,
) -> dict:
    note = get_note(note_id, database_path)
    if note is None:
        raise ValueError(f"笔记不存在或已删除: {note_id}")
    return update_note(
        note_id,
        title=note["title"],
        content_html=note["content_html"],
        main_category=main_category,
        sub_category=sub_category,
        sub_sub_category=sub_sub_category,
        database_path=database_path,
    )


def _directory_depth(connection: sqlite3.Connection, directory_id: int) -> int:
    return len(_get_directory_path(connection, directory_id))


def _rename_notes_for_directory_path(
    connection: sqlite3.Connection,
    old_path: tuple[str, ...],
    new_name: str,
    timestamp: str,
) -> None:
    if len(old_path) == 1:
        connection.execute(
            """
            UPDATE notes
            SET main_category = ?, updated_at = ?
            WHERE is_deleted = 0 AND main_category = ?
            """,
            (new_name, timestamp, old_path[0]),
        )
    elif len(old_path) == 2:
        connection.execute(
            """
            UPDATE notes
            SET sub_category = ?, updated_at = ?
            WHERE is_deleted = 0 AND main_category = ? AND sub_category = ?
            """,
            (new_name, timestamp, old_path[0], old_path[1]),
        )
    elif len(old_path) == 3:
        connection.execute(
            """
            UPDATE notes
            SET sub_sub_category = ?, updated_at = ?
            WHERE is_deleted = 0
              AND main_category = ?
              AND sub_category = ?
              AND sub_sub_category = ?
            """,
            (new_name, timestamp, old_path[0], old_path[1], old_path[2]),
        )


def _soft_delete_notes_under_path(
    connection: sqlite3.Connection,
    path: tuple[str, ...],
    timestamp: str,
    trash_group_id: str,
) -> None:
    if len(path) == 1:
        where_clause = "main_category = ?"
        values = (path[0],)
    elif len(path) == 2:
        where_clause = "main_category = ? AND sub_category = ?"
        values = (path[0], path[1])
    elif len(path) == 3:
        where_clause = "main_category = ? AND sub_category = ? AND sub_sub_category = ?"
        values = (path[0], path[1], path[2])
    else:
        raise ValueError("目录路径无效")

    connection.execute(
        f"""
        UPDATE notes
        SET directory_id = NULL,
            is_deleted = 1,
            deleted_at = ?,
            updated_at = ?,
            trash_group_id = ?
        WHERE {where_clause}
        """,
        (
            timestamp,
            timestamp,
            trash_group_id,
            *values,
        ),
    )


def _restore_notes_under_path(
    connection: sqlite3.Connection,
    path: tuple[str, ...],
    timestamp: str,
    trash_group_ids: set[str],
) -> None:
    if not trash_group_ids:
        return
    if len(path) == 1:
        where_clause = "main_category = ?"
        values = (path[0],)
        sub = DEFAULT_SUB_CATEGORY
        sub_sub = DEFAULT_SUB_SUB_CATEGORY
    elif len(path) == 2:
        where_clause = "main_category = ? AND sub_category = ?"
        values = (path[0], path[1])
        sub = path[1]
        sub_sub = DEFAULT_SUB_SUB_CATEGORY
    elif len(path) == 3:
        where_clause = "main_category = ? AND sub_category = ? AND sub_sub_category = ?"
        values = (path[0], path[1], path[2])
        sub = path[1]
        sub_sub = path[2]
    else:
        raise ValueError("目录路径无效")
    directory_id = _ensure_directory_path(connection, path[0], sub, sub_sub)
    connection.execute(
        f"""
        UPDATE notes
        SET directory_id = ?,
            is_deleted = 0,
            deleted_at = NULL,
            trash_group_id = NULL,
            updated_at = ?
        WHERE {where_clause}
          AND trash_group_id IN ({",".join("?" for _ in trash_group_ids)})
        """,
        (directory_id, timestamp, *values, *trash_group_ids),
    )


def _permanently_delete_notes_under_path(
    connection: sqlite3.Connection, path: tuple[str, ...], trash_group_ids: set[str]
) -> None:
    if not trash_group_ids:
        return
    if len(path) == 1:
        where_clause = "main_category = ?"
        values = (path[0],)
    elif len(path) == 2:
        where_clause = "main_category = ? AND sub_category = ?"
        values = (path[0], path[1])
    elif len(path) == 3:
        where_clause = "main_category = ? AND sub_category = ? AND sub_sub_category = ?"
        values = (path[0], path[1], path[2])
    else:
        raise ValueError("目录路径无效")
    connection.execute(
        f"""
        DELETE FROM notes
        WHERE is_deleted = 1
          AND {where_clause}
          AND trash_group_id IN ({",".join("?" for _ in trash_group_ids)})
        """,
        (*values, *trash_group_ids),
    )


def _ensure_active_parent_path_for_restore(
    connection: sqlite3.Connection, path: tuple[str, ...]
) -> int | None:
    if len(path) <= 1:
        return None
    parent_path = path[:-1]
    if len(parent_path) == 1:
        return _ensure_directory(connection, parent_path[0], None)
    if len(parent_path) == 2:
        main_id = _ensure_directory(connection, parent_path[0], None)
        return _ensure_directory(connection, parent_path[1], main_id)
    main_id = _ensure_directory(connection, parent_path[0], None)
    sub_id = _ensure_directory(connection, parent_path[1], main_id)
    return _ensure_directory(connection, parent_path[2], sub_id)


def _restore_trash_directories_with_connection(
    connection: sqlite3.Connection,
    directory_ids: list[int],
    timestamp: str,
) -> None:
    root_ids = list(dict.fromkeys(int(directory_id) for directory_id in directory_ids))
    if not root_ids:
        return

    restore_ids: set[int] = set()
    root_parent_targets: dict[int, int | None] = {}
    paths_by_root: dict[int, list[tuple[str, ...]]] = {}
    trash_groups_by_root: dict[int, set[str]] = {}
    rows_by_id: dict[int, sqlite3.Row] = {}

    for root_id in root_ids:
        root = connection.execute(
            """
            SELECT id, name, parent_id, is_deleted
            FROM directories
            WHERE id = ? AND is_deleted = 1
            """,
            (root_id,),
        ).fetchone()
        if root is None:
            raise ValueError(f"回收站中找不到目录: {root_id}")
        root_path = _get_directory_path(connection, root_id)
        root_parent_targets[root_id] = _ensure_active_parent_path_for_restore(
            connection, root_path
        )
        tree_ids = _collect_descendant_directory_ids(connection, root_id)
        restore_ids.update(tree_ids)
        paths_by_root[root_id] = [
            _get_directory_path(connection, target_id) for target_id in tree_ids
        ]
        trash_groups_by_root[root_id] = _trash_group_ids_for_directories(
            connection, tree_ids
        )

    placeholders = ",".join("?" for _ in restore_ids)
    rows = connection.execute(
        f"""
        SELECT id, name, parent_id, is_deleted
        FROM directories
        WHERE id IN ({placeholders})
        """,
        tuple(restore_ids),
    ).fetchall()
    rows_by_id = {row["id"]: row for row in rows}
    if len(rows_by_id) != len(restore_ids) or any(
        row["is_deleted"] != 1 for row in rows_by_id.values()
    ):
        raise ValueError("待恢复的目录树状态已变更，请刷新后重试。")

    prospective_names: set[tuple[int | None, str]] = set()
    for target_id, row in rows_by_id.items():
        target_parent_id = root_parent_targets.get(target_id, row["parent_id"])
        key = (target_parent_id, row["name"])
        if key in prospective_names:
            raise ValueError(
                f'同级已存在分类“{row["name"]}”，请先重命名或删除冲突分类后再恢复。'
            )
        prospective_names.add(key)
        if target_parent_id is None:
            conflict = connection.execute(
                """
                SELECT id FROM directories
                WHERE parent_id IS NULL AND name = ? AND is_deleted = 0
                """,
                (row["name"],),
            ).fetchone()
        else:
            conflict = connection.execute(
                """
                SELECT id FROM directories
                WHERE parent_id = ? AND name = ? AND is_deleted = 0
                """,
                (target_parent_id, row["name"]),
            ).fetchone()
        if conflict is not None and conflict["id"] not in restore_ids:
            raise ValueError(
                f'同级已存在分类“{row["name"]}”，请先重命名或删除冲突分类后再恢复。'
            )

    for root_id, parent_id in root_parent_targets.items():
        connection.execute(
            "UPDATE directories SET parent_id = ? WHERE id = ? AND is_deleted = 1",
            (parent_id, root_id),
        )
    connection.executemany(
        """
        UPDATE directories
        SET is_deleted = 0, deleted_at = NULL, trash_group_id = NULL, updated_at = ?
        WHERE id = ? AND is_deleted = 1
        """,
        [(timestamp, target_id) for target_id in restore_ids],
    )
    for root_id in root_ids:
        for path in paths_by_root[root_id]:
            _restore_notes_under_path(
                connection,
                path,
                timestamp,
                trash_groups_by_root[root_id],
            )


def _trash_group_ids_for_directories(
    connection: sqlite3.Connection, directory_ids: set[int]
) -> set[str]:
    if not directory_ids:
        return set()
    placeholders = ",".join("?" for _ in directory_ids)
    rows = connection.execute(
        f"""
        SELECT DISTINCT trash_group_id
        FROM directories
        WHERE id IN ({placeholders}) AND trash_group_id IS NOT NULL
        """,
        tuple(directory_ids),
    ).fetchall()
    return {row["trash_group_id"] for row in rows}


def _collect_descendant_directory_ids(
    connection: sqlite3.Connection, directory_id: int
) -> set[int]:
    ids = {directory_id}
    rows = connection.execute(
        "SELECT id FROM directories WHERE parent_id = ?",
        (directory_id,),
    ).fetchall()
    for row in rows:
        ids.update(_collect_descendant_directory_ids(connection, row["id"]))
    return ids
