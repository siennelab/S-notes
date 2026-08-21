"""SQLite owner for the frozen-R1 Gale mirror schema and validation."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import tempfile
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.snotes.application.ports import GaleMirrorWriter
from src.snotes.domain.gale import (
    MIRROR_SCHEMA_VERSION,
    GaleError,
    GaleMirrorRecord,
    GaleMirrorResult,
    GaleMirrorVerification,
)


GENERATOR_APP_VERSION = "S-notes 1.0.0 R1A"
REQUIRED_TABLES = {"mirror_metadata", "mirror_notes", "mirror_notes_fts"}
CATALOG_TABLES = {
    "mirror_directories",
    "mirror_tags",
    "mirror_ai_visibility_options",
    "mirror_ai_targets",
}
FTS_SHADOW_TABLES = {
    "mirror_notes_fts_data", "mirror_notes_fts_idx",
    "mirror_notes_fts_content", "mirror_notes_fts_docsize",
    "mirror_notes_fts_config",
}
SCHEMA = """
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
    CHECK ((visibility = 'full' AND redacted = 0)
        OR (visibility = 'redacted' AND redacted = 1))
);
CREATE INDEX idx_mirror_notes_source_updated_at
ON mirror_notes(source_updated_at DESC, source_note_id DESC);
CREATE VIRTUAL TABLE mirror_notes_fts USING fts5(
    title, body, directory, tags, tokenize='unicode61'
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


class SQLiteGaleMirrorWriter(GaleMirrorWriter):
    def write(
        self,
        records: Sequence[GaleMirrorRecord],
        output_path: Path,
        *,
        actor_target_id: str,
        directories: Sequence[dict] = (),
        tags: Sequence[str] = (),
        visibility_options: Sequence[tuple[str, str]] = (),
        ai_targets: Sequence[tuple[str, str]] = (),
    ) -> GaleMirrorResult:
        output = Path(output_path)
        if not output.parent.is_dir() or output.is_symlink():
            raise GaleError("unsafe_output_path")
        if not all(isinstance(item, GaleMirrorRecord) for item in records):
            raise GaleError("raw_record_rejected")
        descriptor, name = tempfile.mkstemp(
            prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
        )
        os.close(descriptor)
        candidate = Path(name)
        os.chmod(candidate, 0o600)
        try:
            self._write_database(
                candidate,
                records,
                actor_target_id,
                directories=directories,
                tags=tags,
                visibility_options=visibility_options,
                ai_targets=ai_targets,
            )
            verification = self.verify(
                candidate,
                expected_note_count=len(records),
                expected_actor=actor_target_id,
            )
            digest = _sha256(candidate)
            os.replace(candidate, output)
            os.chmod(output, 0o600)
        except GaleError:
            raise
        except Exception as error:
            raise GaleError("mirror_generation_failed") from error
        finally:
            for path in (candidate, Path(f"{candidate}-journal")):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        return GaleMirrorResult(
            output,
            MIRROR_SCHEMA_VERSION,
            actor_target_id,
            verification.note_count,
            verification.full_note_count,
            verification.redacted_note_count,
            digest,
            verification.file_mode,
        )

    def verify(
        self,
        mirror_path: Path,
        *,
        expected_note_count: int | None = None,
        expected_actor: str,
    ) -> GaleMirrorVerification:
        path = Path(mirror_path)
        if not path.is_file() or path.is_symlink():
            raise GaleError("mirror_unavailable")
        if _mode(path) != "0600":
            raise GaleError("mirror_permissions_unsafe")
        connection = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        try:
            if [row[0] for row in connection.execute(
                "PRAGMA integrity_check"
            ).fetchall()] != ["ok"]:
                raise GaleError("integrity_check_failed")
            schema = connection.execute(
                "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            tables = {str(row["name"]) for row in schema if row["type"] == "table"}
            indexes = {str(row["name"]) for row in schema if row["type"] == "index"}
            if not REQUIRED_TABLES.issubset(tables):
                raise GaleError("required_table_missing")
            if not tables.issubset(REQUIRED_TABLES | CATALOG_TABLES | FTS_SHADOW_TABLES):
                raise GaleError("unexpected_table")
            if "idx_mirror_notes_source_updated_at" not in indexes:
                raise GaleError("required_index_missing")
            metadata = {
                str(row[0]): str(row[1])
                for row in connection.execute(
                    "SELECT meta_key, meta_value FROM mirror_metadata"
                ).fetchall()
            }
            required = {
                "mirror_schema_version", "generated_at", "target_actor",
                "generator_app_version", "note_count", "generation_summary",
            }
            if set(metadata) != required:
                raise GaleError("metadata_invalid")
            if metadata["mirror_schema_version"] != MIRROR_SCHEMA_VERSION:
                raise GaleError("schema_version_mismatch")
            if metadata["target_actor"] != expected_actor:
                raise GaleError("target_actor_mismatch")
            if not metadata["generated_at"] or not metadata["generator_app_version"]:
                raise GaleError("metadata_invalid")
            note_count = int(connection.execute(
                "SELECT COUNT(*) FROM mirror_notes"
            ).fetchone()[0])
            fts_count = int(connection.execute(
                "SELECT COUNT(*) FROM mirror_notes_fts"
            ).fetchone()[0])
            full_count = int(connection.execute(
                "SELECT COUNT(*) FROM mirror_notes WHERE visibility='full'"
            ).fetchone()[0])
            redacted_count = int(connection.execute(
                "SELECT COUNT(*) FROM mirror_notes WHERE visibility='redacted'"
            ).fetchone()[0])
            invalid = int(connection.execute(
                """SELECT COUNT(*) FROM mirror_notes
                WHERE visibility NOT IN ('full', 'redacted')
                   OR redacted != CASE visibility WHEN 'redacted' THEN 1 ELSE 0 END"""
            ).fetchone()[0])
            content_mismatch = int(connection.execute(
                """SELECT COUNT(*)
                FROM mirror_notes_fts AS search
                JOIN mirror_notes AS notes ON notes.mirror_note_id = search.rowid
                WHERE search.title != notes.title OR search.body != notes.body
                   OR search.directory != notes.directory
                   OR search.tags != notes.tags_text"""
            ).fetchone()[0])
            missing_fts = int(connection.execute(
                """SELECT COUNT(*) FROM mirror_notes AS notes
                WHERE NOT EXISTS (
                    SELECT 1 FROM mirror_notes_fts AS search
                    WHERE search.rowid = notes.mirror_note_id
                )"""
            ).fetchone()[0])
            orphaned_fts = int(connection.execute(
                """SELECT COUNT(*) FROM mirror_notes_fts AS search
                WHERE NOT EXISTS (
                    SELECT 1 FROM mirror_notes AS notes
                    WHERE notes.mirror_note_id = search.rowid
                )"""
            ).fetchone()[0])
            if invalid:
                raise GaleError("unsafe_visibility")
            if note_count != fts_count:
                raise GaleError("fts_count_mismatch")
            if missing_fts or orphaned_fts:
                raise GaleError("fts_rowid_mismatch")
            if content_mismatch:
                raise GaleError("fts_content_mismatch")
            if CATALOG_TABLES.issubset(tables):
                _validate_catalog_tables(connection)
            if int(metadata["note_count"]) != note_count:
                raise GaleError("metadata_count_mismatch")
            if expected_note_count is not None and note_count != expected_note_count:
                raise GaleError("expected_count_mismatch")
            summary = json.loads(metadata["generation_summary"])
            if summary != {
                "full_note_count": full_count,
                "redacted_note_count": redacted_count,
            }:
                raise GaleError("metadata_count_mismatch")
        except (sqlite3.Error, ValueError, json.JSONDecodeError) as error:
            if isinstance(error, GaleError):
                raise
            raise GaleError("mirror_validation_failed") from error
        finally:
            connection.close()
        return GaleMirrorVerification(
            note_count, fts_count, full_count, redacted_count, _mode(path)
        )

    @staticmethod
    def _write_database(
        path: Path,
        records: Sequence[GaleMirrorRecord],
        actor_target_id: str,
        *,
        directories: Sequence[dict] = (),
        tags: Sequence[str] = (),
        visibility_options: Sequence[tuple[str, str]] = (),
        ai_targets: Sequence[tuple[str, str]] = (),
    ) -> None:
        full = sum(item.visibility == "full" for item in records)
        redacted = sum(item.visibility == "redacted" for item in records)
        metadata = {
            "mirror_schema_version": MIRROR_SCHEMA_VERSION,
            "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(
                timespec="seconds"
            ),
            "target_actor": actor_target_id,
            "generator_app_version": GENERATOR_APP_VERSION,
            "note_count": str(len(records)),
            "generation_summary": json.dumps(
                {"full_note_count": full, "redacted_note_count": redacted},
                ensure_ascii=True, sort_keys=True, separators=(",", ":"),
            ),
        }
        connection = sqlite3.connect(path)
        try:
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(SCHEMA)
            connection.executemany(
                "INSERT INTO mirror_metadata(meta_key, meta_value) VALUES (?, ?)",
                metadata.items(),
            )
            for mirror_id, item in enumerate(records, 1):
                tags_json = json.dumps(
                    item.tags, ensure_ascii=False, separators=(",", ":")
                )
                tags_text = " ".join(item.tags)
                connection.execute(
                    """INSERT INTO mirror_notes (
                        mirror_note_id, source_note_id, title, body, directory,
                        tags_json, tags_text, source_updated_at, source_version,
                        visibility, redacted, redaction_count, ai_visibility,
                        ai_targets_json, main_category, sub_category
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        mirror_id, item.source_note_id, item.title, item.body,
                        item.directory, tags_json, tags_text,
                        item.source_updated_at, item.source_version,
                        item.visibility, int(item.redacted), item.redaction_count,
                        item.ai_visibility,
                        json.dumps(
                            item.ai_targets,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        item.main_category,
                        item.sub_category,
                    ),
                )
                connection.execute(
                    """INSERT INTO mirror_notes_fts(
                        rowid, title, body, directory, tags
                    ) VALUES (?, ?, ?, ?, ?)""",
                    (mirror_id, item.title, item.body, item.directory, tags_text),
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


def _mode(path: Path) -> str:
    return f"{stat.S_IMODE(path.stat().st_mode):04o}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_catalog_tables(connection: sqlite3.Connection) -> None:
    for table in CATALOG_TABLES:
        count = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        if count < 0:
            raise GaleError("catalog_invalid")
    invalid_directory = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM mirror_directories
            WHERE NOT json_valid(path_json)
            """
        ).fetchone()[0]
    )
    if invalid_directory:
        raise GaleError("catalog_invalid")
