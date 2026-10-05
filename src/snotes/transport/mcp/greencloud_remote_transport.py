"""Authenticated Streamable HTTP transport for the fixed Gale MCP server."""

from __future__ import annotations

from contextlib import asynccontextmanager
import json
import logging
from typing import Any, AsyncIterator, Protocol

from fastmcp.server.auth import AuthProvider
from fastmcp.server.auth.middleware import RequireAuthMiddleware
from fastmcp.server.http import build_resource_metadata_url
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.types import Receive, Scope, Send


LOGGER = logging.getLogger("snotes_greencloud_remote_mcp")
MCP_PATH = "/mcp"
GALE_READ_SCOPE = "gale.read"
ANONYMOUS_MCP_METHODS = frozenset(
    {
        "initialize",
        "notifications/initialized",
        "ping",
        "tools/list",
    }
)
MAX_ANONYMOUS_MCP_BODY_BYTES = 64 * 1024
OAUTH_SECURITY_SCHEMES = [
    {
        "type": "oauth2",
        "scopes": [GALE_READ_SCOPE],
    }
]


class GreenCloudRemoteRuntime(Protocol):
    server: Any

    def close(self) -> None: ...


class _RuntimeOwner:
    """Make transport and composition-root cleanup safe to call together."""

    def __init__(self, runtime: GreenCloudRemoteRuntime) -> None:
        self.runtime = runtime
        self.closed = False

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.runtime.close()
        except Exception:
            # Runtime failures must not serialize credential-bearing exceptions.
            LOGGER.error("runtime_close_failed")


class ToolSecurityMetadataApp:
    """Add OAuth discovery metadata without changing the internal tool registry."""

    def __init__(self, app: Any) -> None:
        self._app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        response_start: dict[str, Any] | None = None
        response_body: list[bytes] = []

        async def transform_send(message: dict[str, Any]) -> None:
            nonlocal response_start
            if message["type"] == "http.response.start":
                response_start = message
                return
            if message["type"] != "http.response.body":
                await send(message)
                return

            response_body.append(message.get("body", b""))
            if message.get("more_body", False):
                return

            body = b"".join(response_body)
            changed = _add_tool_security_metadata(body)
            if changed is not None:
                body = changed

            if response_start is not None:
                start = response_start
                if changed is not None:
                    headers = [
                        header
                        for header in start.get("headers", [])
                        if header[0].lower() != b"content-length"
                    ]
                    headers.append(
                        (b"content-length", str(len(body)).encode("ascii"))
                    )
                    start = {**start, "headers": headers}
                await send(start)
            await send(
                {
                    "type": "http.response.body",
                    "body": body,
                    "more_body": False,
                }
            )

        await self._app(scope, receive, transform_send)


class MixedAuthMCPRoute:
    """Allow protocol discovery while keeping every tool call authenticated."""

    def __init__(
        self,
        *,
        protected_app: Any,
        anonymous_app: Any,
        resource_metadata_url: str,
    ) -> None:
        self._protected_app = protected_app
        self._anonymous_app = anonymous_app
        self._resource_metadata_url = resource_metadata_url

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or _has_authorization_header(scope)
        ):
            await self._protected_app(scope, receive, send)
            return

        body = await _read_limited_http_body(receive)
        if body is None:
            await _send_json_response(
                send,
                status_code=413,
                payload={
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32600,
                        "message": "Request body too large.",
                    },
                },
            )
            return

        replay_receive = _replay_http_body(body)
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            await self._protected_app(scope, replay_receive, send)
            return
        if not isinstance(payload, dict):
            await self._protected_app(scope, replay_receive, send)
            return

        method = payload.get("method")
        if method in ANONYMOUS_MCP_METHODS:
            await self._anonymous_app(scope, replay_receive, send)
            return
        if method == "tools/call":
            await _send_tool_auth_challenge(
                send,
                request_id=payload.get("id"),
                resource_metadata_url=self._resource_metadata_url,
            )
            return

        # Unknown and malformed methods never reach the unauthenticated server.
        await self._protected_app(scope, replay_receive, send)


def build_greencloud_remote_app(
    runtime: GreenCloudRemoteRuntime,
    auth_provider: AuthProvider,
) -> Starlette:
    """Build one stateless JSON Streamable HTTP app over the internal Server."""
    manager = StreamableHTTPSessionManager(
        runtime.server,
        json_response=True,
        stateless=True,
    )
    transport_app = ToolSecurityMetadataApp(manager.handle_request)
    auth_routes = auth_provider.get_routes(MCP_PATH)
    resource_url = auth_provider._get_resource_url(MCP_PATH)
    resource_metadata_url = (
        build_resource_metadata_url(resource_url) if resource_url else None
    )
    protected_app = RequireAuthMiddleware(
        transport_app,
        auth_provider.required_scopes,
        resource_metadata_url,
    )
    mixed_app = MixedAuthMCPRoute(
        protected_app=protected_app,
        anonymous_app=transport_app,
        resource_metadata_url=str(resource_metadata_url or ""),
    )
    routes = [
        *auth_routes,
        Route(
            MCP_PATH,
            endpoint=mixed_app,
            methods=["POST", "DELETE"],
        ),
    ]
    owner = _RuntimeOwner(runtime)

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        try:
            async with manager.run():
                yield
        finally:
            owner.close()

    app = Starlette(
        routes=routes,
        middleware=auth_provider.get_middleware(),
        lifespan=lifespan,
    )
    app.state.greencloud_runtime = runtime
    app.state.greencloud_runtime_owner = owner
    app.state.streamable_http_manager = manager
    return app


# Short alias for callers and fixtures that use the conventional app factory name.
build_app = build_greencloud_remote_app


def _add_tool_security_metadata(body: bytes) -> bytes | None:
    try:
        payload = json.loads(body)
        tools = payload["result"]["tools"]
        if not isinstance(tools, list):
            return None
        for tool in tools:
            if not isinstance(tool, dict):
                return None
            schemes = [dict(OAUTH_SECURITY_SCHEMES[0])]
            tool["securitySchemes"] = schemes
            metadata = tool.get("_meta")
            if not isinstance(metadata, dict):
                metadata = {}
                tool["_meta"] = metadata
            metadata["securitySchemes"] = [dict(OAUTH_SECURITY_SCHEMES[0])]
    except (
        KeyError,
        TypeError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        return None
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _has_authorization_header(scope: Scope) -> bool:
    return any(
        name.lower() == b"authorization"
        for name, _value in scope.get("headers", [])
    )


async def _read_limited_http_body(receive: Receive) -> bytes | None:
    chunks: list[bytes] = []
    total_bytes = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return b""
        if message["type"] != "http.request":
            continue
        chunk = message.get("body", b"")
        total_bytes += len(chunk)
        if total_bytes > MAX_ANONYMOUS_MCP_BODY_BYTES:
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


def _replay_http_body(body: bytes):
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {
            "type": "http.request",
            "body": body,
            "more_body": False,
        }

    return receive


async def _send_tool_auth_challenge(
    send: Send,
    *,
    request_id: object,
    resource_metadata_url: str,
) -> None:
    challenge_parts = []
    if resource_metadata_url:
        challenge_parts.append(
            f'resource_metadata="{resource_metadata_url}"'
        )
    challenge_parts.extend(
        (
            'error="insufficient_scope"',
            'error_description="Authentication with gale.read is required."',
        )
    )
    await _send_json_response(
        send,
        status_code=200,
        payload={
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": "Authentication required.",
                    }
                ],
                "_meta": {
                    "mcp/www_authenticate": [
                        "Bearer " + ", ".join(challenge_parts)
                    ],
                },
                "isError": True,
            },
        },
    )


async def _send_json_response(
    send: Send,
    *,
    status_code: int,
    payload: dict[str, Any],
) -> None:
    body = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
