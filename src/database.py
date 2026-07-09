from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from src.tag_manager import normalize_tag
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
    ai_visibility TEXT NOT NULL DEFAULT '不导出给 AI',
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
    "trash_group_id": "TEXT",
}

DIRECTORY_COLUMNS = {
    "is_deleted": "INTEGER NOT NULL DEFAULT 0",
    "deleted_at": "TEXT",
    "trash_group_id": "TEXT",
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
        _migrate_directories_table(connection)
        _seed_initial_categories(connection)
        _ensure_directory_paths_for_notes(connection)
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


def _migrate_directories_table(connection: sqlite3.Connection) -> None:
    existing_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(directories)").fetchall()
    }
    for column_name, column_definition in DIRECTORY_COLUMNS.items():
        if column_name not in existing_columns:
            connection.execute(
                f"ALTER TABLE directories ADD COLUMN {column_name} {column_definition}"
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
        root_path = _get_directory_path(connection, directory_id)
        active_parent_id = _ensure_active_parent_path_for_restore(connection, root_path)
        directory_ids = _collect_descendant_directory_ids(connection, directory_id)
        paths = [_get_directory_path(connection, target_id) for target_id in directory_ids]
        trash_group_ids = _trash_group_ids_for_directories(connection, directory_ids)
        connection.execute(
            "UPDATE directories SET parent_id = ? WHERE id = ?",
            (active_parent_id, directory_id),
        )
        connection.executemany(
            """
            UPDATE directories
            SET is_deleted = 0, deleted_at = NULL, trash_group_id = NULL, updated_at = ?
            WHERE id = ?
            """,
            [(timestamp, target_id) for target_id in directory_ids],
        )
        for path in paths:
            _restore_notes_under_path(connection, path, timestamp, trash_group_ids)


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


def set_note_tags(
    note_id: int, tags: list[str], database_path: Path = DATABASE_PATH
) -> list[str]:
    normalized_tags = []
    seen = set()
    for raw_tag in tags:
        tag = normalize_tag(raw_tag)
        if tag and tag not in seen:
            normalized_tags.append(tag)
            seen.add(tag)

    timestamp = now_shanghai_iso()
    with _connect(database_path) as connection:
        note_exists = connection.execute(
            "SELECT id FROM notes WHERE id = ? AND is_deleted = 0",
            (note_id,),
        ).fetchone()
        if note_exists is None:
            raise ValueError(f"笔记不存在或已删除: {note_id}")

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
    return normalized_tags


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
            WHERE is_deleted = 0 AND ai_visibility != ?
            ORDER BY updated_at DESC, created_at DESC, id DESC
            """,
            (DEFAULT_AI_VISIBILITY,),
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
