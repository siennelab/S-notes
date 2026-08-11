"""Pure data objects for the Package 1 note core."""

from __future__ import annotations

from dataclasses import dataclass


DEFAULT_MAIN_CATEGORY = "临时"
DEFAULT_SUB_CATEGORY = "无"
DEFAULT_AI_VISIBILITY = "hidden"


@dataclass(frozen=True, slots=True)
class NoteLocation:
    main: str = DEFAULT_MAIN_CATEGORY
    sub: str = DEFAULT_SUB_CATEGORY

    def values(self) -> tuple[str, str]:
        return (
            self.main or DEFAULT_MAIN_CATEGORY,
            self.sub or DEFAULT_SUB_CATEGORY,
        )


def location_matches_directory_prefix(
    note_location: NoteLocation,
    directory_location: NoteLocation,
) -> bool:
    """Return whether a note belongs to the frozen-R1 directory prefix."""
    prefix = tuple(
        value for value in directory_location.values() if value not in {"", "无"}
    )
    return note_location.values()[: len(prefix)] == prefix


@dataclass(frozen=True, slots=True)
class Note:
    id: int
    note_uuid: str
    revision: int
    directory_id: int | None
    title: str
    content_html: str
    main_category: str
    sub_category: str
    tags: str
    ai_visibility: str
    is_pinned: bool
    is_deleted: bool
    created_at: str
    updated_at: str
    deleted_at: str | None
    trash_group_id: str | None

    @property
    def location(self) -> NoteLocation:
        return NoteLocation(
            self.main_category,
            self.sub_category,
        )


@dataclass(frozen=True, slots=True)
class Directory:
    id: int
    name: str
    parent_id: int | None
    sort_order: int
    created_at: str
    updated_at: str
    is_deleted: bool
    deleted_at: str | None
    trash_group_id: str | None


@dataclass(frozen=True, slots=True)
class TrashSnapshot:
    directories: tuple[Directory, ...]
    notes: tuple[Note, ...]


@dataclass(frozen=True, slots=True)
class TrashResult:
    note_count: int
    directory_count: int
    root_count: int


@dataclass(frozen=True, slots=True)
class TrashSelectionResult:
    note_ids: tuple[int, ...]
    directory_ids: tuple[int, ...]

    @property
    def processed_count(self) -> int:
        return len(self.note_ids) + len(self.directory_ids)


@dataclass(frozen=True, slots=True)
class ExportResult:
    path: str
    note_count: int
