"""Application owner for directory and ordinary-trash actions."""

from __future__ import annotations

from src.snotes.application.ports import DirectoriesRepository
from src.snotes.domain.models import Directory, TrashResult, TrashSnapshot


class DirectoryService:
    def __init__(self, directories: DirectoriesRepository) -> None:
        self._directories = directories

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
