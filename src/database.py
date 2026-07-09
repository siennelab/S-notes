from __future__ import annotations

import sqlite3
from pathlib import Path

from src.time_utils import now_shanghai, now_shanghai_iso


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATABASE_PATH = PROJECT_ROOT / "data" / "s_notes.db"
DEFAULT_MAIN_CATEGORY = "临时"
DEFAULT_SUB_CATEGORY = "无"
DEFAULT_SUB_SUB_CATEGORY = "无"
DEFAULT_AI_VISIBILITY = "不导出给 AI"
INITIAL_MAIN_CATEGORIES = ("项目", "设备", "账号", "待办", "临时", "备忘", "终端指令")


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS directories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    parent_id INTEGER REFERENCES directories(id),
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
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
    ai_visibility TEXT NOT NULL DEFAULT '不导出给 AI',
    is_pinned INTEGER NOT NULL DEFAULT 0,
    is_deleted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
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

CREATE TABLE IF NOT EXISTS note_ai_targets (
    id INTEGER PRIMARY KEY,
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    target_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_receipts (
    id INTEGER PRIMARY KEY,
    note_id INTEGER REFERENCES notes(id) ON DELETE SET NULL,
    receipt_text TEXT NOT NULL,
    imported_at TEXT NOT NULL
);
"""


NOTE_COLUMNS = {
    "content_html": "TEXT NOT NULL DEFAULT ''",
    "main_category": "TEXT NOT NULL DEFAULT '临时'",
    "sub_category": "TEXT NOT NULL DEFAULT '无'",
    "sub_sub_category": "TEXT NOT NULL DEFAULT '无'",
    "tags": "TEXT NOT NULL DEFAULT ''",
    "ai_visibility": "TEXT NOT NULL DEFAULT '不导出给 AI'",
    "is_pinned": "INTEGER NOT NULL DEFAULT 0",
    "is_deleted": "INTEGER NOT NULL DEFAULT 0",
}


def _connect(database_path: Path = DATABASE_PATH) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(database_path: Path = DATABASE_PATH) -> Path:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(database_path) as connection:
        connection.executescript(SCHEMA_SQL)
        _migrate_notes_table(connection)
        _seed_initial_categories(connection)
    return database_path


def _migrate_notes_table(connection: sqlite3.Connection) -> None:
    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(notes)").fetchall()
    }
    for column_name, column_definition in NOTE_COLUMNS.items():
        if column_name not in existing_columns:
            connection.execute(
                f"ALTER TABLE notes ADD COLUMN {column_name} {column_definition}"
            )


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


def create_note(database_path: Path = DATABASE_PATH) -> dict:
    timestamp = now_shanghai_iso()
    title = f"未命名笔记 {now_shanghai().strftime('%Y-%m-%d %H:%M')}"
    with _connect(database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO notes (
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
            VALUES (?, '', '', ?, ?, ?, '', ?, 0, 0, ?, ?)
            """,
            (
                title,
                DEFAULT_MAIN_CATEGORY,
                DEFAULT_SUB_CATEGORY,
                DEFAULT_SUB_SUB_CATEGORY,
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


def get_note(note_id: int, database_path: Path = DATABASE_PATH) -> dict | None:
    with _connect(database_path) as connection:
        row = connection.execute(
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
                is_deleted,
                created_at,
                updated_at
            FROM notes
            WHERE id = ? AND is_deleted = 0
            """,
            (note_id,),
        ).fetchone()
    return dict(row) if row else None


def update_note(
    note_id: int,
    *,
    title: str,
    content_html: str,
    database_path: Path = DATABASE_PATH,
) -> dict:
    timestamp = now_shanghai_iso()
    with _connect(database_path) as connection:
        connection.execute(
            """
            UPDATE notes
            SET title = ?,
                body_rich_text = ?,
                content_html = ?,
                updated_at = ?
            WHERE id = ? AND is_deleted = 0
            """,
            (title, content_html, content_html, timestamp, note_id),
        )
        changed = connection.total_changes
    if changed == 0:
        raise ValueError(f"笔记不存在或已删除: {note_id}")
    note = get_note(note_id, database_path)
    if note is None:
        raise RuntimeError(f"保存后读取失败: {note_id}")
    return note
