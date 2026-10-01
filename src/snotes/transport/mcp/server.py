"""Independent R2 local stdio MCP entrypoint."""

from __future__ import annotations

import argparse
import json
import sys

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Settings

from src.snotes.bootstrap import build_local_mcp_service
from src.snotes.reminder_authority import (
    ReminderAuthority,
    ReminderAuthorityError,
    resolve_reminder_authority,
)
from src.snotes.transport.mcp.runtime import (
    RuntimeConfigurationError,
    RuntimeConfig,
    load_runtime_config,
)
from src.snotes.transport.mcp.tools import register_tools


def build_server(
    config: RuntimeConfig,
    *,
    reminder_authority: ReminderAuthority | None = None,
) -> FastMCP:
    Settings.model_config["env_file"] = None
    service = build_local_mcp_service(
        actor_target_id=config.actor_target_id,
        resolved_data_root=config.data_root,
        reminder_authority=(
            reminder_authority
            if reminder_authority is not None
            else resolve_reminder_authority()
        ),
        create_confirmation_required=(
            config.tool_allowlist is not None
            and (
                "create_note" in config.tool_allowlist
                or "create_note_submission" in config.tool_allowlist
            )
        ),
    )
    mcp = FastMCP(
        name="S-notes Local MCP",
        instructions=(
            "Use only the registered S-notes tools. The server fixes actor identity; "
            "hidden and unauthorized notes behave as missing."
        ),
        log_level="ERROR",
        warn_on_duplicate_tools=True,
        warn_on_duplicate_resources=True,
        warn_on_duplicate_prompts=True,
    )
    register_tools(
        mcp,
        service,
        read_only=config.read_only,
        allowed_tools=config.tool_allowlist,
    )
    return mcp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="snotes-r2-mcp")
    parser.add_argument(
        "command", choices=("serve", "doctor"), nargs="?", default="serve"
    )
    args = parser.parse_args(argv)
    try:
        config = load_runtime_config()
        server = build_server(config)
        if args.command == "doctor":
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "actor_target_id": config.actor_target_id,
                        "read_only_mode": config.read_only,
                        "tool_count": len(server._tool_manager.list_tools()),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        server.run(transport="stdio")
        return 0
    except (RuntimeConfigurationError, ReminderAuthorityError) as error:
        _startup_error(str(error))
        return 2
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        _startup_error(type(error).__name__)
        return 2


def _startup_error(reason: str) -> None:
    sys.stderr.write(f"S-notes MCP startup failed: {reason}\n")
    sys.stderr.flush()


if __name__ == "__main__":
    raise SystemExit(main())
