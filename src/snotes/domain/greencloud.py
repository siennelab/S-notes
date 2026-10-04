"""Pure records for GreenCloud proposal delivery to the Mac authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.snotes.domain.gale import GalePullResult


GREEN_CLOUD_SOURCE = "GreenCloud"
GREEN_CLOUD_ACTORS = frozenset({"claude-code", "clawbot"})
GREEN_CLOUD_OPERATIONS = frozenset(
    {"create", "prepend", "append", "update_note_metadata", "edit"}
)
GREEN_CLOUD_TERMINAL_STATES = frozenset({"accepted", "rejected", "conflict"})


class GreenCloudError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class GreenCloudSubmission:
    submission_uuid: str
    source_actor: str
    request_id: str
    operation_type: str
    note_id: int | None
    expected_version: str | None
    payload: dict[str, Any]
    created_at: str
    delivery_state: str
    delivered_at: str


@dataclass(frozen=True, slots=True)
class GreenCloudPullResult:
    exported_count: int
    imported_count: int
    acknowledged_count: int
    duplicate_count: int


@dataclass(frozen=True, slots=True)
class MultiSourcePullResult:
    """One background pass; failures are source names tried during the pass."""

    gale: GalePullResult | None
    greencloud: GreenCloudPullResult | None
    failures: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GreenCloudTerminalReceipt:
    """One durable Mac decision still needing Cloud delivery."""

    receipt_id: int
    source_actor: str
    cloud_request_id: str
    terminal_state: str
    receipt_payload: dict[str, object]
    fingerprint: str
    mirror_published: bool
