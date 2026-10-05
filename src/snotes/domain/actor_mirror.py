"""Domain records for the local GreenCloud actor mirror contract."""

from __future__ import annotations

from dataclasses import dataclass


ACTOR_MIRROR_SCHEMA_VERSION = "2"
ACTOR_MIRROR_DIALECT = "snotes-markdown-v1"


class ActorMirrorError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ActorMirrorRecord:
    source_note_id: int
    title: str
    safe_text: str
    safe_markdown: str | None
    directory: str
    tags: tuple[str, ...]
    source_updated_at: str
    source_version: str
    visibility: str
    redaction_count: int
    ai_visibility: str
    ai_targets: tuple[str, ...]
    main_category: str = ""
    sub_category: str = ""
    dialect: str = ACTOR_MIRROR_DIALECT
