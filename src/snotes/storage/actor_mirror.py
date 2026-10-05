"""SQLite candidate writer and strict validator for GreenCloud actor mirrors.

This intentionally has a separate schema from the frozen Gale production
mirror: safe Markdown is a GreenCloud-only field.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from contextlib import closing
import json
import os
import sqlite3
import stat
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.snotes.domain.actor_mirror import (
    ACTOR_MIRROR_SCHEMA_VERSION,
    ActorMirrorError,
    ActorMirrorRecord,
)
from src.snotes.application.ports import ActorMirrorWriter


SCHEMA = """
CREATE TABLE mirror_metadata (meta_key TEXT PRIMARY KEY, meta_value TEXT NOT NULL);
CREATE TABLE mirror_notes (
 mirror_note_id INTEGER PRIMARY KEY,
 source_note_id INTEGER NOT NULL UNIQUE CHECK(source_note_id > 0),
 title TEXT NOT NULL, safe_text TEXT NOT NULL, safe_markdown TEXT,
 directory TEXT NOT NULL, main_category TEXT NOT NULL, sub_category TEXT NOT NULL,
 tags_json TEXT NOT NULL CHECK(json_valid(tags_json)),
 tags_text TEXT NOT NULL, source_updated_at TEXT NOT NULL,
 source_version TEXT NOT NULL, visibility TEXT NOT NULL CHECK(visibility IN ('full','redacted')),
 redaction_count INTEGER NOT NULL CHECK(redaction_count >= 0),
 ai_visibility TEXT NOT NULL, ai_targets_json TEXT NOT NULL CHECK(json_valid(ai_targets_json)),
 dialect TEXT NOT NULL CHECK(dialect = 'snotes-markdown-v1'),
 CHECK((visibility='full' AND safe_markdown IS NOT NULL AND redaction_count=0)
    OR (visibility='redacted' AND safe_markdown IS NULL))
);
CREATE INDEX idx_actor_mirror_source_updated
 ON mirror_notes(source_updated_at DESC, source_note_id DESC);
CREATE VIRTUAL TABLE mirror_notes_fts USING fts5(title, safe_text, directory, tags, tokenize='unicode61');
"""
REQUIRED_TABLES = {
    "mirror_metadata", "mirror_notes", "mirror_notes_fts",
    "mirror_notes_fts_data", "mirror_notes_fts_idx", "mirror_notes_fts_content",
    "mirror_notes_fts_docsize", "mirror_notes_fts_config",
}
REQUIRED_METADATA = {
    "mirror_schema_version", "generated_at", "target_actor", "note_count",
    "hash", "source_schema", "markdown_dialect", "metadata_catalog",
}

EMPTY_METADATA_CATALOG = {
    "directories": [],
    "tags": [],
    "visibility_options": [],
    "ai_targets": [],
}


class SQLiteActorMirrorWriter(ActorMirrorWriter):
    def write(
        self, records, output_path: Path, *, actor: str, source_schema: str,
        metadata_catalog: Mapping[str, object] | None = None,
    ) -> dict:
        return write_actor_mirror(
            records, output_path, actor=actor, source_schema=source_schema,
            metadata_catalog=metadata_catalog,
        )

    def verify(self, path: Path, *, expected_actor: str, expected_note_count: int | None = None) -> dict:
        return verify_actor_mirror(path, expected_actor=expected_actor, expected_note_count=expected_note_count)


def write_actor_mirror(
    records: tuple[ActorMirrorRecord, ...], output_path: Path, *, actor: str,
    source_schema: str, generated_at: str | None = None,
    metadata_catalog: Mapping[str, object] | None = None,
) -> dict:
    output = Path(output_path)
    if not output.parent.is_dir() or output.is_symlink():
        raise ActorMirrorError("unsafe_output_path")
    if not all(isinstance(record, ActorMirrorRecord) for record in records):
        raise ActorMirrorError("raw_record_rejected")
    catalog = validate_metadata_catalog(
        EMPTY_METADATA_CATALOG if metadata_catalog is None else metadata_catalog
    )
    fd, name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".candidate", dir=output.parent)
    os.close(fd)
    candidate = Path(name)
    os.chmod(candidate, 0o600)
    try:
        connection = sqlite3.connect(candidate)
        try:
            connection.execute("PRAGMA journal_mode=DELETE")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(SCHEMA)
            content_hash = records_hash(records, catalog)
            metadata = {
                "mirror_schema_version": ACTOR_MIRROR_SCHEMA_VERSION,
                "generated_at": generated_at or datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
                "target_actor": actor,
                "note_count": str(len(records)),
                "hash": content_hash,
                "source_schema": source_schema,
                "markdown_dialect": "snotes-markdown-v1",
                "metadata_catalog": json.dumps(
                    catalog, ensure_ascii=False, sort_keys=True,
                    separators=(",", ":"),
                ),
            }
            connection.executemany("INSERT INTO mirror_metadata VALUES (?,?)", metadata.items())
            for index, item in enumerate(records, 1):
                tags_json = json.dumps(item.tags, ensure_ascii=False, separators=(",", ":"))
                connection.execute(
                    """INSERT INTO mirror_notes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (index, item.source_note_id, item.title, item.safe_text, item.safe_markdown,
                     item.directory, item.main_category, item.sub_category,
                     tags_json, " ".join(item.tags), item.source_updated_at,
                     item.source_version, item.visibility, item.redaction_count, item.ai_visibility,
                     json.dumps(item.ai_targets, ensure_ascii=False, separators=(",", ":")), item.dialect),
                )
                connection.execute("INSERT INTO mirror_notes_fts(rowid,title,safe_text,directory,tags) VALUES (?,?,?,?,?)",
                                   (index, item.title, item.safe_text, item.directory, " ".join(item.tags)))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        verification = verify_actor_mirror(candidate, expected_actor=actor, expected_note_count=len(records))
        digest = _file_hash(candidate)
        os.replace(candidate, output)
        os.chmod(output, 0o600)
        return {**verification, "hash": content_hash, "file_hash": digest, "output_path": output}
    except ActorMirrorError:
        raise
    except Exception as error:
        raise ActorMirrorError("mirror_generation_failed") from error
    finally:
        for suffix in ("", "-journal", "-wal", "-shm"):
            try:
                Path(f"{candidate}{suffix}").unlink()
            except FileNotFoundError:
                pass


def verify_actor_mirror(path: Path, *, expected_actor: str, expected_note_count: int | None = None) -> dict:
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise ActorMirrorError("mirror_unavailable")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ActorMirrorError("mirror_permissions_unsafe")
    try:
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        if [row[0] for row in connection.execute("PRAGMA integrity_check")] != ["ok"]:
            raise ActorMirrorError("integrity_check_failed")
        # Compare SQLite's complete stored DDL against this contract, including
        # columns, types, CHECK/UNIQUE/PK constraints, indexes and FTS shadows.
        # Building the reference with the same engine avoids FTS version drift.
        with closing(sqlite3.connect(":memory:")) as reference:
            reference.executescript(SCHEMA)
            query = "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
            expected = [tuple(row) for row in reference.execute(query)]
        if [tuple(row) for row in connection.execute(query)] != expected:
            raise ActorMirrorError("schema_mismatch")
        metadata = {row["meta_key"]: row["meta_value"] for row in connection.execute("SELECT * FROM mirror_metadata")}
        if set(metadata) != REQUIRED_METADATA or metadata["mirror_schema_version"] != ACTOR_MIRROR_SCHEMA_VERSION:
            raise ActorMirrorError("metadata_invalid")
        if metadata["target_actor"] != expected_actor:
            raise ActorMirrorError("target_actor_mismatch")
        if metadata["markdown_dialect"] != "snotes-markdown-v1" or not metadata["generated_at"]:
            raise ActorMirrorError("metadata_invalid")
        if metadata["source_schema"] != "snotes-markdown-v1":
            raise ActorMirrorError("source_schema_mismatch")
        catalog = validate_metadata_catalog(
            json.loads(metadata["metadata_catalog"])
        )
        notes = [dict(row) for row in connection.execute("SELECT * FROM mirror_notes ORDER BY mirror_note_id")]
        fts_count = int(connection.execute("SELECT COUNT(*) FROM mirror_notes_fts").fetchone()[0])
        if len(notes) != fts_count or int(metadata["note_count"]) != len(notes):
            raise ActorMirrorError("metadata_count_mismatch")
        if expected_note_count is not None and expected_note_count != len(notes):
            raise ActorMirrorError("expected_count_mismatch")
        fts_mismatch = connection.execute("""SELECT COUNT(*) FROM mirror_notes n JOIN mirror_notes_fts f
            ON f.rowid=n.mirror_note_id WHERE f.title!=n.title OR f.safe_text!=n.safe_text
            OR f.directory!=n.directory OR f.tags!=n.tags_text""").fetchone()[0]
        if fts_mismatch:
            raise ActorMirrorError("fts_content_mismatch")
        missing_fts, orphan_fts = connection.execute("""SELECT
            (SELECT COUNT(*) FROM mirror_notes n WHERE NOT EXISTS
                (SELECT 1 FROM mirror_notes_fts f WHERE f.rowid=n.mirror_note_id)),
            (SELECT COUNT(*) FROM mirror_notes_fts f WHERE NOT EXISTS
                (SELECT 1 FROM mirror_notes n WHERE n.mirror_note_id=f.rowid))""").fetchone()
        if missing_fts or orphan_fts:
            raise ActorMirrorError("fts_rowid_mismatch")
        records = tuple(ActorMirrorRecord(
            source_note_id=row["source_note_id"], title=row["title"], safe_text=row["safe_text"],
            safe_markdown=row["safe_markdown"], directory=row["directory"],
            main_category=row["main_category"], sub_category=row["sub_category"],
            tags=tuple(json.loads(row["tags_json"])), source_updated_at=row["source_updated_at"],
            source_version=row["source_version"], visibility=row["visibility"],
            redaction_count=row["redaction_count"], ai_visibility=row["ai_visibility"],
            ai_targets=tuple(json.loads(row["ai_targets_json"])), dialect=row["dialect"]
        ) for row in notes)
        for record in records:
            try:
                grants = set(json.loads(json.dumps(record.ai_targets)))
            except (ValueError, TypeError):
                raise ActorMirrorError("metadata_invalid") from None
            if record.visibility == "hidden" or not ({"common", expected_actor} & grants):
                raise ActorMirrorError("unauthorized_record")
            if record.visibility not in {"full", "redacted"}:
                raise ActorMirrorError("unsafe_visibility")
        if records_hash(records, catalog) != metadata["hash"]:
            raise ActorMirrorError("hash_mismatch")
        return {
            "note_count": len(notes), "target_actor": expected_actor,
            "hash": metadata["hash"], "metadata_catalog": catalog,
        }
    except ActorMirrorError:
        raise
    except (sqlite3.Error, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise ActorMirrorError("mirror_validation_failed") from error
    finally:
        if "connection" in locals():
            connection.close()


def records_hash(
    records: tuple[ActorMirrorRecord, ...],
    metadata_catalog: Mapping[str, object] | None = None,
) -> str:
    catalog = validate_metadata_catalog(
        EMPTY_METADATA_CATALOG if metadata_catalog is None else metadata_catalog
    )
    payload = json.dumps({
        "mirror_schema_version": ACTOR_MIRROR_SCHEMA_VERSION,
        "metadata_catalog": catalog,
        "records": [{
        "source_note_id": row.source_note_id, "title": row.title, "safe_text": row.safe_text,
        "safe_markdown": row.safe_markdown, "directory": row.directory,
        "main_category": row.main_category, "sub_category": row.sub_category,
        "tags": row.tags,
        "source_updated_at": row.source_updated_at, "source_version": row.source_version,
        "visibility": row.visibility, "redaction_count": row.redaction_count,
        "ai_visibility": row.ai_visibility, "ai_targets": row.ai_targets, "dialect": row.dialect,
    } for row in records],
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_metadata_catalog(catalog: object) -> dict[str, list]:
    """Return a plain validated catalog safe for hashing and Cloud reads."""
    if not isinstance(catalog, Mapping) or set(catalog) != set(EMPTY_METADATA_CATALOG):
        raise ActorMirrorError("metadata_catalog_invalid")
    directories = catalog["directories"]
    tags = catalog["tags"]
    visibility_options = catalog["visibility_options"]
    ai_targets = catalog["ai_targets"]
    if not all(isinstance(value, list) for value in (
        directories, tags, visibility_options, ai_targets
    )):
        raise ActorMirrorError("metadata_catalog_invalid")

    clean_directories: list[dict] = []
    directory_ids: set[int] = set()
    for item in directories:
        required = {
            "directory_id", "name", "parent_id", "main_category",
            "sub_category", "path",
        }
        if not isinstance(item, Mapping) or set(item) != required:
            raise ActorMirrorError("metadata_catalog_invalid")
        directory_id = item["directory_id"]
        parent_id = item["parent_id"]
        path = item["path"]
        if (
            isinstance(directory_id, bool)
            or not isinstance(directory_id, int)
            or directory_id < 1
            or directory_id in directory_ids
            or (
                parent_id is not None
                and (
                    isinstance(parent_id, bool)
                    or not isinstance(parent_id, int)
                    or parent_id < 1
                )
            )
            or not isinstance(path, list)
            or not 1 <= len(path) <= 2
            or not all(isinstance(value, str) and value for value in path)
        ):
            raise ActorMirrorError("metadata_catalog_invalid")
        name = item["name"]
        main_category = item["main_category"]
        sub_category = item["sub_category"]
        if (
            not isinstance(name, str) or not name
            or not isinstance(main_category, str) or not main_category
            or not isinstance(sub_category, str)
            or name != path[-1]
            or main_category != path[0]
            or sub_category != (path[1] if len(path) > 1 else "")
        ):
            raise ActorMirrorError("metadata_catalog_invalid")
        directory_ids.add(directory_id)
        clean_directories.append({
            "directory_id": directory_id,
            "name": name,
            "parent_id": parent_id,
            "main_category": main_category,
            "sub_category": sub_category,
            "path": list(path),
        })
    if any(
        item["parent_id"] is not None
        and item["parent_id"] not in directory_ids
        for item in clean_directories
    ):
        raise ActorMirrorError("metadata_catalog_invalid")

    if (
        not all(isinstance(tag, str) and tag for tag in tags)
        or len(tags) != len(set(tags))
    ):
        raise ActorMirrorError("metadata_catalog_invalid")

    clean_visibility = _validate_option_list(
        visibility_options, "value", "label"
    )
    clean_targets = _validate_option_list(
        ai_targets, "target_id", "display_name"
    )
    return {
        "directories": clean_directories,
        "tags": list(tags),
        "visibility_options": clean_visibility,
        "ai_targets": clean_targets,
    }


def _validate_option_list(
    values: list, identifier_key: str, label_key: str,
) -> list[dict]:
    clean: list[dict] = []
    seen: set[str] = set()
    for item in values:
        if (
            not isinstance(item, Mapping)
            or set(item) != {identifier_key, label_key}
        ):
            raise ActorMirrorError("metadata_catalog_invalid")
        identifier = item[identifier_key]
        label = item[label_key]
        if (
            not isinstance(identifier, str) or not identifier
            or not isinstance(label, str) or not label
            or identifier in seen
        ):
            raise ActorMirrorError("metadata_catalog_invalid")
        seen.add(identifier)
        clean.append({identifier_key: identifier, label_key: label})
    return clean


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
