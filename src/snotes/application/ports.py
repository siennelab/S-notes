"""Repository interfaces owned by Package 1 application services."""

from __future__ import annotations

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
        content_html: str,
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
        content_html: str,
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
        addition_html: str = "",
        title: str | None = None,
        directory_id: int | None = None,
        location: NoteLocation | None = None,
        tags: Sequence[str] | None = None,
    ) -> dict: ...

    def accept_pending_append(
        self,
        *,
        receipt_id: int,
        addition_html: str,
        accepted_body: str,
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
