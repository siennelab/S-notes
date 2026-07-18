from __future__ import annotations

import logging
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from src.mcp_server.runtime import RuntimeConfig
from src.mcp_server.schemas import (
    ContentText,
    GetNoteResponse,
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
    ReturnedRange,
    SearchNotesResponse,
    SearchResult,
    TagList,
    TitleText,
    VersionText,
    VersionToken,
    WriteReceipt,
)
from src.services import ai_access_service


LOGGER = logging.getLogger("snotes_mcp")
WRITE_RULES = (
    "This tool may modify local data or create a pending human-confirmation proposal. "
    "request_id must remain stable for idempotency; retry the same operation with the same "
    "request_id and never invent a new request_id merely because no natural-language reply "
    "arrived. Deletion and full-body replacement are unsupported. The actor identity is fixed "
    "by the server and cannot be supplied or changed by the caller."
)


class SnotesToolAdapter:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config

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
            offset = (page - 1) * page_size
            result = ai_access_service.safe_search_notes(
                query,
                self.config.actor_target_id,
                limit=page_size,
                offset=offset,
                directory_id=directory_id,
                tag_id=tag_id,
                tags=tags,
                database_path=self.config.database_path,
                redaction_rules_path=self.config.redaction_rules_path,
            )
            items = [
                SearchResult(
                    note_id=item["note_id"],
                    title=item["title"],
                    directory=item["directory"],
                    tags=item["tags"],
                    updated_at=item["updated_at"],
                    snippet=item["best_safe_snippet"],
                    matched_field=item["matched_in"],
                    relevance={
                        "score": item["relevance"]["score"],
                        "explanation": item["relevance"]["reason"],
                    },
                )
                for item in result["items"]
            ]
            total = int(result["total_count"])
            return SearchNotesResponse(
                status="ok",
                query=query,
                page=page,
                page_size=page_size,
                total_visible_matches=total,
                has_more=offset + len(items) < total,
                results=items,
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
            note = ai_access_service.safe_get_note_by_id(
                note_id,
                self.config.actor_target_id,
                database_path=self.config.database_path,
                redaction_rules_path=self.config.redaction_rules_path,
            )
            if note is None:
                return GetNoteResponse(
                    status="not_found",
                    note_id=note_id,
                    reason="未找到可访问的笔记。",
                )
            safe_content = note["body"]
            total = len(safe_content)
            start = min(offset, total)
            end = min(start + max_chars, total)
            has_more = end < total
            return GetNoteResponse(
                status="ok",
                note_id=note["note_id"],
                title=note["title"],
                directory=note["directory"],
                tags=note["tags"],
                updated_at=note["updated_at"],
                version=note["version"],
                content=safe_content[start:end],
                returned_range=ReturnedRange(start=start, end=end),
                has_more=has_more,
                next_offset=end if has_more else None,
                total_safe_chars=total,
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
            records = ai_access_service.safe_list_directory_records(
                self.config.actor_target_id,
                database_path=self.config.database_path,
                redaction_rules_path=self.config.redaction_rules_path,
            )
            return ListDirectoriesResponse(status="ok", directories=records)
        except Exception as error:
            _log_safe_failure("list_directories", error)
            return ListDirectoriesResponse(
                status="failed", directories=[], reason="本地数据库暂时不可用。"
            )

    def list_tags(self) -> ListTagsResponse:
        try:
            records = ai_access_service.safe_list_tag_records(
                self.config.actor_target_id,
                database_path=self.config.database_path,
                redaction_rules_path=self.config.redaction_rules_path,
            )
            return ListTagsResponse(status="ok", tags=records)
        except Exception as error:
            _log_safe_failure("list_tags", error)
            return ListTagsResponse(
                status="failed", tags=[], reason="本地数据库暂时不可用。"
            )

    def create_note(
        self,
        request_id: RequestId,
        title: TitleText,
        content: ContentText,
        directory_id: PositiveId | None = None,
        tags: TagList | None = None,
    ) -> WriteReceipt:
        if self.config.read_only:
            return self._read_only_receipt(request_id, "create")
        try:
            result = ai_access_service.ai_create_note(
                self.config.actor_target_id,
                request_id,
                title=title,
                body=content,
                tags=tags,
                directory_id=directory_id,
                database_path=self.config.database_path,
            )
            return _write_receipt(result)
        except Exception as error:
            return self._failed_write("create_note", request_id, error)

    def prepend_note(
        self,
        request_id: RequestId,
        note_id: PositiveId,
        content: ContentText,
        expected_version: VersionToken | None = None,
        expected_updated_at: VersionText | None = None,
    ) -> WriteReceipt:
        if self.config.read_only:
            return self._read_only_receipt(request_id, "prepend", note_id)
        try:
            result = ai_access_service.ai_prepend_note(
                self.config.actor_target_id,
                request_id,
                note_id,
                content,
                expected_updated_at=expected_updated_at,
                expected_version=expected_version,
                database_path=self.config.database_path,
            )
            return _write_receipt(result)
        except Exception as error:
            return self._failed_write("prepend_note", request_id, error, note_id)

    def append_note(
        self,
        request_id: RequestId,
        note_id: PositiveId,
        content: ContentText,
        expected_version: VersionToken | None = None,
        expected_updated_at: VersionText | None = None,
    ) -> WriteReceipt:
        if self.config.read_only:
            return self._read_only_receipt(request_id, "append", note_id)
        try:
            result = ai_access_service.ai_append_note(
                self.config.actor_target_id,
                request_id,
                note_id,
                content,
                expected_updated_at=expected_updated_at,
                expected_version=expected_version,
                database_path=self.config.database_path,
            )
            return _write_receipt(result)
        except Exception as error:
            return self._failed_write("append_note", request_id, error, note_id)

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
        if self.config.read_only:
            return self._read_only_receipt(request_id, "metadata", note_id)
        try:
            result = ai_access_service.ai_update_note_metadata(
                self.config.actor_target_id,
                request_id,
                note_id,
                expected_updated_at=expected_updated_at,
                expected_version=expected_version,
                title=title,
                directory_id=directory_id,
                add_tags=add_tags,
                remove_tags=remove_tags,
                database_path=self.config.database_path,
            )
            return _write_receipt(result)
        except Exception as error:
            return self._failed_write("update_note_metadata", request_id, error, note_id)

    def get_operation_status(self, request_id: RequestId) -> OperationStatusResponse:
        try:
            result = ai_access_service.safe_get_ai_operation_status(
                request_id,
                self.config.actor_target_id,
                database_path=self.config.database_path,
            )
            if result is None:
                return OperationStatusResponse(status="not_found", request_id=request_id)
            return OperationStatusResponse(
                status=result["status"],
                request_id=request_id,
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
        except Exception as error:
            _log_safe_failure("get_operation_status", error)
            return OperationStatusResponse(
                status="failed",
                request_id=request_id,
                failure_or_conflict_reason="本地数据库暂时不可用。",
            )

    def _read_only_receipt(
        self,
        request_id: str,
        operation_type: str,
        note_id: int | None = None,
    ) -> WriteReceipt:
        try:
            result = ai_access_service.record_ai_rejected_operation(
                self.config.actor_target_id,
                request_id,
                operation_type,
                "Server 当前以只读模式运行；正式笔记未修改。",
                note_id=note_id,
                database_path=self.config.database_path,
            )
            return _write_receipt(result)
        except Exception as error:
            return self._failed_write(operation_type, request_id, error, note_id)

    def _failed_write(
        self,
        tool_name: str,
        request_id: str,
        error: Exception,
        note_id: int | None = None,
    ) -> WriteReceipt:
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


def register_tools(mcp: FastMCP, config: RuntimeConfig) -> None:
    adapter = SnotesToolAdapter(config)
    read_annotations = ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
    write_annotations = ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )

    mcp.add_tool(
        adapter.search_notes,
        name="search_notes",
        description=(
            "Search only notes visible to the server's fixed actor. Redaction happens before "
            "matching, snippets, filters, and counts; returns one bounded snippet per note."
        ),
        annotations=read_annotations,
    )
    mcp.add_tool(
        adapter.get_note,
        name="get_note",
        description=(
            "Read a bounded range of one visible note's safe text. Hidden, deleted, and "
            "unauthorized IDs all return not_found; redacted notes return only redacted text. "
            "Its opaque version is the preferred round-trip credential for a later modification."
        ),
        annotations=read_annotations,
    )
    mcp.add_tool(
        adapter.list_directories,
        name="list_directories",
        description=(
            "List only directories represented by notes visible to the fixed actor. Returned "
            "directory IDs are existing writable choices; hidden-only directories are absent."
        ),
        annotations=read_annotations,
    )
    mcp.add_tool(
        adapter.list_tags,
        name="list_tags",
        description=(
            "List only tags present on notes visible to the fixed actor. Hidden-only and "
            "unauthorized-note tags and counts are never returned."
        ),
        annotations=read_annotations,
    )
    mcp.add_tool(
        adapter.create_note,
        name="create_note",
        description="Create a plain-text note with full visibility for only the fixed actor. " + WRITE_RULES,
        annotations=write_annotations,
    )
    mcp.add_tool(
        adapter.prepend_note,
        name="prepend_note",
        description=(
            "Prepend plain text without replacing or deleting existing body content. Supply the "
            "version returned by get_note (preferred), or its legacy updated_at credential. "
            + WRITE_RULES
        ),
        annotations=write_annotations,
    )
    mcp.add_tool(
        adapter.append_note,
        name="append_note",
        description=(
            "Append plain text without replacing or deleting existing body content. Supply the "
            "version returned by get_note (preferred), or its legacy updated_at credential. "
            + WRITE_RULES
        ),
        annotations=write_annotations,
    )
    mcp.add_tool(
        adapter.update_note_metadata,
        name="update_note_metadata",
        description=(
            "Change only title, an existing visible directory, or ordinary tag additions/removals. "
            "Supply the version returned by get_note (preferred), or its legacy updated_at credential. "
            + WRITE_RULES
        ),
        annotations=write_annotations,
    )
    mcp.add_tool(
        adapter.get_operation_status,
        name="get_operation_status",
        description=(
            "Read the fixed actor's prior write receipt by request_id without returning note body, "
            "history text, permission configuration, or another actor's operation."
        ),
        annotations=read_annotations,
    )
    _enforce_strict_input_schemas(mcp)


def _enforce_strict_input_schemas(mcp: FastMCP) -> None:
    # MCP SDK 1.28.1 defaults function argument models to ignoring unknown keys.
    # R1 explicitly forbids unknown tool fields, so the locked SDK models are rebuilt.
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


def _write_receipt(result: dict) -> WriteReceipt:
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


def _log_safe_failure(tool_name: str, error: Exception) -> None:
    LOGGER.error("tool=%s failed kind=%s", tool_name, type(error).__name__)
