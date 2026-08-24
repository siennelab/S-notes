"""Frozen-R1 local MCP public result models and input constraints."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


QueryText = Annotated[str, Field(max_length=500)]
RequestId = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^\S+$")]
TitleText = Annotated[str, Field(min_length=1, max_length=500)]
ContentText = Annotated[str, Field(min_length=1, max_length=50_000)]
VersionText = Annotated[str, Field(min_length=1, max_length=80)]
VersionToken = Annotated[
    str,
    Field(min_length=74, max_length=74, pattern=r"^snotes-v1-[0-9a-f]{64}$"),
]
PositiveId = Annotated[int, Field(ge=1, le=9_223_372_036_854_775_807)]
PageNumber = Annotated[int, Field(ge=1, le=1_000_000)]
PageSize = Annotated[int, Field(ge=1, le=20)]
OffsetValue = Annotated[int, Field(ge=0, le=100_000_000)]
MaxChars = Annotated[int, Field(ge=1, le=12_000)]
TagName = Annotated[str, Field(min_length=1, max_length=80)]
TagList = Annotated[list[TagName], Field(min_length=1, max_length=32)]
CategoryText = Annotated[str, Field(min_length=1, max_length=120)]
AiTargetText = Annotated[str, Field(min_length=1, max_length=80)]
AiTargetList = Annotated[list[AiTargetText], Field(max_length=32)]
LifecycleText = Annotated[str, Field(min_length=1, max_length=40)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Relevance(StrictModel):
    score: int
    explanation: str


class SearchResult(StrictModel):
    note_id: int
    title: str
    directory: str
    tags: list[str]
    updated_at: str
    snippet: str
    matched_field: str
    relevance: Relevance


class SearchNotesResponse(StrictModel):
    status: Literal["ok", "invalid_request", "failed"]
    query: str
    page: int
    page_size: int
    total_visible_matches: int
    has_more: bool
    results: list[SearchResult]
    reason: str = ""


class ReturnedRange(StrictModel):
    start: int
    end: int


class GetNoteResponse(StrictModel):
    status: Literal["ok", "not_found", "invalid_request", "failed"]
    note_id: int | None = None
    title: str = ""
    directory: str = ""
    main_category: str = ""
    sub_category: str = ""
    tags: list[str] = Field(default_factory=list)
    ai_visibility: str = ""
    ai_targets: list[str] = Field(default_factory=list)
    updated_at: str = ""
    version: str = ""
    content: str = ""
    returned_range: ReturnedRange | None = None
    has_more: bool = False
    next_offset: int | None = None
    total_safe_chars: int = 0
    reason: str = ""


class DirectoryRecord(StrictModel):
    directory_id: int
    directory: str
    main_category: str = ""
    sub_category: str = ""
    writable: bool


class ListDirectoriesResponse(StrictModel):
    status: Literal["ok", "failed"]
    directories: list[DirectoryRecord]
    reason: str = ""


class TagRecord(StrictModel):
    tag_id: int
    name: str


class ListTagsResponse(StrictModel):
    status: Literal["ok", "failed"]
    tags: list[TagRecord]
    reason: str = ""


class AiVisibilityOption(StrictModel):
    value: str
    label: str


class AiTargetOption(StrictModel):
    target_id: str
    display_name: str


class ListAiMetadataOptionsResponse(StrictModel):
    status: Literal["ok", "failed"]
    ai_visibility_options: list[AiVisibilityOption]
    ai_targets: list[AiTargetOption]
    reason: str = ""


WriteStatus = Literal[
    "created",
    "updated",
    "pending_confirmation",
    "already_processed",
    "conflict",
    "not_found",
    "rejected",
    "no_change",
    "failed",
]


class WriteReceipt(StrictModel):
    status: WriteStatus
    request_id: str
    note_id: int | None = None
    title: str = ""
    processing_mode: str
    changed_formal_note: bool
    pending_confirmation: bool
    failure_or_conflict_reason: str = ""
    proposal_id: int | None = None
    previous_status: str | None = None


class OperationStatusResponse(StrictModel):
    status: WriteStatus | Literal["not_found"]
    request_id: str
    operation_type: str = ""
    note_id: int | None = None
    title: str = ""
    processing_mode: str = ""
    changed_formal_note: bool = False
    pending_confirmation: bool = False
    failure_or_conflict_reason: str = ""
    created_at: str = ""
    updated_at: str = ""
    proposal_id: int | None = None
    previous_status: str | None = None


class ReminderLabelRecord(StrictModel):
    label_id: int
    name: str
    is_default: bool
    sort_order: int


class ListReminderLabelsResponse(StrictModel):
    status: Literal["ok", "failed"]
    labels: list[ReminderLabelRecord]
    reason: str = ""


class ReminderLabelPayload(StrictModel):
    label_id: int
    name: str


class ReminderTableRecord(StrictModel):
    table_id: int
    title: str
    label: ReminderLabelPayload
    ai_visible: bool
    item_count: int
    matching_item_id: int | None = None
    match_count: int = 0
    updated_at: str


class ReminderOccurrencePayload(StrictModel):
    occurrence_id: int
    occurrence_key: str
    action_date: str
    deadline_date: str | None = None
    status: str
    completed_at: str | None = None


class ReminderRecord(StrictModel):
    item_id: int
    reminder_id: int
    table_id: int
    table_title: str
    title: str
    note: str = ""
    label: ReminderLabelPayload
    lifecycle: str
    status: str
    status_label: str
    action_spec: dict
    deadline_spec: dict | None = None
    schedule: str | None = None
    next_action_date: str | None = None
    next_deadline_date: str | None = None
    updated_at: str
    occurrence: ReminderOccurrencePayload | None = None


class ListReminderTablesResponse(StrictModel):
    status: Literal["ok", "failed"]
    page: int
    page_size: int
    total_visible_matches: int
    has_more: bool
    results: list[ReminderTableRecord]
    reason: str = ""


class GetReminderTableResponse(StrictModel):
    status: Literal["ok", "not_found", "failed"]
    table_id: int | None = None
    table: ReminderTableRecord | None = None
    reason: str = ""


class ListReminderItemsResponse(StrictModel):
    status: Literal["ok", "failed"]
    table_id: int | None = None
    page: int
    page_size: int
    total_visible_matches: int
    has_more: bool
    results: list[ReminderRecord]
    reason: str = ""


class GetReminderItemResponse(StrictModel):
    status: Literal["ok", "not_found", "failed"]
    item_id: int | None = None
    item: ReminderRecord | None = None
    reason: str = ""


class ReminderWriteReceipt(StrictModel):
    status: WriteStatus
    request_id: str
    table_id: int | None = None
    item_id: int | None = None
    reminder_id: int | None = None
    title: str = ""
    processing_mode: str
    changed_formal_reminder: bool
    pending_confirmation: bool
    failure_or_conflict_reason: str = ""
    previous_status: str | None = None
