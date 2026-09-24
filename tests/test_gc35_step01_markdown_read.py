"""GreenCloud 3.5 step 1: verbatim, authorized Markdown reads."""

from __future__ import annotations

import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import Mock, patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.local_mcp_write_service import note_version_token
from src.snotes.application.note_service import NoteService
from src.snotes.bootstrap import build_local_mcp_service
from src.snotes.domain.models import NoteLocation
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.schema import initialize_database


ROOT = Path(__file__).resolve().parents[1]
MARKDOWN = (
    "# Heading\n\n**bold** [link](https://example.invalid)\n\n"
    "| a | b |\n| --- | --- |\n| 1 | 2 |\n\n"
    '- [x] done\n\n:size[small]{size="15"} '
    ':size[large]{size="20"}\n'
    ':size[invalid]{size="19"} :unknown[keep]{key="value"}\n'
    "  trailing spaces  \n\n" + ("long line *[raw]*\n" * 800)
)


class GreenCloudStep01MarkdownReadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="snotes-gc35-step01-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "home").mkdir()
        (self.root / "config").mkdir()
        self.connections = initialize_database(self.root / "s_notes.db")
        directories = SQLiteDirectoriesRepository(self.connections)
        repository = SQLiteNotesRepository(self.connections, directories)
        self.notes = NoteService(repository)
        self.metadata = AiAccessMetadataService(repository)
        self.service = build_local_mcp_service(
            actor_target_id="yan", resolved_data_root=self.root
        )
        self.ids = {
            "full": self._seed(MARKDOWN, "full", ("yan",)),
            "empty": self._seed("", "full", ("yan",)),
            "redacted": self._seed("private original", "redacted", ("yan",)),
            "hidden": self._seed("hidden original", "hidden", ("yan",)),
            "other": self._seed("other actor", "full", ("clawbot",)),
            "deleted": self._seed("deleted original", "full", ("yan",)),
        }
        self.notes.soft_delete_note(self.ids["deleted"])

    def _seed(self, body: str, visibility: str, targets: tuple[str, ...]) -> int:
        location = NoteLocation("项目", "GreenCloudStep01")
        note = self.notes.create_note(location)
        self.notes.save_note(
            note.id, title=f"fixture {note.id}", content_markdown=body,
            location=location, expected_revision=note.revision,
        )
        self.metadata.set_note_visibility(note.id, visibility)
        self.metadata.set_note_targets(note.id, targets)
        return note.id

    def _stored(self, note_id: int) -> tuple[str, int, str]:
        with self.connections.read() as connection:
            row = connection.execute(
                "SELECT content_markdown, revision, updated_at FROM notes WHERE id = ?",
                (note_id,),
            ).fetchone()
        return row["content_markdown"], row["revision"], row["updated_at"]

    def test_full_read_is_exact_and_changes_no_note_state(self) -> None:
        for key, expected in (("full", MARKDOWN), ("empty", "")):
            note_id = self.ids[key]
            before = self._stored(note_id)
            self.assertEqual(before[0].encode("utf-8"), expected.encode("utf-8"))
            response = self.service.get_note_markdown(note_id, modification_allowed=True)
            self.assertEqual(response["status"], "ok")
            self.assertEqual(response["note_id"], note_id)
            self.assertEqual(response["markdown"].encode("utf-8"), before[0].encode("utf-8"))
            self.assertEqual(response["dialect"], "snotes-markdown-v1")
            self.assertEqual(response["version"], note_version_token(note_id, before[2]))
            self.assertEqual(self._stored(note_id), before)
            self.assertEqual(response["version"], note_version_token(note_id, self._stored(note_id)[2]))

    def test_inaccessible_notes_and_missing_id_are_indistinguishable(self) -> None:
        for key in ("redacted", "hidden", "other", "deleted"):
            note_id = self.ids[key]
            before = self._stored(note_id)
            response = self.service.get_note_markdown(note_id, modification_allowed=True)
            self.assertEqual(response["status"], "not_found", key)
            self.assertNotIn("markdown", response)
            self.assertNotIn("version", response)
            self.assertEqual(self._stored(note_id), before)
        missing = self.service.get_note_markdown(9_999_999, modification_allowed=True)
        self.assertEqual(missing["status"], "not_found")
        self.assertEqual(missing["reason"], response["reason"])

    def test_no_modification_capability_denies_full_note(self) -> None:
        result = self.service.get_note_markdown(self.ids["full"])
        self.assertEqual(result["status"], "not_found")
        self.assertNotIn("markdown", result)

    def test_body_path_opens_no_write_transaction_when_audit_is_stubbed(self) -> None:
        note_id = self.ids["full"]
        before = self._stored(note_id)
        self.service._audit = Mock()
        with patch.object(self.service._notes._notes.connections, "write", side_effect=AssertionError("write transaction")):
            result = self.service.get_note_markdown(note_id, modification_allowed=True)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(self._stored(note_id), before)
        self.service._audit.record_event.assert_called_once()

    def test_real_audit_only_inserts_metadata_log(self) -> None:
        note_id = self.ids["full"]
        before = self._stored(note_id)
        statements: list[str] = []
        original_open = self.service._notes._notes.connections._open

        def traced_open():
            connection = original_open()
            connection.set_trace_callback(statements.append)
            return connection

        with patch.object(self.service._notes._notes.connections, "_open", side_effect=traced_open):
            result = self.service.get_note_markdown(note_id, modification_allowed=True)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(self._stored(note_id), before)
        writes = [statement.strip().upper() for statement in statements if statement.strip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE", "BEGIN"))]
        self.assertEqual(len([statement for statement in writes if statement.startswith("BEGIN")]), 1)
        self.assertEqual(len([statement for statement in writes if statement.startswith("INSERT INTO AI_CALL_LOGS")]), 1)
        self.assertEqual(len(writes), 2, writes)
        with self.connections.read() as connection:
            row = connection.execute(
                "SELECT operation_type, note_id, result_count, summary FROM ai_call_logs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(tuple(row), ("get_note", note_id, 1, ""))
        self.assertNotIn(MARKDOWN[:40], " ".join(statements))

    @asynccontextmanager
    async def _session(self, *, read_only: bool, allowlist: set[str] | None = None):
        env = {
            "HOME": str(self.root / "home"),
            "PYTHONPYCACHEPREFIX": str(self.root / "pycache"),
            "S_NOTES_DATA_ROOT": str(self.root),
            "SNOTES_MCP_READ_ONLY": "1" if read_only else "0",
            "SNOTES_MCP_ACTOR_TARGET_ID": "yan",
            "SNOTES_GALE_TUNNEL_ALLOW_DATA_ROOT_OVERRIDE": "1",
        }
        if allowlist is not None:
            env["SNOTES_MCP_TOOL_ALLOWLIST"] = ",".join(sorted(allowlist))
        parameters = StdioServerParameters(
            command=str(ROOT / "scripts" / "run_snotes_mcp_stdio.sh"),
            args=[], cwd=str(ROOT), env=env,
        )
        async with stdio_client(parameters) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                yield session

    async def test_mcp_schema_description_annotations_and_profile_gate(self) -> None:
        async with self._session(read_only=False) as session:
            tools = {tool.name: tool for tool in (await session.list_tools()).tools}
            markdown = tools["get_note_markdown"]
            self.assertNotIn("anyOf", markdown.inputSchema)
            self.assertNotIn("oneOf", markdown.inputSchema)
            self.assertEqual(set(markdown.inputSchema["properties"]), {"note_id"})
            self.assertIn("snotes-markdown-v1", markdown.description)
            self.assertIn("CommonMark", markdown.description)
            self.assertIn("GFM", markdown.description)
            self.assertIn('size="15"', markdown.description)
            self.assertIn('size="20"', markdown.description)
            self.assertIn("Never delete unknown", markdown.description)
            self.assertTrue(markdown.annotations.readOnlyHint)
            result = await session.call_tool("get_note_markdown", {"note_id": self.ids["full"]})
            self.assertFalse(result.isError)
            self.assertEqual(result.structuredContent["markdown"].encode("utf-8"), MARKDOWN.encode("utf-8"))

        for read_only, allowlist in ((True, None), (False, {"get_note_markdown"})):
            async with self._session(read_only=read_only, allowlist=allowlist) as session:
                tools = {tool.name: tool for tool in (await session.list_tools()).tools}
                self.assertIn("get_note_markdown", tools)
                result = await session.call_tool("get_note_markdown", {"note_id": self.ids["full"]})
                self.assertFalse(result.isError)
                self.assertEqual(result.structuredContent["status"], "not_found")

