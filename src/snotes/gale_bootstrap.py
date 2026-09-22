"""Headless composition root shared by the GUI and packaged Gale helper."""

from __future__ import annotations

import os
from pathlib import Path

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_audit_service import AiAuditService
from src.snotes.application.ai_receipt_inbox_service import AiReceiptInboxService
from src.snotes.application.ai_safe_read_service import AiSafeReadService
from src.snotes.application.gale_service import (
    GaleControlService,
    GaleMirrorService,
    GalePublishService,
    GalePullService,
)
from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.note_service import NoteService
from src.snotes.application.ports import GaleOutboxTransport, GalePublishTransport
from src.snotes.application.tag_service import TagService
from src.snotes.domain.markdown import markdown_to_plain_text
from src.snotes.storage.gale_mirror import SQLiteGaleMirrorWriter
from src.snotes.storage.repositories.ai_audit import SQLiteAiAuditRepository
from src.snotes.storage.repositories.ai_receipts import SQLiteAiReceiptInboxRepository
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.transport.gale.remote import (
    OUTBOX_MODES,
    SystemOracleOutboxTransport,
    SystemOraclePublishTransport,
)
from src.snotes.transport.gale.settings import JsonGaleRuntimeSettings


DATA_ROOT_ENV = "S_NOTES_DATA_ROOT"


def build_gale_control_service(
    *,
    data_root: Path | None = None,
    publish_transport: GalePublishTransport | None = None,
    outbox_transport: GaleOutboxTransport | None = None,
    outbox_mode: str = "production",
) -> GaleControlService:
    root = resolve_gale_data_root(data_root)
    connections = initialize_database(root / "s_notes.db")
    directories = SQLiteDirectoriesRepository(connections)
    notes = SQLiteNotesRepository(connections, directories)
    tags = SQLiteTagsRepository(connections)
    audit = AiAuditService(SQLiteAiAuditRepository(connections))
    metadata = AiAccessMetadataService(notes)
    receipts = AiReceiptInboxService(
        SQLiteAiReceiptInboxRepository(connections, directories, tags),
        notes=NoteService(notes),
        tags=TagService(tags),
        ai_metadata=metadata,
    )
    return compose_gale_control(
        root,
        notes,
        receipts,
        audit,
        directories=DirectoryService(directories, notes),
        tags=TagService(tags),
        ai_metadata=metadata,
        publish_transport=publish_transport,
        outbox_transport=outbox_transport,
        outbox_mode=outbox_mode,
    )


def compose_gale_control(
    root: Path,
    notes: SQLiteNotesRepository,
    receipts: AiReceiptInboxService,
    audit: AiAuditService,
    *,
    directories: DirectoryService | None = None,
    tags: TagService | None = None,
    ai_metadata: AiAccessMetadataService | None = None,
    publish_transport: GalePublishTransport | None = None,
    outbox_transport: GaleOutboxTransport | None = None,
    outbox_mode: str = "production",
) -> GaleControlService:
    settings = JsonGaleRuntimeSettings(root / "config" / "gale_runtime.json")
    mirror = GaleMirrorService(
        notes,
        AiSafeReadService(
            root / "config" / "local_redaction_rules.json",
            body_renderer=markdown_to_plain_text,
        ),
        SQLiteGaleMirrorWriter(),
        audit,
        directories=directories,
        tags=tags,
        ai_metadata=ai_metadata,
    )
    publish = GalePublishService(
        mirror,
        publish_transport or SystemOraclePublishTransport(),
        audit,
        mirror_path=root / "gale" / "runtime" / "gale-mirror.db",
        settings=settings,
    )
    try:
        expected_source_actor = OUTBOX_MODES[outbox_mode][1]
    except KeyError as error:
        raise ValueError("outbox_mode_invalid") from error
    pull = GalePullService(
        outbox_transport or SystemOracleOutboxTransport(mode=outbox_mode),
        receipts,
        audit,
        expected_source_actor=expected_source_actor,
    )
    return GaleControlService(publish, pull, settings)


def resolve_gale_data_root(data_root: Path | None) -> Path:
    from src.app_paths import default_data_root

    if data_root is not None:
        return default_data_root(environ={DATA_ROOT_ENV: str(data_root)})
    if DATA_ROOT_ENV in os.environ:
        return default_data_root()
    formal_root = default_data_root(environ={})
    if not (formal_root / "s_notes.db").is_file():
        raise RuntimeError("formal S-notes database is unavailable")
    return formal_root
