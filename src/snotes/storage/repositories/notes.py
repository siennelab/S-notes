"""SQLite implementation of the ordinary note lifecycle port."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from datetime import datetime

from src.snotes.application.ports import AiTargetCatalogEntry, NotesRepository
from src.snotes.domain.errors import NoteNotFoundError, NoteRevisionConflictError
from src.snotes.domain.models import DEFAULT_AI_VISIBILITY, Note, NoteLocation
from src.snotes.storage.connection import SQLiteConnectionFactory
from src.snotes.storage.ai_metadata import (
    replace_note_ai_targets,
    sync_ai_target_catalog,
)
from src.snotes.storage.mappers import note_from_row
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.support import Clock, next_note_updated_at, now_shanghai, timestamp_seconds


NOTE_SELECT = """
SELECT id, note_uuid, revision, directory_id, title, content_html,
       main_category, sub_category, sub_sub_category, tags, ai_visibility,
       is_pinned, is_deleted, created_at, updated_at, deleted_at, trash_group_id
FROM notes
"""


class SQLiteNotesRepository(NotesRepository):
    def __init__(
        self,
        connections: SQLiteConnectionFactory,
        directories: SQLiteDirectoriesRepository,
        *,
        clock: Clock = now_shanghai,
        uuid_factory: Callable[[], uuid.UUID] = uuid.uuid4,
    ) -> None:
        self.connections = connections
        self.directories = directories
        self.clock = clock
        self.uuid_factory = uuid_factory

    def create(self, location: NoteLocation) -> Note:
        timestamp = timestamp_seconds(self.clock)
        note_uuid = str(self.uuid_factory())
        title = f"未命名笔记 {self.clock().strftime('%Y-%m-%d %H:%M')}"
        main, sub, sub_sub = location.values()
        with self.connections.write() as connection:
            directory_id = self.directories.ensure_location(connection, location)
            cursor = connection.execute(
                """
                INSERT INTO notes (
                    note_uuid, revision, directory_id, title, body_rich_text,
                    content_html, main_category, sub_category, sub_sub_category,
                    tags, ai_visibility, is_pinned, is_deleted, created_at, updated_at
                ) VALUES (?, 1, ?, ?, '', '', ?, ?, ?, '', ?, 0, 0, ?, ?)
                """,
                (
                    note_uuid,
                    directory_id,
                    title,
                    main,
                    sub,
                    sub_sub,
                    DEFAULT_AI_VISIBILITY,
                    timestamp,
                    timestamp,
                ),
            )
            return self.read_from_connection(connection, int(cursor.lastrowid), True)

    def get(self, note_id: int, *, include_deleted: bool = False) -> Note | None:
        with self.connections.read() as connection:
            row = connection.execute(
                NOTE_SELECT
                + " WHERE id = ?"
                + ("" if include_deleted else " AND is_deleted = 0"),
                (note_id,),
            ).fetchone()
        return note_from_row(row) if row is not None else None

    def list_active(self) -> list[Note]:
        with self.connections.read() as connection:
            rows = connection.execute(
                NOTE_SELECT
                + " WHERE is_deleted = 0"
                + " ORDER BY is_pinned DESC, updated_at DESC, id DESC"
            ).fetchall()
        return [note_from_row(row) for row in rows]

    def get_ai_access_metadata(
        self,
        note_id: int,
    ) -> tuple[Note, tuple[str, ...]]:
        with self.connections.read() as connection:
            note = self.read_from_connection(connection, note_id, False)
            target_ids = self._read_ai_target_ids(connection, note_id)
        return note, target_ids

    def set_ai_visibility(
        self,
        note_id: int,
        visibility: str,
    ) -> tuple[Note, tuple[str, ...]]:
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            cursor = connection.execute(
                """
                UPDATE notes
                SET ai_visibility = ?, revision = revision + 1, updated_at = ?
                WHERE id = ? AND is_deleted = 0
                """,
                (visibility, timestamp, note_id),
            )
            if cursor.rowcount != 1:
                raise NoteNotFoundError(note_id)
            note = self.read_from_connection(connection, note_id, False)
            target_ids = self._read_ai_target_ids(connection, note_id)
        return note, target_ids

    def set_ai_targets(
        self,
        note_id: int,
        target_ids: Sequence[str],
        target_catalog: Sequence[AiTargetCatalogEntry],
    ) -> tuple[Note, tuple[str, ...]]:
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            exists = connection.execute(
                "SELECT id FROM notes WHERE id = ? AND is_deleted = 0",
                (note_id,),
            ).fetchone()
            if exists is None:
                raise NoteNotFoundError(note_id)
            sync_ai_target_catalog(connection, target_catalog, timestamp)
            replace_note_ai_targets(connection, note_id, target_ids, timestamp)
            connection.execute(
                """
                UPDATE notes
                SET revision = revision + 1, updated_at = ?
                WHERE id = ?
                """,
                (timestamp, note_id),
            )
            note = self.read_from_connection(connection, note_id, False)
            stored_target_ids = self._read_ai_target_ids(connection, note_id)
        return note, stored_target_ids

    def save(
        self,
        note_id: int,
        *,
        title: str,
        content_html: str,
        location: NoteLocation,
        expected_revision: int,
    ) -> Note:
        with self.connections.write() as connection:
            current = connection.execute(
                "SELECT updated_at FROM notes WHERE id = ? AND is_deleted = 0",
                (note_id,),
            ).fetchone()
            if current is None:
                raise NoteNotFoundError(note_id)
            timestamp = next_note_updated_at(current["updated_at"], self.clock)
            main, sub, sub_sub = location.values()
            directory_id = self.directories.ensure_location(connection, location)
            cursor = connection.execute(
                """
                UPDATE notes
                SET title = ?, body_rich_text = ?, content_html = ?,
                    directory_id = ?, main_category = ?, sub_category = ?,
                    sub_sub_category = ?, revision = revision + 1, updated_at = ?
                WHERE id = ? AND is_deleted = 0 AND revision = ?
                """,
                (
                    title,
                    content_html,
                    content_html,
                    directory_id,
                    main,
                    sub,
                    sub_sub,
                    timestamp,
                    note_id,
                    int(expected_revision),
                ),
            )
            if cursor.rowcount != 1:
                raise NoteRevisionConflictError()
            return self.read_from_connection(connection, note_id, False)

    def rename(self, note_id: int, title: str) -> Note:
        clean_title = title.strip() or "未命名笔记"
        with self.connections.write() as connection:
            current = connection.execute(
                "SELECT title, updated_at FROM notes WHERE id = ? AND is_deleted = 0",
                (note_id,),
            ).fetchone()
            if current is None:
                raise NoteNotFoundError(note_id)
            if clean_title != current["title"]:
                timestamp = next_note_updated_at(current["updated_at"], self.clock)
                connection.execute(
                    """
                    UPDATE notes
                    SET title = ?, revision = revision + 1, updated_at = ?
                    WHERE id = ? AND is_deleted = 0
                    """,
                    (clean_title, timestamp, note_id),
                )
            return self.read_from_connection(connection, note_id, False)

    def move(self, note_id: int, location: NoteLocation) -> Note:
        with self.connections.write() as connection:
            self.move_in_connection(connection, note_id, location)
            return self.read_from_connection(connection, note_id, False)

    def move_many(self, note_ids: Sequence[int], location: NoteLocation) -> list[Note]:
        unique_ids = list(dict.fromkeys(int(note_id) for note_id in note_ids))
        with self.connections.write() as connection:
            for note_id in unique_ids:
                self.move_in_connection(connection, note_id, location)
            return [
                self.read_from_connection(connection, note_id, False)
                for note_id in unique_ids
            ]

    def set_pinned(self, note_id: int, pinned: bool) -> Note:
        with self.connections.write() as connection:
            current = connection.execute(
                "SELECT updated_at FROM notes WHERE id = ? AND is_deleted = 0",
                (note_id,),
            ).fetchone()
            if current is None:
                raise NoteNotFoundError(note_id)
            timestamp = next_note_updated_at(current["updated_at"], self.clock)
            connection.execute(
                """
                UPDATE notes
                SET is_pinned = ?, revision = revision + 1, updated_at = ?
                WHERE id = ? AND is_deleted = 0
                """,
                (1 if pinned else 0, timestamp, note_id),
            )
            return self.read_from_connection(connection, note_id, False)

    def soft_delete(self, note_id: int) -> Note:
        deleted_at = timestamp_seconds(self.clock)
        trash_group_id = uuid.uuid4().hex
        with self.connections.write() as connection:
            current = connection.execute(
                "SELECT updated_at FROM notes WHERE id = ? AND is_deleted = 0",
                (note_id,),
            ).fetchone()
            if current is None:
                raise NoteNotFoundError(note_id)
            timestamp = next_note_updated_at(current["updated_at"], self.clock)
            connection.execute(
                """
                UPDATE notes
                SET is_deleted = 1, deleted_at = ?, revision = revision + 1,
                    updated_at = ?, trash_group_id = ?
                WHERE id = ? AND is_deleted = 0
                """,
                (deleted_at, timestamp, trash_group_id, note_id),
            )
            return self.read_from_connection(connection, note_id, True)

    def restore(self, note_id: int) -> Note:
        with self.connections.write() as connection:
            row = connection.execute(
                """
                SELECT main_category, sub_category, sub_sub_category, updated_at
                FROM notes WHERE id = ? AND is_deleted = 1
                """,
                (note_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"回收站中找不到笔记: {note_id}")
            location = NoteLocation(
                row["main_category"], row["sub_category"], row["sub_sub_category"]
            )
            directory_id = self.directories.ensure_location(connection, location)
            timestamp = next_note_updated_at(row["updated_at"], self.clock)
            connection.execute(
                """
                UPDATE notes
                SET is_deleted = 0, deleted_at = NULL, trash_group_id = NULL,
                    directory_id = ?, revision = revision + 1, updated_at = ?
                WHERE id = ? AND is_deleted = 1
                """,
                (directory_id, timestamp, note_id),
            )
            return self.read_from_connection(connection, note_id, False)

    def permanently_delete_trashed(self, note_id: int) -> None:
        with self.connections.write() as connection:
            cursor = connection.execute(
                "DELETE FROM notes WHERE id = ? AND is_deleted = 1", (note_id,)
            )
            if cursor.rowcount != 1:
                raise ValueError(f"回收站中找不到笔记: {note_id}")

    def move_in_connection(self, connection, note_id: int, location: NoteLocation) -> None:
        current = connection.execute(
            "SELECT updated_at FROM notes WHERE id = ? AND is_deleted = 0",
            (note_id,),
        ).fetchone()
        if current is None:
            raise NoteNotFoundError(note_id)
        directory_id = self.directories.ensure_location(connection, location)
        main, sub, sub_sub = location.values()
        timestamp = next_note_updated_at(current["updated_at"], self.clock)
        connection.execute(
            """
            UPDATE notes
            SET directory_id = ?, main_category = ?, sub_category = ?,
                sub_sub_category = ?, revision = revision + 1, updated_at = ?
            WHERE id = ? AND is_deleted = 0
            """,
            (directory_id, main, sub, sub_sub, timestamp, note_id),
        )

    def read_from_connection(
        self, connection, note_id: int, include_deleted: bool
    ) -> Note:
        row = connection.execute(
            NOTE_SELECT
            + " WHERE id = ?"
            + ("" if include_deleted else " AND is_deleted = 0"),
            (note_id,),
        ).fetchone()
        if row is None:
            raise NoteNotFoundError(note_id, deleted_allowed=include_deleted)
        return note_from_row(row)

    @staticmethod
    def _read_ai_target_ids(connection, note_id: int) -> tuple[str, ...]:
        rows = connection.execute(
            """
            SELECT target_id
            FROM note_ai_targets
            WHERE note_id = ?
            ORDER BY target_id COLLATE NOCASE ASC
            """,
            (note_id,),
        ).fetchall()
        return tuple(str(row["target_id"]) for row in rows)
