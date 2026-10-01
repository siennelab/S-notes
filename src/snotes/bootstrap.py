"""Composition root for the R2 S-notes application."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtWidgets import QApplication

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_write_authorization import AiWriteAuthorization
from src.snotes.application.ai_audit_service import AiAuditService
from src.snotes.application.ai_receipt_inbox_service import AiReceiptInboxService
from src.snotes.application.ai_safe_export_service import AiSafeExportService
from src.snotes.application.ai_safe_read_service import AiSafeReadService
from src.snotes.application.local_mcp_service import LocalMcpService
from src.snotes.application.note_service import NoteService
from src.snotes.application.reminder_service import ReminderService
from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.export_service import ExportService
from src.snotes.application.tag_service import TagService
from src.snotes.exporting.ordinary import OrdinaryExportWriter
from src.snotes.domain.markdown import markdown_to_plain_text
from src.snotes.exporting.ai_safe import AiSafePackageWriter
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.ai_receipts import SQLiteAiReceiptInboxRepository
from src.snotes.storage.repositories.ai_audit import SQLiteAiAuditRepository
from src.snotes.storage.repositories.ai_mcp_operations import (
    SQLiteAiMcpOperationRepository,
)
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.gale_bootstrap import compose_gale_control
from src.snotes.reminder_authority import (
    ReminderAuthority,
    ReminderAuthorityError,
    resolve_reminder_authority,
)
from src.snotes.transport.reminder_ntfy import NtfyReminderTransport
from src.snotes.ui.main_window import MainWindow
from src.snotes.ui.markdown_canonicalizer import MarkdownCanonicalizer


DATA_ROOT_ENV = "S_NOTES_DATA_ROOT"


def resolve_rebuild_data_root(data_root: Path | None = None) -> Path:
    from src.app_paths import default_data_root

    if data_root is None:
        return default_data_root()
    return default_data_root(environ={DATA_ROOT_ENV: str(data_root)})


def build_application(
    argv: Sequence[str] | None = None,
    *,
    data_root: Path | None = None,
    reminder_authority: ReminderAuthority,
) -> tuple[QApplication, MainWindow]:
    """Compose the application against the canonical or isolated data root.

    ``reminder_authority`` is required: composition never guesses it.
    """
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
        canonicalizer=MarkdownCanonicalizer(),
        notes=note_service,
        authorization=AiWriteAuthorization(directories=directory_service),
        tags=tag_service,
        ai_metadata=ai_access_metadata_service,
        mcp_operations=SQLiteAiMcpOperationRepository(
            connections, tags
        ),
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
            body_renderer=markdown_to_plain_text,
        ),
        AiSafePackageWriter(root / "exports" / "ai_readbox"),
        ai_audit_service,
    )
    gale_control_service = compose_gale_control(
        root,
        notes,
        receipt_inbox_service,
        ai_audit_service,
        directories=directory_service,
        tags=tag_service,
        ai_metadata=ai_access_metadata_service,
    )
    remote_controller = None
    if reminder_authority.is_remote:
        from src.snotes.transport.remote_reminder_client import RemoteReminderClient
        from src.snotes.ui.remote_reminder_controller import RemoteReminderController
        from src.snotes.ui.reminder_owner import OwnerSnapshotCache, owner_credential

        remote_controller = RemoteReminderController(
            RemoteReminderClient(reminder_authority.endpoint, owner_credential),
            cache=OwnerSnapshotCache(root / "gui-only-reminder" / "snapshot.json"),
        )
        reminder_service = None
    else:
        reminder_service = compose_reminder_service(root, reminder_authority)
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
        reminder_service=reminder_service,
        remote_reminder_controller=remote_controller,
    )
    if remote_controller is not None:
        from src.snotes.ui.reminder_legacy_check import start_legacy_check
        window.setWindowTitle("S-notes Step 7 · GreenCloud staging")
        start_legacy_check(window)
    application.aboutToQuit.connect(window.body_edit.page.deleteLater)
    return application, window


def compose_reminder_service(
    root: Path,
    reminder_authority: ReminderAuthority,
) -> ReminderService:
    if reminder_authority.is_remote:
        raise ReminderAuthorityError("local_reminder_service_requires_local_authority")
    connections = initialize_database(Path(root) / "s_notes.db")
    repository = SQLiteReminderRepository(connections)
    settings = ReminderService(repository).initialize_settings()
    endpoint = ReminderService._private_ntfy_endpoint(settings.ntfy_endpoint)
    if not endpoint and reminder_authority.helper_retired:
        raise RuntimeError("private_reminder_endpoint_required")
    transport = NtfyReminderTransport(endpoint or "https://ntfy.sh")
    return ReminderService(repository, transport=transport)


def build_local_mcp_service(
    *,
    actor_target_id: str,
    resolved_data_root: Path,
    reminder_authority: ReminderAuthority,
    create_confirmation_required: bool = False,
) -> LocalMcpService:
    """Compose MCP after its runtime owner has resolved the data root."""
    root = Path(resolved_data_root)
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
    safe_reads = AiSafeReadService(
        root / "config" / "local_redaction_rules.json", body_renderer=markdown_to_plain_text,
    )
    authorization = AiWriteAuthorization(directories=directories, safe_reads=safe_reads)
    receipt_inbox = AiReceiptInboxService(
        SQLiteAiReceiptInboxRepository(
            connections, directories_repository, tags_repository
        ),
        canonicalizer=MarkdownCanonicalizer(),
        notes=notes,
        authorization=authorization,
        tags=tags,
        ai_metadata=ai_metadata,
        mcp_operations=SQLiteAiMcpOperationRepository(
            connections, tags_repository
        ),
    )
    remote_reminders = None
    if reminder_authority.is_remote:
        from src.snotes.application.remote_mcp_reminder import compose_remote_mcp_reminder
        from src.snotes.transport.remote_reminder_client import RemoteReminderClient

        remote_reminders = compose_remote_mcp_reminder(
            actor_target_id,
            reminder_authority.endpoint,
            client_factory=RemoteReminderClient,
        )
        reminder_service = None
    else:
        reminder_repository = SQLiteReminderRepository(connections)
        reminder_service = ReminderService(reminder_repository)
    return LocalMcpService(
        actor_target_id=actor_target_id,
        notes=notes,
        directories=directories,
        tags=tags,
        ai_metadata=ai_metadata,
        safe_reads=safe_reads,
        authorization=authorization,
        audit=audit,
        receipt_inbox=receipt_inbox,
        reminders=reminder_service,
        remote_reminders=remote_reminders,
        create_confirmation_required=create_confirmation_required,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    data_root: Path | None = None,
    reminder_authority: ReminderAuthority | None = None,
) -> int:
    application, window = build_application(
        argv,
        data_root=data_root,
        reminder_authority=(
            reminder_authority
            if reminder_authority is not None
            else resolve_reminder_authority()
        ),
    )
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
