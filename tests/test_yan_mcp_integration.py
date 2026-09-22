from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_receipt_inbox_service import (
    AiReceiptAcceptFields,
    AiReceiptInboxService,
)
from src.snotes.application.note_service import NoteService
from src.snotes.application.reminder_service import ReminderService
from src.snotes.application.tag_service import TagService
from src.snotes.domain.models import NoteLocation
from src.snotes.domain.reminders import REMINDER_ACTIVE, ReminderDraft
from src.snotes.storage.repositories.ai_mcp_operations import (
    SQLiteAiMcpOperationRepository,
)
from src.snotes.storage.repositories.ai_receipts import (
    SQLiteAiReceiptInboxRepository,
)
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database
from tests.test_package5_local_mcp_protocol import (
    CLAWDE_NOTE_TOOLS,
    CLAWDE_REMINDER_TOOLS,
    CLAWDE_TOOLS,
    ROOT,
)


class YanMcpIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-yan-mcp-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        config = self.root / "config"
        config.mkdir()
        (config / "local_redaction_rules.json").write_text(
            json.dumps({"FictionalSecret": "[MASKED]"}),
            encoding="utf-8",
        )
        self.connections = initialize_database(self.root / "s_notes.db")
        self.directories = SQLiteDirectoriesRepository(self.connections)
        notes_repository = SQLiteNotesRepository(self.connections, self.directories)
        self.tags_repository = SQLiteTagsRepository(self.connections)
        self.notes = NoteService(notes_repository)
        self.tags = TagService(self.tags_repository)
        self.metadata = AiAccessMetadataService(notes_repository)
        self.reminders = ReminderService(SQLiteReminderRepository(self.connections))
        self.receipts = AiReceiptInboxService(
            SQLiteAiReceiptInboxRepository(
                self.connections,
                self.directories,
                self.tags_repository,
            ),
            notes=self.notes,
            tags=self.tags,
            ai_metadata=self.metadata,
            mcp_operations=SQLiteAiMcpOperationRepository(
                self.connections,
                self.tags_repository,
            ),
        )
        self.note_ids = {
            "common": self._seed_note("Common visible", "common body", "full", ("common",)),
            "yan": self._seed_note(
                "Yan visible", "YAN_FULL_NEEDLE", "full", ("yan",)
            ),
            "clawbot": self._seed_note(
                "Clawbot visible", "clawbot body", "full", ("clawbot",)
            ),
            "gale": self._seed_note("Gale visible", "gale body", "full", ("gale",)),
            "hidden": self._seed_note(
                "Yan hidden", "YAN_HIDDEN_NEEDLE", "hidden", ("yan",)
            ),
            "redacted": self._seed_note(
                "FictionalSecret yan",
                "FictionalSecret redacted body",
                "redacted",
                ("yan",),
            ),
        }
        self.visible_table = self.reminders.create_table("FICTIONAL_YAN_VISIBLE_TABLE")
        self.hidden_table = self.reminders.create_table(
            "FICTIONAL_YAN_HIDDEN_TABLE",
            ai_visible=False,
        )
        self.reminders.create_reminder(
            ReminderDraft(
                title="FICTIONAL_YAN_VISIBLE_ITEM",
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "one_time", "date": "2026-09-10"},
                table_id=self.visible_table.id,
            )
        )

    def _seed_note(
        self,
        title: str,
        body: str,
        visibility: str,
        targets: tuple[str, ...],
    ) -> int:
        location = NoteLocation("项目", title.replace(" ", ""))
        note = self.notes.create_note(location)
        note = self.notes.save_note(
            note.id,
            title=title,
            content_markdown=body,
            location=location,
            expected_revision=note.revision,
        )
        self.metadata.set_note_visibility(note.id, visibility)
        self.metadata.set_note_targets(note.id, targets)
        return note.id

    def _env(
        self,
        *,
        actor: str,
        tool_allowlist: set[str] | None = None,
    ) -> dict[str, str]:
        values = {
            "HOME": str(self.home),
            "PYTHONPYCACHEPREFIX": str(self.root / "pycache"),
            "S_NOTES_DATA_ROOT": str(self.root),
            "SNOTES_MCP_READ_ONLY": "0",
            "SNOTES_MCP_ACTOR_TARGET_ID": actor,
            "SNOTES_YAN_ALLOW_DATA_ROOT_OVERRIDE": "1",
        }
        if tool_allowlist is not None:
            values["SNOTES_MCP_TOOL_ALLOWLIST"] = ",".join(sorted(tool_allowlist))
        return values

    @asynccontextmanager
    async def session(
        self,
        *,
        script: str,
        actor: str,
        tool_allowlist: set[str] | None = None,
    ):
        parameters = StdioServerParameters(
            command=str(ROOT / "scripts" / script),
            args=[],
            cwd=str(ROOT),
            env=self._env(actor=actor, tool_allowlist=tool_allowlist),
        )
        async with stdio_client(parameters) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                yield session

    async def call(self, session: ClientSession, name: str, arguments: dict) -> dict:
        response = await session.call_tool(name, arguments)
        self.assertFalse(
            response.isError,
            [item.model_dump() for item in response.content],
        )
        self.assertIsNotNone(response.structuredContent)
        return response.structuredContent

    async def test_yan_actor_projection_permissions_and_receipts(self) -> None:
        async with self.session(
            script="run_snotes_mcp_stdio.sh",
            actor="clawbot",
            tool_allowlist=CLAWDE_TOOLS,
        ) as clawbot_session:
            clawbot_tools = {
                tool.name for tool in (await clawbot_session.list_tools()).tools
            }

        async with self.session(
            script="run_snotes_mcp_stdio.sh",
            actor="gale",
        ) as metadata_session:
            options = await self.call(
                metadata_session,
                "list_ai_metadata_options",
                {},
            )
            self.assertIn(
                ("yan", "砚"),
                [
                    (item["target_id"], item["display_name"])
                    for item in options["ai_targets"]
                ],
            )

        formal_before = len(self.notes.list_active_notes())
        async with self.session(
            script="run_snotes_yan_mcp_stdio.sh",
            actor="gale",
        ) as session:
            yan_tools = {tool.name for tool in (await session.list_tools()).tools}
            self.assertEqual(yan_tools, CLAWDE_TOOLS)
            self.assertEqual(yan_tools, clawbot_tools)
            self.assertEqual(yan_tools & CLAWDE_NOTE_TOOLS, CLAWDE_NOTE_TOOLS)
            self.assertEqual(
                yan_tools & CLAWDE_REMINDER_TOOLS,
                CLAWDE_REMINDER_TOOLS,
            )

            expected_search_counts = {
                "common body": 1,
                "YAN_FULL_NEEDLE": 1,
                "clawbot body": 0,
                "gale body": 0,
                "YAN_HIDDEN_NEEDLE": 0,
                "FictionalSecret yan": 0,
                "MASKED yan": 1,
            }
            for query, expected_count in expected_search_counts.items():
                with self.subTest(query=query):
                    result = await self.call(
                        session,
                        "search_notes",
                        {"query": query},
                    )
                    self.assertEqual(result["total_visible_matches"], expected_count)

            for key, expected_status in {
                "common": "ok",
                "yan": "ok",
                "clawbot": "not_found",
                "gale": "not_found",
                "hidden": "not_found",
            }.items():
                with self.subTest(note=key):
                    result = await self.call(
                        session,
                        "get_note",
                        {"note_id": self.note_ids[key]},
                    )
                    self.assertEqual(result["status"], expected_status)
            redacted = await self.call(
                session,
                "get_note",
                {"note_id": self.note_ids["redacted"]},
            )
            redacted_text = json.dumps(redacted, ensure_ascii=False)
            self.assertNotIn("FictionalSecret", redacted_text)
            self.assertIn("[MASKED]", redacted_text)

            tables = await self.call(
                session,
                "list_reminder_tables",
                {"query": "FICTIONAL_YAN"},
            )
            hidden_table = await self.call(
                session,
                "get_reminder_table",
                {"table_id": self.hidden_table.id},
            )
            created_label = await self.call(
                session,
                "create_reminder_label",
                {
                    "request_id": "yan-create-reminder-label",
                    "name": "FICTIONAL_YAN_LABEL",
                },
            )
            created_item = await self.call(
                session,
                "create_reminder_item",
                {
                    "request_id": "yan-create-reminder-item",
                    "table_id": self.visible_table.id,
                    "title": "FICTIONAL_YAN_CREATED_ITEM",
                    "lifecycle": REMINDER_ACTIVE,
                    "action_spec": {"type": "one_time", "date": "2026-09-18"},
                },
            )
            self.assertEqual(tables["total_visible_matches"], 1)
            self.assertEqual(hidden_table["status"], "not_found")
            self.assertEqual(created_label["status"], "created")
            self.assertTrue(created_label["changed_formal_reminder"])
            self.assertEqual(created_item["status"], "created")
            self.assertTrue(created_item["changed_formal_reminder"])

            pending_create = await self.call(
                session,
                "create_note",
                {
                    "request_id": "yan-create-note",
                    "title": "Yan fictional pending",
                    "content": "yan pending body",
                },
            )
            self.assertEqual(pending_create["status"], "pending_confirmation")
            self.assertFalse(pending_create["changed_formal_note"])
            self.assertTrue(pending_create["pending_confirmation"])
            self.assertEqual(len(self.notes.list_active_notes()), formal_before)
            receipt = self.receipts.get_receipt(pending_create["proposal_id"])
            self.assertEqual(receipt.source, "砚")
            self.assertEqual(receipt.actor_target_id, "yan")
            self.assertEqual(receipt.ai_targets, "yan")

            target_state = self.receipts.receipt_target_state(receipt)
            accepted = self.receipts.accept_receipt(
                receipt.id,
                AiReceiptAcceptFields(
                    title=receipt.title,
                    body=receipt.body,
                    main_category=receipt.main_category,
                    sub_category=receipt.sub_category,
                    tags=receipt.tags,
                    visibility=receipt.ai_visibility,
                    target_ids=target_state.target_ids,
                ),
            )
            initial = await self.call(
                session,
                "get_note",
                {"note_id": accepted.note.id},
            )
            pending_append = await self.call(
                session,
                "append_note",
                {
                    "request_id": "yan-append-note",
                    "note_id": accepted.note.id,
                    "content": "yan pending append",
                    "expected_version": initial["version"],
                },
            )
            self.assertEqual(pending_append["status"], "pending_confirmation")
            self.assertFalse(pending_append["changed_formal_note"])
            unchanged = await self.call(
                session,
                "get_note",
                {"note_id": accepted.note.id},
            )
            self.assertEqual(unchanged["content"], "yan pending body")

            current = self.notes.get_note(accepted.note.id)
            self.notes.save_note(
                current.id,
                title=current.title,
                content_markdown=current.content_markdown + "<p>external change</p>",
                location=current.location,
                expected_revision=current.revision,
            )
            stale = await self.call(
                session,
                "append_note",
                {
                    "request_id": "yan-stale-append",
                    "note_id": accepted.note.id,
                    "content": "must not append",
                    "expected_version": initial["version"],
                },
            )
            self.assertEqual(stale["status"], "conflict")
            self.assertFalse(stale["changed_formal_note"])
            self.assertFalse(stale["pending_confirmation"])

        with self.connections.read() as connection:
            reminder_actors = {
                row["actor_target_id"]
                for row in connection.execute(
                    """
                    SELECT actor_target_id FROM ai_reminder_operations
                    WHERE request_id IN (?, ?)
                    """,
                    ("yan-create-reminder-label", "yan-create-reminder-item"),
                ).fetchall()
            }
        self.assertEqual(reminder_actors, {"yan"})


if __name__ == "__main__":
    unittest.main()
