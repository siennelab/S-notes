"""Fresh-connection, read-only access to one GreenCloud actor mirror."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Sequence

from src.snotes.domain.actor_mirror import ActorMirrorError
from src.snotes.domain.search_query import match_search_fields, parse_search_query
from src.snotes.storage.actor_mirror import validate_metadata_catalog


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
        parsed_query = parse_search_query(" ".join(str(token) for token in tokens))
        if not parsed_query.tokens:
            return []
        connection = self._open()
        try:
            rows = connection.execute(
                """
                SELECT notes.source_note_id, notes.title, notes.safe_text,
                       notes.directory, notes.tags_json, notes.tags_text,
                       notes.source_updated_at, notes.source_version,
                       notes.visibility, notes.main_category, notes.sub_category,
                       notes.ai_visibility, notes.ai_targets_json
                FROM mirror_notes AS notes
                """,
            ).fetchall()
            ranked: list[tuple[int, str, int, sqlite3.Row]] = []
            for row in rows:
                match = match_search_fields(
                    parsed_query,
                    title=row["title"],
                    body=row["safe_text"],
                    directory=row["directory"],
                    tags=row["tags_text"],
                )
                if match is None:
                    continue
                ranked.append(
                    (
                        match.score,
                        str(row["source_updated_at"]),
                        int(row["source_note_id"]),
                        row,
                    )
                )
            ranked.sort(key=lambda item: item[:3], reverse=True)
            return [self._safe_note(item[3]) for item in ranked[: int(limit)]]
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
                       source_updated_at, source_version, visibility,
                       main_category, sub_category, ai_visibility, ai_targets_json
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

    def metadata_catalog(self) -> dict:
        connection = self._open()
        try:
            row = connection.execute(
                "SELECT meta_value FROM mirror_metadata WHERE meta_key = 'metadata_catalog'"
            ).fetchone()
            if row is None:
                raise CloudMirrorReadError("catalog_read_failed")
            return validate_metadata_catalog(json.loads(row["meta_value"]))
        except (sqlite3.Error, ValueError, TypeError, ActorMirrorError) as error:
            raise CloudMirrorReadError("catalog_read_failed") from error
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
        targets = json.loads(row["ai_targets_json"])
        if not isinstance(targets, list) or not all(isinstance(target, str) for target in targets):
            raise CloudMirrorReadError("mirror_read_failed")
        return {
            "note_id": int(row["source_note_id"]),
            "title": str(row["title"]),
            "safe_text": str(row["safe_text"]),
            "directory": str(row["directory"]),
            "main_category": str(row["main_category"]),
            "sub_category": str(row["sub_category"]),
            "tags": tags,
            "ai_visibility": str(row["ai_visibility"]),
            "ai_targets": targets,
            "updated_at": str(row["source_updated_at"]),
            "version": str(row["source_version"]),
            "visibility": str(row["visibility"]),
        }
