"""Application owner for per-note AI visibility and target metadata."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.snotes.application.ai_safe_read_service import (
    AI_TARGET_DEFINITIONS,
    AI_VISIBILITY_VALUES,
    AiTargetDefinition,
    normalize_ai_visibility,
    normalize_target_ids,
    visibility_display,
)
from src.snotes.application.ports import AiTargetCatalogEntry, NotesRepository
from src.snotes.domain.models import Note


@dataclass(frozen=True, slots=True)
class AiAccessMetadata:
    note_id: int
    visibility: str
    target_ids: tuple[str, ...]
    target_names: tuple[str, ...]
    revision: int
    updated_at: str


class AiAccessMetadataService:
    """Normalize metadata policy and delegate persistence to one repository."""

    def __init__(self, notes: NotesRepository) -> None:
        self._notes = notes

    def available_visibilities(self) -> tuple[str, ...]:
        return AI_VISIBILITY_VALUES

    def available_visibility_options(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (visibility, visibility_display(visibility))
            for visibility in AI_VISIBILITY_VALUES
        )

    def available_targets(self) -> tuple[AiTargetDefinition, ...]:
        return AI_TARGET_DEFINITIONS

    def get_note_metadata(self, note_id: int) -> AiAccessMetadata:
        note, target_ids = self._notes.get_ai_access_metadata(note_id)
        return self._materialize(note, target_ids)

    def set_note_visibility(
        self,
        note_id: int,
        visibility: object,
    ) -> AiAccessMetadata:
        clean_visibility = normalize_ai_visibility(visibility)
        note, target_ids = self._notes.set_ai_visibility(
            note_id,
            clean_visibility,
        )
        return self._materialize(note, target_ids)

    def set_note_targets(
        self,
        note_id: int,
        targets: Sequence[str],
    ) -> AiAccessMetadata:
        target_ids = normalize_target_ids(targets)
        note, stored_target_ids = self._notes.set_ai_targets(
            note_id,
            target_ids,
            self._target_catalog(),
        )
        return self._materialize(note, stored_target_ids)

    @staticmethod
    def _target_catalog() -> tuple[AiTargetCatalogEntry, ...]:
        return tuple(
            (item.target_id, item.display_name, item.slug, item.system)
            for item in AI_TARGET_DEFINITIONS
        )

    @staticmethod
    def _materialize(
        note: Note,
        stored_target_ids: Sequence[str],
    ) -> AiAccessMetadata:
        target_set = set(stored_target_ids)
        definitions = tuple(
            item for item in AI_TARGET_DEFINITIONS if item.target_id in target_set
        )
        return AiAccessMetadata(
            note_id=note.id,
            visibility=normalize_ai_visibility(note.ai_visibility),
            target_ids=tuple(item.target_id for item in definitions),
            target_names=tuple(item.display_name for item in definitions),
            revision=note.revision,
            updated_at=note.updated_at,
        )
