"""Composition root for the R2 S-notes application."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtWidgets import QApplication

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_audit_service import AiAuditService
from src.snotes.application.ai_receipt_inbox_service import AiReceiptInboxService
from src.snotes.application.ai_safe_export_service import AiSafeExportService
from src.snotes.application.ai_safe_read_service import AiSafeReadService
from src.snotes.application.local_mcp_service import LocalMcpService
from src.snotes.application.note_service import NoteService
from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.export_service import ExportService
from src.snotes.application.tag_service import TagService
from src.snotes.exporting.ordinary import OrdinaryExportWriter
from src.snotes.exporting.ordinary import html_to_markdown
from src.snotes.exporting.ordinary import html_to_text
from src.snotes.exporting.ai_safe import AiSafePackageWriter
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.ai_receipts import SQLiteAiReceiptInboxRepository
from src.snotes.storage.repositories.ai_audit import SQLiteAiAuditRepository
from src.snotes.storage.repositories.ai_mcp_operations import (
    SQLiteAiMcpOperationRepository,
)
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.gale_bootstrap import compose_gale_control
from src.snotes.ui.main_window import MainWindow


DATA_ROOT_ENV = "S_NOTES_DATA_ROOT"
CANDIDATE_LABEL_ENV = "S_NOTES_CANDIDATE_LABEL"
FORMAL_DATA_ROOT = Path("/Users/s/Library/Application Support/S-notes")


def resolve_rebuild_data_root(data_root: Path | None = None) -> Path:
    supplied = data_root if data_root is not None else os.environ.get(DATA_ROOT_ENV)
    if not supplied:
        raise RuntimeError(
            "S-lumen rebuild requires an explicit isolated S_NOTES_DATA_ROOT"
        )
    resolved = Path(supplied).expanduser().resolve()
    if resolved == FORMAL_DATA_ROOT.resolve():
        raise RuntimeError("S-lumen rebuild refuses the formal S-notes data root")
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise RuntimeError(
            f"{DATA_ROOT_ENV} is not creatable and writable"
        ) from error
    return resolved


def build_application(
    argv: Sequence[str] | None = None,
    *,
    data_root: Path | None = None,
) -> tuple[QApplication, MainWindow]:
    """Compose the Package 2 runtime against an explicit isolated data root."""
    application = QApplication.instance()
    if application is None:
        application = QApplication(list(argv) if argv is not None else sys.argv)

    root = resolve_rebuild_data_root(data_root)
    connections = initialize_database(root / "s_notes.db")
    directories = SQLiteDirectoriesRepository(connections)
    notes = SQLiteNotesRepository(connections, directories)
    tags = SQLiteTagsRepository(connections)
    note_service = NoteService(notes)
    ai_access_metadata_service = AiAccessMetadataService(notes)
    directory_service = DirectoryService(directories, notes)
    tag_service = TagService(tags)
    receipt_inbox_service = AiReceiptInboxService(
        SQLiteAiReceiptInboxRepository(connections, directories, tags),
        notes=note_service,
        tags=tag_service,
        ai_metadata=ai_access_metadata_service,
    )
    export_service = ExportService(
        notes,
        OrdinaryExportWriter(root / "exports", root / "s_notes.db"),
    )
    ai_audit_service = AiAuditService(SQLiteAiAuditRepository(connections))
    ai_safe_export_service = AiSafeExportService(
        notes,
        AiSafeReadService(
            root / "config" / "local_redaction_rules.json",
            body_renderer=html_to_markdown,
        ),
        AiSafePackageWriter(root / "exports" / "ai_readbox"),
        ai_audit_service,
    )
    gale_control_service = compose_gale_control(
        root,
        notes,
        receipt_inbox_service,
        ai_audit_service,
    )
    window = MainWindow(
        note_service,
        theme_settings_path=root / "config" / "ui_settings.ini",
        directory_service=directory_service,
        tag_service=tag_service,
        export_service=export_service,
        ai_access_metadata_service=ai_access_metadata_service,
        receipt_inbox_service=receipt_inbox_service,
        ai_safe_export_service=ai_safe_export_service,
        ai_audit_service=ai_audit_service,
        gale_control_service=gale_control_service,
    )
    candidate_label = os.environ.get(CANDIDATE_LABEL_ENV, "").strip()
    if candidate_label:
        window.setWindowTitle(f"S-notes — {candidate_label}")
    return application, window


def build_local_mcp_service(
    *,
    actor_target_id: str,
    data_root: Path | None = None,
) -> LocalMcpService:
    """Compose the local stdio MCP against the same R2 owners as the GUI."""
    root = resolve_rebuild_data_root(data_root)
    connections = initialize_database(root / "s_notes.db")
    directories_repository = SQLiteDirectoriesRepository(connections)
    notes_repository = SQLiteNotesRepository(
        connections, directories_repository
    )
    tags_repository = SQLiteTagsRepository(connections)
    notes = NoteService(notes_repository)
    directories = DirectoryService(directories_repository, notes_repository)
    tags = TagService(tags_repository)
    ai_metadata = AiAccessMetadataService(notes_repository)
    audit = AiAuditService(SQLiteAiAuditRepository(connections))
    receipt_inbox = AiReceiptInboxService(
        SQLiteAiReceiptInboxRepository(
            connections, directories_repository, tags_repository
        ),
        notes=notes,
        tags=tags,
        ai_metadata=ai_metadata,
        mcp_operations=SQLiteAiMcpOperationRepository(
            connections, tags_repository
        ),
    )
    return LocalMcpService(
        actor_target_id=actor_target_id,
        notes=notes,
        directories=directories,
        tags=tags,
        ai_metadata=ai_metadata,
        safe_reads=AiSafeReadService(
            root / "config" / "local_redaction_rules.json",
            body_renderer=html_to_text,
        ),
        audit=audit,
        receipt_inbox=receipt_inbox,
    )


def main(argv: Sequence[str] | None = None, *, data_root: Path | None = None) -> int:
    application, window = build_application(argv, data_root=data_root)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
