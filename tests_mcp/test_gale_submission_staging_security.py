from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastmcp import Client

from src.gale_submission_staging_mcp import build_server
from src.services.gale_submission_outbox import outbox_status


class GaleSubmissionStagingSecurityTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-r1b-staging-security-",
            dir="/private/tmp",
        )
        self.outbox = Path(self.temporary.name) / "outbox.db"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    async def test_tool_rejects_unpullable_directory_and_unstable_key(
        self,
    ) -> None:
        server = build_server(self.outbox)
        async with Client(server) as session:
            invalid_directory = await session.call_tool(
                "create_note_submission",
                {
                    "title": "FICTIONAL_INVALID_DIRECTORY",
                    "content": "Fictional staging validation content.",
                    "suggested_directory": "一/二/三/四",
                    "idempotency_key": "fictional-valid-key",
                },
            )
            invalid_key = await session.call_tool(
                "create_note_submission",
                {
                    "title": "FICTIONAL_INVALID_KEY",
                    "content": "Fictional staging validation content.",
                    "idempotency_key": "../override database",
                },
            )
        self.assertEqual(invalid_directory.structured_content["status"], "failed")
        self.assertEqual(
            invalid_directory.structured_content["reason"],
            "suggested_directory_invalid",
        )
        self.assertEqual(invalid_key.structured_content["status"], "failed")
        self.assertEqual(
            invalid_key.structured_content["reason"],
            "idempotency_key_invalid",
        )
        self.assertEqual(
            outbox_status(self.outbox),
            {"queued": 0, "delivered": 0, "acknowledged": 0},
        )


if __name__ == "__main__":
    unittest.main()
