"""Production HTTPS entrypoint for Claude Web's Notes-only GreenCloud MCP."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
import json
import logging
import os
import sys
from typing import Any, AsyncIterator, Protocol

from fastmcp.server.auth import AuthProvider
from fastmcp.server.auth.middleware import RequireAuthMiddleware
from fastmcp.server.http import build_resource_metadata_url
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Route
import uvicorn

from src.cloud_read_mcp import ActorCloudRuntime, compose_actor_cloud_runtime
from src.snotes.transport.mcp.cloud_read_server import build_cloud_read_server


HOST = "127.0.0.1"
PORT = 8770
MCP_PATH = "/mcp"
CLAUDE_CODE_UID = 2001
CLAUDE_CODE_ACTOR = "claude-code"


class _NotesRuntime(Protocol):
    server: Any

    def close(self) -> None: ...


@dataclass(slots=True)
class ClaudeWebNotesRuntime:
    """Own the existing actor-fixed Notes services and their MCP registry."""

    notes: ActorCloudRuntime
    server: Any

    def close(self) -> None:
        self.notes.close()


class _RuntimeOwner:
    """Make lifespan and entrypoint cleanup safe to call together."""

    def __init__(self, runtime: _NotesRuntime) -> None:
        self.runtime = runtime
        self.closed = False

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.runtime.close()
        except Exception:
            logging.getLogger("snotes_claude_web_remote_mcp").error(
                "runtime_close_failed"
            )


def compose_claude_web_notes_runtime(
    effective_uid: int | None = None,
) -> ClaudeWebNotesRuntime:
    """Compose only UID 2001's Claude Notes mirror and proposal service."""
    uid = os.geteuid() if effective_uid is None else int(effective_uid)
    if uid != CLAUDE_CODE_UID:
        raise RuntimeError("claude_code_os_identity_required")
    notes = compose_actor_cloud_runtime(uid)
    try:
        if notes.binding.uid != CLAUDE_CODE_UID or notes.binding.actor != CLAUDE_CODE_ACTOR:
            raise RuntimeError("claude_code_os_identity_required")
        server = build_cloud_read_server(
            notes.read_service,
            notes.proposal_service,
        )
        return ClaudeWebNotesRuntime(notes=notes, server=server)
    except Exception:
        notes.close()
        raise


def build_claude_web_remote_app(
    runtime: _NotesRuntime,
    auth_provider: AuthProvider,
) -> Starlette:
    """Expose one fully authenticated, stateless Notes-only MCP route."""
    manager = StreamableHTTPSessionManager(
        runtime.server,
        json_response=True,
        stateless=True,
    )
    resource_url = auth_provider._get_resource_url(MCP_PATH)
    resource_metadata_url = (
        build_resource_metadata_url(resource_url) if resource_url else None
    )
    protected_app = RequireAuthMiddleware(
        manager.handle_request,
        auth_provider.required_scopes,
        resource_metadata_url,
    )
    owner = _RuntimeOwner(runtime)

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        try:
            async with manager.run():
                yield
        finally:
            owner.close()

    app = Starlette(
        routes=[
            *auth_provider.get_routes(MCP_PATH),
            Route(MCP_PATH, endpoint=protected_app, methods=["POST", "DELETE"]),
        ],
        middleware=auth_provider.get_middleware(),
        lifespan=lifespan,
    )
    app.state.claude_web_runtime = runtime
    app.state.claude_web_runtime_owner = owner
    app.state.streamable_http_manager = manager
    return app


def _configure_safe_logging() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    for logger_name in (
        "fastmcp",
        "mcp",
        "authlib",
        "httpx",
        "httpcore",
        "uvicorn.error",
    ):
        logging.getLogger(logger_name).setLevel(logging.CRITICAL)


def main() -> int:
    runtime: ClaudeWebNotesRuntime | None = None
    app: Starlette | None = None
    os.umask(0o077)
    _configure_safe_logging()
    try:
        if len(sys.argv) != 1:
            raise RuntimeError("arguments_forbidden")
        from src.snotes.transport.mcp.claude_web_remote_auth import (
            build_auth_provider,
        )

        runtime = compose_claude_web_notes_runtime()
        app = build_claude_web_remote_app(runtime, build_auth_provider())
        uvicorn.run(
            app,
            host=HOST,
            port=PORT,
            access_log=False,
            server_header=False,
            proxy_headers=False,
            log_level="warning",
        )
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        sys.stderr.write(
            json.dumps(
                {"status": "failed", "reason": "startup_failed"},
                separators=(",", ":"),
            )
            + "\n"
        )
        sys.stderr.flush()
        return 2
    finally:
        if app is not None:
            app.state.claude_web_runtime_owner.close()
        elif runtime is not None:
            try:
                runtime.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
