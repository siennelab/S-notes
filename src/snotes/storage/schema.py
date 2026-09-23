"""Current R2 schema and targeted migrations for explicit database paths."""

from __future__ import annotations

from pathlib import Path
from contextlib import closing

from src.snotes.domain.reminders import DEFAULT_REMINDER_LABEL_NAME
from src.snotes.storage.connection import SQLiteConnectionFactory, require_markdown_database
import sqlite3
from src.snotes.storage.support import timestamp_seconds


INITIAL_MAIN_CATEGORIES = ("项目", "设备", "账号", "待办", "临时", "备忘", "终端指令")
CATEGORY_SCHEMA_VERSION_KEY = "category_schema_version"
CATEGORY_SCHEMA_VERSION = "2"
REMINDER_SCHEMA_VERSION_KEY = "reminder_schema_version"
REMINDER_SCHEMA_VERSION = "4"

SCHEMA_STATEMENTS = (
    """
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
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS notes (
        id INTEGER PRIMARY KEY,
        note_uuid TEXT,
        revision INTEGER NOT NULL DEFAULT 1,
        directory_id INTEGER REFERENCES directories(id),
        title TEXT NOT NULL DEFAULT '',
        content_markdown TEXT NOT NULL DEFAULT '',
        main_category TEXT NOT NULL DEFAULT '临时',
        sub_category TEXT NOT NULL DEFAULT '无',
        tags TEXT NOT NULL DEFAULT '',
        ai_visibility TEXT NOT NULL DEFAULT 'hidden',
        is_pinned INTEGER NOT NULL DEFAULT 0,
        is_deleted INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT,
        trash_group_id TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tags (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS note_tags (
        note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
        tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
        PRIMARY KEY (note_id, tag_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_targets (
        target_id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL UNIQUE,
        slug TEXT NOT NULL UNIQUE,
        is_system INTEGER NOT NULL DEFAULT 0,
        is_enabled INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS note_ai_targets (
        id INTEGER PRIMARY KEY,
        note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
        target_id TEXT NOT NULL REFERENCES ai_targets(target_id),
        created_at TEXT NOT NULL,
        UNIQUE(note_id, target_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memory_receipts (
        id INTEGER PRIMARY KEY,
        note_id INTEGER REFERENCES notes(id) ON DELETE SET NULL,
        receipt_text TEXT NOT NULL,
        imported_at TEXT NOT NULL
    )
    """,
    """
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
        result_status TEXT,
        external_submission_id TEXT,
        idempotency_key TEXT,
        accepted_note_uuid TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS app_settings (
        setting_key TEXT PRIMARY KEY,
        setting_value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
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
    )
    """,
    """
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
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_operations (
        request_id TEXT PRIMARY KEY,
        actor_target_id TEXT NOT NULL,
        operation_type TEXT NOT NULL,
        status TEXT NOT NULL,
        result_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_migration_issues (
        id INTEGER PRIMARY KEY,
        issue_type TEXT NOT NULL,
        source_value TEXT NOT NULL,
        note_id INTEGER,
        recorded_at TEXT NOT NULL,
        UNIQUE(issue_type, source_value, note_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schema_meta (
        meta_key TEXT PRIMARY KEY,
        meta_value TEXT NOT NULL
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_notes_note_uuid
    ON notes(note_uuid)
    WHERE note_uuid IS NOT NULL AND note_uuid <> ''
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_receipt_external_submission
    ON ai_receipt_inbox(source, external_submission_id)
    WHERE external_submission_id IS NOT NULL AND external_submission_id <> ''
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_receipt_idempotency_key
    ON ai_receipt_inbox(source, idempotency_key)
    WHERE idempotency_key IS NOT NULL AND idempotency_key <> ''
    """,
    """
    CREATE TRIGGER IF NOT EXISTS notes_assign_uuid_after_insert
    AFTER INSERT ON notes
    FOR EACH ROW
    WHEN NEW.note_uuid IS NULL OR trim(NEW.note_uuid) = ''
    BEGIN
        UPDATE notes
        SET note_uuid = lower(
            hex(randomblob(4)) || '-' ||
            hex(randomblob(2)) || '-4' ||
            substr(hex(randomblob(2)), 2) || '-' ||
            substr('89ab', (random() & 3) + 1, 1) ||
            substr(hex(randomblob(2)), 2) || '-' ||
            hex(randomblob(6))
        )
        WHERE id = NEW.id;
    END
    """,
)

REMINDER_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS reminder_labels (
        id INTEGER PRIMARY KEY,
        label_key TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        sort_order INTEGER NOT NULL DEFAULT 0,
        is_deleted INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY,
        table_id INTEGER REFERENCES reminder_tables(id),
        title TEXT NOT NULL,
        lifecycle TEXT NOT NULL DEFAULT 'active',
        action_spec_json TEXT NOT NULL,
        deadline_spec_json TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        completed_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reminder_tables (
        id INTEGER PRIMARY KEY,
        table_key TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        label_id INTEGER NOT NULL REFERENCES reminder_labels(id),
        ai_visible INTEGER NOT NULL DEFAULT 1,
        sort_order INTEGER NOT NULL DEFAULT 0,
        is_deleted INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT,
        trash_group_id TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reminder_occurrences (
        id INTEGER PRIMARY KEY,
        reminder_id INTEGER NOT NULL REFERENCES reminders(id) ON DELETE CASCADE,
        occurrence_key TEXT NOT NULL,
        action_date TEXT NOT NULL,
        deadline_date TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        completed_at TEXT,
        action_notified_at TEXT,
        deadline_notified_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(reminder_id, occurrence_key)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_reminder_occurrences_due
    ON reminder_occurrences(status, action_date, deadline_date)
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_reminder_labels_active_name
    ON reminder_labels(lower(trim(name)))
    WHERE is_deleted = 0
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_reminder_tables_label_visible
    ON reminder_tables(label_id, ai_visible, is_deleted)
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_reminder_operations (
        id INTEGER PRIMARY KEY,
        happened_at TEXT NOT NULL,
        actor_target_id TEXT NOT NULL,
        action TEXT NOT NULL,
        table_id INTEGER,
        table_title TEXT NOT NULL DEFAULT '',
        item_id INTEGER,
        item_title TEXT NOT NULL DEFAULT '',
        label_id INTEGER,
        label_name TEXT NOT NULL DEFAULT '',
        request_id TEXT NOT NULL,
        changed_fields_json TEXT NOT NULL DEFAULT '[]',
        before_json TEXT NOT NULL DEFAULT '{}',
        after_json TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_ai_reminder_operations_recent
    ON ai_reminder_operations(id DESC)
    """,
)


def initialize_database(database_path: Path) -> SQLiteConnectionFactory:
    """Create a Markdown database or maintain an already-marked R2 schema."""
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Inspect existing files read-only, before ANY schema writes.
    fresh = not path.exists()
    if not fresh:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as existing:
            require_markdown_database(existing)
    connections = SQLiteConnectionFactory(path)
    # Fresh initialization is the only path that may open before the marker exists.
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        if fresh:
            connection.execute("INSERT INTO schema_meta VALUES ('body_format', 'markdown')")
        _migrate_two_level_category_schema(connection)
        _migrate_reminder_schema(connection)
        count = int(connection.execute("SELECT COUNT(*) FROM directories").fetchone()[0])
        if count == 0:
            timestamp = timestamp_seconds()
            connection.executemany(
                """
                INSERT INTO directories (
                    name, parent_id, sort_order, created_at, updated_at
                ) VALUES (?, NULL, ?, ?, ?)
                """,
                [
                    (name, sort_order, timestamp, timestamp)
                    for sort_order, name in enumerate(INITIAL_MAIN_CATEGORIES)
                ],
            )
    return connections


def _migrate_reminder_schema(connection) -> None:
    for statement in REMINDER_SCHEMA_STATEMENTS:
        connection.execute(statement)
    _ensure_reminder_columns(connection)
    _ensure_default_reminder_label(connection)
    _ensure_default_reminder_table(connection)
    _ensure_ai_reminder_operation_columns(connection)
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_reminders_table_lifecycle
        ON reminders(table_id, lifecycle)
        """
    )
    connection.execute(
        """
        INSERT INTO schema_meta (meta_key, meta_value)
        VALUES (?, ?)
        ON CONFLICT(meta_key) DO UPDATE SET meta_value = excluded.meta_value
        """,
        (REMINDER_SCHEMA_VERSION_KEY, REMINDER_SCHEMA_VERSION),
    )


def _ensure_reminder_columns(connection) -> None:
    reminder_columns = {
        str(row["name"])
        for row in connection.execute('PRAGMA table_info("reminders")')
    }
    additions = {
        "table_id": "ALTER TABLE reminders ADD COLUMN table_id INTEGER REFERENCES reminder_tables(id)",
        "multi_notify": "ALTER TABLE reminders ADD COLUMN multi_notify INTEGER NOT NULL DEFAULT 0",
        # NULL retains the original schedule algorithm, including its initial anchor.
        "recurrence_mode": "ALTER TABLE reminders ADD COLUMN recurrence_mode TEXT",
    }
    for column, statement in additions.items():
        if column not in reminder_columns:
            connection.execute(statement)

    occurrence_columns = {
        str(row["name"])
        for row in connection.execute('PRAGMA table_info("reminder_occurrences")')
    }
    for days in (7, 3, 1):
        column = f"advance_{days}_notified_at"
        if column not in occurrence_columns:
            connection.execute(f"ALTER TABLE reminder_occurrences ADD COLUMN {column} TEXT")


def _ensure_default_reminder_label(connection) -> None:
    timestamp = timestamp_seconds()
    connection.execute(
        """
        INSERT INTO reminder_labels (
            label_key, name, sort_order, is_deleted, created_at, updated_at
        ) VALUES ('default', '未归类', 0, 0, ?, ?)
        ON CONFLICT(label_key) DO UPDATE SET
            is_deleted = 0,
            deleted_at = NULL,
            updated_at = excluded.updated_at
        """,
        (timestamp, timestamp),
    )
    row = connection.execute(
        "SELECT id FROM reminder_labels WHERE label_key = 'default'"
    ).fetchone()
    if row is not None:
        connection.execute(
            """
            UPDATE reminder_labels
            SET name = COALESCE(NULLIF(trim(name), ''), ?)
            WHERE id = ?
            """,
            (DEFAULT_REMINDER_LABEL_NAME, int(row["id"])),
        )


def _ensure_default_reminder_table(connection) -> None:
    timestamp = timestamp_seconds()
    label_row = connection.execute(
        "SELECT id FROM reminder_labels WHERE label_key = 'default'"
    ).fetchone()
    if label_row is None:
        raise RuntimeError("default_reminder_label_missing")
    default_label_id = int(label_row["id"])
    connection.execute(
        """
        INSERT INTO reminder_tables (
            table_key, title, label_id, ai_visible, sort_order, is_deleted,
            created_at, updated_at
        ) VALUES ('default', '原有提醒', ?, 1, 0, 0, ?, ?)
        ON CONFLICT(table_key) DO UPDATE SET
            label_id = COALESCE(reminder_tables.label_id, excluded.label_id),
            is_deleted = 0,
            deleted_at = NULL,
            trash_group_id = NULL,
            updated_at = excluded.updated_at
        """,
        (default_label_id, timestamp, timestamp),
    )
    table_row = connection.execute(
        "SELECT id FROM reminder_tables WHERE table_key = 'default'"
    ).fetchone()
    if table_row is not None:
        connection.execute(
            "UPDATE reminders SET table_id = ? WHERE table_id IS NULL",
            (int(table_row["id"]),),
        )


def _ensure_ai_reminder_operation_columns(connection) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute('PRAGMA table_info("ai_reminder_operations")')
    }
    additions = {
        "table_id": "ALTER TABLE ai_reminder_operations ADD COLUMN table_id INTEGER",
        "table_title": "ALTER TABLE ai_reminder_operations ADD COLUMN table_title TEXT NOT NULL DEFAULT ''",
        "item_id": "ALTER TABLE ai_reminder_operations ADD COLUMN item_id INTEGER",
        "item_title": "ALTER TABLE ai_reminder_operations ADD COLUMN item_title TEXT NOT NULL DEFAULT ''",
        "label_id": "ALTER TABLE ai_reminder_operations ADD COLUMN label_id INTEGER",
        "label_name": "ALTER TABLE ai_reminder_operations ADD COLUMN label_name TEXT NOT NULL DEFAULT ''",
    }
    for column, statement in additions.items():
        if column not in columns:
            connection.execute(statement)


def _migrate_two_level_category_schema(connection) -> None:
    """Drop the retired third category column without preserving its values."""
    version_row = connection.execute(
        "SELECT meta_value FROM schema_meta WHERE meta_key = ?",
        (CATEGORY_SCHEMA_VERSION_KEY,),
    ).fetchone()
    current_version = str(version_row["meta_value"]) if version_row is not None else None
    old_columns_present = False
    for table in ("notes", "ai_receipt_inbox"):
        columns = {
            str(row["name"])
            for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
        if "sub_sub_category" in columns:
            old_columns_present = True

    if old_columns_present or current_version != CATEGORY_SCHEMA_VERSION:
        _remove_empty_third_level_directories(connection)

    for table in ("notes", "ai_receipt_inbox"):
        columns = {
            str(row["name"])
            for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
        if "sub_sub_category" in columns:
            connection.execute(
                f'ALTER TABLE "{table}" DROP COLUMN sub_sub_category'
            )
    connection.execute(
        """
        INSERT INTO schema_meta (meta_key, meta_value)
        VALUES (?, ?)
        ON CONFLICT(meta_key) DO UPDATE SET meta_value = excluded.meta_value
        """,
        (CATEGORY_SCHEMA_VERSION_KEY, CATEGORY_SCHEMA_VERSION),
    )


def _remove_empty_third_level_directories(connection) -> None:
    """Delete only empty directories below the supported two-level hierarchy."""
    rows = connection.execute(
        """
        WITH RECURSIVE directory_depth(id, depth) AS (
            SELECT id, 1
            FROM directories
            WHERE parent_id IS NULL
            UNION ALL
            SELECT directories.id, directory_depth.depth + 1
            FROM directories
            JOIN directory_depth ON directories.parent_id = directory_depth.id
        )
        SELECT directory_depth.id,
               EXISTS(
                   SELECT 1 FROM notes
                   WHERE notes.directory_id = directory_depth.id
               ) AS has_notes,
               EXISTS(
                   SELECT 1 FROM directories AS children
                   WHERE children.parent_id = directory_depth.id
               ) AS has_children
        FROM directory_depth
        WHERE directory_depth.depth > 2
        """
    ).fetchall()
    if any(bool(row["has_notes"]) or bool(row["has_children"]) for row in rows):
        raise RuntimeError(
            "存在有关联笔记或子目录的第三级目录，无法自动删除。"
        )
    connection.executemany(
        "DELETE FROM directories WHERE id = ?",
        [(int(row["id"]),) for row in rows],
    )
