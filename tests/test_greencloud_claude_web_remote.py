"""Targeted fictional checks for Claude Web's Notes-only remote MCP."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastmcp.server.auth import AccessToken, AuthProvider
import httpx

from src.claude_web_remote_mcp import (
    CLAUDE_CODE_ACTOR,
    CLAUDE_CODE_UID,
    build_claude_web_remote_app,
    compose_claude_web_notes_runtime,
)
from src.services.gale_submission_outbox import (
    acknowledge_submission,
    export_unacknowledged,
    get_operation_status,
    initialize_outbox,
    record_terminal_receipt,
)
from src.snotes.application.cloud_proposal_service import CloudProposalService
from src.snotes.application.cloud_read_service import CloudReadService
from src.snotes.storage.cloud_read import SQLiteCloudMirrorReader
from src.snotes.transport.cloud_outbox_http import _ActorOutboxGateway
from src.snotes.transport.mcp.cloud_read_server import (
    build_cloud_read_server,
    cloud_read_tools,
)
from tests.greencloud_gale_fixture import build_fixtures


VALID_TOKEN = "fictional-claude-web-oauth-token"
AUTH_HEADERS = {
    "authorization": f"Bearer {VALID_TOKEN}",
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}
ANONYMOUS_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}


class StaticNotesProvider(AuthProvider):
    def __init__(self) -> None:
        super().__init__(
            base_url="https://fictional-claude.example.test",
            required_scopes=["notes"],
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        if token != VALID_TOKEN:
            return None
        return AccessToken(
            token=token,
            client_id="fictional-claude-client",
            scopes=["notes"],
            expires_at=int(time.time()) + 300,
            resource="https://fictional-claude.example.test/mcp",
            subject="fictional-owner",
        )


class FixtureRuntime:
    def __init__(self, server: object) -> None:
        self.server = server
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


class ClaudeWebRemoteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-claude-web-remote-fictional-"
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.home_patch = patch.dict(os.environ, {"HOME": str(self.home)})
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)

        self.manifest = build_fixtures(self.root / "fixture")
        self.reader = SQLiteCloudMirrorReader(
            self.root
            / "fixture"
            / "mirror"
            / CLAUDE_CODE_ACTOR
            / "current.db"
        )
        self.outbox = self.root / "outbox.db"
        initialize_outbox(self.outbox, generalized=True)
        proposal_service = CloudProposalService(
            self.reader,
            _ActorOutboxGateway(self.outbox, CLAUDE_CODE_ACTOR),
        )
        server = build_cloud_read_server(
            CloudReadService(self.reader),
            proposal_service,
        )
        self.runtime = FixtureRuntime(server)
        self.app = build_claude_web_remote_app(
            self.runtime,
            StaticNotesProvider(),
        )

    async def _rpc(
        self,
        client: httpx.AsyncClient,
        request_id: int,
        method: str,
        params: dict | None = None,
        *,
        headers: dict[str, str] | None = None,
        path: str = "/mcp",
        extra: dict | None = None,
    ) -> httpx.Response:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        if extra:
            payload.update(extra)
        return await client.post(
            path,
            headers=AUTH_HEADERS if headers is None else headers,
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

    def test_runtime_refuses_every_non_claude_uid(self) -> None:
        for uid in (0, 2002, 9999):
            with self.assertRaisesRegex(
                RuntimeError,
                "claude_code_os_identity_required",
            ):
                compose_claude_web_notes_runtime(uid)

        fake_notes = unittest.mock.MagicMock()
        fake_notes.binding.uid = CLAUDE_CODE_UID
        fake_notes.binding.actor = CLAUDE_CODE_ACTOR
        with patch(
            "src.claude_web_remote_mcp.compose_actor_cloud_runtime",
            return_value=fake_notes,
        ) as compose:
            runtime = compose_claude_web_notes_runtime(CLAUDE_CODE_UID)
            compose.assert_called_once_with(CLAUDE_CODE_UID)
            runtime.close()
            fake_notes.close.assert_called_once_with()

    async def test_auth_notes_registry_and_claude_only_reads(self) -> None:
        ids = self.manifest["ids"]
        transport = httpx.ASGITransport(app=self.app)
        async with self.app.router.lifespan_context(self.app):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="https://fictional-claude.example.test",
            ) as client:
                anonymous = await self._rpc(
                    client,
                    1,
                    "initialize",
                    {},
                    headers=ANONYMOUS_HEADERS,
                )
                self.assertEqual(anonymous.status_code, 401)
                self.assertIn(
                    "/.well-known/oauth-protected-resource/mcp",
                    anonymous.headers["www-authenticate"],
                )
                wrong = await self._rpc(
                    client,
                    2,
                    "tools/list",
                    {},
                    headers={
                        **ANONYMOUS_HEADERS,
                        "authorization": "Bearer fictional-wrong-token",
                    },
                )
                self.assertEqual(wrong.status_code, 401)

                initialized = await self._rpc(client, 20, "initialize", {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "fictional-claude-client", "version": "1"},
                })
                self.assertIn("result", initialized.json())
                self.assertIn("tools", initialized.json()["result"]["capabilities"])

                listed = await self._rpc(client, 3, "tools/list", {})
                tools = listed.json()["result"]["tools"]
                self.assertEqual(
                    {tool["name"] for tool in tools},
                    {tool.name for tool in cloud_read_tools(proposal_enabled=True)},
                )
                self.assertTrue(
                    all("securitySchemes" not in tool for tool in tools)
                )
                self.assertFalse(
                    any("reminder" in tool["name"] for tool in tools)
                )

                searched = await self._rpc(
                    client,
                    4,
                    "tools/call",
                    {"name": "search_notes", "arguments": {"query": "fictional"}},
                    headers={**AUTH_HEADERS, "x-snotes-actor": "gale"},
                    path="/mcp?actor=gale",
                    extra={"actor": "clawbot"},
                )
                visible = {item["note_id"] for item in self._body(searched)["results"]}
                self.assertEqual(
                    visible,
                    {
                        ids["common"],
                        ids["gale_authorized"],
                        ids["claude"],
                        ids["redacted"],
                    },
                )
                for request_id, name in ((21, "get_note"), (22, "get_note_markdown")):
                    allowed = await self._rpc(client, request_id, "tools/call", {
                        "name": name, "arguments": {"note_id": ids["claude"]},
                    })
                    self.assertEqual(self._body(allowed)["status"], "ok")
                for offset, key in enumerate(("gale", "clawbot", "hidden"), 5):
                    denied = await self._rpc(
                        client,
                        offset,
                        "tools/call",
                        {"name": "get_note", "arguments": {"note_id": ids[key]}},
                    )
                    self.assertEqual(self._body(denied)["status"], "not_found")

                spoofed = await self._rpc(
                    client,
                    8,
                    "tools/call",
                    {
                        "name": "get_note",
                        "arguments": {
                            "note_id": ids["common"],
                            "source_actor": "gale",
                        },
                    },
                )
                self.assertTrue(spoofed.json()["result"].get("isError"))
                for request_id, actor in enumerate(
                    ("gale", "common", "antigravity", "clawbot", "arbitrary-actor"), 30
                ):
                    injected = await self._rpc(client, request_id, "tools/call", {
                        "name": "get_note",
                        "arguments": {"note_id": ids["common"], "actor": actor},
                    })
                    self.assertTrue(injected.json()["result"].get("isError"))
                reminder = await self._rpc(
                    client,
                    9,
                    "tools/call",
                    {"name": "list_reminder_tables", "arguments": {}},
                )
                reminder_result = reminder.json()["result"]
                if not reminder_result.get("isError"):
                    self.assertEqual(self._body(reminder)["status"], "failed")

        self.assertEqual(self.runtime.close_count, 1)

    async def test_create_proposal_and_terminal_receipt_stay_claude_scoped(self) -> None:
        transport = httpx.ASGITransport(app=self.app)
        async with self.app.router.lifespan_context(self.app):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="https://fictional-claude.example.test",
            ) as client:
                created = await self._rpc(
                    client,
                    10,
                    "tools/call",
                    {
                        "name": "create_note",
                        "arguments": {
                            "request_id": "fictional-claude-web-create",
                            "title": "FICTIONAL Claude proposal",
                            "content": "# Fictional\n\nProposal body",
                        },
                    },
                )
                created_body = self._body(created)
                self.assertEqual(created_body["delivery_state"], "queued")
                stored = get_operation_status(
                    self.outbox,
                    source_actor=CLAUDE_CODE_ACTOR,
                    request_id="fictional-claude-web-create",
                )
                self.assertIsNotNone(stored)
                self.assertIsNone(
                    get_operation_status(
                        self.outbox,
                        source_actor="gale",
                        request_id="fictional-claude-web-create",
                    )
                )

                exported = export_unacknowledged(self.outbox)
                proposal = next(
                    item
                    for item in exported
                    if item.submission_uuid == created_body["submission_uuid"]
                )
                acknowledge_submission(
                    self.outbox,
                    submission_uuid=proposal.submission_uuid,
                    local_receipt_id="fictional-local-receipt",
                )
                record_terminal_receipt(
                    self.outbox,
                    source_actor=CLAUDE_CODE_ACTOR,
                    request_id="fictional-claude-web-create",
                    terminal_state="accepted",
                    receipt_payload={"formal_note_id": 4242},
                )
                status = await self._rpc(
                    client,
                    11,
                    "tools/call",
                    {
                        "name": "get_operation_status",
                        "arguments": {
                            "request_id": "fictional-claude-web-create"
                        },
                    },
                )
                status_body = self._body(status)
                self.assertEqual(status_body["status"], "accepted")
                self.assertEqual(status_body["receipt"], {"formal_note_id": 4242})


if __name__ == "__main__":
    unittest.main()
