"""Frozen-R1 MCP schemas projected onto the R2 application service."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from src.snotes.application.local_mcp_service import LocalMcpService
from src.snotes.transport.mcp.schemas import (
    AiTargetList,
    CategoryText,
    ContentText,
    GetNoteResponse,
    ListAiMetadataOptionsResponse,
    ListDirectoriesResponse,
    ListTagsResponse,
    MaxChars,
    OffsetValue,
    OperationStatusResponse,
    PageNumber,
    PageSize,
    PositiveId,
    QueryText,
    RequestId,
    SearchNotesResponse,
    TagList,
    TitleText,
    VersionText,
    VersionToken,
    WriteReceipt,
)


LOGGER = logging.getLogger("snotes_r2_mcp")
SubmissionDirectoryText = Annotated[str, Field(max_length=200)]
SubmissionMetadataText = Annotated[str, Field(max_length=200)]
SubmissionTagList = Annotated[list[str], Field(max_length=32)]
SubmissionAiTargetList = Annotated[list[str], Field(max_length=32)]
READ_TOOL_NAMES = frozenset({"search_notes", "get_note"})
WRITE_TOOL_NAMES = frozenset(
    {
        "list_directories",
        "list_tags",
        "list_ai_metadata_options",
        "create_note",
        "prepend_note",
        "append_note",
        "update_note_metadata",
        "get_operation_status",
    }
)
PROFILE_TOOL_NAMES = frozenset({"create_note_submission"})
ALL_TOOL_NAMES = READ_TOOL_NAMES | WRITE_TOOL_NAMES | PROFILE_TOOL_NAMES
WRITE_RULES = (
    "This tool may modify local data or create a pending human-confirmation proposal. "
    "request_id must remain stable for idempotency; retry the same operation with the same "
    "request_id and never invent a new request_id merely because no natural-language reply "
    "arrived. Deletion and full-body replacement are unsupported. The actor identity is fixed "
    "by the server and cannot be supplied or changed by the caller."
)


class LocalMcpToolAdapter:
    def __init__(self, service: LocalMcpService) -> None:
        self.service = service

    def search_notes(
        self,
        query: QueryText,
        page: PageNumber = 1,
        page_size: PageSize = 10,
        directory_id: PositiveId | None = None,
        tag_id: PositiveId | None = None,
        tags: TagList | None = None,
    ) -> SearchNotesResponse:
        try:
            return SearchNotesResponse.model_validate(
                self.service.search_notes(
                    query,
                    page=page,
                    page_size=page_size,
                    directory_id=directory_id,
                    tag_id=tag_id,
                    tags=tags,
                )
            )
        except Exception as error:
            _log_safe_failure("search_notes", error)
            return SearchNotesResponse(
                status="failed",
                query=query,
                page=page,
                page_size=page_size,
                total_visible_matches=0,
                has_more=False,
                results=[],
                reason="本地数据库暂时不可用；没有返回笔记数据。",
            )

    def get_note(
        self,
        note_id: PositiveId,
        offset: OffsetValue = 0,
        max_chars: MaxChars = 4_000,
    ) -> GetNoteResponse:
        try:
            return GetNoteResponse.model_validate(
                self.service.get_note(
                    note_id, offset=offset, max_chars=max_chars
                )
            )
        except Exception as error:
            _log_safe_failure("get_note", error)
            return GetNoteResponse(
                status="failed",
                note_id=note_id,
                reason="本地数据库暂时不可用；没有返回笔记数据。",
            )

    def list_directories(self) -> ListDirectoriesResponse:
        try:
            return ListDirectoriesResponse.model_validate(
                self.service.list_directories()
            )
        except Exception as error:
            _log_safe_failure("list_directories", error)
            return ListDirectoriesResponse(
                status="failed",
                directories=[],
                reason="本地数据库暂时不可用。",
            )

    def list_tags(self) -> ListTagsResponse:
        try:
            return ListTagsResponse.model_validate(self.service.list_tags())
        except Exception as error:
            _log_safe_failure("list_tags", error)
            return ListTagsResponse(
                status="failed",
                tags=[],
                reason="本地数据库暂时不可用。",
            )

    def list_ai_metadata_options(self) -> ListAiMetadataOptionsResponse:
        try:
            return ListAiMetadataOptionsResponse.model_validate(
                self.service.list_ai_metadata_options()
            )
        except Exception as error:
            _log_safe_failure("list_ai_metadata_options", error)
            return ListAiMetadataOptionsResponse(
                status="failed",
                ai_visibility_options=[],
                ai_targets=[],
                reason="本地数据库暂时不可用。",
            )

    def create_note(
        self,
        request_id: RequestId,
        title: TitleText,
        content: ContentText,
        directory_id: PositiveId | None = None,
        main_category: CategoryText | None = None,
        sub_category: CategoryText | None = None,
        tags: TagList | None = None,
        ai_visibility: str | None = None,
        ai_targets: AiTargetList | None = None,
    ) -> WriteReceipt:
        return self._write(
            "create_note",
            request_id,
            lambda: self.service.create_note(
                request_id,
                title,
                content,
                directory_id=directory_id,
                main_category=main_category,
                sub_category=sub_category,
                tags=tags,
                ai_visibility=ai_visibility,
                ai_targets=ai_targets,
            ),
        )

    def create_note_submission(
        self,
        title: TitleText,
        content: ContentText,
        suggested_directory: SubmissionDirectoryText = "",
        suggested_tags: SubmissionTagList | None = None,
        ai_visibility: str | None = None,
        ai_targets: SubmissionAiTargetList | None = None,
        main_category: SubmissionMetadataText | None = None,
        sub_category: SubmissionMetadataText | None = None,
        tags: SubmissionTagList | None = None,
        idempotency_key: RequestId | None = None,
    ) -> WriteReceipt:
        request_id = idempotency_key or f"gale-tunnel-{uuid.uuid4().hex}"
        selected_main, selected_sub = _submission_location(
            suggested_directory,
            main_category,
            sub_category,
        )
        selected_tags = _optional_list(tags if tags is not None else suggested_tags)
        return self._write(
            "create_note_submission",
            request_id,
            lambda: self.service.create_note(
                request_id,
                title,
                content,
                main_category=selected_main,
                sub_category=selected_sub,
                tags=selected_tags,
                ai_visibility=_optional_text(ai_visibility),
                ai_targets=_optional_list(ai_targets),
            ),
        )

    def prepend_note(
        self,
        request_id: RequestId,
        note_id: PositiveId,
        content: ContentText,
        expected_version: VersionToken | None = None,
        expected_updated_at: VersionText | None = None,
    ) -> WriteReceipt:
        return self._write(
            "prepend_note",
            request_id,
            lambda: self.service.prepend_note(
                request_id=request_id,
                note_id=note_id,
                content=content,
                expected_version=expected_version,
                expected_updated_at=expected_updated_at,
            ),
            note_id,
        )

    def append_note(
        self,
        request_id: RequestId,
        note_id: PositiveId,
        content: ContentText,
        expected_version: VersionToken | None = None,
        expected_updated_at: VersionText | None = None,
    ) -> WriteReceipt:
        return self._write(
            "append_note",
            request_id,
            lambda: self.service.append_note(
                request_id=request_id,
                note_id=note_id,
                content=content,
                expected_version=expected_version,
                expected_updated_at=expected_updated_at,
            ),
            note_id,
        )

    def update_note_metadata(
        self,
        request_id: RequestId,
        note_id: PositiveId,
        expected_version: VersionToken | None = None,
        expected_updated_at: VersionText | None = None,
        title: TitleText | None = None,
        directory_id: PositiveId | None = None,
        add_tags: TagList | None = None,
        remove_tags: TagList | None = None,
    ) -> WriteReceipt:
        return self._write(
            "update_note_metadata",
            request_id,
            lambda: self.service.update_note_metadata(
                request_id=request_id,
                note_id=note_id,
                expected_version=expected_version,
                expected_updated_at=expected_updated_at,
                title=title,
                directory_id=directory_id,
                add_tags=add_tags,
                remove_tags=remove_tags,
            ),
            note_id,
        )

    def get_operation_status(
        self, request_id: RequestId
    ) -> OperationStatusResponse:
        try:
            return _operation_response(
                self.service.get_operation_status(request_id)
            )
        except Exception as error:
            _log_safe_failure("get_operation_status", error)
            return OperationStatusResponse(
                status="failed",
                request_id=request_id,
                failure_or_conflict_reason="本地数据库暂时不可用。",
            )

    @staticmethod
    def _write(
        tool_name: str,
        request_id: str,
        call,
        note_id: int | None = None,
    ) -> WriteReceipt:
        try:
            return _write_response(call())
        except Exception as error:
            _log_safe_failure(tool_name, error)
            return WriteReceipt(
                status="failed",
                request_id=request_id,
                note_id=note_id,
                processing_mode="none",
                changed_formal_note=False,
                pending_confirmation=False,
                failure_or_conflict_reason="本地数据库暂时不可用；操作没有执行。",
            )


def register_tools(
    mcp: FastMCP,
    service: LocalMcpService,
    *,
    read_only: bool,
    allowed_tools: frozenset[str] | None = None,
) -> None:
    if allowed_tools is not None and not allowed_tools.issubset(ALL_TOOL_NAMES):
        raise ValueError("unknown_tool_allowlist")
    enabled_tools = READ_TOOL_NAMES if read_only else READ_TOOL_NAMES | WRITE_TOOL_NAMES
    if allowed_tools is not None:
        enabled_tools = ALL_TOOL_NAMES & allowed_tools
    adapter = LocalMcpToolAdapter(service)

    def add_enabled_tool(function, *, name, description, annotations):
        if name in enabled_tools:
            mcp.add_tool(
                function,
                name=name,
                description=description,
                annotations=annotations,
            )
    read_annotations = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    write_annotations = ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    add_enabled_tool(
        adapter.search_notes,
        name="search_notes",
        description=(
            "Search only notes visible to the server's fixed actor. Redaction happens before "
            "matching, snippets, filters, and counts; returns one bounded snippet per note."
        ),
        annotations=read_annotations,
    )
    add_enabled_tool(
        adapter.get_note,
        name="get_note",
        description=(
            "Read a bounded range of one visible note's safe text. Hidden, deleted, and "
            "unauthorized IDs all return not_found; redacted notes return only redacted text. "
            "Its opaque version is the preferred round-trip credential for a later modification."
        ),
        annotations=read_annotations,
    )
    if read_only:
        _enforce_strict_input_schemas(mcp)
        return
    registrations = (
        (
            adapter.list_directories,
            "list_directories",
            "List only directories represented by notes visible to the fixed actor. Returned "
            "directory IDs are existing writable choices; hidden-only directories are absent.",
            read_annotations,
        ),
        (
            adapter.list_tags,
            "list_tags",
            "List only tags present on notes visible to the fixed actor. Hidden-only and "
            "unauthorized-note tags and counts are never returned.",
            read_annotations,
        ),
        (
            adapter.list_ai_metadata_options,
            "list_ai_metadata_options",
            "List current AI visibility options and selectable AI target stable IDs.",
            read_annotations,
        ),
        (
            adapter.create_note_submission,
            "create_note_submission",
            "Create one Gale note submission through the fixed actor. The request is "
            "stored as an existing AI Center pending proposal for S to review; it never "
            "directly creates a formal note under the Gale Tunnel profile. "
            "Use idempotency_key as the stable retry key when available.",
            write_annotations,
        ),
        (
            adapter.create_note,
            "create_note",
            "Request a plain-text note with optional AI visibility, AI targets, category, "
            "subcategory, and tags metadata. "
            "A restricted server profile may place the request in AI Center for human "
            "confirmation before the formal note is created. "
            + WRITE_RULES,
            write_annotations,
        ),
        (
            adapter.prepend_note,
            "prepend_note",
            "Prepend plain text without replacing or deleting existing body content. Supply the "
            "version returned by get_note (preferred), or its legacy updated_at credential. "
            + WRITE_RULES,
            write_annotations,
        ),
        (
            adapter.append_note,
            "append_note",
            "Append plain text without replacing or deleting existing body content. Supply the "
            "version returned by get_note (preferred), or its legacy updated_at credential. "
            + WRITE_RULES,
            write_annotations,
        ),
        (
            adapter.update_note_metadata,
            "update_note_metadata",
            "Change only title, an existing visible directory, or ordinary tag additions/removals. "
            "Supply the version returned by get_note (preferred), or its legacy updated_at credential. "
            + WRITE_RULES,
            write_annotations,
        ),
        (
            adapter.get_operation_status,
            "get_operation_status",
            "Read the fixed actor's prior write receipt by request_id without returning note body, "
            "history text, permission configuration, or another actor's operation.",
            read_annotations,
        ),
    )
    for function, name, description, annotations in registrations:
        add_enabled_tool(
            function,
            name=name,
            description=description,
            annotations=annotations,
        )
    _enforce_strict_input_schemas(mcp)


def _enforce_strict_input_schemas(mcp: FastMCP) -> None:
    for tool in mcp._tool_manager.list_tools():
        argument_model = tool.fn_metadata.arg_model
        argument_model.model_config["extra"] = "forbid"
        argument_model.model_rebuild(force=True)
        tool.parameters = argument_model.model_json_schema(by_alias=True)
        if tool.name in {"prepend_note", "append_note", "update_note_metadata"}:
            tool.parameters["anyOf"] = [
                {"required": ["expected_version"]},
                {"required": ["expected_updated_at"]},
            ]


def _write_response(result: dict) -> WriteReceipt:
    return WriteReceipt(
        status=result["status"],
        request_id=result["request_id"],
        note_id=result.get("note_id"),
        title=result.get("title", ""),
        processing_mode=result.get("handling_mode", "none"),
        changed_formal_note=bool(result.get("modified_formal_note")),
        pending_confirmation=bool(result.get("entered_confirmation")),
        failure_or_conflict_reason=result.get("reason", ""),
        proposal_id=result.get("proposal_id"),
        previous_status=result.get("previous_status"),
    )


def _operation_response(result: dict) -> OperationStatusResponse:
    return OperationStatusResponse(
        status=result["status"],
        request_id=result["request_id"],
        operation_type=result.get("operation_type", ""),
        note_id=result.get("note_id"),
        title=result.get("title", ""),
        processing_mode=result.get("handling_mode", ""),
        changed_formal_note=bool(result.get("modified_formal_note")),
        pending_confirmation=bool(result.get("entered_confirmation")),
        failure_or_conflict_reason=result.get("reason", ""),
        created_at=result.get("created_at", ""),
        updated_at=result.get("updated_at", ""),
        proposal_id=result.get("proposal_id"),
        previous_status=result.get("previous_status"),
    )


def _log_safe_failure(tool_name: str, error: Exception) -> None:
    LOGGER.error("tool=%s failed kind=%s", tool_name, type(error).__name__)


def _submission_location(
    suggested_directory: str,
    main_category: str | None,
    sub_category: str | None,
) -> tuple[str | None, str | None]:
    clean_main = _optional_text(main_category)
    clean_sub = _optional_text(sub_category)
    if clean_main is not None or clean_sub is not None:
        return clean_main, clean_sub
    parts = [part.strip() for part in suggested_directory.split("/") if part.strip()]
    if not parts:
        return None, None
    return parts[0], parts[1] if len(parts) > 1 else None


def _optional_text(value: object) -> str | None:
    clean = str(value or "").strip()
    return clean or None


def _optional_list(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    return cleaned or None
