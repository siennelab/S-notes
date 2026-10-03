"""GreenCloud 3.5-B Step 8A Reminder migration rehearsal.

The source database is copied with SQLite's backup API and is subsequently
opened read-only.  The candidate is bootstrapped through the application's
real ``initialize_database`` path.  This module never opens a network socket
and never constructs a notification transport.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import stat
import tempfile
from contextlib import closing, contextmanager
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Iterator, Sequence

from src.app_paths import default_data_root
from src.snotes.application.reminder_service import ReminderService
from src.snotes.domain.reminder_schedule import SHANGHAI
from src.snotes.domain.reminders import REMINDER_ACTIVE, ReminderDraft
from src.snotes.storage.connection import SQLiteConnectionFactory
from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
from src.snotes.storage.schema import (
    REMINDER_SCHEMA_VERSION,
    REMINDER_SCHEMA_VERSION_KEY,
    initialize_database,
)


REMINDER_TABLES = (
    "reminder_labels",
    "reminder_tables",
    "reminders",
    "reminder_occurrences",
    "ai_reminder_operations",
)
SCHEMA_SCOPE_TABLES = (*REMINDER_TABLES, "app_settings")
CANONICAL_REMINDER_COLUMNS = (
    "id",
    "table_id",
    "title",
    "lifecycle",
    "action_spec_json",
    "deadline_spec_json",
    "note",
    "created_at",
    "updated_at",
    "completed_at",
    "multi_notify",
    "recurrence_mode",
    "deleted_at",
    "trash_group_id",
)
CANONICAL_AI_REMINDER_OPERATION_COLUMNS = (
    "id",
    "happened_at",
    "actor_target_id",
    "action",
    "table_id",
    "table_title",
    "item_id",
    "item_title",
    "label_id",
    "label_name",
    "request_id",
    "changed_fields_json",
    "before_json",
    "after_json",
)
LEGACY_AI_REMINDER_OPERATION_DEFAULT_ONLY_COLUMNS = frozenset(
    {"reminder_id", "reminder_title"}
)
LEGACY_REMINDER_SEMANTIC_COLUMNS = frozenset({"label_id", "ai_visible"})
ACCEPTED_SOURCE_REMINDER_SCHEMA_VERSIONS = frozenset(
    {"4", REMINDER_SCHEMA_VERSION}
)
STRUCTURALLY_COMPATIBLE_STALE_REMINDER_SCHEMA_VERSION = "3"
BUSINESS_SETTING_KEYS = frozenset(
    {
        "reminder/notifications_enabled",
        "reminder/daily_time",
    }
)
EXCLUDED_REMINDER_SETTING_KEYS = frozenset(
    {
        "reminder/ntfy_endpoint",
        "reminder/ntfy_topic",
        "reminder/last_test_topic",
        "reminder/last_scheduled_date",
    }
)
KNOWN_REMINDER_SETTING_KEYS = BUSINESS_SETTING_KEYS | EXCLUDED_REMINDER_SETTING_KEYS
LIVE_STAGING_DATABASE = Path("/srv/snotes/data/reminder.db")


class RehearsalError(RuntimeError):
    """A fail-closed Step 8A precondition or reconciliation failure."""


def resolve_formal_reminder_database() -> Path:
    """Resolve the database through the same data-root contract as the App."""
    return default_data_root() / "s_notes.db"


def _require_regular_file(path: Path, *, label: str) -> Path:
    target = Path(path)
    try:
        metadata = target.lstat()
    except FileNotFoundError as error:
        raise RehearsalError(f"{label}_missing") from error
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RehearsalError(f"{label}_regular_file_required")
    return target


def _require_distinct(paths: Sequence[Path]) -> None:
    resolved = [Path(path).resolve() for path in paths]
    if len(resolved) != len(set(resolved)):
        raise RehearsalError("rehearsal_paths_must_be_distinct")


@contextmanager
def _read_only_database(path: Path) -> Iterator[sqlite3.Connection]:
    target = _require_regular_file(path, label="database")
    connection = sqlite3.connect(
        f"{target.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=5.0,
    )
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        yield connection
    finally:
        connection.close()


def quick_check(path: Path) -> bool:
    with _read_only_database(path) as connection:
        return [str(row[0]) for row in connection.execute("PRAGMA quick_check")] == ["ok"]


def foreign_key_violations(path: Path) -> list[tuple[object, ...]]:
    with _read_only_database(path) as connection:
        return [tuple(row) for row in connection.execute("PRAGMA foreign_key_check")]


def create_consistent_snapshot(source_path: Path, snapshot_path: Path) -> Path:
    """Create a point-in-time SQLite backup without writing the live source."""
    source = _require_regular_file(Path(source_path), label="source_database")
    snapshot = Path(snapshot_path)
    _require_distinct((source, snapshot))
    if snapshot.exists() or snapshot.is_symlink():
        raise RehearsalError("snapshot_already_exists")
    snapshot.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".step08a-snapshot-",
        dir=snapshot.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        os.chmod(temporary, 0o600)
        with _read_only_database(source) as source_connection:
            with closing(sqlite3.connect(temporary)) as destination:
                source_connection.backup(destination)
        if not quick_check(temporary):
            raise RehearsalError("source_snapshot_quick_check_failed")
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, snapshot)
        os.chmod(snapshot, 0o400)
        return snapshot
    finally:
        temporary.unlink(missing_ok=True)


def initialize_candidate(candidate_path: Path) -> Path:
    """Bootstrap a new candidate through the actual Cloud-compatible schema."""
    candidate = Path(candidate_path)
    if candidate.exists() or candidate.is_symlink():
        raise RehearsalError("candidate_already_exists")
    candidate.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    previous_umask = os.umask(0o077)
    try:
        initialize_database(candidate)
    finally:
        os.umask(previous_umask)
    os.chmod(candidate, 0o600)
    return candidate


def _columns(connection: sqlite3.Connection, table: str) -> tuple[tuple[object, ...], ...]:
    return tuple(tuple(row) for row in connection.execute(f'PRAGMA table_xinfo("{table}")'))


def _column_names(connection: sqlite3.Connection, table: str) -> tuple[str, ...]:
    return tuple(str(row[1]) for row in _columns(connection, table))


def _column_definitions(
    connection: sqlite3.Connection,
    table: str,
) -> dict[str, tuple[object, ...]]:
    # Ignore cid: the migration contract is name-based and does not depend on
    # the ordinal left by an older additive migration.
    return {
        str(row[1]): tuple(row[2:6])
        for row in connection.execute(f'PRAGMA table_xinfo("{table}")')
    }


def _foreign_key_signatures(
    connection: sqlite3.Connection,
    table: str,
    *,
    columns: frozenset[str] | None = None,
) -> set[tuple[object, ...]]:
    signatures: set[tuple[object, ...]] = set()
    for row in connection.execute(f'PRAGMA foreign_key_list("{table}")'):
        source_column = str(row[3])
        if columns is None or source_column in columns:
            # Ignore SQLite's generated id/seq so an allowed legacy FK does
            # not make the canonical FK contract ordinal-dependent.
            signatures.add(tuple(row[index] for index in range(2, 8)))
    return signatures


def _normalized_sql(sql: object) -> str | None:
    if sql is None:
        return None
    return " ".join(str(sql).split())


def _unique_constraint_signatures(
    connection: sqlite3.Connection,
    table: str,
) -> set[tuple[object, ...]]:
    signatures: set[tuple[object, ...]] = set()
    for index in connection.execute(f'PRAGMA index_list("{table}")'):
        if int(index[2]) != 1:
            continue
        name = str(index[1])
        escaped_name = name.replace('"', '""')
        definition = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = ?",
            (name,),
        ).fetchone()
        # seqno orders index terms, but cid is intentionally excluded: rebuilt
        # tables may assign different physical ordinals to the same named fields.
        terms = tuple(
            (row[2], row[4], int(row[3]), int(row[5]))
            for row in connection.execute(f'PRAGMA index_xinfo("{escaped_name}")')
        )
        signatures.add(
            (
                str(index[3]),
                int(index[4]),
                terms,
                _normalized_sql(definition[0] if definition is not None else None),
            )
        )
    return signatures


def _require_plain_scope_tables(connection: sqlite3.Connection) -> None:
    for table in SCHEMA_SCOPE_TABLES:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        if row is None or row[0] is None:
            raise RehearsalError(f"reminder_schema_table_missing:{table}")
        table_sql = str(row[0]).strip().rstrip(";")
        if re.search(r"\bCHECK\s*\(", table_sql, flags=re.IGNORECASE):
            raise RehearsalError(f"reminder_schema_check_incompatible:{table}")
        closing = table_sql.rfind(")")
        if closing < 0 or table_sql[closing + 1 :].strip():
            raise RehearsalError(f"reminder_schema_table_options_incompatible:{table}")
    placeholders = ", ".join("?" for _ in SCHEMA_SCOPE_TABLES)
    trigger = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'trigger' "
        f"AND tbl_name IN ({placeholders}) LIMIT 1",
        SCHEMA_SCOPE_TABLES,
    ).fetchone()
    if trigger is not None:
        raise RehearsalError("reminder_schema_trigger_incompatible")


def _validate_scope_table_shape(
    source: sqlite3.Connection,
    candidate: sqlite3.Connection,
    table: str,
    *,
    allowed_source_columns: frozenset[str] = frozenset(),
) -> None:
    source_columns = frozenset(_column_names(source, table))
    candidate_columns = frozenset(_column_names(candidate, table))
    if not candidate_columns.issubset(source_columns):
        raise RehearsalError(f"reminder_schema_columns_incompatible:{table}")
    if source_columns - candidate_columns - allowed_source_columns:
        raise RehearsalError(f"reminder_schema_columns_incompatible:{table}")
    source_definitions = _column_definitions(source, table)
    candidate_definitions = _column_definitions(candidate, table)
    if any(
        source_definitions[column] != candidate_definitions[column]
        for column in candidate_columns
    ):
        raise RehearsalError(f"reminder_schema_column_definition_incompatible:{table}")
    if _unique_constraint_signatures(source, table) != _unique_constraint_signatures(
        candidate, table
    ):
        raise RehearsalError(f"reminder_schema_unique_constraints_incompatible:{table}")


def _schema_version(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        "SELECT meta_value FROM schema_meta WHERE meta_key = ?",
        (REMINDER_SCHEMA_VERSION_KEY,),
    ).fetchone()
    return str(row[0]) if row is not None else None


def _reminder_setting_keys(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT setting_key FROM app_settings WHERE setting_key LIKE 'reminder/%'"
        )
    }


def validate_schema_compatibility(
    source: sqlite3.Connection,
    candidate: sqlite3.Connection,
) -> dict[str, object]:
    source_version = _schema_version(source)
    candidate_version = _schema_version(candidate)
    if candidate_version != REMINDER_SCHEMA_VERSION:
        raise RehearsalError("reminder_schema_version_incompatible")
    recognized_versions = ACCEPTED_SOURCE_REMINDER_SCHEMA_VERSIONS | {
        STRUCTURALLY_COMPATIBLE_STALE_REMINDER_SCHEMA_VERSION
    }
    if source_version not in recognized_versions:
        raise RehearsalError("reminder_schema_version_incompatible")

    _require_plain_scope_tables(source)
    _require_plain_scope_tables(candidate)

    for table in ("reminder_labels", "reminder_tables", "reminder_occurrences", "app_settings"):
        _validate_scope_table_shape(source, candidate, table)
        if _foreign_key_signatures(source, table) != _foreign_key_signatures(candidate, table):
            raise RehearsalError(f"reminder_schema_foreign_keys_incompatible:{table}")

    _validate_ai_reminder_operation_schema(source, candidate)

    canonical = frozenset(CANONICAL_REMINDER_COLUMNS)
    source_columns = frozenset(_column_names(source, "reminders"))
    candidate_columns = frozenset(_column_names(candidate, "reminders"))
    if candidate_columns != canonical:
        raise RehearsalError("candidate_reminder_schema_contract_incompatible")
    if not canonical.issubset(source_columns):
        raise RehearsalError("source_reminder_schema_missing_required_field")
    if source_columns - canonical - LEGACY_REMINDER_SEMANTIC_COLUMNS:
        raise RehearsalError("unknown_source_reminder_field")

    source_definitions = _column_definitions(source, "reminders")
    candidate_definitions = _column_definitions(candidate, "reminders")
    if any(
        source_definitions[column] != candidate_definitions[column]
        for column in CANONICAL_REMINDER_COLUMNS
    ):
        raise RehearsalError("reminder_schema_column_definition_incompatible")
    if _unique_constraint_signatures(source, "reminders") != _unique_constraint_signatures(
        candidate, "reminders"
    ):
        raise RehearsalError("reminder_schema_unique_constraints_incompatible:reminders")

    source_foreign_keys = _foreign_key_signatures(source, "reminders")
    candidate_foreign_keys = _foreign_key_signatures(candidate, "reminders")
    approved_legacy_label_foreign_key = (
        "reminder_labels",
        "label_id",
        "id",
        "NO ACTION",
        "NO ACTION",
        "NONE",
    )
    if (
        candidate_foreign_keys - source_foreign_keys
        or source_foreign_keys
        - candidate_foreign_keys
        - {approved_legacy_label_foreign_key}
    ):
        raise RehearsalError("reminder_schema_foreign_keys_incompatible:reminders")

    _validate_legacy_reminder_semantics(source, source_columns)
    unknown = _reminder_setting_keys(source) - KNOWN_REMINDER_SETTING_KEYS
    if unknown:
        # Do not print values or unreviewed key names; a new key needs an explicit
        # business-versus-transport classification before migration.
        raise RehearsalError("unclassified_reminder_setting_key")

    # Marker 3 is accepted only here, after exact physical structure,
    # constraints, legacy semantics, and setting classifications have passed.
    return {
        "source_marker": source_version,
        "candidate_marker": candidate_version,
        "structurally_compatible": True,
        "stale_source_marker": (
            source_version == STRUCTURALLY_COMPATIBLE_STALE_REMINDER_SCHEMA_VERSION
        ),
    }


def _validate_ai_reminder_operation_schema(
    source: sqlite3.Connection,
    candidate: sqlite3.Connection,
) -> None:
    table = "ai_reminder_operations"
    canonical = frozenset(CANONICAL_AI_REMINDER_OPERATION_COLUMNS)
    source_columns = frozenset(_column_names(source, table))
    candidate_columns = frozenset(_column_names(candidate, table))
    if candidate_columns != canonical:
        raise RehearsalError("candidate_ai_reminder_operation_schema_contract_incompatible")

    source_only = source_columns - canonical
    if source_only - LEGACY_AI_REMINDER_OPERATION_DEFAULT_ONLY_COLUMNS:
        raise RehearsalError("unknown_source_ai_reminder_operation_field")
    if not canonical.issubset(source_columns):
        raise RehearsalError("reminder_schema_columns_incompatible:ai_reminder_operations")

    source_definitions = _column_definitions(source, table)
    candidate_definitions = _column_definitions(candidate, table)
    if any(
        source_definitions[column] != candidate_definitions[column]
        for column in CANONICAL_AI_REMINDER_OPERATION_COLUMNS
    ):
        raise RehearsalError("reminder_schema_column_definition_incompatible:ai_reminder_operations")
    if _foreign_key_signatures(source, table) != _foreign_key_signatures(candidate, table):
        raise RehearsalError("reminder_schema_foreign_keys_incompatible:ai_reminder_operations")
    if _unique_constraint_signatures(source, table) != _unique_constraint_signatures(
        candidate, table
    ):
        raise RehearsalError(
            "reminder_schema_unique_constraints_incompatible:ai_reminder_operations"
        )

    if "reminder_id" in source_only:
        non_default = source.execute(
            "SELECT 1 FROM ai_reminder_operations "
            "WHERE reminder_id IS NOT NULL LIMIT 1"
        ).fetchone()
        if non_default is not None:
            raise RehearsalError("legacy_ai_reminder_operation_reminder_id_non_default")
    if "reminder_title" in source_only:
        non_default = source.execute(
            "SELECT 1 FROM ai_reminder_operations "
            "WHERE typeof(reminder_title) != 'text' "
            "OR reminder_title COLLATE BINARY != '' LIMIT 1"
        ).fetchone()
        if non_default is not None:
            raise RehearsalError("legacy_ai_reminder_operation_reminder_title_non_default")


def _validate_legacy_reminder_semantics(
    source: sqlite3.Connection,
    source_columns: frozenset[str],
) -> None:
    missing_table = source.execute(
        "SELECT 1 FROM reminders AS reminders "
        "LEFT JOIN reminder_tables AS tables ON tables.id = reminders.table_id "
        "WHERE reminders.table_id IS NULL OR tables.id IS NULL LIMIT 1"
    ).fetchone()
    if missing_table is not None:
        raise RehearsalError("reminder_table_relation_invalid")

    if "label_id" in source_columns:
        mismatch = source.execute(
            "SELECT 1 FROM reminders AS reminders "
            "JOIN reminder_tables AS tables ON tables.id = reminders.table_id "
            "WHERE reminders.label_id IS NOT NULL "
            "AND reminders.label_id != tables.label_id LIMIT 1"
        ).fetchone()
        if mismatch is not None:
            raise RehearsalError("legacy_reminder_label_mapping_conflict")

    if "ai_visible" in source_columns:
        invalid = source.execute(
            "SELECT 1 FROM reminders WHERE ai_visible IS NOT NULL "
            "AND ai_visible NOT IN (0, 1) LIMIT 1"
        ).fetchone()
        if invalid is not None:
            raise RehearsalError("legacy_reminder_ai_visibility_invalid")
        expansion = source.execute(
            "SELECT 1 FROM reminders AS reminders "
            "JOIN reminder_tables AS tables ON tables.id = reminders.table_id "
            "WHERE reminders.ai_visible = 0 AND tables.ai_visible = 1 LIMIT 1"
        ).fetchone()
        if expansion is not None:
            raise RehearsalError("legacy_reminder_ai_visibility_expansion")


def _candidate_is_fresh(connection: sqlite3.Connection) -> bool:
    counts = {
        table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        for table in REMINDER_TABLES
    }
    labels = connection.execute(
        "SELECT label_key FROM reminder_labels ORDER BY id"
    ).fetchall()
    tables = connection.execute(
        "SELECT table_key FROM reminder_tables ORDER BY id"
    ).fetchall()
    reminder_settings = int(
        connection.execute(
            "SELECT COUNT(*) FROM app_settings WHERE setting_key LIKE 'reminder/%'"
        ).fetchone()[0]
    )
    shared_operations = int(connection.execute("SELECT COUNT(*) FROM ai_operations").fetchone()[0])
    return (
        counts["reminder_labels"] == 1
        and counts["reminder_tables"] == 1
        and all(counts[name] == 0 for name in REMINDER_TABLES[2:])
        and [str(row[0]) for row in labels] == ["default"]
        and [str(row[0]) for row in tables] == ["default"]
        and reminder_settings == 0
        and shared_operations == 0
    )


def _insert_rows(
    source: sqlite3.Connection,
    candidate: sqlite3.Connection,
    table: str,
    *,
    columns: Sequence[str] | None = None,
) -> None:
    selected = list(columns) if columns is not None else [
        str(row[1]) for row in source.execute(f'PRAGMA table_info("{table}")')
    ]
    quoted = ", ".join(f'"{column}"' for column in selected)
    placeholders = ", ".join("?" for _ in selected)
    rows = source.execute(f'SELECT {quoted} FROM "{table}" ORDER BY rowid').fetchall()
    if rows:
        candidate.executemany(
            f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})',
            [tuple(row) for row in rows],
        )


def migrate_snapshot(snapshot_path: Path, candidate_path: Path) -> None:
    """Copy the exact allowlisted Reminder state into a fresh candidate."""
    snapshot = _require_regular_file(Path(snapshot_path), label="source_snapshot")
    candidate_path = _require_regular_file(Path(candidate_path), label="candidate_database")
    _require_distinct((snapshot, candidate_path, LIVE_STAGING_DATABASE))
    if not quick_check(snapshot):
        raise RehearsalError("source_snapshot_quick_check_failed")

    with _read_only_database(snapshot) as source:
        with closing(sqlite3.connect(candidate_path)) as candidate:
            candidate.row_factory = sqlite3.Row
            candidate.execute("PRAGMA foreign_keys = ON")
            candidate.execute("BEGIN IMMEDIATE")
            try:
                validate_schema_compatibility(source, candidate)
                if not _candidate_is_fresh(candidate):
                    raise RehearsalError("candidate_not_fresh")
                for table in reversed(REMINDER_TABLES):
                    candidate.execute(f'DELETE FROM "{table}"')
                for table in REMINDER_TABLES:
                    _insert_rows(
                        source,
                        candidate,
                        table,
                        columns=(
                            CANONICAL_REMINDER_COLUMNS
                            if table == "reminders"
                            else CANONICAL_AI_REMINDER_OPERATION_COLUMNS
                            if table == "ai_reminder_operations"
                            else None
                        ),
                    )

                candidate.executemany(
                    "INSERT INTO app_settings (setting_key, setting_value, updated_at) "
                    "VALUES (?, ?, ?)",
                    [
                        tuple(row)
                        for row in source.execute(
                            "SELECT setting_key, setting_value, updated_at FROM app_settings "
                            "WHERE setting_key IN (?, ?) ORDER BY setting_key",
                            tuple(sorted(BUSINESS_SETTING_KEYS)),
                        )
                    ],
                )
                candidate.commit()
            except Exception:
                candidate.rollback()
                raise


def _row_map(
    connection: sqlite3.Connection,
    table: str,
    *,
    keys: Sequence[str],
    columns: Sequence[str] | None = None,
    where: str = "",
    parameters: Sequence[object] = (),
) -> dict[tuple[object, ...], dict[str, object]]:
    selected = list(columns) if columns is not None else [
        str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')
    ]
    quoted = ", ".join(f'"{column}"' for column in selected)
    rows = connection.execute(
        f'SELECT {quoted} FROM "{table}" {where}',
        tuple(parameters),
    ).fetchall()
    return {
        tuple(row[key] for key in keys): {column: row[column] for column in selected}
        for row in rows
    }


def _compare_maps(
    source_rows: dict[tuple[object, ...], dict[str, object]],
    candidate_rows: dict[tuple[object, ...], dict[str, object]],
) -> dict[str, object]:
    differences: list[dict[str, object]] = []
    for identity in sorted(set(source_rows) | set(candidate_rows), key=repr):
        source = source_rows.get(identity)
        candidate = candidate_rows.get(identity)
        if source is None or candidate is None:
            fields = ["__missing__"]
        else:
            fields = sorted(
                column for column in source if source[column] != candidate.get(column)
            )
        if fields:
            differences.append({"identity": list(identity), "fields": fields})
    return {
        "source_count": len(source_rows),
        "candidate_count": len(candidate_rows),
        "diff": len(differences),
        "differences": differences,
    }


def _table_comparison(
    source: sqlite3.Connection,
    candidate: sqlite3.Connection,
    table: str,
) -> dict[str, object]:
    return _compare_maps(
        _row_map(source, table, keys=("id",)),
        _row_map(candidate, table, keys=("id",)),
    )


def _reminder_comparison(
    source: sqlite3.Connection,
    candidate: sqlite3.Connection,
) -> dict[str, object]:
    return _compare_maps(
        _row_map(
            source,
            "reminders",
            keys=("id",),
            columns=CANONICAL_REMINDER_COLUMNS,
        ),
        _row_map(
            candidate,
            "reminders",
            keys=("id",),
            columns=CANONICAL_REMINDER_COLUMNS,
        ),
    )


def _ai_reminder_operation_comparison(
    source: sqlite3.Connection,
    candidate: sqlite3.Connection,
) -> dict[str, object]:
    return _compare_maps(
        _row_map(
            source,
            "ai_reminder_operations",
            keys=("id",),
            columns=CANONICAL_AI_REMINDER_OPERATION_COLUMNS,
        ),
        _row_map(
            candidate,
            "ai_reminder_operations",
            keys=("id",),
            columns=CANONICAL_AI_REMINDER_OPERATION_COLUMNS,
        ),
    )


def _effective_reminder_semantics(
    connection: sqlite3.Connection,
) -> dict[tuple[object, ...], dict[str, object]]:
    rows = connection.execute(
        "SELECT reminders.id AS id, tables.label_id AS effective_label_id, "
        "tables.ai_visible AS effective_ai_visible "
        "FROM reminders AS reminders "
        "JOIN reminder_tables AS tables ON tables.id = reminders.table_id"
    ).fetchall()
    return {
        (row["id"],): {
            "id": row["id"],
            "effective_label_id": row["effective_label_id"],
            "effective_ai_visible": row["effective_ai_visible"],
        }
        for row in rows
    }


def _semantic_mapping_comparison(
    source: sqlite3.Connection,
    candidate: sqlite3.Connection,
) -> dict[str, object]:
    return _compare_maps(
        _effective_reminder_semantics(source),
        _effective_reminder_semantics(candidate),
    )


def _settings_comparison(
    source: sqlite3.Connection,
    candidate: sqlite3.Connection,
) -> dict[str, object]:
    where = "WHERE setting_key IN (?, ?)"
    keys = tuple(sorted(BUSINESS_SETTING_KEYS))
    columns = ("setting_key", "setting_value", "updated_at")
    return _compare_maps(
        _row_map(source, "app_settings", keys=("setting_key",), columns=columns, where=where, parameters=keys),
        _row_map(candidate, "app_settings", keys=("setting_key",), columns=columns, where=where, parameters=keys),
    )


def _multi_notification_comparison(
    source: sqlite3.Connection,
    candidate: sqlite3.Connection,
) -> dict[str, object]:
    reminder_columns = ("id", "multi_notify", "recurrence_mode")
    occurrence_columns = (
        "id",
        "reminder_id",
        "occurrence_key",
        "action_notified_at",
        "deadline_notified_at",
        "advance_7_notified_at",
        "advance_3_notified_at",
        "advance_1_notified_at",
    )
    source_rows = _row_map(source, "reminders", keys=("id",), columns=reminder_columns)
    candidate_rows = _row_map(candidate, "reminders", keys=("id",), columns=reminder_columns)
    source_occurrences = _row_map(source, "reminder_occurrences", keys=("id",), columns=occurrence_columns)
    candidate_occurrences = _row_map(candidate, "reminder_occurrences", keys=("id",), columns=occurrence_columns)
    source_rows.update({("occurrence", *key): value for key, value in source_occurrences.items()})
    candidate_rows.update({("occurrence", *key): value for key, value in candidate_occurrences.items()})
    return _compare_maps(source_rows, candidate_rows)


def _clock_for(day: date) -> float:
    return datetime.combine(day, time(12, 0), tzinfo=SHANGHAI).timestamp()


def _service(path: Path, day: date, *, read_only: bool) -> ReminderService:
    connections = SQLiteConnectionFactory(path, read_only=read_only)
    repository = SQLiteReminderRepository(
        connections,
        clock=lambda: datetime.fromtimestamp(_clock_for(day), SHANGHAI),
    )
    return ReminderService(repository, clock=lambda: _clock_for(day))


def _active_displayable_ids(path: Path) -> set[int]:
    with _read_only_database(path) as connection:
        return {
            int(row[0])
            for row in connection.execute(
                "SELECT reminders.id FROM reminders "
                "JOIN reminder_tables ON reminder_tables.id = reminders.table_id "
                "WHERE reminders.lifecycle = ? AND reminder_tables.is_deleted = 0",
                (REMINDER_ACTIVE,),
            )
        }


def _schedule_projection(path: Path, day: date) -> dict[int, dict[str, object]]:
    service = _service(path, day, read_only=True)
    daily_time = service.settings().daily_time
    active_items = [
        item for item in service.list_reminders() if item.reminder.lifecycle == REMINDER_ACTIVE
    ]
    returned_ids = {item.reminder.id for item in active_items}
    expected_ids = _active_displayable_ids(path)
    if returned_ids != expected_ids:
        raise RehearsalError("schedule_projection_incomplete")
    projection: dict[int, dict[str, object]] = {}
    for item in active_items:
        occurrence = item.occurrence
        projection[item.reminder.id] = {
            "status": item.status,
            "next_action_date": item.next_action_date,
            "next_deadline_date": item.next_deadline_date,
            "occurrence": None if occurrence is None else {
                "id": occurrence.id,
                "key": occurrence.occurrence_key,
                "action_date": occurrence.action_date,
                "deadline_date": occurrence.deadline_date,
                "status": occurrence.status,
                "completed_at": occurrence.completed_at,
                "action_notified_at": occurrence.action_notified_at,
                "deadline_notified_at": occurrence.deadline_notified_at,
                "advance_7_notified_at": occurrence.advance_7_notified_at,
                "advance_3_notified_at": occurrence.advance_3_notified_at,
                "advance_1_notified_at": occurrence.advance_1_notified_at,
            },
            "multi_notify": item.reminder.multi_notify,
            "recurrence_mode": item.reminder.recurrence_mode,
            "notification_points": _notification_point_projection(item, daily_time),
        }
    return projection


def _due_datetime(day: date, daily_time: str) -> str:
    try:
        hour, minute = (int(part) for part in daily_time.split(":", 1))
        due = datetime.combine(day, time(hour, minute), tzinfo=SHANGHAI)
    except (TypeError, ValueError) as error:
        raise RehearsalError("reminder_daily_time_invalid") from error
    return due.isoformat(timespec="minutes")


def _notification_point_projection(item, daily_time: str) -> list[dict[str, object]]:
    """Project the nodes implemented by ``pending_notifications`` without writes."""
    occurrence = item.occurrence
    if occurrence is None or occurrence.status != "pending":
        return []
    action = date.fromisoformat(occurrence.action_date)
    deadline = date.fromisoformat(occurrence.deadline_date) if occurrence.deadline_date else None
    normal_anchor = deadline or action if item.reminder.multi_notify else action
    points: list[dict[str, object]] = []
    if item.reminder.multi_notify:
        for days in (7, 3, 1):
            kind = f"advance_{days}"
            due_day = normal_anchor - timedelta(days=days)
            points.append(
                {
                    "kind": kind,
                    "due_at": _due_datetime(due_day, daily_time),
                    "offset_days": -days,
                    "stable_identity": [item.reminder.id, occurrence.id, kind],
                    "dedupe_identity": [item.reminder.id, occurrence.id, kind],
                    "notified_at": getattr(occurrence, f"{kind}_notified_at"),
                }
            )
    normal_kind = "deadline" if item.reminder.multi_notify and deadline else "action"
    points.append(
        {
            "kind": normal_kind,
            "due_at": _due_datetime(normal_anchor, daily_time),
            "offset_days": 0,
            "stable_identity": [item.reminder.id, occurrence.id, normal_kind],
            "dedupe_identity": [item.reminder.id, occurrence.id, "normal"],
            "notified_at": (
                occurrence.deadline_notified_at
                if normal_kind == "deadline"
                else occurrence.action_notified_at
            ),
        }
    )
    if not item.reminder.multi_notify and deadline is not None:
        # Existing scheduler recovery emits this only when the action node was
        # missed.  Both fields share one normal-node guard.
        points.append(
            {
                "kind": "deadline_fallback",
                "due_at": _due_datetime(deadline, daily_time),
                "offset_days": (deadline - action).days,
                "stable_identity": [item.reminder.id, occurrence.id, "deadline"],
                "dedupe_identity": [item.reminder.id, occurrence.id, "normal"],
                "notified_at": occurrence.deadline_notified_at,
            }
        )
    return points


def _notification_probe_dates(path: Path, as_of: date) -> list[date]:
    probes = {as_of}
    with _read_only_database(path) as connection:
        rows = connection.execute(
            "SELECT occurrences.action_date, occurrences.deadline_date, reminders.multi_notify "
            "FROM reminder_occurrences AS occurrences "
            "JOIN reminders ON reminders.id = occurrences.reminder_id "
            "JOIN reminder_tables ON reminder_tables.id = reminders.table_id "
            "WHERE reminders.lifecycle = ? AND reminder_tables.is_deleted = 0 "
            "AND occurrences.status = 'pending'",
            (REMINDER_ACTIVE,),
        ).fetchall()
    for row in rows:
        action = date.fromisoformat(str(row["action_date"]))
        deadline = date.fromisoformat(str(row["deadline_date"])) if row["deadline_date"] else None
        if bool(row["multi_notify"]):
            anchor = deadline or action
            probes.update(anchor - timedelta(days=days) for days in (7, 3, 1))
            probes.add(anchor)
        else:
            probes.add(action)
            if deadline is not None:
                probes.add(deadline)
    return sorted(probes)


def _clone_database(source: Path, destination: Path) -> None:
    with _read_only_database(source) as source_connection:
        with closing(sqlite3.connect(destination)) as candidate:
            source_connection.backup(candidate)


def _pending_notification_identities(path: Path, day: date) -> list[tuple[int, int, str]]:
    service = _service(path, day, read_only=False)
    return sorted(
        (notification.reminder_id, notification.occurrence_id, notification.kind)
        for notification in service.pending_notifications(day)
    )


def _notification_probe(path: Path, probes: Sequence[date]) -> dict[str, object]:
    results: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="snotes-step08a-schedule-") as temporary:
        root = Path(temporary)
        for index, day in enumerate(probes):
            clone = root / f"probe-{index}.db"
            _clone_database(path, clone)
            results[day.isoformat()] = [list(identity) for identity in _pending_notification_identities(clone, day)]
    return results


def schedule_comparison(
    source_path: Path,
    candidate_path: Path,
    *,
    as_of: date,
) -> dict[str, object]:
    source_projection = _schedule_projection(source_path, as_of)
    candidate_projection = _schedule_projection(candidate_path, as_of)
    projection = _compare_maps(
        {(identity,): value for identity, value in source_projection.items()},
        {(identity,): value for identity, value in candidate_projection.items()},
    )
    probes = sorted(
        set(_notification_probe_dates(source_path, as_of))
        | set(_notification_probe_dates(candidate_path, as_of))
    )
    source_notifications = _notification_probe(source_path, probes)
    candidate_notifications = _notification_probe(candidate_path, probes)
    notification_differences = [
        day for day in source_notifications if source_notifications[day] != candidate_notifications[day]
    ]
    source_identity_count = sum(len(value) for value in source_notifications.values())
    candidate_identity_count = sum(len(value) for value in candidate_notifications.values())
    source_daily_time = _service(source_path, as_of, read_only=True).settings().daily_time
    candidate_daily_time = _service(candidate_path, as_of, read_only=True).settings().daily_time
    due_time_diff = int(source_daily_time != candidate_daily_time)
    return {
        "source_count": projection["source_count"],
        "candidate_count": projection["candidate_count"],
        "projection_diff": projection["diff"],
        "projection_differences": projection["differences"],
        "probe_count": len(probes),
        "source_notification_identity_count": source_identity_count,
        "candidate_notification_identity_count": candidate_identity_count,
        "notification_diff": len(notification_differences),
        "notification_difference_dates": notification_differences,
        "due_time_diff": due_time_diff,
        "coverage": [
            "occurrence_identity",
            "next_current_schedule",
            "notification_due_at_Asia/Shanghai",
            "notification_offset_days",
            "notification_kind",
            "stable_identity",
            "normal_node_shared_dedupe_identity",
            "pending_notifications_disposable_clone_probe",
        ],
        "diff": int(projection["diff"]) + len(notification_differences) + due_time_diff,
    }


def _no_occurrence_ids(path: Path, as_of: date) -> list[int]:
    return sorted(
        identity
        for identity, row in _schedule_projection(path, as_of).items()
        if row["occurrence"] is None
    )


def no_occurrence_business_path_reachable() -> bool:
    """Exercise the normal one-time completion path against fictional data."""
    with tempfile.TemporaryDirectory(prefix="snotes-step08a-no-occurrence-") as temporary:
        path = Path(temporary) / "fictional.db"
        connections = initialize_database(path)
        clock = lambda: datetime(2026, 10, 2, 12, 0, tzinfo=SHANGHAI)
        repository = SQLiteReminderRepository(connections, clock=clock)
        service = ReminderService(repository, clock=lambda: clock().timestamp())
        reminder = service.create_reminder(
            ReminderDraft(
                "FICTIONAL no-occurrence reachability",
                REMINDER_ACTIVE,
                {"type": "one_time", "date": "2026-10-03"},
            )
        )
        before = service.get_reminder(reminder.id)
        service.ai_complete_item(
            actor_target_id="fictional-actor",
            request_id="fictional-complete",
            item_id=reminder.id,
        )
        after = service.get_reminder(reminder.id)
        history = service.history(reminder.id)
        return bool(
            before is not None
            and before.occurrence is not None
            and after is not None
            and after.reminder.lifecycle == REMINDER_ACTIVE
            and after.status == "completed"
            and after.occurrence is None
            and len(history) == 1
            and history[0].status == "completed"
        )


def reconcile(
    snapshot_path: Path,
    candidate_path: Path,
    *,
    as_of: date | None = None,
) -> dict[str, object]:
    snapshot = _require_regular_file(Path(snapshot_path), label="source_snapshot")
    candidate = _require_regular_file(Path(candidate_path), label="candidate_database")
    _require_distinct((snapshot, candidate, LIVE_STAGING_DATABASE))
    comparison_day = as_of or datetime.now(SHANGHAI).date()
    with _read_only_database(snapshot) as source, _read_only_database(candidate) as target:
        schema_guard = validate_schema_compatibility(source, target)
        tables = {
            table: (
                _reminder_comparison(source, target)
                if table == "reminders"
                else _ai_reminder_operation_comparison(source, target)
                if table == "ai_reminder_operations"
                else _table_comparison(source, target, table)
            )
            for table in REMINDER_TABLES
        }
        mappings = _semantic_mapping_comparison(source, target)
        settings = _settings_comparison(source, target)
        multi_notification = _multi_notification_comparison(source, target)
        target_reminder_keys = _reminder_setting_keys(target)
        transport_keys_present = sorted(target_reminder_keys & EXCLUDED_REMINDER_SETTING_KEYS)
        shared_ai_operations = int(target.execute("SELECT COUNT(*) FROM ai_operations").fetchone()[0])

    quick_source = quick_check(snapshot)
    quick_candidate = quick_check(candidate)
    foreign_keys = foreign_key_violations(candidate)
    persisted_diff = (
        sum(int(result["diff"]) for result in tables.values())
        + int(mappings["diff"])
        + int(settings["diff"])
        + int(multi_notification["diff"])
        + len(transport_keys_present)
        + shared_ai_operations
        + len(foreign_keys)
        + int(not quick_source)
        + int(not quick_candidate)
    )
    result: dict[str, object] = {
        "as_of": comparison_day.isoformat(),
        "schema_guard": schema_guard,
        "tables": tables,
        "reminder_semantic_mappings": mappings,
        "multi_notification": multi_notification,
        "business_settings": settings,
        "source_quick_check": quick_source,
        "candidate_quick_check": quick_candidate,
        "candidate_foreign_key_check": not foreign_keys,
        "candidate_foreign_key_violation_count": len(foreign_keys),
        "transport_settings_migrated": bool(transport_keys_present),
        "shared_ai_operations_migrated": shared_ai_operations != 0,
    }
    if persisted_diff:
        # A persisted-data mismatch is a Step 8A stop condition.  Do not keep
        # simulating schedules after the migration has already failed.
        result.update(
            {
                "schedule": {"skipped": True, "diff": 0},
                "no_occurrence": {"skipped": True, "diff": 0},
                "total_diff": persisted_diff,
                "pass": False,
            }
        )
        return result

    schedules = schedule_comparison(snapshot, candidate, as_of=comparison_day)
    source_no_occurrence = _no_occurrence_ids(snapshot, comparison_day)
    candidate_no_occurrence = _no_occurrence_ids(candidate, comparison_day)
    no_occurrence_diff = int(source_no_occurrence != candidate_no_occurrence)
    reachable = no_occurrence_business_path_reachable()
    if not reachable:
        raise RehearsalError("no_occurrence_reachability_probe_failed")
    total_diff = persisted_diff + int(schedules["diff"]) + no_occurrence_diff
    result.update(
        {
            "schedule": schedules,
            "no_occurrence": {
                "source_count": len(source_no_occurrence),
                "candidate_count": len(candidate_no_occurrence),
                "source_ids": source_no_occurrence,
                "candidate_ids": candidate_no_occurrence,
                "diff": no_occurrence_diff,
                "observed_in_snapshot": bool(source_no_occurrence),
                "reachable": reachable,
                "candidate_parity": no_occurrence_diff == 0,
                "reachability_path": "ai_complete_item_current_occurrence_only",
            },
            "total_diff": total_diff,
            "pass": total_diff == 0,
        }
    )
    return result


def rehearse_from_snapshot(
    snapshot_path: Path,
    candidate_path: Path,
    *,
    as_of: date | None = None,
) -> dict[str, object]:
    initialize_candidate(candidate_path)
    migrate_snapshot(snapshot_path, candidate_path)
    return reconcile(snapshot_path, candidate_path, as_of=as_of)


def run_rehearsal(
    source_path: Path,
    snapshot_path: Path,
    candidate_path: Path,
    *,
    as_of: date | None = None,
) -> dict[str, object]:
    _require_distinct((Path(source_path), Path(snapshot_path), Path(candidate_path), LIVE_STAGING_DATABASE))
    create_consistent_snapshot(source_path, snapshot_path)
    return rehearse_from_snapshot(snapshot_path, candidate_path, as_of=as_of)


def _date_argument(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="snapshot, migrate, and reconcile")
    run.add_argument("--source", type=Path, default=resolve_formal_reminder_database())
    run.add_argument("--snapshot", type=Path, required=True)
    run.add_argument("--candidate", type=Path, required=True)
    run.add_argument("--as-of", type=_date_argument)
    existing = subparsers.add_parser("from-snapshot", help="migrate an existing snapshot")
    existing.add_argument("--snapshot", type=Path, required=True)
    existing.add_argument("--candidate", type=Path, required=True)
    existing.add_argument("--as-of", type=_date_argument)
    compare = subparsers.add_parser("reconcile", help="reconcile existing databases")
    compare.add_argument("--snapshot", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--as-of", type=_date_argument)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "run":
            result = run_rehearsal(
                arguments.source,
                arguments.snapshot,
                arguments.candidate,
                as_of=arguments.as_of,
            )
        elif arguments.command == "from-snapshot":
            result = rehearse_from_snapshot(
                arguments.snapshot,
                arguments.candidate,
                as_of=arguments.as_of,
            )
        else:
            result = reconcile(
                arguments.snapshot,
                arguments.candidate,
                as_of=arguments.as_of,
            )
    except RehearsalError as error:
        print(json.dumps({"pass": False, "error": str(error)}, sort_keys=True))
        return 2
    except Exception as error:
        # CLI output is report material.  Unexpected database/OS errors may
        # contain source values or private paths, so expose only their class.
        print(json.dumps({"pass": False, "error": type(error).__name__}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
