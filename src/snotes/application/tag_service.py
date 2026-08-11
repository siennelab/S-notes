"""Application owner for ordinary note-tag assignment."""

from __future__ import annotations

from collections.abc import Sequence

from src.snotes.application.ports import TagRecord, TagsRepository
from src.snotes.domain.tags import parse_tags


class TagService:
    def __init__(self, tags: TagsRepository) -> None:
        self._tags = tags

    def set_note_tags(self, note_id: int, tags: Sequence[str]) -> list[str]:
        return self._tags.set_for_note(note_id, tags)

    def get_note_tags(self, note_id: int) -> list[str]:
        return self._tags.get_for_note(note_id)

    def list_tags(self) -> list[str]:
        return self._tags.list_all()

    def get_note_tag_records(self, note_id: int) -> list[TagRecord]:
        return self._tags.get_records_for_note(note_id)

    @staticmethod
    def normalize_tags_text(value: str) -> tuple[str, ...]:
        return tuple(parse_tags(value))

    def set_note_tags_from_text(self, note_id: int, value: str) -> list[str]:
        wanted = list(self.normalize_tags_text(value))
        current = self.get_note_tags(note_id)
        if set(wanted) == set(current):
            return current
        return self.set_note_tags(note_id, wanted)
