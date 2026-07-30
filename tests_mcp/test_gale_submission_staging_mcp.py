from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import httpx
from fastmcp import Client

from src.gale_submission_staging_mcp import build_app, build_server
from src.services.gale_submission_outbox import outbox_status


class GaleSubmissionStagingMcpTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-r1b-staging-mcp-",
            dir="/private/tmp",
        )
        self.outbox = Path(self.temporary.name) / "outbox.db"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_exactly_one_create_tool_and_idempotent_result(self) -> None:
        server = build_server(self.outbox)
        async with Client(server) as session:
            tools = await session.list_tools()
            self.assertEqual(
                [tool.name for tool in tools],
                ["create_note_submission"],
            )
            tool = tools[0]
            self.assertFalse(tool.annotations.readOnlyHint)
            self.assertFalse(tool.annotations.destructiveHint)
            self.assertFalse(tool.annotations.openWorldHint)
            self.assertNotIn("source_actor", tool.inputSchema["properties"])
            self.assertNotIn("delivery_state", tool.inputSchema["properties"])
            self.assertNotIn("database_path", tool.inputSchema["properties"])

            arguments = {
                "title": "FICTIONAL_REMOTE_GALE_SUBMISSION_MCP",
                "content": (
                    "A fictional Oracle outbox submission for R1B staging."
                ),
                "suggested_directory": "虚构测试/Gale 投稿/待确认",
                "suggested_tags": ["虚构", "R1B"],
                "idempotency_key": "fictional-r1b-mcp-stable",
            }
            first = await session.call_tool(
                "create_note_submission",
                arguments,
            )
            second = await session.call_tool(
                "create_note_submission",
                arguments,
            )
            self.assertFalse(first.is_error)
            self.assertFalse(second.is_error)
            self.assertEqual(first.structured_content["status"], "ok")
            self.assertFalse(first.structured_content["duplicate"])
            self.assertTrue(second.structured_content["duplicate"])
            self.assertEqual(
                first.structured_content["submission_uuid"],
                second.structured_content["submission_uuid"],
            )
            self.assertNotIn("content", first.structured_content)
            self.assertEqual(outbox_status(self.outbox)["queued"], 1)

            empty = await session.call_tool(
                "create_note_submission",
                {"title": "", "content": "fictional"},
                raise_on_error=False,
            )
            oversized = await session.call_tool(
                "create_note_submission",
                {"title": "fictional", "content": "x" * 50_001},
                raise_on_error=False,
            )
            extra = await session.call_tool(
                "create_note_submission",
                {
                    "title": "fictional",
                    "content": "fictional",
                    "delivery_state": "acknowledged",
                },
                raise_on_error=False,
            )
            self.assertTrue(empty.is_error)
            self.assertTrue(oversized.is_error)
            self.assertTrue(extra.is_error)

    async def test_health_is_safe_and_contains_counts_only(self) -> None:
        app = build_app(self.outbox)
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://127.0.0.1",
            ) as client:
                response = await client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "queued": 0,
                "delivered": 0,
                "acknowledged": 0,
                "tools": 1,
            },
        )


if __name__ == "__main__":
    unittest.main()
