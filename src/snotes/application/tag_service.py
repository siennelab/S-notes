"""Application owner for ordinary note-tag assignment."""

from __future__ import annotations

from collections.abc import Sequence

from src.snotes.application.ports import TagsRepository


class TagService:
    def __init__(self, tags: TagsRepository) -> None:
        self._tags = tags

    def set_note_tags(self, note_id: int, tags: Sequence[str]) -> list[str]:
        return self._tags.set_for_note(note_id, tags)

    def get_note_tags(self, note_id: int) -> list[str]:
        return self._tags.get_for_note(note_id)

    def list_tags(self) -> list[str]:
        return self._tags.list_all()
