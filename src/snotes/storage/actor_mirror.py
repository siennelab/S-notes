"""SQLite candidate writer and strict validator for GreenCloud actor mirrors.

This intentionally has a separate schema from the frozen Gale production
mirror: safe Markdown is a GreenCloud-only field.
"""

from __future__ import annotations

import hashlib
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
 directory TEXT NOT NULL, tags_json TEXT NOT NULL CHECK(json_valid(tags_json)),
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
    "hash", "source_schema", "markdown_dialect",
}


class SQLiteActorMirrorWriter(ActorMirrorWriter):
    def write(self, records, output_path: Path, *, actor: str, source_schema: str) -> dict:
        return write_actor_mirror(records, output_path, actor=actor, source_schema=source_schema)

    def verify(self, path: Path, *, expected_actor: str, expected_note_count: int | None = None) -> dict:
        return verify_actor_mirror(path, expected_actor=expected_actor, expected_note_count=expected_note_count)


def write_actor_mirror(
    records: tuple[ActorMirrorRecord, ...], output_path: Path, *, actor: str,
    source_schema: str, generated_at: str | None = None,
) -> dict:
    output = Path(output_path)
    if not output.parent.is_dir() or output.is_symlink():
        raise ActorMirrorError("unsafe_output_path")
    if not all(isinstance(record, ActorMirrorRecord) for record in records):
        raise ActorMirrorError("raw_record_rejected")
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
            content_hash = records_hash(records)
            metadata = {
                "mirror_schema_version": ACTOR_MIRROR_SCHEMA_VERSION,
                "generated_at": generated_at or datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
                "target_actor": actor,
                "note_count": str(len(records)),
                "hash": content_hash,
                "source_schema": source_schema,
                "markdown_dialect": "snotes-markdown-v1",
            }
            connection.executemany("INSERT INTO mirror_metadata VALUES (?,?)", metadata.items())
            for index, item in enumerate(records, 1):
                tags_json = json.dumps(item.tags, ensure_ascii=False, separators=(",", ":"))
                connection.execute(
                    """INSERT INTO mirror_notes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (index, item.source_note_id, item.title, item.safe_text, item.safe_markdown,
                     item.directory, tags_json, " ".join(item.tags), item.source_updated_at,
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
        schema = connection.execute("SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
        tables = {row["name"] for row in schema if row["type"] == "table"}
        if tables != REQUIRED_TABLES:
            raise ActorMirrorError("schema_mismatch")
        metadata = {row["meta_key"]: row["meta_value"] for row in connection.execute("SELECT * FROM mirror_metadata")}
        if set(metadata) != REQUIRED_METADATA or metadata["mirror_schema_version"] != ACTOR_MIRROR_SCHEMA_VERSION:
            raise ActorMirrorError("metadata_invalid")
        if metadata["target_actor"] != expected_actor:
            raise ActorMirrorError("target_actor_mismatch")
        if metadata["markdown_dialect"] != "snotes-markdown-v1" or not metadata["generated_at"]:
            raise ActorMirrorError("metadata_invalid")
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
        if records_hash(records) != metadata["hash"]:
            raise ActorMirrorError("hash_mismatch")
        return {"note_count": len(notes), "target_actor": expected_actor, "hash": metadata["hash"]}
    except ActorMirrorError:
        raise
    except (sqlite3.Error, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise ActorMirrorError("mirror_validation_failed") from error
    finally:
        if "connection" in locals():
            connection.close()


def records_hash(records: tuple[ActorMirrorRecord, ...]) -> str:
    payload = json.dumps([{
        "source_note_id": row.source_note_id, "title": row.title, "safe_text": row.safe_text,
        "safe_markdown": row.safe_markdown, "directory": row.directory, "tags": row.tags,
        "source_updated_at": row.source_updated_at, "source_version": row.source_version,
        "visibility": row.visibility, "redaction_count": row.redaction_count,
        "ai_visibility": row.ai_visibility, "ai_targets": row.ai_targets, "dialect": row.dialect,
    } for row in records], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
