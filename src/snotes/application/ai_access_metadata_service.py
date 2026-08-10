"""Application owner for per-note AI visibility and target metadata."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.snotes.application.ai_actor_registry import (
    AI_TARGET_DEFINITIONS,
    AiTargetDefinition,
    normalize_target_ids,
    selectable_target_definitions,
    target_definition,
)
from src.snotes.application.ai_safe_read_service import (
    AI_VISIBILITY_VALUES,
    normalize_ai_visibility,
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
    legacy_target_ids: tuple[str, ...]
    legacy_target_names: tuple[str, ...]
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
        return selectable_target_definitions()

    def prepare_values(
        self,
        visibility: object,
        targets: Sequence[str],
    ) -> tuple[str, tuple[str, ...]]:
        """Apply the same metadata policy before a transactional note create."""
        return (
            normalize_ai_visibility(visibility),
            tuple(normalize_target_ids(targets)),
        )

    def target_catalog(self) -> tuple[AiTargetCatalogEntry, ...]:
        return tuple(
            (
                item.target_id,
                item.display_name,
                item.slug,
                item.system,
                item.selectable,
            )
            for item in AI_TARGET_DEFINITIONS
        )

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
        _note, stored_target_ids = self._notes.get_ai_access_metadata(note_id)
        preserved_legacy_ids = tuple(
            target_id
            for target_id in stored_target_ids
            if (
                (definition := target_definition(target_id)) is None
                or not definition.selectable
            )
        )
        requested = set(target_ids)
        target_ids.extend(
            target_id
            for target_id in preserved_legacy_ids
            if target_id not in requested
        )
        note, stored_target_ids = self._notes.set_ai_targets(
            note_id,
            target_ids,
            self.target_catalog(),
        )
        return self._materialize(note, stored_target_ids)

    @staticmethod
    def _materialize(
        note: Note,
        stored_target_ids: Sequence[str],
    ) -> AiAccessMetadata:
        target_set = set(stored_target_ids)
        definitions = tuple(
            item for item in AI_TARGET_DEFINITIONS if item.target_id in target_set
        )
        known_ids = {item.target_id for item in definitions}
        unknown_ids = tuple(
            sorted(target_set - known_ids, key=str.casefold)
        )
        legacy_definitions = tuple(
            item for item in definitions if not item.selectable
        )
        legacy_ids = tuple(item.target_id for item in legacy_definitions) + unknown_ids
        legacy_names = tuple(
            item.display_name for item in legacy_definitions
        ) + tuple(f"未知对象（{target_id}）" for target_id in unknown_ids)
        return AiAccessMetadata(
            note_id=note.id,
            visibility=normalize_ai_visibility(note.ai_visibility),
            target_ids=tuple(item.target_id for item in definitions) + unknown_ids,
            target_names=tuple(item.display_name for item in definitions)
            + tuple(f"未知对象（{target_id}）" for target_id in unknown_ids),
            legacy_target_ids=legacy_ids,
            legacy_target_names=legacy_names,
            revision=note.revision,
            updated_at=note.updated_at,
        )
