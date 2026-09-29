"""Minimal stdio MCP surface for one fixed GreenCloud actor mirror."""

from __future__ import annotations

import json
import sys

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from src.snotes.application.cloud_read_service import (
    DEFAULT_SEARCH_LIMIT,
    MAX_QUERY_LENGTH,
    MAX_SEARCH_LIMIT,
    CloudReadService,
)


READ_ANNOTATIONS = types.ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def build_cloud_read_server(service: CloudReadService) -> Server:
    server = Server(
        "snotes-cloud-read",
        instructions=(
            "Read only the actor mirror fixed by the operating-system identity. "
            "Unavailable, unauthorized, hidden, and redacted Markdown all behave as not_found."
        ),
    )

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return cloud_read_tools()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> dict:
        return call_cloud_read_tool(service, name, arguments)

    return server


def cloud_read_tools() -> list[types.Tool]:
    note_id_schema = {
        "type": "integer",
        "minimum": 1,
        "maximum": 9_223_372_036_854_775_807,
    }
    return [
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


def call_cloud_read_tool(
    service: CloudReadService,
    name: str,
    arguments: dict | None,
) -> dict:
    values = arguments or {}
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


async def serve_cloud_read(service: CloudReadService) -> None:
    server = build_cloud_read_server(service)
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
