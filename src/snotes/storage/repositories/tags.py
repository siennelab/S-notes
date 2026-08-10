"""SQLite implementation of frozen ordinary-tag behavior."""

from __future__ import annotations

from collections.abc import Sequence

from src.snotes.application.ports import TagsRepository
from src.snotes.domain.errors import NoteNotFoundError
from src.snotes.storage.connection import SQLiteConnectionFactory
from src.snotes.storage.support import Clock, normalize_tags, now_shanghai, timestamp_seconds


class SQLiteTagsRepository(TagsRepository):
    def __init__(
        self,
        connections: SQLiteConnectionFactory,
        *,
        clock: Clock = now_shanghai,
    ) -> None:
        self.connections = connections
        self.clock = clock

    def set_for_note(self, note_id: int, tags: Sequence[str]) -> list[str]:
        normalized = normalize_tags(tags)
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            exists = connection.execute(
                "SELECT id FROM notes WHERE id = ? AND is_deleted = 0", (note_id,)
            ).fetchone()
            if exists is None:
                raise NoteNotFoundError(note_id)
            self.replace_for_note_in_connection(
                connection,
                note_id,
                normalized,
                timestamp,
            )
            connection.execute(
                """
                UPDATE notes
                SET tags = ?, updated_at = ?, revision = revision + 1
                WHERE id = ?
                """,
                (", ".join(normalized), timestamp, note_id),
            )
        return normalized

    def set_for_new_note_in_connection(
        self,
        connection,
        note_id: int,
        tags: Sequence[str],
        timestamp: str,
    ) -> tuple[str, ...]:
        normalized = tuple(normalize_tags(tags))
        self.replace_for_note_in_connection(
            connection,
            note_id,
            normalized,
            timestamp,
        )
        connection.execute(
            "UPDATE notes SET tags = ?, updated_at = ? WHERE id = ?",
            (", ".join(normalized), timestamp, note_id),
        )
        return normalized

    @staticmethod
    def replace_for_note_in_connection(
        connection,
        note_id: int,
        tags: Sequence[str],
        timestamp: str,
    ) -> None:
        connection.execute("DELETE FROM note_tags WHERE note_id = ?", (note_id,))
        for tag in tags:
            connection.execute(
                "INSERT OR IGNORE INTO tags (name, created_at) VALUES (?, ?)",
                (tag, timestamp),
            )
            tag_id = connection.execute(
                "SELECT id FROM tags WHERE name = ?", (tag,)
            ).fetchone()["id"]
            connection.execute(
                "INSERT OR IGNORE INTO note_tags (note_id, tag_id) VALUES (?, ?)",
                (note_id, tag_id),
            )

    def get_for_note(self, note_id: int) -> list[str]:
        with self.connections.read() as connection:
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
        return [str(row["name"]) for row in rows]

    def list_all(self) -> list[str]:
        with self.connections.read() as connection:
            rows = connection.execute(
                "SELECT name FROM tags ORDER BY name COLLATE NOCASE ASC, id ASC"
            ).fetchall()
        return [str(row["name"]) for row in rows]
