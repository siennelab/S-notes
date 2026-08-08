"""Sole application owner for ordinary export scope and format decisions."""

from __future__ import annotations

from collections.abc import Sequence

from src.snotes.application.ports import NotesRepository, OrdinaryExportWriter
from src.snotes.domain.models import (
    ExportResult,
    Note,
    NoteLocation,
    location_matches_directory_prefix,
)


FORMAT_MARKDOWN = "markdown"
FORMAT_JSON = "json"


class ExportService:
    def __init__(
        self,
        notes: NotesRepository,
        writer: OrdinaryExportWriter,
    ) -> None:
        self._notes = notes
        self._writer = writer

    def export_current(self, note_id: int, export_format: str) -> ExportResult:
        note = self._notes.get(note_id)
        return self._write([note] if note is not None else [], "current_note", export_format)

    def export_note_ids(
        self,
        note_ids: Sequence[int],
        scope: str,
        export_format: str,
    ) -> ExportResult:
        wanted = {int(note_id) for note_id in note_ids}
        notes = [note for note in self._notes.list_active() if note.id in wanted]
        return self._write(self._ordinary_order(notes), scope, export_format)

    def export_directories(
        self,
        locations: Sequence[NoteLocation],
        export_format: str,
    ) -> ExportResult:
        notes_by_id: dict[int, Note] = {}
        active = self._notes.list_active()
        for location in locations:
            for note in active:
                if location_matches_directory_prefix(note.location, location):
                    notes_by_id[note.id] = note
        return self._write(
            self._ordinary_order(list(notes_by_id.values())),
            "selected_directories",
            export_format,
        )

    def export_all(self, export_format: str) -> ExportResult:
        return self._write(
            self._ordinary_order(self._notes.list_active()),
            "all_notes",
            export_format,
        )

    def backup_database(self) -> ExportResult:
        return self._writer.write_database_backup()

    def _write(
        self,
        notes: Sequence[Note],
        scope: str,
        export_format: str,
    ) -> ExportResult:
        if not notes:
            raise ValueError("当前范围没有可导出的笔记。")
        if export_format == FORMAT_MARKDOWN:
            return self._writer.write_markdown(notes, scope)
        if export_format == FORMAT_JSON:
            return self._writer.write_json(notes, scope)
        raise ValueError("请选择 Markdown 或 JSON 格式。")

    @staticmethod
    def _ordinary_order(notes: Sequence[Note]) -> list[Note]:
        return sorted(
            notes,
            key=lambda note: (note.updated_at, note.created_at, note.id),
            reverse=True,
        )
