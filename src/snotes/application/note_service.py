"""Application owner for ordinary note lifecycle actions."""

from __future__ import annotations

import re
from collections.abc import Sequence

from src.snotes.application.ai_actor_registry import resolve_target_id
from src.snotes.application.ai_safe_read_service import AI_VISIBILITY_VALUES
from src.snotes.application.ports import NotesRepository
from src.snotes.application.derived_text_cache import DerivedTextCache
from src.snotes.domain.models import Note, NoteLocation, location_matches_directory_prefix
from src.snotes.domain.relations import RelatedNote, build_related_notes
from src.snotes.domain.search_query import match_search_fields, parse_search_query
from src.snotes.domain.tags import normalize_tag, parse_tags


class NoteService:
    def __init__(self, notes: NotesRepository) -> None:
        self._notes = notes
        self.derived_text = DerivedTextCache()

    def create_note(self, location: NoteLocation | None = None) -> Note:
        return self._notes.create(location or NoteLocation())

    def get_note(self, note_id: int, *, include_deleted: bool = False) -> Note | None:
        return self._notes.get(note_id, include_deleted=include_deleted)

    def list_active_notes(self) -> list[Note]:
        """Return ordinary active notes in the frozen R1 sidebar order."""
        return self._notes.list_active()

    def notes_for_directory(self, location: NoteLocation | None = None) -> list[Note]:
        if location is None:
            return self._notes.list_active()
        return [
            note
            for note in self._notes.list_active()
            if location_matches_directory_prefix(note.location, location)
        ]

    def notes_by_ids(self, note_ids: Sequence[int]) -> list[Note]:
        wanted = {int(note_id) for note_id in note_ids}
        return self._ordinary_order(
            [note for note in self._notes.list_active() if note.id in wanted]
        )

    def search_notes(self, query: str) -> list[Note]:
        if re.fullmatch(r"#[0-9]+", query):
            try:
                note = self.get_note(int(query[1:]))
            except (ValueError, OverflowError):
                # Unbounded input can exceed Python/SQLite's integer range.
                return []
            return [note] if note is not None else []
        clean = query.strip()
        if clean.startswith("#") and len(clean) > 1:
            return self.notes_for_tag(clean[1:].strip())
        parsed = parse_search_query(query)
        if not parsed.tokens:
            return []
        matches: list[tuple[int, Note]] = []
        for note in self._notes.list_active():
            match = match_search_fields(
                parsed,
                title=note.title,
                body=self.derived_text.get(note),
                directory=" / ".join(note.location.values()),
                tags=(note.tags, *parse_tags(note.tags)),
            )
            if match is not None:
                matches.append((match.score, note))
        matches.sort(
            key=lambda item: (
                item[0], item[1].updated_at, item[1].created_at, item[1].id
            ),
            reverse=True,
        )
        return [note for _score, note in matches]

    def notes_for_tag(self, tag: str) -> list[Note]:
        wanted = normalize_tag(tag)
        return self._ordinary_order(
            [
                note
                for note in self._notes.list_active()
                if wanted and wanted in parse_tags(note.tags)
            ]
        )

    def smart_view(
        self,
        name: str,
        *,
        actor_target_id: str | None = None,
        query: str = "",
    ) -> tuple[str, list[Note]]:
        if name == "ai":
            return "AI资料", self.ai_materials(
                actor_target_id=actor_target_id,
                query=query,
            )
        notes = self._notes.list_active()
        if name == "pinned":
            return "固定", self._ordinary_order(
                [note for note in notes if note.is_pinned]
            )
        if name == "recent":
            return "最近", self._ordinary_order(notes)[:10]
        if name == "todo":
            return "待办", self._ordinary_order(
                [
                    note
                    for note in notes
                    if note.main_category == "待办"
                    or "todo" in parse_tags(note.tags)
                    or "todo" in note.tags
                ]
            )
        return "目录", []

    def ai_materials(
        self,
        *,
        actor_target_id: str | None = None,
        query: str = "",
    ) -> list[Note]:
        """Return frozen-R1 AI-material notes with actor and search combined."""
        actor_id = None
        if actor_target_id is not None:
            actor_id = resolve_target_id(actor_target_id)
            if actor_id is None:
                raise ValueError(f"未知 AI 适用对象：{actor_target_id}")
        parsed = parse_search_query(query)
        if not parsed.is_blank and not parsed.tokens:
            return []
        notes = self._notes.list_ai_materials(
            actor_target_id=actor_id,
            visible_values=AI_VISIBILITY_VALUES[1:],
        )
        if not parsed.tokens:
            return self._ordinary_order(notes)
        matches: list[tuple[int, Note]] = []
        for note in notes:
            match = match_search_fields(
                parsed,
                title=note.title,
                body=self.derived_text.get(note),
                directory=" / ".join(note.location.values()),
                tags=(note.tags, *parse_tags(note.tags)),
            )
            if match is not None:
                matches.append((match.score, note))
        matches.sort(
            key=lambda item: (
                item[0], item[1].updated_at, item[1].created_at, item[1].id
            ),
            reverse=True,
        )
        return [note for _score, note in matches]

    def related_notes(self, note_id: int | None) -> list[RelatedNote]:
        notes = self._notes.list_active()
        return build_related_notes(note_id, notes, {note.id: self.derived_text.get(note) for note in notes})

    def save_note(
        self,
        note_id: int,
        *,
        title: str,
        content_markdown: str,
        location: NoteLocation,
        expected_revision: int,
        tags_text: str | None = None,
    ) -> Note:
        """Save editable note fields atomically when tags text is supplied.

        ``None`` keeps the established body-only behavior for non-editor callers.
        """
        return self._notes.save(
            note_id,
            title=title,
            content_markdown=content_markdown,
            location=location,
            expected_revision=expected_revision,
            tags=None if tags_text is None else parse_tags(tags_text),
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

    @staticmethod
    def _ordinary_order(notes: Sequence[Note]) -> list[Note]:
        return sorted(
            notes,
            key=lambda note: (note.updated_at, note.created_at, note.id),
            reverse=True,
        )
