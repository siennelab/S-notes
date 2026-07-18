from __future__ import annotations

import argparse
import json
import sys

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.server import Settings

from src.mcp_server.runtime import (
    RuntimeConfigurationError,
    doctor_report,
    load_runtime_config,
    runtime_health,
)
from src.mcp_server.tools import register_tools


def build_server(config) -> FastMCP:
    # FastMCP's default Settings model can consult a cwd .env file. S-notes MCP
    # intentionally accepts only its documented environment variables/config file.
    Settings.model_config["env_file"] = None
    mcp = FastMCP(
        name="S-notes Local MCP",
        instructions=(
            "Use only the nine registered S-notes tools. The local server fixes the actor "
            "identity. Hidden and unauthorized notes behave as missing. Writes are idempotent "
            "by request_id and may require human confirmation."
        ),
        log_level="ERROR",
        warn_on_duplicate_tools=True,
        warn_on_duplicate_resources=True,
        warn_on_duplicate_prompts=True,
    )
    register_tools(mcp, config)
    return mcp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="snotes-mcp")
    parser.add_argument("command", choices=("serve", "doctor"), nargs="?", default="serve")
    args = parser.parse_args(argv)
    try:
        config = load_runtime_config()
        if args.command == "doctor":
            report = doctor_report(config)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["status"] == "ok" else 2

        health = runtime_health(config)
        if health["status"] != "ok":
            _startup_error(health["reason"] or "runtime_health_failed")
            return 2
        server = build_server(config)
        server.run(transport="stdio")
        return 0
    except RuntimeConfigurationError as error:
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
