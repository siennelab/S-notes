"""Application interpretation of current and historical acceptance results."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from collections.abc import Mapping

from src.snotes.domain.notes_operation_result import HandlingMode, OperationStatus


class AcceptanceKind(StrEnum):
    SUCCESS = "success"
    CONFLICT = "conflict"
    REJECTED = "rejected"
    PENDING = "pending"


@dataclass(frozen=True, slots=True)
class AcceptanceOutcome:
    kind: AcceptanceKind
    reason: str = ""

    @property
    def accepted(self) -> bool:
        return self.kind == AcceptanceKind.SUCCESS

    @property
    def is_conflict(self) -> bool:
        return self.kind == AcceptanceKind.CONFLICT


def operation_outcome(result: Mapping | None) -> AcceptanceOutcome:
    result = result or {}
    status = result.get("status")
    if status == OperationStatus.ALREADY_PROCESSED:
        status = result.get("previous_status")
    if status == OperationStatus.CONFLICT:
        kind = AcceptanceKind.CONFLICT
    elif status in {OperationStatus.CREATED, OperationStatus.UPDATED, OperationStatus.ACCEPTED, OperationStatus.NO_CHANGE}:
        kind = AcceptanceKind.SUCCESS
    elif status in {OperationStatus.PENDING_CONFIRMATION, "pending"}:
        kind = AcceptanceKind.PENDING
    else:
        kind = AcceptanceKind.REJECTED
    return AcceptanceOutcome(kind, str(result.get("reason") or ""))


def normalize_operation_outcome(result: Mapping) -> dict:
    """Legacy conflicts were stored with human_rejected; never migrate records."""
    normalized = dict(result)
    if operation_outcome(result).is_conflict:
        normalized["handling_mode"] = HandlingMode.NONE
    return normalized
