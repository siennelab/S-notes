"""Fresh-connection, read-only access to one GreenCloud actor mirror."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Sequence


class CloudMirrorReadError(RuntimeError):
    """The fixed actor mirror could not be read safely."""


class SQLiteCloudMirrorReader:
    """Open and close the current mirror for every operation.

    ``current.db`` is replaced atomically by the receiver. Holding no connection
    between calls ensures the next MCP request observes the replacement inode.
    """

    def __init__(self, mirror_path: Path) -> None:
        self._mirror_path = Path(mirror_path).absolute()

    def search(self, tokens: Sequence[str], *, limit: int) -> list[dict]:
        expression = " AND ".join(
            f'"{str(token).replace(chr(34), chr(34) * 2)}"' for token in tokens
        )
        if not expression:
            return []
        connection = self._open()
        try:
            rows = connection.execute(
                """
                SELECT notes.source_note_id, notes.title, notes.safe_text,
                       notes.directory, notes.tags_json,
                       notes.source_updated_at, notes.source_version,
                       notes.visibility
                FROM mirror_notes_fts AS search
                JOIN mirror_notes AS notes ON notes.mirror_note_id = search.rowid
                WHERE mirror_notes_fts MATCH ?
                ORDER BY bm25(mirror_notes_fts), notes.source_updated_at DESC,
                         notes.source_note_id DESC
                LIMIT ?
                """,
                (expression, int(limit)),
            ).fetchall()
            return [self._safe_note(row) for row in rows]
        except (sqlite3.Error, ValueError, TypeError, json.JSONDecodeError) as error:
            raise CloudMirrorReadError("mirror_read_failed") from error
        finally:
            connection.close()

    def get(self, note_id: int) -> dict | None:
        connection = self._open()
        try:
            row = connection.execute(
                """
                SELECT source_note_id, title, safe_text, directory, tags_json,
                       source_updated_at, source_version, visibility
                FROM mirror_notes
                WHERE source_note_id = ?
                """,
                (int(note_id),),
            ).fetchone()
            return None if row is None else self._safe_note(row)
        except (sqlite3.Error, ValueError, TypeError, json.JSONDecodeError) as error:
            raise CloudMirrorReadError("mirror_read_failed") from error
        finally:
            connection.close()

    def get_markdown(self, note_id: int) -> dict | None:
        connection = self._open()
        try:
            row = connection.execute(
                """
                SELECT source_note_id, source_version, safe_markdown, dialect
                FROM mirror_notes
                WHERE source_note_id = ? AND safe_markdown IS NOT NULL
                """,
                (int(note_id),),
            ).fetchone()
            if row is None:
                return None
            return {
                "note_id": int(row["source_note_id"]),
                "version": str(row["source_version"]),
                "markdown": str(row["safe_markdown"]),
                "dialect": str(row["dialect"]),
            }
        except (sqlite3.Error, ValueError, TypeError) as error:
            raise CloudMirrorReadError("mirror_read_failed") from error
        finally:
            connection.close()

    def _open(self) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                f"{self._mirror_path.as_uri()}?mode=ro&immutable=1",
                uri=True,
                timeout=5.0,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            return connection
        except (sqlite3.Error, ValueError) as error:
            if connection is not None:
                connection.close()
            raise CloudMirrorReadError("mirror_unavailable") from error

    @staticmethod
    def _safe_note(row: sqlite3.Row) -> dict:
        tags = json.loads(row["tags_json"])
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise CloudMirrorReadError("mirror_read_failed")
        return {
            "note_id": int(row["source_note_id"]),
            "title": str(row["title"]),
            "safe_text": str(row["safe_text"]),
            "directory": str(row["directory"]),
            "tags": tags,
            "updated_at": str(row["source_updated_at"]),
            "version": str(row["source_version"]),
            "visibility": str(row["visibility"]),
        }
