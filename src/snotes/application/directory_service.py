"""Application owner for directory and ordinary-trash actions."""

from __future__ import annotations

from collections.abc import Sequence

from src.snotes.application.ports import DirectoriesRepository, NotesRepository
from src.snotes.domain.models import (
    Directory,
    TrashResult,
    TrashSelectionResult,
    TrashSnapshot,
)


class DirectoryService:
    def __init__(
        self,
        directories: DirectoriesRepository,
        notes: NotesRepository | None = None,
    ) -> None:
        self._directories = directories
        self._notes = notes

    def create_directory(
        self, name: str, parent_id: int | None = None
    ) -> Directory:
        return self._directories.create(name, parent_id)

    def list_directories(self) -> list[Directory]:
        return self._directories.list_active()

    def get_directory_path(self, directory_id: int) -> tuple[str, ...]:
        return self._directories.get_path(directory_id)

    def rename_directory(self, directory_id: int, name: str) -> None:
        self._directories.rename(directory_id, name)

    def soft_delete_directory(self, directory_id: int) -> str:
        return self._directories.soft_delete_tree(directory_id)

    def restore_directory(self, directory_id: int) -> None:
        self._directories.restore_tree(directory_id)

    def permanently_delete_directory(self, directory_id: int) -> None:
        self._directories.permanently_delete_tree(directory_id)

    def list_trash(self) -> TrashSnapshot:
        return self._directories.list_trash()

    def empty_trash(self) -> TrashResult:
        return self._directories.empty_trash()

    def restore_trash_items(
        self,
        note_ids: Sequence[int],
        directory_ids: Sequence[int],
    ) -> TrashSelectionResult:
        notes = self._require_notes()
        root_directories = self._root_directory_ids(directory_ids)
        restored_directories: list[int] = []
        for directory_id in root_directories:
            self._directories.restore_tree(directory_id)
            restored_directories.append(directory_id)
        restored_notes: list[int] = []
        for note_id in self._unique_ids(note_ids):
            note = notes.get(note_id, include_deleted=True)
            if note is not None and note.is_deleted:
                notes.restore(note_id)
                restored_notes.append(note_id)
        return TrashSelectionResult(
            note_ids=tuple(restored_notes),
            directory_ids=tuple(restored_directories),
        )

    def permanently_delete_trash_items(
        self,
        note_ids: Sequence[int],
        directory_ids: Sequence[int],
    ) -> TrashSelectionResult:
        notes = self._require_notes()
        root_directories = self._root_directory_ids(directory_ids)
        deleted_directories: list[int] = []
        for directory_id in root_directories:
            self._directories.permanently_delete_tree(directory_id)
            deleted_directories.append(directory_id)
        deleted_notes: list[int] = []
        for note_id in self._unique_ids(note_ids):
            note = notes.get(note_id, include_deleted=True)
            if note is not None and note.is_deleted:
                notes.permanently_delete_trashed(note_id)
                deleted_notes.append(note_id)
        return TrashSelectionResult(
            note_ids=tuple(deleted_notes),
            directory_ids=tuple(deleted_directories),
        )

    def _root_directory_ids(self, values: Sequence[int]) -> list[int]:
        unique = self._unique_ids(values)
        paths = {
            directory_id: self._directories.get_path(directory_id)
            for directory_id in unique
        }
        return [
            directory_id
            for directory_id in unique
            if not any(
                other_id != directory_id
                and paths[directory_id][: len(paths[other_id])] == paths[other_id]
                for other_id in unique
            )
        ]

    def _require_notes(self) -> NotesRepository:
        if self._notes is None:
            raise RuntimeError("回收站批量操作需要笔记 repository")
        return self._notes

    @staticmethod
    def _unique_ids(values: Sequence[int]) -> list[int]:
        return list(dict.fromkeys(int(value) for value in values))
