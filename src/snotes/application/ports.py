"""Repository interfaces owned by Package 1 application services."""

from __future__ import annotations

from typing import Protocol, Sequence

from src.snotes.domain.ai_receipts import (
    AiReceiptAcceptCommand,
    AiReceiptAcceptPersistence,
    AiReceiptDiscardPersistence,
    AiReceiptInboxItem,
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


class OrdinaryExportWriter(Protocol):
    def write_markdown(self, notes: Sequence[Note], scope: str) -> ExportResult: ...

    def write_json(self, notes: Sequence[Note], scope: str) -> ExportResult: ...

    def write_database_backup(self) -> ExportResult: ...


class AiSafePackageWriter(Protocol):
    def write_plan(self, plan: AiSafeExportPlan) -> AiSafeExportResult: ...


class AiAuditRepository(Protocol):
    def record(self, event: AiAuditEvent) -> None: ...

    def list(self, operation_type: str | None = None) -> list[AiCallLog]: ...
