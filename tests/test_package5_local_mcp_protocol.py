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


ROOT = Path(__file__).resolve().parents[1]
NORMAL_TOOLS = {
    "search_notes",
    "get_note",
    "list_reminder_labels",
    "list_reminder_tables",
    "get_reminder_table",
    "list_reminder_items",
    "get_reminder_item",
    "list_directories",
    "list_tags",
    "list_ai_metadata_options",
    "create_note",
    "prepend_note",
    "append_note",
    "update_note_metadata",
    "get_operation_status",
    "create_reminder_label",
    "create_reminder_table",
    "update_reminder_table",
    "create_reminder_item",
    "update_reminder_item",
    "complete_reminder_item",
    "pause_reminder_item",
    "resume_reminder_item",
}
READ_ONLY_TOOLS = {
    "search_notes",
    "get_note",
    "list_reminder_labels",
    "list_reminder_tables",
    "get_reminder_table",
    "list_reminder_items",
    "get_reminder_item",
}
CLAWDE_TOOLS = {
    "search_notes",
    "get_note",
    "list_directories",
    "list_tags",
    "list_ai_metadata_options",
    "create_note",
    "append_note",
}
CLAWDE_DISALLOWED_TOOLS = NORMAL_TOOLS - CLAWDE_TOOLS
GALE_TUNNEL_TOOLS = {
    "search_notes",
    "get_note",
    "list_directories",
    "list_tags",
    "list_ai_metadata_options",
    "create_note_submission",
    "list_reminder_labels",
    "list_reminder_tables",
    "get_reminder_table",
    "list_reminder_items",
    "get_reminder_item",
    "create_reminder_label",
    "create_reminder_table",
    "update_reminder_table",
    "create_reminder_item",
    "update_reminder_item",
    "complete_reminder_item",
    "pause_reminder_item",
    "resume_reminder_item",
}


class Package5LocalMcpProtocolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        config = self.root / "config"
        config.mkdir()
        self.rules_path = config / "local_redaction_rules.json"
        self.rules_path.write_text(
            json.dumps({"FictionalSecret": "[MASKED]"}),
            encoding="utf-8",
        )
        self.connections = initialize_database(self.root / "s_notes.db")
        self.directories_repository = SQLiteDirectoriesRepository(self.connections)
        notes_repository = SQLiteNotesRepository(
            self.connections, self.directories_repository
        )
        self.tags_repository = SQLiteTagsRepository(self.connections)
        self.notes = NoteService(notes_repository)
        self.tags = TagService(self.tags_repository)
        self.metadata = AiAccessMetadataService(notes_repository)
        self.reminders = ReminderService(SQLiteReminderRepository(self.connections))
        self.ids: dict[str, int] = {}
        self.reminder_ids: dict[str, int] = {}
        self._seed_fixture()

    def _seed_note(
        self,
        key: str,
        *,
        title: str,
        body: str,
        visibility: str,
        targets: tuple[str, ...],
        tags: tuple[str, ...],
        location: NoteLocation,
    ) -> int:
        note = self.notes.create_note(location)
        note = self.notes.save_note(
            note.id,
            title=title,
            content_html=f"<p>{body}</p>",
            location=location,
            expected_revision=note.revision,
        )
        self.tags.set_note_tags(note.id, tags)
        self.metadata.set_note_visibility(note.id, visibility)
        self.metadata.set_note_targets(note.id, targets)
        self.ids[key] = note.id
        return note.id

    def _seed_fixture(self) -> None:
        self._seed_note(
            "common",
            title="Common visible",
            body="common body needle",
            visibility="full",
            targets=("common",),
            tags=("common-tag",),
            location=NoteLocation("项目", "CommonVisible"),
        )
        self._seed_note(
            "clawbot",
            title="Clawbot visible",
            body="clawbot body needle",
            visibility="full",
            targets=("clawbot",),
            tags=("clawbot-tag",),
            location=NoteLocation("项目", "ClawbotVisible"),
        )
        self._seed_note(
            "gale",
            title="Gale visible",
            body="gale body needle",
            visibility="full",
            targets=("gale",),
            tags=("gale-tag",),
            location=NoteLocation("项目", "GaleVisible"),
        )
        self._seed_note(
            "hidden",
            title="Clawbot hidden",
            body="hidden body needle",
            visibility="hidden",
            targets=("clawbot",),
            tags=("hidden-tag",),
            location=NoteLocation("项目", "HiddenVisible"),
        )
        self._seed_note(
            "redacted",
            title="FictionalSecret clawbot",
            body="FictionalSecret redacted body",
            visibility="redacted",
            targets=("clawbot",),
            tags=("FictionalSecret-tag",),
            location=NoteLocation("项目", "FictionalSecretDirectory"),
        )
        visible_table = self.reminders.create_table("FICTIONAL_GALE_VISIBLE_TABLE")
        hidden_table = self.reminders.create_table(
            "FICTIONAL_GALE_HIDDEN_TABLE",
            ai_visible=False,
        )
        visible_reminder = self.reminders.create_reminder(
            ReminderDraft(
                title="FICTIONAL_GALE_VISIBLE_REMINDER",
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "one_time", "date": "2026-09-10"},
                table_id=visible_table.id,
            )
        )
        hidden_reminder = self.reminders.create_reminder(
            ReminderDraft(
                title="FICTIONAL_GALE_HIDDEN_REMINDER",
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "one_time", "date": "2026-09-10"},
                table_id=hidden_table.id,
            )
        )
        self.reminder_ids.update(
            {
                "visible_table": visible_table.id,
                "hidden_table": hidden_table.id,
                "visible_reminder": visible_reminder.id,
                "hidden_reminder": hidden_reminder.id,
            }
        )

    def _env(
        self,
        *,
        read_only: bool,
        actor: str = "gale",
        tool_allowlist: set[str] | None = None,
    ) -> dict[str, str]:
        values = {
            "HOME": str(self.home),
            "PYTHONPYCACHEPREFIX": str(self.root / "pycache"),
            "S_NOTES_DATA_ROOT": str(self.root),
            "SNOTES_MCP_READ_ONLY": "1" if read_only else "0",
            "SNOTES_MCP_ACTOR_TARGET_ID": actor,
            "SNOTES_GALE_TUNNEL_ALLOW_DATA_ROOT_OVERRIDE": "1",
        }
        if tool_allowlist is not None:
            values["SNOTES_MCP_TOOL_ALLOWLIST"] = ",".join(sorted(tool_allowlist))
        return values

    @asynccontextmanager
    async def session(
        self,
        *,
        read_only: bool,
        actor: str = "gale",
        tool_allowlist: set[str] | None = None,
        script: str = "run_snotes_mcp_stdio.sh",
    ):
        parameters = StdioServerParameters(
            command=str(ROOT / "scripts" / script),
            args=[],
            cwd=str(ROOT),
            env=self._env(
                read_only=read_only,
                actor=actor,
                tool_allowlist=tool_allowlist,
            ),
        )
        async with stdio_client(parameters) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                yield session

    async def call(self, session: ClientSession, name: str, arguments: dict) -> dict:
        response = await session.call_tool(name, arguments)
        self.assertFalse(response.isError, [item.model_dump() for item in response.content])
        self.assertIsNotNone(response.structuredContent)
        return response.structuredContent

    async def test_normal_catalog_and_frozen_strict_schemas(self) -> None:
        async with self.session(read_only=False) as session:
            tools = (await session.list_tools()).tools
            self.assertEqual({tool.name for tool in tools}, NORMAL_TOOLS)
            self.assertEqual(len(tools), len(NORMAL_TOOLS))
            self.assertEqual((await session.list_resources()).resources, [])
            self.assertEqual((await session.list_prompts()).prompts, [])
            by_name = {tool.name: tool for tool in tools}
            for tool in tools:
                self.assertFalse(tool.inputSchema.get("additionalProperties", True))
                self.assertNotIn("actor_target_id", tool.inputSchema.get("properties", {}))
            self.assertEqual(
                by_name["search_notes"].inputSchema["properties"]["page_size"]["maximum"],
                20,
            )
            self.assertEqual(
                by_name["get_note"].inputSchema["properties"]["max_chars"]["maximum"],
                12_000,
            )
            create_properties = by_name["create_note"].inputSchema["properties"]
            for name in (
                "main_category",
                "sub_category",
                "ai_visibility",
                "ai_targets",
                "tags",
            ):
                self.assertIn(name, create_properties)
            for name in ("prepend_note", "append_note", "update_note_metadata"):
                self.assertEqual(
                    by_name[name].inputSchema["anyOf"],
                    [
                        {"required": ["expected_version"]},
                        {"required": ["expected_updated_at"]},
                    ],
                )
            directories = await self.call(session, "list_directories", {})
            tags = await self.call(session, "list_tags", {})
            navigation = json.dumps([directories, tags], ensure_ascii=False)
            self.assertIn("CommonVisible", navigation)
            self.assertIn("GaleVisible", navigation)
            self.assertNotIn("ClawbotVisible", navigation)
            self.assertNotIn("HiddenVisible", navigation)
            self.assertNotIn("clawbot-tag", navigation)
            self.assertNotIn("hidden-tag", navigation)
            self.assertIn("main_category", directories["directories"][0])
            self.assertIn("sub_category", directories["directories"][0])
            options = await self.call(session, "list_ai_metadata_options", {})
            self.assertEqual(options["status"], "ok")
            self.assertIn(
                "redacted",
                [item["value"] for item in options["ai_visibility_options"]],
            )
            self.assertIn(
                ("antigravity", "Antigravity"),
                [
                    (item["target_id"], item["display_name"])
                    for item in options["ai_targets"]
                ],
            )

    async def test_gale_tunnel_profile_projects_v3_tools_and_pending_create(self) -> None:
        receipt_service = AiReceiptInboxService(
            SQLiteAiReceiptInboxRepository(
                self.connections,
                self.directories_repository,
                self.tags_repository,
            ),
            notes=self.notes,
            tags=self.tags,
            ai_metadata=self.metadata,
            mcp_operations=SQLiteAiMcpOperationRepository(
                self.connections, self.tags_repository
            ),
        )
        formal_before = len(self.notes.list_active_notes())

        async with self.session(
            read_only=True,
            actor="clawbot",
            script="run_snotes_gale_tunnel_mcp_stdio.sh",
        ) as session:
            tools = (await session.list_tools()).tools
            self.assertEqual({tool.name for tool in tools}, GALE_TUNNEL_TOOLS)
            self.assertEqual(len(tools), len(GALE_TUNNEL_TOOLS))
            by_name = {tool.name: tool for tool in tools}
            for tool in tools:
                self.assertNotIn(
                    "actor_target_id", tool.inputSchema.get("properties", {})
                )
            self.assertNotIn("create_note", by_name)
            self.assertNotIn("append_note", by_name)
            self.assertNotIn("update_note_metadata", by_name)
            self.assertNotIn("get_operation_status", by_name)
            self.assertIn(
                "idempotency_key",
                by_name["create_note_submission"].inputSchema["properties"],
            )

            gale = await self.call(session, "search_notes", {"query": "gale body"})
            clawbot = await self.call(
                session, "search_notes", {"query": "clawbot body"}
            )
            self.assertEqual(gale["total_visible_matches"], 1)
            self.assertEqual(clawbot["total_visible_matches"], 0)

            labels = await self.call(session, "list_reminder_labels", {})
            tables = await self.call(
                session, "list_reminder_tables", {"query": "FICTIONAL_GALE"}
            )
            hidden_table = await self.call(
                session,
                "get_reminder_table",
                {"table_id": self.reminder_ids["hidden_table"]},
            )
            hidden_items = await self.call(
                session,
                "list_reminder_items",
                {"table_id": self.reminder_ids["hidden_table"]},
            )
            hidden_item = await self.call(
                session,
                "get_reminder_item",
                {"item_id": self.reminder_ids["hidden_reminder"]},
            )
            hidden_update = await self.call(
                session,
                "update_reminder_table",
                {
                    "request_id": "gale-tunnel-hidden-table-update",
                    "table_id": self.reminder_ids["hidden_table"],
                    "title": "FICTIONAL_SHOULD_NOT_WRITE",
                },
            )
            visible_items = await self.call(
                session,
                "list_reminder_items",
                {"table_id": self.reminder_ids["visible_table"]},
            )
            created_label = await self.call(
                session,
                "create_reminder_label",
                {
                    "request_id": "gale-tunnel-create-reminder-label",
                    "name": "FICTIONAL_GALE_TUNNEL_LABEL",
                },
            )

            self.assertEqual(labels["status"], "ok")
            self.assertGreaterEqual(len(labels["labels"]), 1)
            self.assertEqual(tables["status"], "ok")
            self.assertEqual(tables["total_visible_matches"], 1)
            self.assertEqual(
                tables["results"][0]["table_id"],
                self.reminder_ids["visible_table"],
            )
            self.assertEqual(hidden_table["status"], "not_found")
            self.assertEqual(hidden_items["status"], "ok")
            self.assertEqual(hidden_items["total_visible_matches"], 0)
            self.assertEqual(hidden_item["status"], "not_found")
            self.assertEqual(hidden_update["status"], "not_found")
            self.assertEqual(visible_items["status"], "ok")
            self.assertEqual(visible_items["total_visible_matches"], 1)
            self.assertEqual(created_label["status"], "created")

            created = await self.call(
                session,
                "create_note_submission",
                {
                    "idempotency_key": "gale-tunnel-fictional-create",
                    "title": "Gale Tunnel fictional pending",
                    "content": "fictional tunnel body",
                    "suggested_directory": "",
                    "suggested_tags": [],
                    "ai_visibility": "",
                    "ai_targets": [],
                    "main_category": "",
                    "sub_category": "",
                    "tags": [],
                },
            )
            duplicate = await self.call(
                session,
                "create_note_submission",
                {
                    "idempotency_key": "gale-tunnel-fictional-create",
                    "title": "Gale Tunnel fictional pending",
                    "content": "fictional tunnel body",
                    "suggested_directory": "",
                    "suggested_tags": [],
                    "ai_visibility": "",
                    "ai_targets": [],
                    "main_category": "",
                    "sub_category": "",
                    "tags": [],
                },
            )

        self.assertEqual(created["status"], "pending_confirmation")
        self.assertFalse(created["changed_formal_note"])
        self.assertTrue(created["pending_confirmation"])
        self.assertEqual(duplicate["status"], "already_processed")
        self.assertEqual(duplicate["previous_status"], "pending_confirmation")
        self.assertEqual(len(self.notes.list_active_notes()), formal_before)
        pending = receipt_service.list_pending_receipts()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].id, created["proposal_id"])
        self.assertEqual(pending[0].source, "Gale")
        self.assertEqual(pending[0].actor_target_id, "gale")
        with self.connections.read() as connection:
            actor_row = connection.execute(
                "SELECT actor_target_id FROM ai_operations WHERE request_id = ?",
                ("gale-tunnel-create-reminder-label",),
            ).fetchone()
        self.assertIsNotNone(actor_row)
        self.assertEqual(actor_row["actor_target_id"], "gale")
        self.assertEqual(pending[0].title, "Gale Tunnel fictional pending")
        self.assertEqual(pending[0].body, "fictional tunnel body")
        self.assertEqual(pending[0].tags, "")
        self.assertEqual(pending[0].ai_visibility, "full")
        self.assertEqual(pending[0].ai_targets, "gale")

    async def test_gale_tunnel_submission_metadata_enters_existing_pending(self) -> None:
        dynamic_main = self.directories_repository.create("Gale Tunnel 主分类")
        self.directories_repository.create("Gale Tunnel 子分类", dynamic_main.id)
        receipt_service = AiReceiptInboxService(
            SQLiteAiReceiptInboxRepository(
                self.connections,
                self.directories_repository,
                self.tags_repository,
            ),
            notes=self.notes,
            tags=self.tags,
            ai_metadata=self.metadata,
            mcp_operations=SQLiteAiMcpOperationRepository(
                self.connections, self.tags_repository
            ),
        )
        formal_before = len(self.notes.list_active_notes())

        async with self.session(
            read_only=True,
            actor="clawbot",
            script="run_snotes_gale_tunnel_mcp_stdio.sh",
        ) as session:
            created = await self.call(
                session,
                "create_note_submission",
                {
                    "idempotency_key": "gale-tunnel-metadata-create",
                    "title": "Gale Tunnel metadata pending",
                    "content": "metadata body",
                    "suggested_tags": [],
                    "main_category": "Gale Tunnel 主分类",
                    "sub_category": "Gale Tunnel 子分类",
                    "tags": ["metadata-gale", "#Second"],
                    "ai_visibility": "redacted",
                    "ai_targets": ["gale"],
                },
            )

        self.assertEqual(created["status"], "pending_confirmation")
        self.assertEqual(len(self.notes.list_active_notes()), formal_before)
        pending = receipt_service.list_pending_receipts()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].id, created["proposal_id"])
        self.assertEqual(pending[0].actor_target_id, "gale")
        self.assertEqual(pending[0].main_category, "Gale Tunnel 主分类")
        self.assertEqual(pending[0].sub_category, "Gale Tunnel 子分类")
        self.assertEqual(pending[0].tags, "metadata-gale, Second")
        self.assertEqual(pending[0].ai_visibility, "redacted")
        self.assertEqual(pending[0].ai_targets, "gale")

    async def test_read_only_catalog_is_only_two_and_write_is_unknown(self) -> None:
        async with self.session(read_only=True) as session:
            tools = (await session.list_tools()).tools
            self.assertEqual({tool.name for tool in tools}, READ_ONLY_TOOLS)
            self.assertEqual(len(tools), len(READ_ONLY_TOOLS))
            blocked = await session.call_tool(
                "create_note",
                {"request_id": "blocked", "title": "No", "content": "No"},
            )
            self.assertTrue(blocked.isError)
            self.assertIn(
                "unknown tool: create_note",
                json.dumps(
                    [item.model_dump() for item in blocked.content],
                    ensure_ascii=False,
                ).lower(),
            )

    async def test_create_note_accepts_complete_dynamic_metadata(self) -> None:
        dynamic_main = self.directories_repository.create("运行时主分类")
        self.directories_repository.create("运行时子分类", dynamic_main.id)

        async with self.session(read_only=False) as session:
            options = await self.call(session, "list_ai_metadata_options", {})
            target_id = next(
                item["target_id"]
                for item in options["ai_targets"]
                if item["target_id"] == "antigravity"
            )
            created = await self.call(
                session,
                "create_note",
                {
                    "request_id": "normal-complete-metadata",
                    "title": "Runtime metadata create",
                    "content": "complete metadata body",
                    "main_category": "运行时主分类",
                    "sub_category": "运行时子分类",
                    "tags": ["RuntimeTag", "#runtimeTag", "second"],
                    "ai_visibility": "redacted",
                    "ai_targets": ["gale", target_id],
                },
            )
            readback = await self.call(
                session,
                "get_note",
                {"note_id": created["note_id"]},
            )

        self.assertEqual(created["status"], "created")
        self.assertEqual(readback["status"], "ok")
        self.assertEqual(readback["main_category"], "运行时主分类")
        self.assertEqual(readback["sub_category"], "运行时子分类")
        self.assertEqual(readback["tags"], ["runtimetag", "second"])
        self.assertEqual(readback["ai_visibility"], "redacted")
        self.assertEqual(readback["ai_targets"], ["gale", "antigravity"])
        note = self.notes.get_note(created["note_id"])
        self.assertEqual(note.location, NoteLocation("运行时主分类", "运行时子分类"))
        self.assertEqual(self.tags.get_note_tags(note.id), ["runtimetag", "second"])
        metadata = self.metadata.get_note_metadata(note.id)
        self.assertEqual(metadata.visibility, "redacted")
        self.assertEqual(metadata.target_ids, ("gale", "antigravity"))

    async def test_create_note_rejects_deleted_or_mismatched_category(self) -> None:
        deleted_main = self.directories_repository.create("已删除测试主分类")
        self.directories_repository.create("已删除测试子分类", deleted_main.id)
        self.directories_repository.soft_delete_tree(deleted_main.id)

        async with self.session(read_only=False) as session:
            deleted = await self.call(
                session,
                "create_note",
                {
                    "request_id": "normal-deleted-category",
                    "title": "Deleted category create",
                    "content": "body",
                    "main_category": "已删除测试主分类",
                    "sub_category": "已删除测试子分类",
                },
            )
            mismatched = await self.call(
                session,
                "create_note",
                {
                    "request_id": "normal-mismatched-category",
                    "title": "Mismatch category create",
                    "content": "body",
                    "main_category": "项目",
                    "sub_category": "GaleVisible",
                    "directory_id": self.notes.get_note(
                        self.ids["common"]
                    ).directory_id,
                },
            )

        self.assertEqual(deleted["status"], "rejected")
        self.assertEqual(mismatched["status"], "rejected")

    async def test_antigravity_can_query_create_and_readback_metadata(self) -> None:
        dynamic_main = self.directories_repository.create("Antigravity 运行时主分类")
        self.directories_repository.create("Antigravity 运行时子分类", dynamic_main.id)

        async with self.session(read_only=False, actor="antigravity") as session:
            directories = await self.call(session, "list_directories", {})
            tags = await self.call(session, "list_tags", {})
            options = await self.call(session, "list_ai_metadata_options", {})
            self.assertIn(
                "CommonVisible",
                json.dumps(directories, ensure_ascii=False),
            )
            self.assertIn("common-tag", json.dumps(tags, ensure_ascii=False))
            self.assertIn(
                "antigravity",
                [item["target_id"] for item in options["ai_targets"]],
            )
            created = await self.call(
                session,
                "create_note",
                {
                    "request_id": "antigravity-complete-metadata",
                    "title": "Antigravity metadata create",
                    "content": "antigravity metadata body",
                    "main_category": "Antigravity 运行时主分类",
                    "sub_category": "Antigravity 运行时子分类",
                    "tags": ["antigravity-runtime"],
                    "ai_visibility": "full",
                    "ai_targets": ["antigravity"],
                },
            )
            readback = await self.call(
                session,
                "get_note",
                {"note_id": created["note_id"]},
            )

        self.assertEqual(created["status"], "created")
        self.assertEqual(readback["main_category"], "Antigravity 运行时主分类")
        self.assertEqual(readback["sub_category"], "Antigravity 运行时子分类")
        self.assertEqual(readback["tags"], ["antigravity-runtime"])
        self.assertEqual(readback["ai_visibility"], "full")
        self.assertEqual(readback["ai_targets"], ["antigravity"])

    async def test_clawbot_permissions_redaction_and_unified_search(self) -> None:
        async with self.session(read_only=True) as session:
            common = await self.call(
                session, "search_notes", {"query": "common body"}
            )
            clawbot = await self.call(
                session, "search_notes", {"query": "clawbot visible"}
            )
            gale = await self.call(
                session, "search_notes", {"query": "gale body"}
            )
            hidden = await self.call(
                session, "search_notes", {"query": "hidden body"}
            )
            raw = await self.call(
                session, "search_notes", {"query": "FictionalSecret"}
            )
            safe = await self.call(
                session, "search_notes", {"query": "MASKED clawbot"}
            )
            self.assertEqual(common["total_visible_matches"], 1)
            self.assertEqual(clawbot["total_visible_matches"], 1)
            self.assertEqual(gale["total_visible_matches"], 0)
            self.assertEqual(hidden["total_visible_matches"], 0)
            self.assertEqual(raw["total_visible_matches"], 0)
            self.assertEqual(safe["total_visible_matches"], 1)

            common_note = await self.call(
                session, "get_note", {"note_id": self.ids["common"]}
            )
            clawbot_note = await self.call(
                session, "get_note", {"note_id": self.ids["clawbot"]}
            )
            gale_note = await self.call(
                session, "get_note", {"note_id": self.ids["gale"]}
            )
            hidden_note = await self.call(
                session, "get_note", {"note_id": self.ids["hidden"]}
            )
            redacted = await self.call(
                session, "get_note", {"note_id": self.ids["redacted"]}
            )
            self.assertEqual(common_note["status"], "ok")
            self.assertEqual(clawbot_note["status"], "ok")
            self.assertEqual(gale_note["status"], "not_found")
            self.assertEqual(hidden_note["status"], "not_found")
            self.assertEqual(hidden_note["ai_visibility"], "")
            self.assertEqual(hidden_note["ai_targets"], [])
            text = json.dumps(redacted, ensure_ascii=False)
            self.assertNotIn("FictionalSecret", text)
            self.assertIn("[MASKED]", text)

    async def test_clawde_create_pending_accept_discard_and_append_pending(self) -> None:
        with self.connections.write() as connection:
            connection.execute(
                """
                INSERT INTO app_settings(setting_key, setting_value, updated_at)
                VALUES ('ai_create_requires_confirmation', '0', '2026-01-01T00:00:00+08:00')
                """
            )
        receipt_service = AiReceiptInboxService(
            SQLiteAiReceiptInboxRepository(
                self.connections,
                self.directories_repository,
                self.tags_repository,
            ),
            notes=self.notes,
            tags=self.tags,
            ai_metadata=self.metadata,
            mcp_operations=SQLiteAiMcpOperationRepository(
                self.connections, self.tags_repository
            ),
        )
        formal_before = len(self.notes.list_active_notes())

        async with self.session(
            read_only=False,
            actor="clawbot",
            tool_allowlist=CLAWDE_TOOLS,
        ) as session:
            tools = (await session.list_tools()).tools
            self.assertEqual({tool.name for tool in tools}, CLAWDE_TOOLS)
            self.assertEqual(len(tools), len(CLAWDE_TOOLS))
            for name in CLAWDE_DISALLOWED_TOOLS:
                blocked = await session.call_tool(name, {})
                self.assertTrue(blocked.isError)
                self.assertIn(
                    f"unknown tool: {name}",
                    json.dumps(
                        [item.model_dump() for item in blocked.content],
                        ensure_ascii=False,
                    ).lower(),
                )

            clawde_directories = await self.call(session, "list_directories", {})
            clawde_tags = await self.call(session, "list_tags", {})
            clawde_options = await self.call(
                session, "list_ai_metadata_options", {}
            )
            self.assertIn(
                "ClawbotVisible",
                json.dumps(clawde_directories, ensure_ascii=False),
            )
            self.assertIn(
                "clawbot-tag",
                json.dumps(clawde_tags, ensure_ascii=False),
            )
            self.assertIn(
                "clawbot",
                [item["target_id"] for item in clawde_options["ai_targets"]],
            )

            pending_create = await self.call(
                session,
                "create_note",
                {
                    "request_id": "clawde-create-fictional",
                    "title": "Clawde fictional pending",
                    "content": "fictional base body",
                    "tags": ["fictional-clawde"],
                },
            )
            self.assertEqual(pending_create["status"], "pending_confirmation")
            self.assertFalse(pending_create["changed_formal_note"])
            self.assertTrue(pending_create["pending_confirmation"])
            self.assertIsNotNone(pending_create["proposal_id"])
            self.assertEqual(len(self.notes.list_active_notes()), formal_before)

            pending = receipt_service.list_pending_receipts()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].id, pending_create["proposal_id"])
            self.assertEqual(pending[0].source, "Clawbot")
            self.assertEqual(pending[0].actor_target_id, "clawbot")
            self.assertEqual(pending[0].title, "Clawde fictional pending")
            self.assertEqual(pending[0].body, "fictional base body")

            target_state = receipt_service.receipt_target_state(pending[0])
            accepted = receipt_service.accept_receipt(
                pending[0].id,
                AiReceiptAcceptFields(
                    title=pending[0].title,
                    body=pending[0].body,
                    main_category=pending[0].main_category,
                    sub_category=pending[0].sub_category,
                    tags=pending[0].tags,
                    visibility=pending[0].ai_visibility,
                    target_ids=target_state.target_ids,
                ),
            )
            self.assertTrue(accepted.created)
            self.assertEqual(len(self.notes.list_active_notes()), formal_before + 1)
            self.assertEqual(accepted.note.title, "Clawde fictional pending")
            self.assertEqual(accepted.note.content_html, "<p>fictional base body</p>")
            self.assertEqual(receipt_service.list_pending_receipts(), [])

            found = await self.call(
                session,
                "search_notes",
                {"query": "Clawde fictional pending"},
            )
            self.assertEqual(found["total_visible_matches"], 1)
            self.assertEqual(found["results"][0]["note_id"], accepted.note.id)
            initial = await self.call(
                session, "get_note", {"note_id": accepted.note.id}
            )
            self.assertEqual(initial["content"], "fictional base body")
            self.assertEqual(initial["main_category"], pending[0].main_category)
            self.assertEqual(initial["sub_category"], pending[0].sub_category)
            self.assertEqual(initial["tags"], ["fictional-clawde"])
            self.assertEqual(initial["ai_visibility"], "full")
            self.assertEqual(initial["ai_targets"], ["clawbot"])

            pending_discard = await self.call(
                session,
                "create_note",
                {
                    "request_id": "clawde-create-discard-fictional",
                    "title": "Clawde fictional discard",
                    "content": "discard body",
                },
            )
            self.assertEqual(pending_discard["status"], "pending_confirmation")
            self.assertEqual(len(self.notes.list_active_notes()), formal_before + 1)
            discard_receipt = receipt_service.get_receipt(
                pending_discard["proposal_id"]
            )
            discarded = receipt_service.discard_receipt(discard_receipt.id)
            self.assertTrue(discarded.changed)
            self.assertEqual(discarded.receipt.status, "discarded")
            self.assertEqual(receipt_service.list_pending_receipts(), [])
            self.assertEqual(len(self.notes.list_active_notes()), formal_before + 1)

            pending_append = await self.call(
                session,
                "append_note",
                {
                    "request_id": "clawde-append-fictional",
                    "note_id": accepted.note.id,
                    "content": "fictional appended body",
                    "expected_version": initial["version"],
                },
            )
            self.assertEqual(pending_append["status"], "pending_confirmation")
            self.assertFalse(pending_append["changed_formal_note"])
            self.assertTrue(pending_append["pending_confirmation"])
            after = await self.call(
                session, "get_note", {"note_id": accepted.note.id}
            )
            self.assertEqual(after["content"], "fictional base body")
            pending = receipt_service.list_pending_receipts()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].operation_type, "append")
            self.assertEqual(pending[0].actor_target_id, "clawbot")
            append_receipt = pending[0]
            before_append_note = self.notes.get_note(accepted.note.id)
            before_append_metadata = self.metadata.get_note_metadata(accepted.note.id)
            before_append_tags = self.tags.get_note_tags(accepted.note.id)
            accepted_append = receipt_service.accept_receipt(
                append_receipt.id,
                AiReceiptAcceptFields(
                    title=append_receipt.title,
                    body=append_receipt.body,
                    main_category=append_receipt.main_category,
                    sub_category=append_receipt.sub_category,
                    tags=append_receipt.tags,
                    visibility=append_receipt.ai_visibility,
                    target_ids=("clawbot",),
                ),
            )
            self.assertFalse(accepted_append.created)
            self.assertEqual(accepted_append.note.id, accepted.note.id)
            self.assertEqual(len(self.notes.list_active_notes()), formal_before + 1)
            accepted_text = await self.call(
                session, "get_note", {"note_id": accepted.note.id}
            )
            self.assertIn("fictional base body", accepted_text["content"])
            self.assertTrue(
                accepted_text["content"].endswith("fictional appended body")
            )
            self.assertEqual(
                accepted_text["content"].count("fictional appended body"), 1
            )
            self.assertEqual(receipt_service.list_pending_receipts(), [])
            self.assertEqual(
                receipt_service.get_receipt(append_receipt.id).status, "accepted"
            )
            after_append_note = self.notes.get_note(accepted.note.id)
            self.assertEqual(after_append_note.title, before_append_note.title)
            self.assertEqual(after_append_note.location, before_append_note.location)
            self.assertEqual(
                after_append_note.directory_id, before_append_note.directory_id
            )
            after_append_metadata = self.metadata.get_note_metadata(
                accepted.note.id
            )
            self.assertEqual(
                after_append_metadata.visibility, before_append_metadata.visibility
            )
            self.assertEqual(
                after_append_metadata.target_ids, before_append_metadata.target_ids
            )
            self.assertEqual(
                self.tags.get_note_tags(accepted.note.id), before_append_tags
            )
            with self.connections.read() as connection:
                history = connection.execute(
                    """
                    SELECT actor_target_id, modification_type, handling_mode
                    FROM ai_note_history WHERE request_id = ?
                    """,
                    ("clawde-append-fictional",),
                ).fetchall()
                operation = connection.execute(
                    "SELECT status FROM ai_operations WHERE request_id = ?",
                    ("clawde-append-fictional",),
                ).fetchone()
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["actor_target_id"], "clawbot")
            self.assertEqual(history[0]["modification_type"], "append")
            self.assertEqual(history[0]["handling_mode"], "human_approved")
            self.assertEqual(operation["status"], "updated")

            before_discard = accepted_text["content"]
            discard_append = await self.call(
                session,
                "append_note",
                {
                    "request_id": "clawde-append-discard-fictional",
                    "note_id": accepted.note.id,
                    "content": "discarded appended body",
                    "expected_version": accepted_text["version"],
                },
            )
            self.assertEqual(discard_append["status"], "pending_confirmation")
            discard_append_receipt = receipt_service.list_pending_receipts()[0]
            receipt_service.discard_receipt(discard_append_receipt.id)
            after_discard = await self.call(
                session, "get_note", {"note_id": accepted.note.id}
            )
            self.assertEqual(after_discard["content"], before_discard)
            self.assertNotIn("discarded appended body", after_discard["content"])

            stale_append = await self.call(
                session,
                "append_note",
                {
                    "request_id": "clawde-append-stale-fictional",
                    "note_id": accepted.note.id,
                    "content": "stale appended body",
                    "expected_version": after_discard["version"],
                },
            )
            self.assertEqual(stale_append["status"], "pending_confirmation")
            stale_receipt = receipt_service.list_pending_receipts()[0]
            current_note = self.notes.get_note(accepted.note.id)
            self.notes.save_note(
                current_note.id,
                title=current_note.title,
                content_html=current_note.content_html + "<p>external change</p>",
                location=current_note.location,
                expected_revision=current_note.revision,
            )
            with self.assertRaisesRegex(ValueError, "笔记已被更新"):
                receipt_service.accept_receipt(
                    stale_receipt.id,
                    AiReceiptAcceptFields(
                        title=stale_receipt.title,
                        body=stale_receipt.body,
                        main_category=stale_receipt.main_category,
                        sub_category=stale_receipt.sub_category,
                        tags=stale_receipt.tags,
                        visibility=stale_receipt.ai_visibility,
                        target_ids=("clawbot",),
                    ),
                )
            self.assertEqual(
                receipt_service.get_receipt(stale_receipt.id).status, "pending"
            )
            after_conflict = await self.call(
                session, "get_note", {"note_id": accepted.note.id}
            )
            self.assertIn("external change", after_conflict["content"])
            self.assertNotIn("stale appended body", after_conflict["content"])

        with self.connections.read() as connection:
            audit_rows = connection.execute(
                """
                SELECT actor_target_id, operation_type, summary
                FROM ai_call_logs
                WHERE request_id IN (?, ?, ?)
                """,
                (
                    "clawde-create-fictional",
                    "clawde-create-discard-fictional",
                    "clawde-append-fictional",
                ),
            ).fetchall()
        self.assertEqual(len(audit_rows), 3)
        self.assertEqual({row["actor_target_id"] for row in audit_rows}, {"clawbot"})
        self.assertEqual(
            {row["operation_type"] for row in audit_rows}, {"create", "append"}
        )
        self.assertEqual({row["summary"] for row in audit_rows}, {""})

    async def test_pending_create_preserves_ai_proposal_but_accept_uses_s_fields(self) -> None:
        proposal_main = self.directories_repository.create("AI 提议主分类")
        self.directories_repository.create("AI 提议子分类", proposal_main.id)
        final_main = self.directories_repository.create("S 最终主分类")
        self.directories_repository.create("S 最终子分类", final_main.id)
        receipt_service = AiReceiptInboxService(
            SQLiteAiReceiptInboxRepository(
                self.connections,
                self.directories_repository,
                self.tags_repository,
            ),
            notes=self.notes,
            tags=self.tags,
            ai_metadata=self.metadata,
            mcp_operations=SQLiteAiMcpOperationRepository(
                self.connections, self.tags_repository
            ),
        )

        async with self.session(
            read_only=False,
            actor="clawbot",
            tool_allowlist=CLAWDE_TOOLS,
        ) as session:
            pending_create = await self.call(
                session,
                "create_note",
                {
                    "request_id": "clawde-complete-metadata-pending",
                    "title": "AI proposal title",
                    "content": "AI proposal body",
                    "main_category": "AI 提议主分类",
                    "sub_category": "AI 提议子分类",
                    "tags": ["ai-proposal"],
                    "ai_visibility": "full",
                    "ai_targets": ["clawbot"],
                },
            )

        self.assertEqual(pending_create["status"], "pending_confirmation")
        receipt = receipt_service.get_receipt(pending_create["proposal_id"])
        self.assertEqual(receipt.main_category, "AI 提议主分类")
        self.assertEqual(receipt.sub_category, "AI 提议子分类")
        self.assertEqual(receipt.tags, "ai-proposal")
        self.assertEqual(receipt.ai_visibility, "full")
        self.assertEqual(receipt.ai_targets, "clawbot")

        accepted = receipt_service.accept_receipt(
            receipt.id,
            AiReceiptAcceptFields(
                title="S final title",
                body="S final body",
                main_category="S 最终主分类",
                sub_category="S 最终子分类",
                tags="s-final",
                visibility="redacted",
                target_ids=("antigravity",),
            ),
        )

        self.assertEqual(accepted.note.title, "S final title")
        self.assertEqual(
            accepted.note.location,
            NoteLocation("S 最终主分类", "S 最终子分类"),
        )
        self.assertEqual(self.tags.get_note_tags(accepted.note.id), ["s-final"])
        metadata = self.metadata.get_note_metadata(accepted.note.id)
        self.assertEqual(metadata.visibility, "redacted")
        self.assertEqual(metadata.target_ids, ("antigravity",))
        async with self.session(
            read_only=False,
            actor="antigravity",
        ) as session:
            readback = await self.call(
                session,
                "get_note",
                {"note_id": accepted.note.id},
            )
        self.assertEqual(readback["title"], "S final title")
        self.assertEqual(readback["main_category"], "S 最终主分类")
        self.assertEqual(readback["sub_category"], "S 最终子分类")
        self.assertEqual(readback["tags"], ["s-final"])
        self.assertEqual(readback["ai_visibility"], "redacted")
        self.assertEqual(readback["ai_targets"], ["antigravity"])

    async def test_missing_rules_skip_only_redacted_and_audit_is_metadata_only(self) -> None:
        self.rules_path.unlink()
        async with self.session(read_only=True) as session:
            full = await self.call(
                session, "search_notes", {"query": "common body"}
            )
            redacted = await self.call(
                session, "search_notes", {"query": "MASKED"}
            )
            failed_get = await self.call(
                session, "get_note", {"note_id": self.ids["redacted"]}
            )
        self.assertEqual(full["total_visible_matches"], 1)
        self.assertIn("脱敏规则不可用", full["reason"])
        self.assertEqual(redacted["total_visible_matches"], 0)
        self.assertEqual(failed_get["status"], "failed")
        with self.connections.read() as connection:
            rows = connection.execute(
                """
                SELECT summary, operation_type, actor_target_id, request_id,
                       note_id, result_count, status, failure_reason
                FROM ai_call_logs
                """
            ).fetchall()
        self.assertGreaterEqual(len(rows), 3)
        self.assertEqual({row["actor_target_id"] for row in rows}, {"clawbot"})
        self.assertEqual({row["summary"] for row in rows}, {""})
        self.assertNotIn(
            "common body",
            json.dumps([dict(row) for row in rows], ensure_ascii=False),
        )

    async def test_normal_create_get_append_and_operation_status_smoke(self) -> None:
        with self.connections.write() as connection:
            connection.execute(
                """
                INSERT INTO app_settings(setting_key, setting_value, updated_at)
                VALUES ('ai_modify_requires_confirmation', '0', '2026-01-01T00:00:00+08:00')
                """
            )
        async with self.session(read_only=False, actor="gale") as session:
            created = await self.call(
                session,
                "create_note",
                {
                    "request_id": "package5-create",
                    "title": "Package5 fictional",
                    "content": "base",
                },
            )
            self.assertEqual(created["status"], "created")
            note = await self.call(
                session, "get_note", {"note_id": created["note_id"]}
            )
            appended = await self.call(
                session,
                "append_note",
                {
                    "request_id": "package5-append",
                    "note_id": created["note_id"],
                    "content": "-end",
                    "expected_version": note["version"],
                },
            )
            self.assertEqual(appended["status"], "updated")
            after = await self.call(
                session, "get_note", {"note_id": created["note_id"]}
            )
            self.assertEqual(after["content"], "base\n-end")
            prepended = await self.call(
                session,
                "prepend_note",
                {
                    "request_id": "package5-prepend",
                    "note_id": created["note_id"],
                    "content": "start-",
                    "expected_version": after["version"],
                },
            )
            self.assertEqual(prepended["status"], "updated")
            after_prepend = await self.call(
                session, "get_note", {"note_id": created["note_id"]}
            )
            self.assertEqual(after_prepend["content"], "start-\nbase\n-end")
            conflict = await self.call(
                session,
                "append_note",
                {
                    "request_id": "package5-conflict",
                    "note_id": created["note_id"],
                    "content": "must not appear",
                    "expected_version": after["version"],
                },
            )
            self.assertEqual(conflict["status"], "conflict")
            metadata = await self.call(
                session,
                "update_note_metadata",
                {
                    "request_id": "package5-metadata",
                    "note_id": created["note_id"],
                    "expected_version": after_prepend["version"],
                    "add_tags": ["two"],
                },
            )
            self.assertEqual(metadata["status"], "updated")
            final_note = await self.call(
                session, "get_note", {"note_id": created["note_id"]}
            )
            self.assertEqual(final_note["tags"], ["two"])
            self.assertNotIn("must not appear", final_note["content"])
            duplicate = await self.call(
                session,
                "create_note",
                {
                    "request_id": "package5-create",
                    "title": "Must not duplicate",
                    "content": "ignored",
                },
            )
            self.assertEqual(duplicate["status"], "already_processed")
            status = await self.call(
                session,
                "get_operation_status",
                {"request_id": "package5-create"},
            )
            self.assertEqual(status["status"], "created")
            self.assertEqual(status["operation_type"], "create")

    async def test_pending_confirmation_and_duplicate_receipt_semantics(self) -> None:
        with self.connections.write() as connection:
            connection.execute(
                """
                INSERT INTO app_settings(setting_key, setting_value, updated_at)
                VALUES ('ai_create_requires_confirmation', '1', '2026-01-01T00:00:00+08:00')
                """
            )
        async with self.session(read_only=False, actor="gale") as session:
            pending_create = await self.call(
                session,
                "create_note",
                {
                    "request_id": "package5-pending-create",
                    "title": "Pending fictional",
                    "content": "pending body",
                },
            )
            self.assertEqual(pending_create["status"], "pending_confirmation")
            self.assertFalse(pending_create["changed_formal_note"])
            self.assertTrue(pending_create["pending_confirmation"])
            self.assertIsNotNone(pending_create["proposal_id"])
            duplicate = await self.call(
                session,
                "create_note",
                {
                    "request_id": "package5-pending-create",
                    "title": "Ignored",
                    "content": "ignored",
                },
            )
            self.assertEqual(duplicate["status"], "already_processed")
            self.assertEqual(duplicate["previous_status"], "pending_confirmation")

            target = await self.call(
                session, "get_note", {"note_id": self.ids["gale"]}
            )
            pending_append = await self.call(
                session,
                "append_note",
                {
                    "request_id": "package5-pending-append",
                    "note_id": self.ids["gale"],
                    "content": "not formal yet",
                    "expected_version": target["version"],
                },
            )
            self.assertEqual(pending_append["status"], "pending_confirmation")
            unchanged = await self.call(
                session, "get_note", {"note_id": self.ids["gale"]}
            )
            self.assertNotIn("not formal yet", unchanged["content"])


if __name__ == "__main__":
    unittest.main()
