"""Minimal stdio MCP surface for one fixed GreenCloud actor mirror."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from src.snotes.application.cloud_read_service import (
    DEFAULT_SEARCH_LIMIT,
    MAX_QUERY_LENGTH,
    MAX_SEARCH_LIMIT,
    CloudReadService,
)

if TYPE_CHECKING:
    from src.snotes.application.cloud_proposal_service import CloudProposalService


READ_ANNOTATIONS = types.ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
WRITE_ANNOTATIONS = types.ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def build_cloud_read_server(
    service: CloudReadService,
    proposal_service: CloudProposalService | None = None,
) -> Server:
    server = Server(
        "snotes-cloud-read",
        instructions=(
            "Read only the actor mirror fixed by the operating-system identity. "
            "Unavailable, unauthorized, hidden, and redacted Markdown all behave as not_found."
            " Metadata discovery lists current S-notes candidates, not note permissions."
            " Notes writes queue proposals for later Mac review; Mac is the sole Notes authority."
        ),
    )

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return cloud_read_tools(proposal_enabled=proposal_service is not None)

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> dict:
        return call_cloud_read_tool(
            service, name, arguments, proposal_service=proposal_service
        )

    return server


def cloud_read_tools(*, proposal_enabled: bool = False) -> list[types.Tool]:
    note_id_schema = {
        "type": "integer",
        "minimum": 1,
        "maximum": 9_223_372_036_854_775_807,
    }
    tools = [
        types.Tool(
            name="search_notes",
            title="Search actor-safe notes",
            description="Search safe fields in the current OS actor's mirror.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": MAX_QUERY_LENGTH,
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_SEARCH_LIMIT,
                        "default": DEFAULT_SEARCH_LIMIT,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            annotations=READ_ANNOTATIONS,
        ),
        types.Tool(
            name="get_note",
            title="Read actor-safe plain text",
            description="Return safe plain text from the current OS actor's mirror.",
            inputSchema={
                "type": "object",
                "properties": {"note_id": note_id_schema},
                "required": ["note_id"],
                "additionalProperties": False,
            },
            annotations=READ_ANNOTATIONS,
        ),
        types.Tool(
            name="get_note_markdown",
            title="Read authorized Markdown",
            description=(
                "Return stored Markdown only when the actor mirror contains a non-null "
                "safe_markdown value."
            ),
            inputSchema={
                "type": "object",
                "properties": {"note_id": note_id_schema},
                "required": ["note_id"],
                "additionalProperties": False,
            },
            annotations=READ_ANNOTATIONS,
        ),
    ]
    for name, title, description in (
        (
            "list_directories",
            "List S-notes directory catalog",
            "List current formal S-notes categories from the published catalog. "
            "Categories are candidates; note permissions still apply.",
        ),
        (
            "list_tags",
            "List S-notes tag catalog",
            "List current formal S-notes tags from the published catalog.",
        ),
        (
            "list_ai_metadata_options",
            "List AI metadata options",
            "List current AI visibility options and selectable actor stable IDs "
            "from the published catalog. This tool does not change AI permissions.",
        ),
    ):
        tools.append(types.Tool(
            name=name,
            title=title,
            description=description,
            inputSchema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            annotations=READ_ANNOTATIONS,
        ))
    if proposal_enabled:
        tools.extend(_cloud_proposal_tools(note_id_schema))
    return tools


def call_cloud_read_tool(
    service: CloudReadService,
    name: str,
    arguments: dict | None,
    *,
    proposal_service: CloudProposalService | None = None,
) -> dict:
    values = arguments or {}
    catalog_tools = {"list_directories", "list_tags", "list_ai_metadata_options"}
    if name in catalog_tools:
        if (arguments is not None and not isinstance(arguments, dict)) or values:
            return {"status": "failed", "reason": "invalid_request"}
        try:
            return getattr(service, name)()
        except Exception:
            response = {"status": "failed", "reason": "catalog_read_unavailable"}
            if name == "list_ai_metadata_options":
                response.update(ai_visibility_options=[], ai_targets=[])
            else:
                response["directories" if name == "list_directories" else "tags"] = []
            return response
    proposal_names = {
        "create_note",
        "prepend_note",
        "append_note",
        "update_note_metadata",
        "edit_note",
        "get_operation_status",
    }
    blocked_names = {"delete_note", "delete"}
    if name in blocked_names:
        request_id = values.get("request_id", "") if isinstance(values, dict) else ""
        if proposal_service is not None:
            return proposal_service.reject_operation(name, request_id)
        return {"status": "failed", "reason": "mirror_read_unavailable"}
    if name in proposal_names:
        if proposal_service is None:
            return {"status": "failed", "reason": "proposal_service_unavailable"}
        if not isinstance(values, dict):
            return {"status": "rejected", "reason": "invalid_request"}
        if {
            "actor", "source_actor", "actor_id", "actor_target_id", "identity", "principal", "role"
        }.intersection(values):
            return {
                "status": "rejected",
                "request_id": values.get("request_id", ""),
                "reason": "identity_in_body_forbidden",
            }
        allowed_fields = {
            "create_note": {"request_id", "title", "content", "directory_id", "main_category", "sub_category", "tags"},
            "prepend_note": {"request_id", "note_id", "content", "expected_version"},
            "append_note": {"request_id", "note_id", "content", "expected_version"},
            "update_note_metadata": {
                "request_id", "note_id", "expected_version", "title",
                "directory_id", "add_tags", "remove_tags",
            },
            "edit_note": {"request_id", "note_id", "expected_version", "edits"},
            "get_operation_status": {"request_id"},
        }
        if set(values) - allowed_fields[name]:
            return {"status": "rejected", "request_id": values.get("request_id", ""), "reason": "invalid_request"}
        try:
            if name == "create_note":
                return proposal_service.create_note(
                    request_id=values.get("request_id", ""),
                    title=values.get("title", ""),
                    content=values.get("content", ""),
                    directory_id=values.get("directory_id"),
                    main_category=values.get("main_category"),
                    sub_category=values.get("sub_category"),
                    tags=values.get("tags"),
                )
            if name in {"prepend_note", "append_note"}:
                method = (
                    proposal_service.prepend_note
                    if name == "prepend_note"
                    else proposal_service.append_note
                )
                return method(
                    request_id=values.get("request_id", ""),
                    note_id=values.get("note_id", 0),
                    content=values.get("content", ""),
                    expected_version=values.get("expected_version", ""),
                )
            if name == "update_note_metadata":
                metadata = {
                    field: values[field]
                    for field in ("title", "directory_id", "add_tags", "remove_tags")
                    if field in values
                }
                return proposal_service.update_note_metadata(
                    request_id=values.get("request_id", ""),
                    note_id=values.get("note_id", 0),
                    expected_version=values.get("expected_version", ""),
                    **metadata,
                )
            if name == "edit_note":
                return proposal_service.edit_note(
                    request_id=values.get("request_id", ""),
                    note_id=values.get("note_id", 0),
                    expected_version=values.get("expected_version", ""),
                    edits=values.get("edits", []),
                )
            return proposal_service.get_operation_status(values.get("request_id", ""))
        except (TypeError, ValueError):
            return {
                "status": "rejected",
                "request_id": values.get("request_id", ""),
                "reason": "invalid_request",
            }
        except RuntimeError as error:
            return {
                "status": "failed",
                "request_id": values.get("request_id", ""),
                "reason": str(error) or "proposal_service_unavailable",
            }
    try:
        if name == "search_notes":
            return service.search_notes(
                values.get("query", ""),
                limit=values.get("limit", DEFAULT_SEARCH_LIMIT),
            )
        if name == "get_note":
            return service.get_note(values.get("note_id", 0))
        if name == "get_note_markdown":
            return service.get_note_markdown(values.get("note_id", 0))
        raise ValueError("unknown_tool")
    except Exception:
        note_id = values.get("note_id", 0)
        response = {"status": "failed", "reason": "mirror_read_unavailable"}
        if name in {"get_note", "get_note_markdown"}:
            response["note_id"] = note_id
        return response


async def serve_cloud_read(
    service: CloudReadService,
    proposal_service: CloudProposalService | None = None,
) -> None:
    server = build_cloud_read_server(service, proposal_service)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def startup_error(reason: str) -> None:
    sys.stderr.write(
        json.dumps({"status": "failed", "reason": reason}, ensure_ascii=False) + "\n"
    )
    sys.stderr.flush()


def _cloud_proposal_tools(note_id_schema: dict) -> list[types.Tool]:
    request_id = {
        "type": "string",
        "minLength": 1,
        "maxLength": 128,
        "pattern": r"^\S+$",
    }
    version = {
        "type": "string",
        "minLength": 74,
        "maxLength": 74,
        "pattern": r"^snotes-v1-[0-9a-f]{64}$",
    }
    content = {"type": "string", "minLength": 1, "maxLength": 50_000}
    nullable_note_id = {**note_id_schema, "type": ["integer", "null"]}
    return [
        types.Tool(
            name="create_note",
            title="Propose a new note",
            description=(
                "Queue a Markdown create proposal for later Mac review. The Cloud never "
                "creates a formal note. AI visibility and actor targets cannot be supplied."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "request_id": request_id,
                    "title": {"type": "string", "minLength": 1, "maxLength": 500},
                    "content": content,
                    "directory_id": note_id_schema,
                    "main_category": {"type": "string", "minLength": 1, "maxLength": 120},
                    "sub_category": {"type": "string", "minLength": 1, "maxLength": 120},
                    "tags": {
                        "type": "array",
                        "maxItems": 32,
                        "items": {"type": "string", "minLength": 1, "maxLength": 80},
                    },
                },
                "required": ["request_id", "title", "content"],
                "additionalProperties": False,
            },
            annotations=WRITE_ANNOTATIONS,
        ),
        types.Tool(
            name="prepend_note",
            title="Propose prepending to a note",
            description=(
                "Queue a prepend proposal for a full or redacted note visible in this "
                "actor's mirror. Use its current version as expected_version."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "request_id": request_id,
                    "note_id": note_id_schema,
                    "content": content,
                    "expected_version": version,
                },
                "required": ["request_id", "note_id", "content", "expected_version"],
                "additionalProperties": False,
            },
            annotations=WRITE_ANNOTATIONS,
        ),
        types.Tool(
            name="append_note",
            title="Propose appending to a note",
            description=(
                "Queue an append proposal for a full or redacted note visible in this "
                "actor's mirror. Use its current version as expected_version."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "request_id": request_id,
                    "note_id": note_id_schema,
                    "content": content,
                    "expected_version": version,
                },
                "required": ["request_id", "note_id", "content", "expected_version"],
                "additionalProperties": False,
            },
            annotations=WRITE_ANNOTATIONS,
        ),
        types.Tool(
            name="update_note_metadata",
            title="Propose updating note metadata",
            description=(
                "Queue a metadata proposal for a note visible in this actor's mirror. "
                "Only title, directory_id, add_tags, and remove_tags are accepted; "
                "AI visibility and actor targets cannot be supplied."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "request_id": request_id,
                    "note_id": note_id_schema,
                    "expected_version": version,
                    "title": {
                        "type": ["string", "null"],
                        "minLength": 1,
                        "maxLength": 500,
                    },
                    "directory_id": nullable_note_id,
                    "add_tags": {
                        "type": ["array", "null"],
                        "minItems": 1,
                        "maxItems": 32,
                        "items": {"type": "string", "minLength": 1, "maxLength": 80},
                    },
                    "remove_tags": {
                        "type": ["array", "null"],
                        "minItems": 1,
                        "maxItems": 32,
                        "items": {"type": "string", "minLength": 1, "maxLength": 80},
                    },
                },
                "required": ["request_id", "note_id", "expected_version"],
                "additionalProperties": False,
            },
            annotations=WRITE_ANNOTATIONS,
        ),
        types.Tool(
            name="edit_note",
            title="Propose exact Markdown edits",
            description=(
                "Queue an all-or-nothing batch of exact old_markdown to new_markdown edits. "
                "The note must be full-authorized and every old fragment must match once."
                " Preserve snotes-markdown-v1 and its :size directives (15 or 20); "
                "do not remove unknown S-notes directives as formatting cleanup."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "request_id": request_id,
                    "note_id": note_id_schema,
                    "expected_version": version,
                    "edits": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 100,
                        "items": {
                            "type": "object",
                            "properties": {
                                "old_markdown": {
                                    "type": "string", "minLength": 1, "maxLength": 50_000
                                },
                                "new_markdown": {"type": "string", "maxLength": 50_000},
                            },
                            "required": ["old_markdown", "new_markdown"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["request_id", "note_id", "expected_version", "edits"],
                "additionalProperties": False,
            },
            annotations=WRITE_ANNOTATIONS,
        ),
        types.Tool(
            name="get_operation_status",
            title="Read proposal status",
            description="Read only this fixed actor's proposal status by request_id.",
            inputSchema={
                "type": "object",
                "properties": {"request_id": request_id},
                "required": ["request_id"],
                "additionalProperties": False,
            },
            annotations=READ_ANNOTATIONS,
        ),
    ]
