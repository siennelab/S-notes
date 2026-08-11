"""SQLite directory hierarchy and ordinary-trash repository."""

from __future__ import annotations

import sqlite3
import uuid

from src.snotes.application.ports import DirectoriesRepository
from src.snotes.domain.errors import DirectoryNotFoundError
from src.snotes.domain.models import (
    DEFAULT_SUB_CATEGORY,
    Directory,
    NoteLocation,
    TrashResult,
    TrashSnapshot,
)
from src.snotes.storage.connection import SQLiteConnectionFactory
from src.snotes.storage.mappers import directory_from_row, note_from_row
from src.snotes.storage.support import Clock, next_note_updated_at, now_shanghai, timestamp_seconds


DIRECTORY_SELECT = """
SELECT id, name, parent_id, sort_order, created_at, updated_at,
       is_deleted, deleted_at, trash_group_id
FROM directories
"""
TRASH_NOTE_SELECT = """
SELECT id, note_uuid, revision, directory_id, title, content_html,
       main_category, sub_category, tags, ai_visibility,
       is_pinned, is_deleted, created_at, updated_at, deleted_at, trash_group_id
FROM notes
"""


class SQLiteDirectoriesRepository(DirectoriesRepository):
    def __init__(
        self,
        connections: SQLiteConnectionFactory,
        *,
        clock: Clock = now_shanghai,
    ) -> None:
        self.connections = connections
        self.clock = clock

    def create(self, name: str, parent_id: int | None = None) -> Directory:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("目录名称不能为空")
        with self.connections.write() as connection:
            if parent_id is not None and self.depth(connection, parent_id) >= 2:
                raise ValueError("目录最多支持两层")
            if self.find_active(connection, clean_name, parent_id) is not None:
                raise ValueError("同级目录已存在")
            directory_id = self.create_in_connection(connection, clean_name, parent_id)
            row = connection.execute(
                DIRECTORY_SELECT + " WHERE id = ?", (directory_id,)
            ).fetchone()
            return directory_from_row(row)

    def list_active(self) -> list[Directory]:
        with self.connections.read() as connection:
            rows = connection.execute(
                DIRECTORY_SELECT
                + " WHERE is_deleted = 0 ORDER BY name COLLATE NOCASE ASC, id ASC"
            ).fetchall()
        return [directory_from_row(row) for row in rows]

    def get_path(self, directory_id: int) -> tuple[str, ...]:
        with self.connections.read() as connection:
            return self.path_from_connection(connection, directory_id)

    def rename(self, directory_id: int, name: str) -> None:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("目录名称不能为空")
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            row = connection.execute(
                """
                SELECT id, name, parent_id FROM directories
                WHERE id = ? AND is_deleted = 0
                """,
                (directory_id,),
            ).fetchone()
            if row is None:
                raise ValueError("目录不存在")
            existing_id = self.find_active(connection, clean_name, row["parent_id"])
            if existing_id is not None and existing_id != directory_id:
                raise ValueError("同级目录已存在")
            old_path = self.path_from_connection(connection, directory_id)
            connection.execute(
                "UPDATE directories SET name = ?, updated_at = ? WHERE id = ?",
                (clean_name, timestamp, directory_id),
            )
            self.rename_notes_for_path(connection, old_path, clean_name)

    def soft_delete_tree(self, directory_id: int) -> str:
        timestamp = timestamp_seconds(self.clock)
        trash_group_id = uuid.uuid4().hex
        with self.connections.write() as connection:
            active = connection.execute(
                "SELECT id FROM directories WHERE id = ? AND is_deleted = 0",
                (directory_id,),
            ).fetchone()
            if active is None:
                raise DirectoryNotFoundError(directory_id)
            path = self.path_from_connection(connection, directory_id)
            self.soft_delete_notes_under_path(
                connection, path, timestamp, trash_group_id
            )
            descendant_ids = self.collect_descendant_ids(connection, directory_id)
            connection.executemany(
                """
                UPDATE directories
                SET is_deleted = 1, deleted_at = ?, updated_at = ?, trash_group_id = ?
                WHERE id = ?
                """,
                [
                    (timestamp, timestamp, trash_group_id, target_id)
                    for target_id in descendant_ids
                ],
            )
        return trash_group_id

    def restore_tree(self, directory_id: int) -> None:
        timestamp = timestamp_seconds(self.clock)
        with self.connections.write() as connection:
            root = connection.execute(
                """
                SELECT id, name, parent_id FROM directories
                WHERE id = ? AND is_deleted = 1
                """,
                (directory_id,),
            ).fetchone()
            if root is None:
                raise DirectoryNotFoundError(directory_id, trash=True)
            root_path = self.path_from_connection(connection, directory_id)
            parent_target = self.ensure_active_parent_path(connection, root_path)
            restore_ids = self.collect_descendant_ids(connection, directory_id)
            paths = [
                self.path_from_connection(connection, target_id)
                for target_id in restore_ids
            ]
            trash_groups = self.trash_groups(connection, restore_ids)
            self.require_restorable_tree(
                connection, restore_ids, directory_id, parent_target
            )
            connection.execute(
                "UPDATE directories SET parent_id = ? WHERE id = ? AND is_deleted = 1",
                (parent_target, directory_id),
            )
            connection.executemany(
                """
                UPDATE directories
                SET is_deleted = 0, deleted_at = NULL, trash_group_id = NULL,
                    updated_at = ?
                WHERE id = ? AND is_deleted = 1
                """,
                [(timestamp, target_id) for target_id in restore_ids],
            )
            for path in paths:
                self.restore_notes_under_path(connection, path, trash_groups)

    def permanently_delete_tree(self, directory_id: int) -> None:
        with self.connections.write() as connection:
            deleted = connection.execute(
                "SELECT id FROM directories WHERE id = ? AND is_deleted = 1",
                (directory_id,),
            ).fetchone()
            if deleted is None:
                raise DirectoryNotFoundError(directory_id, trash=True)
            directory_ids = self.collect_descendant_ids(connection, directory_id)
            paths = [self.path_from_connection(connection, value) for value in directory_ids]
            trash_groups = self.trash_groups(connection, directory_ids)
            for path in paths:
                self.permanently_delete_notes_under_path(
                    connection, path, trash_groups
                )
            for target_id in sorted(
                directory_ids,
                key=lambda value: self.depth(connection, value),
                reverse=True,
            ):
                connection.execute(
                    "DELETE FROM directories WHERE id = ? AND is_deleted = 1",
                    (target_id,),
                )

    def list_trash(self) -> TrashSnapshot:
        with self.connections.read() as connection:
            directory_rows = connection.execute(
                DIRECTORY_SELECT
                + """
                  WHERE is_deleted = 1
                  ORDER BY deleted_at DESC, name COLLATE NOCASE ASC, id ASC
                  """
            ).fetchall()
            note_rows = connection.execute(
                TRASH_NOTE_SELECT
                + """
                  WHERE is_deleted = 1
                  ORDER BY updated_at DESC, deleted_at DESC, id DESC
                  """
            ).fetchall()
        return TrashSnapshot(
            directories=tuple(directory_from_row(row) for row in directory_rows),
            notes=tuple(note_from_row(row) for row in note_rows),
        )

    def empty_trash(self) -> TrashResult:
        with self.connections.write() as connection:
            note_rows = connection.execute(
                "SELECT id, trash_group_id FROM notes WHERE is_deleted = 1"
            ).fetchall()
            directory_rows = connection.execute(
                "SELECT id, parent_id, trash_group_id FROM directories WHERE is_deleted = 1"
            ).fetchall()
            deleted_directory_ids = {int(row["id"]) for row in directory_rows}
            root_directory_ids = [
                int(row["id"])
                for row in directory_rows
                if row["parent_id"] not in deleted_directory_ids
            ]
            directory_groups = {
                row["trash_group_id"]
                for row in directory_rows
                if row["trash_group_id"] is not None
            }
            independent_notes = [
                row for row in note_rows if row["trash_group_id"] not in directory_groups
            ]
            connection.execute("DELETE FROM notes WHERE is_deleted = 1")
            for directory_id in sorted(
                deleted_directory_ids,
                key=lambda value: self.depth(connection, value),
                reverse=True,
            ):
                connection.execute(
                    "DELETE FROM directories WHERE id = ? AND is_deleted = 1",
                    (directory_id,),
                )
        return TrashResult(
            note_count=len(note_rows),
            directory_count=len(directory_rows),
            root_count=len(root_directory_ids) + len(independent_notes),
        )

    def ensure_location(
        self, connection: sqlite3.Connection, location: NoteLocation
    ) -> int:
        main, sub = location.values()
        main_id = self.ensure_directory(connection, main, None)
        if sub == DEFAULT_SUB_CATEGORY:
            return main_id
        return self.ensure_directory(connection, sub, main_id)

    def find_active(
        self, connection: sqlite3.Connection, name: str, parent_id: int | None
    ) -> int | None:
        if parent_id is None:
            row = connection.execute(
                """
                SELECT id FROM directories
                WHERE parent_id IS NULL AND name = ? AND is_deleted = 0
                """,
                (name,),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT id FROM directories
                WHERE parent_id = ? AND name = ? AND is_deleted = 0
                """,
                (parent_id, name),
            ).fetchone()
        return int(row["id"]) if row is not None else None

    def ensure_directory(
        self, connection: sqlite3.Connection, name: str, parent_id: int | None
    ) -> int:
        directory_id = self.find_active(connection, name, parent_id)
        if directory_id is not None:
            return directory_id
        return self.create_in_connection(connection, name, parent_id)

    def create_in_connection(
        self, connection: sqlite3.Connection, name: str, parent_id: int | None
    ) -> int:
        timestamp = timestamp_seconds(self.clock)
        cursor = connection.execute(
            """
            INSERT INTO directories (name, parent_id, sort_order, created_at, updated_at)
            VALUES (?, ?, 0, ?, ?)
            """,
            (name.strip(), parent_id, timestamp, timestamp),
        )
        return int(cursor.lastrowid)

    def path_from_connection(
        self, connection: sqlite3.Connection, directory_id: int
    ) -> tuple[str, ...]:
        names: list[str] = []
        current_id: int | None = directory_id
        while current_id is not None:
            row = connection.execute(
                "SELECT name, parent_id FROM directories WHERE id = ?", (current_id,)
            ).fetchone()
            if row is None:
                raise DirectoryNotFoundError(directory_id)
            names.append(str(row["name"]))
            current_id = row["parent_id"]
        return tuple(reversed(names))

    def depth(self, connection: sqlite3.Connection, directory_id: int) -> int:
        return len(self.path_from_connection(connection, directory_id))

    def collect_descendant_ids(
        self, connection: sqlite3.Connection, directory_id: int
    ) -> set[int]:
        ids = {directory_id}
        rows = connection.execute(
            "SELECT id FROM directories WHERE parent_id = ?", (directory_id,)
        ).fetchall()
        for row in rows:
            ids.update(self.collect_descendant_ids(connection, int(row["id"])))
        return ids

    def rename_notes_for_path(
        self, connection: sqlite3.Connection, path: tuple[str, ...], new_name: str
    ) -> None:
        column, where_clause, values = self.path_update_parts(path)
        rows = connection.execute(
            f"SELECT id, updated_at FROM notes WHERE is_deleted = 0 AND {where_clause}",
            values,
        ).fetchall()
        for row in rows:
            timestamp = next_note_updated_at(row["updated_at"], self.clock)
            connection.execute(
                f"""
                UPDATE notes
                SET {column} = ?, revision = revision + 1, updated_at = ?
                WHERE id = ? AND is_deleted = 0
                """,
                (new_name, timestamp, row["id"]),
            )

    def soft_delete_notes_under_path(
        self,
        connection: sqlite3.Connection,
        path: tuple[str, ...],
        deleted_at: str,
        trash_group_id: str,
    ) -> None:
        _column, where_clause, values = self.path_update_parts(path)
        rows = connection.execute(
            f"SELECT id, updated_at FROM notes WHERE is_deleted = 0 AND {where_clause}",
            values,
        ).fetchall()
        for row in rows:
            timestamp = next_note_updated_at(row["updated_at"], self.clock)
            connection.execute(
                """
                UPDATE notes
                SET directory_id = NULL, is_deleted = 1, deleted_at = ?,
                    revision = revision + 1, updated_at = ?, trash_group_id = ?
                WHERE id = ? AND is_deleted = 0
                """,
                (deleted_at, timestamp, trash_group_id, row["id"]),
            )

    def restore_notes_under_path(
        self,
        connection: sqlite3.Connection,
        path: tuple[str, ...],
        trash_group_ids: set[str],
    ) -> None:
        if not trash_group_ids:
            return
        _column, where_clause, values = self.path_update_parts(path)
        location = self.location_from_path(path)
        directory_id = self.ensure_location(connection, location)
        ordered_groups = tuple(sorted(trash_group_ids))
        rows = connection.execute(
            f"""
            SELECT id, updated_at FROM notes
            WHERE is_deleted = 1 AND {where_clause}
              AND trash_group_id IN ({','.join('?' for _ in ordered_groups)})
            """,
            (*values, *ordered_groups),
        ).fetchall()
        for row in rows:
            timestamp = next_note_updated_at(row["updated_at"], self.clock)
            connection.execute(
                """
                UPDATE notes
                SET directory_id = ?, is_deleted = 0, deleted_at = NULL,
                    trash_group_id = NULL, revision = revision + 1, updated_at = ?
                WHERE id = ? AND is_deleted = 1
                """,
                (directory_id, timestamp, row["id"]),
            )

    def permanently_delete_notes_under_path(
        self,
        connection: sqlite3.Connection,
        path: tuple[str, ...],
        trash_group_ids: set[str],
    ) -> None:
        if not trash_group_ids:
            return
        _column, where_clause, values = self.path_update_parts(path)
        groups = tuple(sorted(trash_group_ids))
        connection.execute(
            f"""
            DELETE FROM notes
            WHERE is_deleted = 1 AND {where_clause}
              AND trash_group_id IN ({','.join('?' for _ in groups)})
            """,
            (*values, *groups),
        )

    def trash_groups(
        self, connection: sqlite3.Connection, directory_ids: set[int]
    ) -> set[str]:
        if not directory_ids:
            return set()
        ids = tuple(sorted(directory_ids))
        rows = connection.execute(
            f"""
            SELECT DISTINCT trash_group_id FROM directories
            WHERE id IN ({','.join('?' for _ in ids)})
              AND trash_group_id IS NOT NULL
            """,
            ids,
        ).fetchall()
        return {str(row["trash_group_id"]) for row in rows}

    def ensure_active_parent_path(
        self, connection: sqlite3.Connection, path: tuple[str, ...]
    ) -> int | None:
        if len(path) <= 1:
            return None
        parent_id: int | None = None
        for name in path[:-1]:
            parent_id = self.ensure_directory(connection, name, parent_id)
        return parent_id

    def require_restorable_tree(
        self,
        connection: sqlite3.Connection,
        restore_ids: set[int],
        root_id: int,
        root_parent_id: int | None,
    ) -> None:
        ids = tuple(sorted(restore_ids))
        rows = connection.execute(
            f"""
            SELECT id, name, parent_id, is_deleted FROM directories
            WHERE id IN ({','.join('?' for _ in ids)})
            """,
            ids,
        ).fetchall()
        rows_by_id = {int(row["id"]): row for row in rows}
        if len(rows_by_id) != len(restore_ids) or any(
            int(row["is_deleted"]) != 1 for row in rows_by_id.values()
        ):
            raise ValueError("待恢复的目录树状态已变更，请刷新后重试。")
        prospective: set[tuple[int | None, str]] = set()
        for target_id, row in rows_by_id.items():
            parent_id = root_parent_id if target_id == root_id else row["parent_id"]
            key = (parent_id, str(row["name"]))
            if key in prospective:
                raise ValueError(
                    f'同级已存在分类“{row["name"]}”，请先重命名或删除冲突分类后再恢复。'
                )
            prospective.add(key)
            if parent_id is None:
                conflict = connection.execute(
                    """
                    SELECT id FROM directories
                    WHERE parent_id IS NULL AND name = ? AND is_deleted = 0
                    """,
                    (row["name"],),
                ).fetchone()
            else:
                conflict = connection.execute(
                    """
                    SELECT id FROM directories
                    WHERE parent_id = ? AND name = ? AND is_deleted = 0
                    """,
                    (parent_id, row["name"]),
                ).fetchone()
            if conflict is not None and int(conflict["id"]) not in restore_ids:
                raise ValueError(
                    f'同级已存在分类“{row["name"]}”，请先重命名或删除冲突分类后再恢复。'
                )

    @staticmethod
    def path_update_parts(
        path: tuple[str, ...]
    ) -> tuple[str, str, tuple[str, ...]]:
        if len(path) == 1:
            return "main_category", "main_category = ?", (path[0],)
        if len(path) == 2:
            return (
                "sub_category",
                "main_category = ? AND sub_category = ?",
                (path[0], path[1]),
            )
        raise ValueError("目录路径无效")

    @staticmethod
    def location_from_path(path: tuple[str, ...]) -> NoteLocation:
        return NoteLocation(
            path[0],
            path[1] if len(path) >= 2 else DEFAULT_SUB_CATEGORY,
        )
