"""Frozen R1 schema used only for explicitly supplied R2 database paths."""

from __future__ import annotations

from pathlib import Path

from src.snotes.storage.connection import SQLiteConnectionFactory
from src.snotes.storage.support import timestamp_seconds


INITIAL_MAIN_CATEGORIES = ("项目", "设备", "账号", "待办", "临时", "备忘", "终端指令")

FROZEN_SCHEMA_STATEMENTS = (
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


def initialize_database(database_path: Path) -> SQLiteConnectionFactory:
    """Create the frozen schema at an explicit path; never performs migration."""
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connections = SQLiteConnectionFactory(path)
    with connections.write() as connection:
        for statement in FROZEN_SCHEMA_STATEMENTS:
            connection.execute(statement)
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
