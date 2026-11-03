"""Build a local GreenCloud mirror for one formally registered actor."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from src.snotes.application.ai_actor_registry import AI_ACTOR_IDS, target_definition
from src.snotes.application.ai_safe_read_service import (
    AI_VISIBILITY_FULL,
    AI_VISIBILITY_REDACTED,
    AiSafeReadService,
    AiSafeReadSource,
    RedactionUnavailableError,
)
from src.snotes.application.local_mcp_write_service import note_version_token
from src.snotes.domain.actor_mirror import ACTOR_MIRROR_DIALECT, ActorMirrorError, ActorMirrorRecord
from src.snotes.domain.greencloud import GREEN_CLOUD_MIRROR_ACTOR_SEQUENCE
from src.snotes.domain.tags import parse_tags
from src.snotes.application.ports import ActorMirrorWriter, ActorMirrorSource


SUPPORTED_ACTORS = GREEN_CLOUD_MIRROR_ACTOR_SEQUENCE


class ActorMetadataCatalogSource(Protocol):
    def catalog(self) -> dict[str, list]: ...


class ActorMirrorService:
    def __init__(
        self,
        notes: ActorMirrorSource,
        safe_reads: AiSafeReadService,
        writer: ActorMirrorWriter,
        metadata_catalog: ActorMetadataCatalogSource | None = None,
    ) -> None:
        self._notes = notes
        self._safe_reads = safe_reads
        self._writer = writer
        self._metadata_catalog = metadata_catalog

    def generate(self, actor: str, output_path: Path) -> dict:
        actor_id = str(actor or "").strip()
        definition = target_definition(actor_id)
        if actor_id not in AI_ACTOR_IDS or definition is None:
            raise ActorMirrorError("unknown_actor")
        self._notes.validate_mirror_output(Path(output_path))
        source_schema = self._notes.source_schema()
        records: list[ActorMirrorRecord] = []
        for note, target_ids in self._notes.list_active_with_ai_access():
            source = AiSafeReadSource(
                note_id=note.id, title=note.title, content=note.content_markdown,
                visibility=note.ai_visibility, targets=target_ids,
                directory=" / ".join(note.location.values()), tags=tuple(parse_tags(note.tags)),
            )
            try:
                safe = self._safe_reads.read_markdown_note(actor_id, source)
            except RedactionUnavailableError as error:
                raise ActorMirrorError("redaction_rules_unavailable") from error
            if safe is None:
                continue
            safe_text = safe.content
            safe_markdown = note.content_markdown if safe.visibility == AI_VISIBILITY_FULL else None
            records.append(ActorMirrorRecord(
                source_note_id=note.id, title=safe.title, safe_text=safe_text,
                safe_markdown=safe_markdown, directory=safe.directory, tags=safe.tags,
                source_updated_at=note.updated_at,
                source_version=note_version_token(note.id, note.updated_at),
                visibility=safe.visibility, redaction_count=safe.redaction_count,
                ai_visibility=note.ai_visibility, ai_targets=tuple(target_ids),
                main_category=note.main_category,
                sub_category=note.sub_category,
                dialect=ACTOR_MIRROR_DIALECT,
            ))
        metadata_catalog = (
            self._metadata_catalog.catalog()
            if self._metadata_catalog is not None
            else None
        )
        result = self._writer.write(
            tuple(records), Path(output_path), actor=actor_id,
            source_schema=source_schema, metadata_catalog=metadata_catalog,
        )
        result["full_note_count"] = sum(item.visibility == AI_VISIBILITY_FULL for item in records)
        result["redacted_note_count"] = sum(item.visibility == AI_VISIBILITY_REDACTED for item in records)
        return result

    def verify(self, path: Path, *, expected_actor: str, expected_note_count: int | None = None) -> dict:
        if target_definition(expected_actor) is None:
            raise ActorMirrorError("unknown_actor")
        return self._writer.verify(
            path, expected_actor=expected_actor, expected_note_count=expected_note_count
        )
