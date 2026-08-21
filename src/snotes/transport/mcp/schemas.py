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
