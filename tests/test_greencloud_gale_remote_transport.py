"""Fictional-only HTTP transport checks for Gale's unified GreenCloud MCP."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch

from fastmcp.server.auth import AccessToken, AuthProvider
import httpx
from mcp import types
import uvicorn

from src.snotes.application.cloud_proposal_service import CloudProposalService
from src.snotes.application.cloud_read_service import CloudReadService
from src.snotes.application.reminder_service import ReminderService
from src.snotes.application.remote_mcp_reminder import RemoteMcpReminderAdapter
from src.snotes.storage.cloud_read import SQLiteCloudMirrorReader
from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.transport.mcp.greencloud_internal_server import (
    build_greencloud_internal_server,
)
from src.snotes.transport.mcp.greencloud_remote_transport import build_app
from src.snotes.transport.reminder_http import (
    ACTOR_COMMANDS,
    ACTOR_ROLE,
    OWNER_ONLY_COMMANDS,
    ReminderCredential,
    ReminderHttpApplication,
)
from tests.greencloud_gale_fixture import build_fixtures


VALID_TOKEN = "fictional-server-issued-gale-token"
AUTH_HEADERS = {
    "authorization": f"Bearer {VALID_TOKEN}",
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}
ANONYMOUS_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


class StaticFictionalTokenProvider(AuthProvider):
    """A server-side verifier with no Auth0 or network dependency."""

    def __init__(self) -> None:
        super().__init__(
            base_url="https://fictional-greencloud.example.test",
            required_scopes=["gale.read"],
        )
        self.verifications = 0

    async def verify_token(self, token: str) -> AccessToken | None:
        self.verifications += 1
        if token != VALID_TOKEN:
            return None
        return AccessToken(
            token=token,
            client_id="fictional-greencloud-client",
            scopes=["gale.read"],
            expires_at=int(time.time()) + 300,
            resource="https://fictional-greencloud.example.test/mcp",
            subject="fictional-gale",
        )


class CountingReader:
    def __init__(self, reader: SQLiteCloudMirrorReader) -> None:
        self.reader = reader
        self.calls = 0

    def search(self, tokens, *, limit):
        self.calls += 1
        return self.reader.search(tokens, limit=limit)

    def get(self, note_id):
        self.calls += 1
        return self.reader.get(note_id)

    def get_markdown(self, note_id):
        self.calls += 1
        return self.reader.get_markdown(note_id)

    def metadata_catalog(self):
        self.calls += 1
        return self.reader.metadata_catalog()


class NoMutationProposalGateway:
    """Report one fixture operation and reject every attempted mutation."""

    def __init__(self) -> None:
        self.enqueue_attempts: list[dict] = []

    def get_operation_status(self, request_id: str) -> dict | None:
        if request_id == "fictional-reported-operation":
            return {
                "status": "reported",
                "request_id": request_id,
                "reason": "fictional_no_mutation",
            }
        return None

    def enqueue_proposal(self, **values) -> dict:
        self.enqueue_attempts.append(values)
        return {
            "status": "rejected",
            "request_id": values["request_id"],
            "reason": "fictional_no_mutation",
        }


class FixtureReminderClient:
    def __init__(self, app: ReminderHttpApplication) -> None:
        self.app = app
        self.commands: list[str] = []

    def command(self, name: str, payload: dict) -> dict:
        self.commands.append(name)
        response = self.app.handle(
            "POST",
            "/v1/reminder/commands/" + name,
            headers={"Authorization": "Bearer fictional-gale-reminder"},
            body=json.dumps(payload).encode("utf-8"),
        )
        return response.body


class FixtureRuntime:
    def __init__(self, server) -> None:
        self.server = server
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


class GreenCloudGaleRemoteTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-greencloud-remote-fictional-"
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.home_patch = patch.dict(os.environ, {"HOME": str(self.home)})
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)

        self.manifest = build_fixtures(self.root / "fixture")
        self.reader = CountingReader(
            SQLiteCloudMirrorReader(
                self.root / "fixture" / "mirror" / "gale" / "current.db"
            )
        )
        self.gateway = NoMutationProposalGateway()

        reminder_service = ReminderService(
            SQLiteReminderRepository(
                initialize_database(self.root / "fictional-reminders.db")
            )
        )
        self.visible_table = reminder_service.create_table(
            "FICTIONAL Gale visible",
            ai_visible=True,
        )
        self.hidden_table = reminder_service.create_table(
            "FICTIONAL owner hidden",
            ai_visible=False,
        )
        reminder_api = ReminderHttpApplication(
            reminder_service,
            {
                "fictional-gale-reminder": ReminderCredential(
                    "gale",
                    ACTOR_ROLE,
                )
            },
        )
        self.reminder_client = FixtureReminderClient(reminder_api)
        self.server = build_greencloud_internal_server(
            CloudReadService(self.reader),
            CloudProposalService(self.reader, self.gateway),
            RemoteMcpReminderAdapter(self.reminder_client),
        )
        self.runtime = FixtureRuntime(self.server)
        self.provider = StaticFictionalTokenProvider()
        self.app = build_app(self.runtime, self.provider)

    async def _rpc(
        self,
        client: httpx.AsyncClient,
        request_id: int,
        method: str,
        params: dict | None = None,
        *,
        headers: dict[str, str] | None = None,
        path: str = "/mcp",
    ) -> httpx.Response:
        payload: dict = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        return await client.post(
            path,
            headers=headers or AUTH_HEADERS,
            json=payload,
        )

    @staticmethod
    def _body(response: httpx.Response) -> dict:
        result = response.json()["result"]
        if "structuredContent" in result:
            return result["structuredContent"]
        for item in result.get("content", []):
            if item.get("type") == "text":
                return json.loads(item["text"])
        raise AssertionError("missing MCP result body")

    def _assert_rejected(self, response: httpx.Response) -> None:
        result = response.json()["result"]
        if result.get("isError") is True:
            return
        self.assertEqual(self._body(response)["status"], "rejected")

    async def test_loopback_http_fictional_notes_and_reminder(self) -> None:
        """Exercise a real localhost socket without any production data paths."""
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.setblocking(False)
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(self.app, access_log=False,
            log_level="critical", lifespan="on", proxy_headers=False))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            for _ in range(100):
                if server.started:
                    break
                await asyncio.sleep(0.01)
            self.assertTrue(server.started)
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}",
                trust_env=False) as client:
                listed = await self._rpc(client, 1, "tools/list", {})
                self.assertEqual(len(listed.json()["result"]["tools"]), 25)
                for name, arguments in (
                    ("get_note", {"note_id": self.manifest["ids"]["gale"]}),
                    ("list_ai_metadata_options", {}),
                    ("list_reminder_tables", {}),
                ):
                    response = await self._rpc(client, 2, "tools/call",
                        {"name": name, "arguments": arguments})
                    self.assertEqual(self._body(response)["status"], "ok")
                denied = await self._rpc(client, 3, "tools/call",
                    {"name": "get_note", "arguments":
                        {"note_id": self.manifest["ids"]["hidden"]}})
                self.assertEqual(self._body(denied)["status"], "not_found")
        finally:
            server.should_exit = True
            await asyncio.wait_for(serving, timeout=5)
            listener.close()
        self.assertEqual(self.runtime.close_count, 1)

    async def test_discovery_matches_internal_registry_and_auth_fails_closed(self) -> None:
        original = await self.server.request_handlers[types.ListToolsRequest](
            types.ListToolsRequest()
        )
        expected = {
            tool.name: tool.model_dump(by_alias=True, exclude_none=True)
            for tool in original.root.tools
        }
        self.assertEqual(len(expected), 25)

        transport = httpx.ASGITransport(app=self.app)
        async with self.app.router.lifespan_context(self.app):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="https://fictional-greencloud.example.test",
            ) as client:
                initialized = await self._rpc(
                    client,
                    1,
                    "initialize",
                    {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {
                            "name": "fictional-transport-test",
                            "version": "1",
                        },
                    },
                    headers=ANONYMOUS_HEADERS,
                )
                self.assertEqual(initialized.status_code, 200)

                listed = await self._rpc(
                    client,
                    2,
                    "tools/list",
                    {},
                    headers=ANONYMOUS_HEADERS,
                )
                self.assertEqual(listed.status_code, 200)
                advertised = listed.json()["result"]["tools"]
                self.assertEqual(len(advertised), 25)
                for tool in advertised:
                    security = [{"type": "oauth2", "scopes": ["gale.read"]}]
                    self.assertEqual(tool.pop("securitySchemes"), security)
                    metadata = tool.get("_meta", {})
                    self.assertEqual(metadata.pop("securitySchemes"), security)
                    if not metadata:
                        tool.pop("_meta", None)
                    self.assertEqual(tool, expected[tool["name"]])

                calls_before = self.reader.calls
                anonymous_call = await self._rpc(
                    client,
                    3,
                    "tools/call",
                    {
                        "name": "search_notes",
                        "arguments": {"query": "fictional"},
                    },
                    headers=ANONYMOUS_HEADERS,
                )
                self.assertEqual(anonymous_call.status_code, 200)
                self.assertTrue(anonymous_call.json()["result"]["isError"])
                challenge = anonymous_call.json()["result"]["_meta"][
                    "mcp/www_authenticate"
                ][0]
                self.assertIn("gale.read is required", challenge)
                self.assertIn("oauth-protected-resource/mcp", challenge)
                self.assertEqual(self.reader.calls, calls_before)

                wrong = await self._rpc(
                    client,
                    4,
                    "tools/call",
                    {
                        "name": "search_notes",
                        "arguments": {"query": "fictional"},
                    },
                    headers={
                        **ANONYMOUS_HEADERS,
                        "authorization": "Bearer fictional-wrong-token",
                    },
                )
                self.assertEqual(wrong.status_code, 401)
                self.assertEqual(self.reader.calls, calls_before)

                unknown = await self._rpc(
                    client,
                    5,
                    "fictional/unknown",
                    {},
                    headers=ANONYMOUS_HEADERS,
                )
                self.assertEqual(unknown.status_code, 401)

                valid = await self._rpc(
                    client,
                    6,
                    "tools/call",
                    {
                        "name": "search_notes",
                        "arguments": {"query": "fictional"},
                    },
                )
                self.assertEqual(valid.status_code, 200)
                self.assertEqual(self._body(valid)["status"], "ok")
                self.assertGreater(self.reader.calls, calls_before)
                self.assertGreaterEqual(self.provider.verifications, 2)

        self.assertEqual(self.runtime.close_count, 1)
        self.app.state.greencloud_runtime_owner.close()
        self.assertEqual(self.runtime.close_count, 1)

    async def test_fixed_gale_data_and_operation_boundaries(self) -> None:
        ids = self.manifest["ids"]
        transport = httpx.ASGITransport(app=self.app)
        async with self.app.router.lifespan_context(self.app):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="https://fictional-greencloud.example.test",
            ) as client:
                searched = await self._rpc(
                    client,
                    10,
                    "tools/call",
                    {
                        "name": "search_notes",
                        "arguments": {"query": "fictional"},
                    },
                    headers={**AUTH_HEADERS, "x-snotes-actor": "claude-code"},
                    path="/mcp?actor=claude-code",
                )
                visible_ids = {
                    item["note_id"] for item in self._body(searched)["results"]
                }
                self.assertEqual(
                    visible_ids,
                    {ids[key] for key in ("common", "gale", "gale_authorized", "redacted")},
                )
                payload_spoof = await client.post("/mcp", headers=AUTH_HEADERS,
                    json={"jsonrpc": "2.0", "id": 31, "actor": "clawbot",
                        "method": "tools/call", "params": {
                            "actor": "owner", "name": "search_notes",
                            "arguments": {"query": "fictional"}}})
                self.assertEqual(
                    {item["note_id"] for item in self._body(payload_spoof)["results"]},
                    visible_ids,
                )
                for offset, key in enumerate(("claude", "clawbot", "hidden"), 11):
                    response = await self._rpc(
                        client,
                        offset,
                        "tools/call",
                        {
                            "name": "get_note",
                            "arguments": {"note_id": ids[key]},
                        },
                    )
                    self.assertEqual(self._body(response)["status"], "not_found")

                metadata = await self._rpc(
                    client,
                    20,
                    "tools/call",
                    {"name": "list_ai_metadata_options", "arguments": {}},
                )
                metadata_body = self._body(metadata)
                self.assertEqual(metadata_body["status"], "ok")
                self.assertIn(
                    "gale",
                    {target["target_id"] for target in metadata_body["ai_targets"]},
                )

                tables = await self._rpc(
                    client,
                    21,
                    "tools/call",
                    {"name": "list_reminder_tables", "arguments": {}},
                )
                table_body = self._body(tables)
                self.assertEqual(table_body["status"], "ok")
                titles = {item["title"] for item in table_body["results"]}
                self.assertIn(self.visible_table.title, titles)
                self.assertNotIn(self.hidden_table.title, titles)
                hidden = await self._rpc(
                    client,
                    22,
                    "tools/call",
                    {
                        "name": "get_reminder_table",
                        "arguments": {"table_id": self.hidden_table.id},
                    },
                )
                self.assertEqual(self._body(hidden)["status"], "not_found")

                listed = await self._rpc(client, 23, "tools/list", {})
                listed_names = {
                    tool["name"] for tool in listed.json()["result"]["tools"]
                }
                self.assertTrue(ACTOR_COMMANDS.issubset(listed_names))
                self.assertFalse(OWNER_ONLY_COMMANDS & listed_names)
                self.assertNotIn("delete_note", listed_names)
                owner_only = await self._rpc(
                    client,
                    24,
                    "tools/call",
                    {"name": "delete_reminder", "arguments": {"reminder_id": 1}},
                )
                self.assertNotIn(
                    self._body(owner_only).get("status"),
                    {"ok", "queued", "created", "updated"},
                )

                for request_id, name, arguments in (
                    (
                        25,
                        "get_note",
                        {"note_id": ids["common"], "actor": "claude-code"},
                    ),
                    (26, "list_reminder_tables", {"actor": "owner"}),
                ):
                    injected = await self._rpc(
                        client,
                        request_id,
                        "tools/call",
                        {"name": name, "arguments": arguments},
                    )
                    self._assert_rejected(injected)

                common = await self._rpc(
                    client,
                    27,
                    "tools/call",
                    {
                        "name": "get_note",
                        "arguments": {"note_id": ids["common"]},
                    },
                )
                version = self._body(common)["version"]
                for request_id, field, value in (
                    (28, "ai_visibility", "hidden"),
                    (29, "ai_targets", ["claude-code"]),
                ):
                    blocked = await self._rpc(
                        client,
                        request_id,
                        "tools/call",
                        {
                            "name": "update_note_metadata",
                            "arguments": {
                                "request_id": f"fictional-blocked-{field}",
                                "note_id": ids["common"],
                                "expected_version": version,
                                field: value,
                            },
                        },
                    )
                    self._assert_rejected(blocked)
                self.assertEqual(self.gateway.enqueue_attempts, [])

                reported = await self._rpc(
                    client,
                    30,
                    "tools/call",
                    {
                        "name": "get_operation_status",
                        "arguments": {
                            "request_id": "fictional-reported-operation"
                        },
                    },
                )
                self.assertEqual(self._body(reported)["status"], "reported")


if __name__ == "__main__":
    unittest.main()
