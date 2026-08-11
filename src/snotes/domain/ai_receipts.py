"""Pure records for the AI receipt inbox application boundary."""

from __future__ import annotations

from dataclasses import dataclass

from src.snotes.domain.models import NoteLocation


RECEIPT_STATUS_PENDING = "pending"
RECEIPT_STATUS_ACCEPTED = "accepted"
RECEIPT_STATUS_DISCARDED = "discarded"
RECEIPT_STATUS_REJECTED = "rejected"

RECEIPT_STATUS_LABELS = {
    RECEIPT_STATUS_PENDING: "待确认",
    RECEIPT_STATUS_ACCEPTED: "已接受",
    RECEIPT_STATUS_DISCARDED: "已丢弃",
    RECEIPT_STATUS_REJECTED: "已拒绝",
}


@dataclass(frozen=True, slots=True)
class AiReceiptInboxItem:
    """The fields the AI Center needs to render and identify one receipt."""

    id: int
    parse_status: str
    status: str
    source: str
    action: str
    title: str
    main_category: str
    sub_category: str
    sub_sub_category: str
    tags: str
    ai_visibility: str
    ai_targets: str
    body: str
    note_id: int | None
    created_at: str
    updated_at: str
    parse_error: str | None
    request_id: str | None
    actor_target_id: str | None
    operation_type: str | None
    external_submission_id: str | None
    idempotency_key: str | None

    @property
    def pending(self) -> bool:
        return self.status == RECEIPT_STATUS_PENDING

    @property
    def processed(self) -> bool:
        return not self.pending

    @property
    def status_label(self) -> str:
        return RECEIPT_STATUS_LABELS.get(self.status, self.status)


@dataclass(frozen=True, slots=True)
class AiReceiptAcceptCommand:
    title: str
    content_html: str
    location: NoteLocation
    tags: tuple[str, ...]
    visibility: str
    target_ids: tuple[str, ...]
    allow_directory_creation: bool


@dataclass(frozen=True, slots=True)
class AiReceiptAcceptPersistence:
    receipt: AiReceiptInboxItem
    note_id: int
    created: bool


@dataclass(frozen=True, slots=True)
class AiReceiptDiscardPersistence:
    receipt: AiReceiptInboxItem
    changed: bool


@dataclass(frozen=True, slots=True)
class GaleReceiptImport:
    external_submission_id: str
    idempotency_key: str
    title: str
    body: str
    location: NoteLocation
    tags: tuple[str, ...]
