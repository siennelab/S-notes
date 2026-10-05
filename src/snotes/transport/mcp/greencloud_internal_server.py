"""Unified internal stdio MCP assembled from the formal Notes and Reminder tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mcp import types
from mcp.server import Server
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from src.snotes.application.remote_mcp_reminder import RemoteMcpReminderAdapter
from src.snotes.transport.mcp.cloud_read_server import (
    call_cloud_read_tool,
    cloud_read_tools,
)
from src.snotes.transport.mcp.tools import register_tools
from src.snotes.transport.reminder_http import ACTOR_COMMANDS
from src.snotes.transport.cloud_outbox_http import IDENTITY_FIELDS

if TYPE_CHECKING:
    from src.snotes.application.cloud_proposal_service import CloudProposalService
    from src.snotes.application.cloud_read_service import CloudReadService


class ReminderToolRegistry:
    """Project the existing local MCP registry onto the Cloud Reminder adapter."""

    def __init__(self, adapter: RemoteMcpReminderAdapter) -> None:
        registry = FastMCP(
            name="S-notes GreenCloud Reminder registry",
            log_level="ERROR",
        )
        register_tools(
            registry,
            adapter,  # type: ignore[arg-type]
            read_only=False,
            allowed_tools=ACTOR_COMMANDS,
        )
        self._manager = registry._tool_manager
        registered_names = frozenset(tool.name for tool in self._manager.list_tools())
        if registered_names != ACTOR_COMMANDS:
            raise RuntimeError("reminder_tool_registry_mismatch")

    def tools(self) -> list[types.Tool]:
        return [
            types.Tool(
                name=tool.name,
                title=tool.title,
                description=tool.description,
                inputSchema=tool.parameters,
                outputSchema=tool.fn_metadata.output_schema,
                annotations=tool.annotations,
                icons=tool.icons,
                meta=tool.meta,
            )
            for tool in self._manager.list_tools()
        ]

    async def call(self, name: str, arguments: dict | None) -> dict:
        if name not in ACTOR_COMMANDS:
            return {"status": "failed", "reason": "unknown_tool"}
        tool = self._manager.get_tool(name)
        if tool is None:
            return {"status": "failed", "reason": "reminder_tool_unavailable"}
        if arguments is not None and not isinstance(arguments, dict):
            return {"status": "failed", "reason": "invalid_request"}
        values = arguments or {}
        if IDENTITY_FIELDS.intersection(values):
            return {"status": "rejected", "reason": "identity_in_body_forbidden"}
        if set(values) - set(tool.parameters.get("properties", {})):
            return {"status": "failed", "reason": "invalid_request"}
        try:
            result: Any = await tool.run(arguments or {})
        except Exception:
            return {"status": "failed", "reason": "invalid_request"}
        if isinstance(result, BaseModel):
            return result.model_dump(mode="json")
        if isinstance(result, dict):
            return result
        return {"status": "failed", "reason": "reminder_response_invalid"}


def build_greencloud_internal_server(
    read_service: CloudReadService,
    proposal_service: CloudProposalService,
    reminder_adapter: RemoteMcpReminderAdapter,
) -> Server:
    """Combine existing actor-fixed Notes and Reminder surfaces without new policy."""
    reminder_registry = ReminderToolRegistry(reminder_adapter)
    note_tools = cloud_read_tools(proposal_enabled=True)
    note_tool_names = frozenset(tool.name for tool in note_tools)
    note_schemas = {tool.name: tool.inputSchema for tool in note_tools}
    duplicate_names = note_tool_names & ACTOR_COMMANDS
    if duplicate_names:
        raise RuntimeError("internal_tool_name_collision")

    server = Server(
        "snotes-gale-internal",
        instructions=(
            "Use the S-notes Notes and Reminder tools exposed for the fixed Gale "
            "operating-system identity. Actor identity cannot be supplied or changed "
            "by the caller. Notes writes are proposals for Mac review."
        ),
    )

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [*note_tools, *reminder_registry.tools()]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict | None) -> dict:
        if name in note_tool_names:
            if arguments is not None and not isinstance(arguments, dict):
                return {"status": "rejected", "reason": "invalid_request"}
            values = arguments or {}
            if IDENTITY_FIELDS.intersection(values):
                return {"status": "rejected", "reason": "identity_in_body_forbidden"}
            if set(values) - set(note_schemas[name].get("properties", {})):
                return {"status": "rejected", "reason": "invalid_request"}
            return call_cloud_read_tool(
                read_service,
                name,
                arguments,
                proposal_service=proposal_service,
            )
        return await reminder_registry.call(name, arguments)

    return server
