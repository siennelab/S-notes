"""Repository interfaces owned by Package 1 application services."""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Protocol, Sequence

from src.snotes.domain.ai_receipts import (
    AiReceiptAcceptCommand,
    AiReceiptAcceptPersistence,
    AiReceiptDiscardPersistence,
    AiReceiptInboxItem,
    GaleReceiptImport,
)
from src.snotes.domain.gale import (
    GaleActivationResult,
    GaleMirrorRecord,
    GaleMirrorResult,
    GaleMirrorVerification,
    GaleSubmission,
)
from src.snotes.domain.ai_audit import AiAuditEvent, AiCallLog
from src.snotes.domain.ai_safe_export import AiSafeExportPlan, AiSafeExportResult
from src.snotes.domain.models import (
    Directory,
    Note,
    NoteLocation,
    TrashResult,
    TrashSnapshot,
    ExportResult,
)
from src.snotes.domain.reminders import (
    Reminder,
    ReminderDraft,
    ReminderLabel,
    ReminderOperationPage,
    ReminderOccurrence,
    ReminderQuery,
    ReminderTable,
    ReminderTrashSnapshot,
    ReminderSettings,
)


AiTargetCatalogEntry = tuple[str, str, str, bool, bool]
TagRecord = tuple[int, str]


class AiReceiptInboxRepository(Protocol):
    def list_pending(self) -> list[AiReceiptInboxItem]: ...

    def list_processed(self) -> list[AiReceiptInboxItem]: ...

    def get(self, receipt_id: int) -> AiReceiptInboxItem | None: ...

    def delete_if_unchanged(
        self,
        receipt_id: int,
        *,
        expected_status: str,
        expected_note_id: int | None,
    ) -> bool: ...

    def accept_pending(
        self,
        receipt_id: int,
        command: AiReceiptAcceptCommand,
        target_catalog: Sequence[AiTargetCatalogEntry],
    ) -> AiReceiptAcceptPersistence: ...

    def discard_pending(self, receipt_id: int) -> AiReceiptDiscardPersistence: ...

    def import_gale_pending(
        self, submission: GaleReceiptImport
    ) -> tuple[AiReceiptInboxItem, bool]: ...


class NotesRepository(Protocol):
    def create(self, location: NoteLocation) -> Note: ...

    def get(self, note_id: int, *, include_deleted: bool = False) -> Note | None: ...

    def list_active(self) -> list[Note]: ...

    def list_ai_materials(
        self,
        *,
        actor_target_id: str | None,
        visible_values: Sequence[str],
    ) -> list[Note]: ...

    def get_ai_access_metadata(
        self,
        note_id: int,
        *,
        include_deleted: bool = False,
    ) -> tuple[Note, tuple[str, ...]]: ...

    def set_ai_visibility(
        self,
        note_id: int,
        visibility: str,
    ) -> tuple[Note, tuple[str, ...]]: ...

    def set_ai_targets(
        self,
        note_id: int,
        target_ids: Sequence[str],
        target_catalog: Sequence[AiTargetCatalogEntry],
    ) -> tuple[Note, tuple[str, ...]]: ...

    def save(
        self,
        note_id: int,
        *,
        title: str,
        content_markdown: str,
        location: NoteLocation,
        expected_revision: int,
    ) -> Note: ...

    def rename(self, note_id: int, title: str) -> Note: ...

    def move(self, note_id: int, location: NoteLocation) -> Note: ...

    def move_many(self, note_ids: Sequence[int], location: NoteLocation) -> list[Note]: ...

    def set_pinned(self, note_id: int, pinned: bool) -> Note: ...

    def soft_delete(self, note_id: int) -> Note: ...

    def restore(self, note_id: int) -> Note: ...

    def permanently_delete_trashed(self, note_id: int) -> None: ...


class DirectoriesRepository(Protocol):
    def create(self, name: str, parent_id: int | None = None) -> Directory: ...

    def list_active(self) -> list[Directory]: ...

    def get_path(self, directory_id: int) -> tuple[str, ...]: ...

    def rename(self, directory_id: int, name: str) -> None: ...

    def soft_delete_tree(self, directory_id: int) -> str: ...

    def restore_tree(self, directory_id: int) -> None: ...

    def permanently_delete_tree(self, directory_id: int) -> None: ...

    def list_trash(self) -> TrashSnapshot: ...

    def empty_trash(self) -> TrashResult: ...


class TagsRepository(Protocol):
    def set_for_note(self, note_id: int, tags: Sequence[str]) -> list[str]: ...

    def get_for_note(self, note_id: int) -> list[str]: ...

    def list_all(self) -> list[str]: ...

    def get_records_for_note(self, note_id: int) -> list[TagRecord]: ...


class ReminderRepository(Protocol):
    def create(self, draft: ReminderDraft) -> Reminder: ...

    def update(self, reminder_id: int, draft: ReminderDraft) -> Reminder: ...

    def get(self, reminder_id: int, *, include_deleted: bool = False) -> Reminder | None: ...

    def list_active_records(self, table_id: int | None = None) -> list[Reminder]: ...

    def list_records(
        self,
        *,
        include_deleted: bool = False,
        table_id: int | None = None,
    ) -> list[Reminder]: ...

    def list_labels(self, *, include_deleted: bool = False) -> list[ReminderLabel]: ...

    def get_label(self, label_id: int, *, include_deleted: bool = False) -> ReminderLabel | None: ...

    def default_label(self) -> ReminderLabel: ...

    def create_label(self, name: str) -> ReminderLabel: ...

    def rename_label(self, label_id: int, name: str) -> ReminderLabel: ...

    def reorder_labels(self, label_ids: Sequence[int]) -> list[ReminderLabel]: ...

    def soft_delete_label_with_tables(self, label_id: int) -> None: ...

    def list_tables(self, *, include_deleted: bool = False) -> list[ReminderTable]: ...

    def get_table(
        self,
        table_id: int,
        *,
        include_deleted: bool = False,
    ) -> ReminderTable | None: ...

    def default_table(self) -> ReminderTable: ...

    def create_table(
        self,
        *,
        title: str,
        label_id: int | None = None,
        ai_visible: bool = True,
    ) -> ReminderTable: ...

    def update_table_metadata(
        self,
        table_id: int,
        *,
        title: str | None = None,
        label_id: int | None = None,
    ) -> ReminderTable: ...

    def set_table_ai_visible(self, table_id: int, ai_visible: bool) -> ReminderTable: ...

    def soft_delete_table(self, table_id: int) -> None: ...

    def restore_table(self, table_id: int) -> ReminderTable: ...

    def permanently_delete_table(self, table_id: int) -> None: ...

    def count_items_by_table(self, table_id: int, *, include_deleted: bool = False) -> int: ...

    def set_lifecycle(
        self,
        reminder_id: int,
        lifecycle: str,
        completed_at: str | None = None,
    ) -> None: ...

    def permanently_delete_trashed(self, reminder_id: int) -> None: ...

    def list_trash(self) -> ReminderTrashSnapshot: ...

    def delete(self, reminder_id: int) -> None: ...

    def upsert_occurrence(
        self,
        reminder_id: int,
        occurrence_key: str,
        action_date: str,
        deadline_date: str | None,
    ) -> ReminderOccurrence: ...

    def get_occurrence(
        self,
        reminder_id: int,
        occurrence_key: str,
    ) -> ReminderOccurrence | None: ...

    def list_occurrences(self, reminder_id: int) -> list[ReminderOccurrence]: ...

    def delete_pending_occurrences(self, reminder_id: int) -> None: ...

    def mark_occurrence_completed(self, occurrence_id: int, completed_at: str) -> None: ...

    def notification_dispatch_lock(self) -> AbstractContextManager[bool]: ...

    def mark_notified(self, occurrence_id: int, kind: str, notified_at: str) -> None: ...

    def settings(self) -> ReminderSettings: ...

    def save_settings(self, settings: ReminderSettings) -> None: ...

    def get_text_setting(self, key: str, default: str = "") -> str: ...

    def set_text_setting(self, key: str, value: str) -> None: ...

    def get_operation(self, request_id: str, actor_target_id: str | None = None) -> dict | None: ...

    def store_operation(
        self,
        *,
        actor_target_id: str,
        operation_type: str,
        result: dict,
    ) -> dict: ...

    def record_ai_operation(
        self,
        *,
        actor_target_id: str,
        action: str,
        table_id: int | None,
        table_title: str,
        item_id: int | None,
        item_title: str,
        label_id: int | None,
        label_name: str,
        request_id: str,
        changed_fields: Sequence[str],
        before: dict,
        after: dict,
    ) -> None: ...

    def list_ai_operation_logs(self, *, offset: int = 0, limit: int = 50) -> ReminderOperationPage: ...


class ReminderNotificationTransport(Protocol):
    def send(self, *, topic: str, title: str, message: str) -> None: ...


class AiMcpOperationRepository(Protocol):
    """Atomic persistence used by the local-MCP application orchestration."""

    def confirmation_required(self, operation_type: str) -> bool: ...

    def get_operation(
        self,
        request_id: str,
        actor_target_id: str | None = None,
    ) -> dict | None: ...

    def store_terminal(
        self,
        actor_target_id: str,
        operation_type: str,
        result: dict,
    ) -> dict: ...

    def create_direct(
        self,
        *,
        actor_target_id: str,
        request_id: str,
        title: str,
        content_markdown: str,
        directory_id: int,
        location: NoteLocation,
        tags: Sequence[str],
        visibility: str,
        target_ids: Sequence[str],
        target_catalog: Sequence[AiTargetCatalogEntry],
    ) -> dict: ...

    def modify_direct(
        self,
        *,
        actor_target_id: str,
        request_id: str,
        note_id: int,
        operation_type: str,
        expected_revision: int,
        expected_updated_at: str,
        addition_markdown: str = "",
        canonical_markdown: str | None = None,
        expected_content_markdown: str | None = None,
        title: str | None = None,
        directory_id: int | None = None,
        location: NoteLocation | None = None,
        tags: Sequence[str] | None = None,
    ) -> dict: ...

    def accept_pending_append(
        self,
        *,
        receipt_id: int,
        addition_markdown: str,
        accepted_body: str,
        canonical_markdown: str,
        expected_revision: int,
        expected_content_markdown: str,
    ) -> dict: ...

    def create_pending(
        self,
        *,
        actor_target_id: str,
        actor_display: str,
        request_id: str,
        operation_type: str,
        title: str,
        note_id: int | None,
        expected_updated_at: str | None,
        payload: dict,
        location: NoteLocation,
        tags: Sequence[str],
        body: str,
        visibility: str,
        target_ids: Sequence[str],
    ) -> dict: ...


class OrdinaryExportWriter(Protocol):
    def write_markdown(self, notes: Sequence[Note], scope: str) -> ExportResult: ...

    def write_json(self, notes: Sequence[Note], scope: str) -> ExportResult: ...

    def write_database_backup(self) -> ExportResult: ...


class AiSafePackageWriter(Protocol):
    def write_plan(self, plan: AiSafeExportPlan) -> AiSafeExportResult: ...


class AiAuditRepository(Protocol):
    def record(self, event: AiAuditEvent) -> None: ...

    def list(self, operation_type: str | None = None) -> list[AiCallLog]: ...


class GaleMirrorWriter(Protocol):
    def write(
        self,
        records: Sequence[GaleMirrorRecord],
        output_path: Path,
        *,
        actor_target_id: str,
        directories: Sequence[dict] = (),
        tags: Sequence[str] = (),
        visibility_options: Sequence[tuple[str, str]] = (),
        ai_targets: Sequence[tuple[str, str]] = (),
    ) -> GaleMirrorResult: ...

    def verify(
        self,
        mirror_path: Path,
        *,
        expected_note_count: int | None = None,
        expected_actor: str,
    ) -> GaleMirrorVerification: ...


class GalePublishTransport(Protocol):
    """The existing Oracle incoming/current/previous activation boundary."""

    def upload_candidate(self, mirror_path: Path, sha256: str) -> None: ...

    def activate_candidate(
        self, sha256: str, note_count: int
    ) -> GaleActivationResult: ...

    def health(self, sha256: str) -> bool: ...

    def rollback(
        self,
        expected_current_sha256: str,
        expected_previous_sha256: str,
    ) -> GaleActivationResult: ...


class GaleOutboxTransport(Protocol):
    def export_unacknowledged(self) -> Sequence[GaleSubmission]: ...

    def acknowledge(self, submission_uuid: str, local_receipt_id: str) -> None: ...

    def status(self) -> dict[str, int]: ...


class GaleRuntimeSettings(Protocol):
    def get_bool(self, key: str, default: bool) -> bool: ...

    def set_bool(self, key: str, value: bool) -> None: ...

    def get_text(self, key: str, default: str = "") -> str: ...

    def set_text(self, key: str, value: str) -> None: ...
