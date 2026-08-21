from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from src.ai_targets import (
    AI_VISIBILITY_FULL,
    AI_VISIBILITY_LABELS,
    AI_VISIBILITY_REDACTED,
    visibility_display,
)
from src.search_query import (
    build_safe_fts_phrase,
    match_search_fields,
    parse_search_query,
)
from src.services.ai_access_service import materialize_ai_safe_notes_for_target
from src.time_utils import now_shanghai_iso


MIRROR_SCHEMA_VERSION = "2"
GALE_ACTOR_ID = "gale"
GENERATOR_APP_VERSION = "S-notes 1.0.0 R1A"
SEMANTIC_SEARCH_INCLUDED = False

REQUIRED_TABLES = {
    "mirror_metadata",
    "mirror_notes",
    "mirror_notes_fts",
}
CATALOG_TABLES = {
    "mirror_directories",
    "mirror_tags",
    "mirror_ai_visibility_options",
    "mirror_ai_targets",
}
FTS_SHADOW_TABLES = {
    "mirror_notes_fts_data",
    "mirror_notes_fts_idx",
    "mirror_notes_fts_content",
    "mirror_notes_fts_docsize",
    "mirror_notes_fts_config",
}
ALLOWED_TABLES = REQUIRED_TABLES | CATALOG_TABLES | FTS_SHADOW_TABLES
REQUIRED_INDEXES = {
    "idx_mirror_notes_source_updated_at",
}

MIRROR_SCHEMA_SQL = """
CREATE TABLE mirror_metadata (
    meta_key TEXT PRIMARY KEY,
    meta_value TEXT NOT NULL
);

CREATE TABLE mirror_notes (
    mirror_note_id INTEGER PRIMARY KEY,
    source_note_id INTEGER NOT NULL UNIQUE CHECK(source_note_id > 0),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    directory TEXT NOT NULL,
    tags_json TEXT NOT NULL CHECK(json_valid(tags_json)),
    tags_text TEXT NOT NULL,
    source_updated_at TEXT NOT NULL,
    source_version TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK(visibility IN ('full', 'redacted')),
    redacted INTEGER NOT NULL CHECK(redacted IN (0, 1)),
    redaction_count INTEGER NOT NULL CHECK(redaction_count >= 0),
    ai_visibility TEXT NOT NULL DEFAULT '',
    ai_targets_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(ai_targets_json)),
    main_category TEXT NOT NULL DEFAULT '',
    sub_category TEXT NOT NULL DEFAULT '',
    CHECK(
        (visibility = 'full' AND redacted = 0)
        OR (visibility = 'redacted' AND redacted = 1)
    )
);

CREATE INDEX idx_mirror_notes_source_updated_at
ON mirror_notes(source_updated_at DESC, source_note_id DESC);

CREATE VIRTUAL TABLE mirror_notes_fts USING fts5(
    title,
    body,
    directory,
    tags,
    tokenize='unicode61'
);

CREATE TABLE mirror_directories (
    directory_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    parent_id INTEGER,
    main_category TEXT NOT NULL,
    sub_category TEXT NOT NULL DEFAULT '',
    path_json TEXT NOT NULL CHECK(json_valid(path_json))
);

CREATE TABLE mirror_tags (
    name TEXT PRIMARY KEY
);

CREATE TABLE mirror_ai_visibility_options (
    value TEXT PRIMARY KEY,
    label TEXT NOT NULL
);

CREATE TABLE mirror_ai_targets (
    target_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL
);
"""


class GaleMirrorError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class GaleMirrorSafeRecord:
    source_note_id: int
    title: str
    body: str
    directory: str
    tags: tuple[str, ...]
    source_updated_at: str
    source_version: str
    visibility: str
    redacted: bool
    redaction_count: int
    ai_visibility: str = ""
    ai_targets: tuple[str, ...] = ()
    main_category: str = ""
    sub_category: str = ""

    @classmethod
    def from_materialized(cls, safe_note: Mapping[str, Any]) -> GaleMirrorSafeRecord:
        visibility = str(safe_note.get("visibility") or "")
        redacted = bool(safe_note.get("redacted"))
        if visibility not in {AI_VISIBILITY_FULL, AI_VISIBILITY_REDACTED}:
            raise GaleMirrorError("unsafe_visibility")
        if redacted != (visibility == AI_VISIBILITY_REDACTED):
            raise GaleMirrorError("unsafe_redaction_state")
        tags = safe_note.get("tags")
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise GaleMirrorError("unsafe_tags")
        return cls(
            source_note_id=int(safe_note["note_id"]),
            title=str(safe_note.get("title") or ""),
            body=str(safe_note.get("body") or ""),
            directory=str(safe_note.get("directory") or ""),
            tags=tuple(tags),
            source_updated_at=str(safe_note.get("updated_at") or ""),
            source_version=str(safe_note.get("version") or ""),
            visibility=visibility,
            redacted=redacted,
            redaction_count=int(safe_note.get("redaction_count") or 0),
        )


@dataclass(frozen=True)
class GaleMirrorVerification:
    note_count: int
    fts_count: int
    full_note_count: int
    redacted_note_count: int
    file_mode: str
    schema_version: str


@dataclass(frozen=True)
class GaleMirrorExportResult:
    output_path: Path
    schema_version: str
    target_actor: str
    note_count: int
    full_note_count: int
    redacted_note_count: int
    sha256: str
    file_mode: str


def export_gale_mirror(
    *,
    source_database: Path,
    output_path: Path,
    redaction_rules_path: Path,
    actor: str = GALE_ACTOR_ID,
    generator_app_version: str = GENERATOR_APP_VERSION,
) -> GaleMirrorExportResult:
    source = Path(source_database)
    output = Path(output_path)
    rules = Path(redaction_rules_path)
    _validate_export_paths(source, output, rules, actor)

    safe_notes, warning = materialize_ai_safe_notes_for_target(
        actor,
        database_path=source,
        redaction_rules_path=rules,
        source_read_only=True,
    )
    if warning:
        raise GaleMirrorError("redaction_rules_unavailable")
    safe_records = tuple(
        _record_with_formal_metadata(
            source,
            GaleMirrorSafeRecord.from_materialized(safe_note),
        )
        for safe_note in safe_notes
    )
    metadata_catalog = _metadata_catalog(source)

    temporary_path = _new_temporary_mirror_path(output)
    try:
        _write_gale_mirror_database(
            temporary_path,
            safe_records,
            actor=actor,
            generator_app_version=generator_app_version,
            **metadata_catalog,
        )
        os.chmod(temporary_path, 0o600)
        verification = verify_gale_mirror(
            temporary_path,
            expected_note_count=len(safe_records),
            expected_actor=actor,
        )
        digest = _sha256_file(temporary_path)
        os.replace(temporary_path, output)
        return GaleMirrorExportResult(
            output_path=output,
            schema_version=MIRROR_SCHEMA_VERSION,
            target_actor=actor,
            note_count=verification.note_count,
            full_note_count=verification.full_note_count,
            redacted_note_count=verification.redacted_note_count,
            sha256=digest,
            file_mode=verification.file_mode,
        )
    except GaleMirrorError:
        raise
    except Exception as error:
        raise GaleMirrorError("mirror_generation_failed") from error
    finally:
        _remove_temporary_database_files(temporary_path)


def verify_gale_mirror(
    mirror_path: Path,
    *,
    expected_note_count: int | None = None,
    expected_actor: str = GALE_ACTOR_ID,
) -> GaleMirrorVerification:
    path = Path(mirror_path)
    if not path.is_file() or path.is_symlink():
        raise GaleMirrorError("mirror_unavailable")
    if _file_mode(path) != "0600":
        raise GaleMirrorError("mirror_permissions_unsafe")

    connection = _open_mirror_read_only(path)
    try:
        integrity_rows = connection.execute("PRAGMA integrity_check").fetchall()
        if [str(row[0]) for row in integrity_rows] != ["ok"]:
            raise GaleMirrorError("integrity_check_failed")

        schema_rows = connection.execute(
            """
            SELECT type, name
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
        table_names = {
            str(row["name"]) for row in schema_rows if row["type"] == "table"
        }
        index_names = {
            str(row["name"]) for row in schema_rows if row["type"] == "index"
        }
        if not REQUIRED_TABLES.issubset(table_names):
            raise GaleMirrorError("required_table_missing")
        if not table_names.issubset(ALLOWED_TABLES):
            raise GaleMirrorError("unexpected_table")
        if not REQUIRED_INDEXES.issubset(index_names):
            raise GaleMirrorError("required_index_missing")

        metadata = {
            str(row["meta_key"]): str(row["meta_value"])
            for row in connection.execute(
                "SELECT meta_key, meta_value FROM mirror_metadata"
            ).fetchall()
        }
        required_metadata = {
            "mirror_schema_version",
            "generated_at",
            "target_actor",
            "generator_app_version",
            "note_count",
            "generation_summary",
        }
        if set(metadata) != required_metadata:
            raise GaleMirrorError("metadata_invalid")
        schema_version = metadata["mirror_schema_version"]
        if schema_version not in {"1", MIRROR_SCHEMA_VERSION}:
            raise GaleMirrorError("schema_version_mismatch")
        if metadata["target_actor"] != expected_actor:
            raise GaleMirrorError("target_actor_mismatch")
        if not metadata["generated_at"] or not metadata["generator_app_version"]:
            raise GaleMirrorError("metadata_invalid")

        note_count = int(
            connection.execute("SELECT COUNT(*) FROM mirror_notes").fetchone()[0]
        )
        fts_count = int(
            connection.execute("SELECT COUNT(*) FROM mirror_notes_fts").fetchone()[0]
        )
        invalid_visibility_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM mirror_notes
                WHERE visibility NOT IN ('full', 'redacted')
                   OR redacted != CASE visibility WHEN 'redacted' THEN 1 ELSE 0 END
                """
            ).fetchone()[0]
        )
        fts_mismatch_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM mirror_notes_fts AS search
                JOIN mirror_notes AS notes ON notes.mirror_note_id=search.rowid
                WHERE search.title != notes.title
                   OR search.body != notes.body
                   OR search.directory != notes.directory
                   OR search.tags != notes.tags_text
                """
            ).fetchone()[0]
        )
        missing_fts_row_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM mirror_notes AS notes
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM mirror_notes_fts AS search
                    WHERE search.rowid = notes.mirror_note_id
                )
                """
            ).fetchone()[0]
        )
        orphaned_fts_row_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM mirror_notes_fts AS search
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM mirror_notes AS notes
                    WHERE notes.mirror_note_id = search.rowid
                )
                """
            ).fetchone()[0]
        )
        full_note_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM mirror_notes WHERE visibility='full'"
            ).fetchone()[0]
        )
        redacted_note_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM mirror_notes WHERE visibility='redacted'"
            ).fetchone()[0]
        )
        if invalid_visibility_count:
            raise GaleMirrorError("unsafe_visibility")
        if note_count != fts_count:
            raise GaleMirrorError("fts_count_mismatch")
        if missing_fts_row_count or orphaned_fts_row_count:
            raise GaleMirrorError("fts_rowid_mismatch")
        if fts_mismatch_count:
            raise GaleMirrorError("fts_content_mismatch")
        if CATALOG_TABLES.issubset(table_names):
            _validate_catalog_tables(connection)
        if int(metadata["note_count"]) != note_count:
            raise GaleMirrorError("metadata_count_mismatch")
        if expected_note_count is not None and note_count != expected_note_count:
            raise GaleMirrorError("expected_count_mismatch")

        try:
            summary = json.loads(metadata["generation_summary"])
        except (TypeError, ValueError) as error:
            raise GaleMirrorError("metadata_invalid") from error
        if summary != {
            "full_note_count": full_note_count,
            "redacted_note_count": redacted_note_count,
        }:
            raise GaleMirrorError("metadata_count_mismatch")
    except sqlite3.Error as error:
        raise GaleMirrorError("mirror_validation_failed") from error
    finally:
        connection.close()

    return GaleMirrorVerification(
        note_count=note_count,
        fts_count=fts_count,
        full_note_count=full_note_count,
        redacted_note_count=redacted_note_count,
        file_mode=_file_mode(path),
        schema_version=schema_version,
    )


def search_gale_mirror(
    mirror_path: Path,
    query: str,
    *,
    limit: int = 20,
) -> list[dict]:
    parsed_query = parse_search_query(query)
    if not parsed_query.tokens:
        return []
    clean_limit = max(1, min(int(limit), 100))
    connection = _open_mirror_read_only(Path(mirror_path))
    try:
        rows = search_gale_mirror_rows(
            connection,
            parsed_query,
            limit=clean_limit,
        )
    except sqlite3.Error as error:
        raise GaleMirrorError("mirror_search_failed") from error
    finally:
        connection.close()
    return [
        {
            "source_note_id": int(row["source_note_id"]),
            "title": str(row["title"]),
            "body": str(row["body"]),
            "directory": str(row["directory"]),
            "tags": json.loads(row["tags_json"]),
            "source_updated_at": str(row["source_updated_at"]),
            "visibility": str(row["visibility"]),
        }
        for row in rows
    ]


def search_gale_mirror_rows(
    connection: sqlite3.Connection,
    query: object,
    *,
    limit: int,
) -> list[sqlite3.Row]:
    """Search only already-materialized safe mirror fields on a read-only connection."""
    parsed_query = parse_search_query(query)
    if not parsed_query.tokens:
        return []
    exact_fts_ids: set[int] = set()
    match_expression = build_safe_fts_phrase(parsed_query)
    if match_expression is not None:
        exact_fts_ids = {
            int(row[0])
            for row in connection.execute(
                "SELECT rowid FROM mirror_notes_fts WHERE mirror_notes_fts MATCH ?",
                (match_expression,),
            ).fetchall()
        }
    rows = connection.execute(
        """
        SELECT mirror_note_id, source_note_id, title, body, directory,
               tags_json, tags_text, source_updated_at, visibility
        FROM mirror_notes
        """
    ).fetchall()
    ranked: list[tuple[int, int, str, int, sqlite3.Row]] = []
    for row in rows:
        match = match_search_fields(
            parsed_query,
            title=row["title"],
            body=row["body"],
            directory=row["directory"],
            tags=row["tags_text"],
        )
        if match is None:
            continue
        ranked.append(
            (
                match.score,
                int(row["mirror_note_id"] in exact_fts_ids),
                str(row["source_updated_at"]),
                int(row["source_note_id"]),
                row,
            )
        )
    ranked.sort(key=lambda item: item[:4], reverse=True)
    return [item[4] for item in ranked[: int(limit)]]


def _validate_export_paths(
    source: Path,
    output: Path,
    rules: Path,
    actor: str,
) -> None:
    if actor != GALE_ACTOR_ID:
        raise GaleMirrorError("unsupported_actor")
    if not source.is_file():
        raise GaleMirrorError("source_database_unavailable")
    if not rules.is_file():
        raise GaleMirrorError("redaction_rules_unavailable")
    if not output.parent.is_dir():
        raise GaleMirrorError("output_directory_unavailable")
    if output.is_symlink():
        raise GaleMirrorError("unsafe_output_path")
    if output.exists() and not output.is_file():
        raise GaleMirrorError("unsafe_output_path")
    if source.resolve() == output.resolve():
        raise GaleMirrorError("source_output_conflict")
    if rules.resolve() == output.resolve():
        raise GaleMirrorError("rules_output_conflict")


def _new_temporary_mirror_path(output: Path) -> Path:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.",
        suffix=".tmp",
        dir=output.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    os.chmod(temporary_path, 0o600)
    return temporary_path


def _write_gale_mirror_database(
    path: Path,
    records: Sequence[GaleMirrorSafeRecord],
    *,
    actor: str,
    generator_app_version: str,
    directories: Sequence[dict] = (),
    tags: Sequence[str] = (),
    visibility_options: Sequence[tuple[str, str]] = (),
    ai_targets: Sequence[tuple[str, str]] = (),
) -> None:
    if not all(isinstance(record, GaleMirrorSafeRecord) for record in records):
        raise GaleMirrorError("raw_record_rejected")
    full_note_count = sum(
        record.visibility == AI_VISIBILITY_FULL for record in records
    )
    redacted_note_count = sum(
        record.visibility == AI_VISIBILITY_REDACTED for record in records
    )
    metadata = {
        "mirror_schema_version": MIRROR_SCHEMA_VERSION,
        "generated_at": now_shanghai_iso(),
        "target_actor": actor,
        "generator_app_version": str(generator_app_version),
        "note_count": str(len(records)),
        "generation_summary": json.dumps(
            {
                "full_note_count": full_note_count,
                "redacted_note_count": redacted_note_count,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
    }

    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.execute("PRAGMA synchronous = FULL")
        connection.executescript(MIRROR_SCHEMA_SQL)
        connection.executemany(
            "INSERT INTO mirror_metadata(meta_key, meta_value) VALUES (?, ?)",
            metadata.items(),
        )
        for mirror_note_id, record in enumerate(records, start=1):
            tags_json = json.dumps(
                record.tags,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            tags_text = " ".join(record.tags)
            connection.execute(
                """
                INSERT INTO mirror_notes (
                    mirror_note_id, source_note_id, title, body, directory,
                        tags_json, tags_text, source_updated_at, source_version,
                        visibility, redacted, redaction_count, ai_visibility,
                        ai_targets_json, main_category, sub_category
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                (
                    mirror_note_id,
                    record.source_note_id,
                    record.title,
                    record.body,
                    record.directory,
                    tags_json,
                    tags_text,
                    record.source_updated_at,
                    record.source_version,
                    record.visibility,
                    int(record.redacted),
                    record.redaction_count,
                    record.ai_visibility,
                    json.dumps(
                        record.ai_targets,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    record.main_category,
                    record.sub_category,
                ),
            )
            connection.execute(
                """
                INSERT INTO mirror_notes_fts(rowid, title, body, directory, tags)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    mirror_note_id,
                    record.title,
                    record.body,
                    record.directory,
                    tags_text,
                ),
            )
        for directory in directories:
            connection.execute(
                """
                INSERT INTO mirror_directories (
                    directory_id, name, parent_id, main_category,
                    sub_category, path_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    int(directory["directory_id"]),
                    str(directory["name"]),
                    directory["parent_id"],
                    str(directory["main_category"]),
                    str(directory.get("sub_category") or ""),
                    json.dumps(
                        tuple(directory["path"]),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                ),
            )
        connection.executemany(
            "INSERT INTO mirror_tags(name) VALUES (?)",
            ((str(tag),) for tag in tags),
        )
        connection.executemany(
            """
            INSERT INTO mirror_ai_visibility_options(value, label)
            VALUES (?, ?)
            """,
            ((str(value), str(label)) for value, label in visibility_options),
        )
        connection.executemany(
            """
            INSERT INTO mirror_ai_targets(target_id, display_name)
            VALUES (?, ?)
            """,
            ((str(target_id), str(display_name)) for target_id, display_name in ai_targets),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _open_mirror_read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=5.0,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


def _remove_temporary_database_files(temporary_path: Path) -> None:
    for candidate in (
        temporary_path,
        Path(f"{temporary_path}-journal"),
        Path(f"{temporary_path}-wal"),
        Path(f"{temporary_path}-shm"),
    ):
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_with_formal_metadata(
    source: Path,
    record: GaleMirrorSafeRecord,
) -> GaleMirrorSafeRecord:
    connection = sqlite3.connect(source)
    connection.row_factory = sqlite3.Row
    try:
        note_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(notes)").fetchall()
        }
        sub_sub_expression = (
            "sub_sub_category"
            if "sub_sub_category" in note_columns
            else "'' AS sub_sub_category"
        )
        note = connection.execute(
            f"""
            SELECT main_category, sub_category, {sub_sub_expression}, ai_visibility
            FROM notes
            WHERE id = ?
            """,
            (record.source_note_id,),
        ).fetchone()
        if note is None:
            return record
        targets = tuple(
            str(row[0])
            for row in connection.execute(
                """
                SELECT target_id
                FROM note_ai_targets
                WHERE note_id = ?
                ORDER BY target_id
                """,
                (record.source_note_id,),
            ).fetchall()
        )
        tags = tuple(
            str(row[0])
            for row in connection.execute(
                """
                SELECT tags.name
                FROM note_tags
                JOIN tags ON tags.id = note_tags.tag_id
                WHERE note_tags.note_id = ?
                ORDER BY tags.name COLLATE NOCASE
                """,
                (record.source_note_id,),
            ).fetchall()
        )
    finally:
        connection.close()
    return replace(
        record,
        ai_visibility=str(note["ai_visibility"]),
        ai_targets=targets,
        main_category=str(note["main_category"]),
        sub_category=str(note["sub_category"]),
        directory=_formal_directory(note),
        tags=tags,
    )


def _formal_directory(note: sqlite3.Row) -> str:
    parts = (
        str(note["main_category"]),
        str(note["sub_category"]),
        str(note["sub_sub_category"]),
    )
    return " / ".join(part for part in parts if part)


def _metadata_catalog(source: Path) -> dict:
    connection = sqlite3.connect(source)
    connection.row_factory = sqlite3.Row
    try:
        directories = tuple(_directory_catalog(connection))
        tags = tuple(
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM tags ORDER BY name COLLATE NOCASE"
            ).fetchall()
        )
    finally:
        connection.close()
    return {
        "directories": directories,
        "tags": tags,
        "visibility_options": tuple(
            (visibility, visibility_display(visibility))
            for visibility in AI_VISIBILITY_LABELS
        ),
        "ai_targets": _ai_target_catalog(source),
    }


def _ai_target_catalog(source: Path) -> tuple[tuple[str, str], ...]:
    connection = sqlite3.connect(source)
    connection.row_factory = sqlite3.Row
    try:
        return tuple(
            (str(row["target_id"]), str(row["display_name"]))
            for row in connection.execute(
                """
                SELECT target_id, display_name
                FROM ai_targets
                WHERE COALESCE(is_enabled, 1) = 1
                ORDER BY rowid
                """
            ).fetchall()
        )
    finally:
        connection.close()


def _directory_catalog(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        """
        SELECT id, name, parent_id
        FROM directories
        WHERE COALESCE(is_deleted, 0) = 0
        ORDER BY parent_id, sort_order, name, id
        """
    ).fetchall()
    by_id = {int(row["id"]): row for row in rows}
    result: list[dict] = []
    for row in rows:
        path = _directory_path(by_id, int(row["id"]))
        result.append(
            {
                "directory_id": int(row["id"]),
                "name": str(row["name"]),
                "parent_id": (
                    int(row["parent_id"]) if row["parent_id"] is not None else None
                ),
                "main_category": path[0] if path else str(row["name"]),
                "sub_category": path[1] if len(path) > 1 else "",
                "path": path,
            }
        )
    return sorted(result, key=lambda item: (tuple(item["path"]), item["directory_id"]))


def _directory_path(
    rows: dict[int, sqlite3.Row],
    directory_id: int,
) -> tuple[str, ...]:
    names: list[str] = []
    current_id: int | None = directory_id
    seen: set[int] = set()
    while current_id is not None and current_id not in seen:
        seen.add(current_id)
        row = rows.get(current_id)
        if row is None:
            break
        names.append(str(row["name"]))
        current_id = (
            int(row["parent_id"]) if row["parent_id"] is not None else None
        )
    return tuple(reversed(names))


def _validate_catalog_tables(connection: sqlite3.Connection) -> None:
    invalid_directory = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM mirror_directories
            WHERE NOT json_valid(path_json)
            """
        ).fetchone()[0]
    )
    if invalid_directory:
        raise GaleMirrorError("catalog_invalid")


def _file_mode(path: Path) -> str:
    return f"{stat.S_IMODE(path.stat().st_mode):04o}"
