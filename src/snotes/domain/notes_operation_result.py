"""Canonical result contract for Notes AI operations."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class NotesOperationResult:
    """The business result persisted for one Notes AI operation."""

    status: str
    request_id: str
    note_id: int | None
    title: str
    handling_mode: str
    modified_formal_note: bool
    entered_confirmation: bool
    reason: str
    proposal_id: int | None = None

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "status": self.status,
            "request_id": self.request_id,
            "note_id": self.note_id,
            "title": self.title,
            "handling_mode": self.handling_mode,
            "modified_formal_note": self.modified_formal_note,
            "entered_confirmation": self.entered_confirmation,
            "reason": self.reason,
        }
        if self.proposal_id is not None:
            result["proposal_id"] = self.proposal_id
        return result

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> NotesOperationResult:
        proposal_id = value.get("proposal_id")
        note_id = value.get("note_id")
        return cls(
            status=str(value.get("status", "")),
            request_id=str(value.get("request_id", "")),
            note_id=int(note_id) if note_id is not None else None,
            title=str(value.get("title", "")),
            handling_mode=str(value.get("handling_mode", "")),
            modified_formal_note=bool(
                value.get("modified_formal_note", value.get("changed", False))
            ),
            entered_confirmation=bool(
                value.get(
                    "entered_confirmation",
                    value.get("pending_confirmation", False),
                )
            ),
            reason=str(value.get("reason", "")),
            proposal_id=int(proposal_id) if proposal_id is not None else None,
        )


def notes_operation_result(
    status: str,
    request_id: str,
    note_id: int | None,
    title: str,
    handling_mode: str,
    modified_formal_note: bool,
    entered_confirmation: bool,
    reason: str,
    *,
    proposal_id: int | None = None,
) -> dict[str, object]:
    """Build the canonical outbound/persistence mapping."""

    return NotesOperationResult(
        status=status,
        request_id=request_id,
        note_id=note_id,
        title=title,
        handling_mode=handling_mode,
        modified_formal_note=modified_formal_note,
        entered_confirmation=entered_confirmation,
        reason=reason,
        proposal_id=proposal_id,
    ).to_dict()


def serialize_notes_operation_result(
    result: NotesOperationResult | Mapping[str, object],
) -> str:
    """Serialize a Notes result through the sole canonical JSON encoding."""

    typed = (
        result
        if isinstance(result, NotesOperationResult)
        else NotesOperationResult.from_mapping(result)
    )
    return json.dumps(
        typed.to_dict(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def deserialize_operation_result_json(payload: str) -> dict[str, object]:
    """Read canonical/legacy Notes results and pass Reminder results through."""

    result = json.loads(payload)
    if not isinstance(result, dict):
        raise ValueError("AI operation result must be a JSON object")

    notes_shaped = any(
        key in result
        for key in ("note_id", "proposal_id", "modified_formal_note", "changed")
    )
    if notes_shaped:
        if "modified_formal_note" not in result and "changed" in result:
            result["modified_formal_note"] = bool(result["changed"])
        if (
            "entered_confirmation" not in result
            and "pending_confirmation" in result
        ):
            result["entered_confirmation"] = bool(result["pending_confirmation"])
    return result
