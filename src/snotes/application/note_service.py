"""Application owner for ordinary note lifecycle actions."""

from __future__ import annotations

from collections.abc import Sequence

from src.snotes.application.ports import NotesRepository
from src.snotes.domain.models import Note, NoteLocation


class NoteService:
    def __init__(self, notes: NotesRepository) -> None:
        self._notes = notes

    def create_note(self, location: NoteLocation | None = None) -> Note:
        return self._notes.create(location or NoteLocation())

    def get_note(self, note_id: int, *, include_deleted: bool = False) -> Note | None:
        return self._notes.get(note_id, include_deleted=include_deleted)

    def list_active_notes(self) -> list[Note]:
        """Return ordinary active notes in the frozen R1 sidebar order."""
        return self._notes.list_active()

    def save_note(
        self,
        note_id: int,
        *,
        title: str,
        content_html: str,
        location: NoteLocation,
        expected_revision: int,
    ) -> Note:
        return self._notes.save(
            note_id,
            title=title,
            content_html=content_html,
            location=location,
            expected_revision=expected_revision,
        )

    def rename_note(self, note_id: int, title: str) -> Note:
        return self._notes.rename(note_id, title)

    def move_note(self, note_id: int, location: NoteLocation) -> Note:
        return self._notes.move(note_id, location)

    def move_notes(self, note_ids: Sequence[int], location: NoteLocation) -> list[Note]:
        return self._notes.move_many(note_ids, location)

    def set_note_pinned(self, note_id: int, pinned: bool) -> Note:
        return self._notes.set_pinned(note_id, pinned)

    def soft_delete_note(self, note_id: int) -> Note:
        return self._notes.soft_delete(note_id)

    def restore_note(self, note_id: int) -> Note:
        return self._notes.restore(note_id)

    def permanently_delete_note(self, note_id: int) -> None:
        self._notes.permanently_delete_trashed(note_id)
