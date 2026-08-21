"""Pure records for the frozen-R1 Gale mirror and delivery contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


MIRROR_SCHEMA_VERSION = "2"
OUTBOX_SCHEMA_VERSION = 2
GALE_STAGING_SOURCE_ACTOR = "gale-staging"


class GaleError(RuntimeError):
    def __init__(self, code: str, *, previous_preserved: bool = True) -> None:
        self.code = code
        self.previous_preserved = previous_preserved
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class GaleMirrorRecord:
    source_note_id: int
    title: str
    body: str
    directory: str
    tags: tuple[str, ...]
    source_updated_at: str
    source_version: str
    visibility: str
    redacted: bool
    redaction_count: int
    ai_visibility: str = ""
    ai_targets: tuple[str, ...] = ()
    main_category: str = ""
    sub_category: str = ""


@dataclass(frozen=True, slots=True)
class GaleMirrorVerification:
    note_count: int
    fts_count: int
    full_note_count: int
    redacted_note_count: int
    file_mode: str


@dataclass(frozen=True, slots=True)
class GaleMirrorResult:
    output_path: Path
    schema_version: str
    target_actor: str
    note_count: int
    full_note_count: int
    redacted_note_count: int
    sha256: str
    file_mode: str


@dataclass(frozen=True, slots=True)
class GaleActivationResult:
    status: str
    reason: str
    rolled_back: bool = False
    previous_preserved: bool = True


@dataclass(frozen=True, slots=True)
class GalePublishResult:
    status: str
    sha256: str
    note_count: int
    reason: str = "none"
    uploaded: bool = False
    activated: bool = False
    rolled_back: bool = False
    previous_preserved: bool = True


@dataclass(frozen=True, slots=True)
class GaleSubmission:
    submission_uuid: str
    source_actor: str
    external_submission_id: str
    idempotency_key: str
    title: str
    content: str
    suggested_directory: str
    suggested_tags: tuple[str, ...]
    created_at: str
    delivery_state: str
    delivered_at: str
    ai_visibility: str = ""
    ai_targets: tuple[str, ...] = ()
    main_category: str = ""
    sub_category: str = ""
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GalePullResult:
    exported_count: int
    imported_count: int
    acknowledged_count: int
    duplicate_count: int
