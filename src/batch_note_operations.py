from __future__ import annotations

import sqlite3
from pathlib import Path

from src.database import (
    DATABASE_PATH,
    DEFAULT_SUB_CATEGORY,
    DEFAULT_SUB_SUB_CATEGORY,
    _collect_descendant_directory_ids,
    _connect,
    _directory_depth,
    _ensure_active_parent_path_for_restore,
    _ensure_directory_path,
    _get_directory_path,
    _permanently_delete_notes_under_path,
    _restore_notes_under_path,
    _trash_group_ids_for_directories,
)
from src.time_utils import now_shanghai_iso


def move_notes_to_directory(
    note_ids: list[int],
    target_directory_id: int,
    database_path: Path = DATABASE_PATH,
) -> dict:
    unique_note_ids = list(dict.fromkeys(int(note_id) for note_id in note_ids))
    timestamp = now_shanghai_iso()
    with _connect(database_path) as connection:
        target = connection.execute(
            "SELECT id FROM directories WHERE id = ? AND is_deleted = 0",
            (target_directory_id,),
        ).fetchone()
        if target is None:
            raise ValueError("目标目录不存在")
        main, sub, sub_sub = _categories_for_directory(connection, target_directory_id)
        if not unique_note_ids:
            return {
                "moved_count": 0,
                "unchanged_count": 0,
                "note_ids": [],
                "target_path": (main, sub, sub_sub),
            }

        placeholders = ",".join("?" for _ in unique_note_ids)
        rows = connection.execute(
            f"""
            SELECT id, main_category, sub_category, sub_sub_category
            FROM notes
            WHERE is_deleted = 0 AND id IN ({placeholders})
            """,
            tuple(unique_note_ids),
        ).fetchall()
        valid_ids = {row["id"] for row in rows}
        moved_ids = [
            row["id"]
            for row in rows
            if (
                row["main_category"],
                row["sub_category"],
                row["sub_sub_category"],
            )
            != (main, sub, sub_sub)
        ]
        if moved_ids:
            destination_id = _ensure_directory_path(connection, main, sub, sub_sub)
            connection.executemany(
                """
                UPDATE notes
                SET directory_id = ?, main_category = ?, sub_category = ?,
                    sub_sub_category = ?, updated_at = ?
                WHERE id = ? AND is_deleted = 0
                """,
                [
                    (destination_id, main, sub, sub_sub, timestamp, note_id)
                    for note_id in moved_ids
                ],
            )
    return {
        "moved_count": len(moved_ids),
        "unchanged_count": len(valid_ids) - len(moved_ids),
        "note_ids": [note_id for note_id in unique_note_ids if note_id in valid_ids],
        "target_path": (main, sub, sub_sub),
    }


def restore_trash_items(
    note_ids: list[int],
    directory_ids: list[int],
    database_path: Path = DATABASE_PATH,
) -> dict:
    timestamp = now_shanghai_iso()
    with _connect(database_path) as connection:
        root_directory_ids, independent_note_ids = _normalize_trash_selection(
            connection, note_ids, directory_ids
        )
        for directory_id in root_directory_ids:
            _restore_trash_directory(connection, directory_id, timestamp)
        for note_id in independent_note_ids:
            _restore_trash_note(connection, note_id, timestamp)
    return _trash_result(root_directory_ids, independent_note_ids)


def permanently_delete_trash_items(
    note_ids: list[int],
    directory_ids: list[int],
    database_path: Path = DATABASE_PATH,
) -> dict:
    with _connect(database_path) as connection:
        root_directory_ids, independent_note_ids = _normalize_trash_selection(
            connection, note_ids, directory_ids
        )
        _delete_trash_roots(connection, root_directory_ids, independent_note_ids)
    return _trash_result(root_directory_ids, independent_note_ids)


def empty_trash(database_path: Path = DATABASE_PATH) -> dict:
    with _connect(database_path) as connection:
        note_ids = [
            row["id"]
            for row in connection.execute(
                "SELECT id FROM notes WHERE is_deleted = 1"
            ).fetchall()
        ]
        directory_ids = [
            row["id"]
            for row in connection.execute(
                "SELECT id FROM directories WHERE is_deleted = 1"
            ).fetchall()
        ]
        note_count = len(note_ids)
        directory_count = len(directory_ids)
        if not note_count and not directory_count:
            return {
                "note_count": 0,
                "directory_count": 0,
                "root_count": 0,
            }

        root_directory_ids, independent_note_ids = _normalize_trash_selection(
            connection, note_ids, directory_ids
        )
        _delete_trash_roots(connection, root_directory_ids, independent_note_ids)
    return {
        "note_count": note_count,
        "directory_count": directory_count,
        "root_count": len(root_directory_ids) + len(independent_note_ids),
    }


def _categories_for_directory(
    connection: sqlite3.Connection, directory_id: int
) -> tuple[str, str, str]:
    path = _get_directory_path(connection, directory_id)
    if not 1 <= len(path) <= 3:
        raise ValueError("目标目录路径无效")
    return (
        path[0],
        path[1] if len(path) >= 2 else DEFAULT_SUB_CATEGORY,
        path[2] if len(path) >= 3 else DEFAULT_SUB_SUB_CATEGORY,
    )


def _normalize_trash_selection(
    connection: sqlite3.Connection,
    note_ids: list[int],
    directory_ids: list[int],
) -> tuple[list[int], list[int]]:
    unique_directory_ids = list(dict.fromkeys(int(value) for value in directory_ids))
    unique_note_ids = list(dict.fromkeys(int(value) for value in note_ids))
    directory_rows = _rows_for_ids(
        connection, "directories", unique_directory_ids, "id, parent_id"
    )
    selected_directories = {row["id"]: row for row in directory_rows}
    root_directory_ids = [
        directory_id
        for directory_id in unique_directory_ids
        if directory_id in selected_directories
        and not _has_selected_ancestor(
            connection, selected_directories[directory_id]["parent_id"], selected_directories
        )
    ]

    coverage = []
    for directory_id in root_directory_ids:
        descendant_ids = _collect_descendant_directory_ids(connection, directory_id)
        coverage.append(
            (
                _get_directory_path(connection, directory_id),
                _trash_group_ids_for_directories(connection, descendant_ids),
            )
        )

    note_rows = _rows_for_ids(
        connection,
        "notes",
        unique_note_ids,
        "id, main_category, sub_category, sub_sub_category, trash_group_id",
    )
    notes_by_id = {row["id"]: row for row in note_rows}
    independent_note_ids = []
    for note_id in unique_note_ids:
        row = notes_by_id.get(note_id)
        if row is None:
            continue
        note_path = (
            row["main_category"],
            row["sub_category"],
            row["sub_sub_category"],
        )
        covered = any(
            note_path[: len(root_path)] == root_path
            and row["trash_group_id"] in trash_group_ids
            for root_path, trash_group_ids in coverage
        )
        if not covered:
            independent_note_ids.append(note_id)
    return root_directory_ids, independent_note_ids


def _has_selected_ancestor(
    connection: sqlite3.Connection,
    parent_id: int | None,
    selected_directories: dict[int, sqlite3.Row],
) -> bool:
    while parent_id is not None:
        if parent_id in selected_directories:
            return True
        parent = connection.execute(
            "SELECT parent_id FROM directories WHERE id = ?", (parent_id,)
        ).fetchone()
        parent_id = parent["parent_id"] if parent is not None else None
    return False


def _rows_for_ids(
    connection: sqlite3.Connection,
    table: str,
    ids: list[int],
    columns: str,
) -> list[sqlite3.Row]:
    if not ids:
        return []
    if table not in {"notes", "directories"}:
        raise ValueError("不支持的数据表")
    placeholders = ",".join("?" for _ in ids)
    return connection.execute(
        f"SELECT {columns} FROM {table} WHERE is_deleted = 1 AND id IN ({placeholders})",
        tuple(ids),
    ).fetchall()


def _restore_trash_note(
    connection: sqlite3.Connection, note_id: int, timestamp: str
) -> None:
    row = connection.execute(
        """
        SELECT main_category, sub_category, sub_sub_category
        FROM notes WHERE id = ? AND is_deleted = 1
        """,
        (note_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"回收站中找不到笔记: {note_id}")
    directory_id = _ensure_directory_path(
        connection,
        row["main_category"],
        row["sub_category"],
        row["sub_sub_category"],
    )
    connection.execute(
        """
        UPDATE notes
        SET is_deleted = 0, deleted_at = NULL, trash_group_id = NULL,
            directory_id = ?, updated_at = ?
        WHERE id = ? AND is_deleted = 1
        """,
        (directory_id, timestamp, note_id),
    )


def _restore_trash_directory(
    connection: sqlite3.Connection, directory_id: int, timestamp: str
) -> None:
    _require_deleted_directory(connection, directory_id)
    root_path = _get_directory_path(connection, directory_id)
    active_parent_id = _ensure_active_parent_path_for_restore(connection, root_path)
    directory_ids = _collect_descendant_directory_ids(connection, directory_id)
    paths = [_get_directory_path(connection, target_id) for target_id in directory_ids]
    trash_group_ids = _trash_group_ids_for_directories(connection, directory_ids)
    connection.execute(
        "UPDATE directories SET parent_id = ? WHERE id = ?",
        (active_parent_id, directory_id),
    )
    connection.executemany(
        """
        UPDATE directories
        SET is_deleted = 0, deleted_at = NULL, trash_group_id = NULL, updated_at = ?
        WHERE id = ? AND is_deleted = 1
        """,
        [(timestamp, target_id) for target_id in directory_ids],
    )
    for path in paths:
        _restore_notes_under_path(connection, path, timestamp, trash_group_ids)


def _permanently_delete_trash_directory(
    connection: sqlite3.Connection, directory_id: int
) -> None:
    _require_deleted_directory(connection, directory_id)
    directory_ids = _collect_descendant_directory_ids(connection, directory_id)
    paths = [_get_directory_path(connection, target_id) for target_id in directory_ids]
    trash_group_ids = _trash_group_ids_for_directories(connection, directory_ids)
    for path in paths:
        _permanently_delete_notes_under_path(connection, path, trash_group_ids)
    for target_id in sorted(
        directory_ids,
        key=lambda value: _directory_depth(connection, value),
        reverse=True,
    ):
        connection.execute(
            "DELETE FROM directories WHERE id = ? AND is_deleted = 1", (target_id,)
        )


def _delete_trash_roots(
    connection: sqlite3.Connection,
    directory_ids: list[int],
    note_ids: list[int],
) -> None:
    for directory_id in directory_ids:
        _permanently_delete_trash_directory(connection, directory_id)
    if note_ids:
        placeholders = ",".join("?" for _ in note_ids)
        connection.execute(
            f"DELETE FROM notes WHERE is_deleted = 1 AND id IN ({placeholders})",
            tuple(note_ids),
        )


def _require_deleted_directory(
    connection: sqlite3.Connection, directory_id: int
) -> None:
    row = connection.execute(
        "SELECT id FROM directories WHERE id = ? AND is_deleted = 1",
        (directory_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"回收站中找不到目录: {directory_id}")


def _trash_result(directory_ids: list[int], note_ids: list[int]) -> dict:
    return {
        "processed_count": len(directory_ids) + len(note_ids),
        "directory_ids": directory_ids,
        "note_ids": note_ids,
    }
