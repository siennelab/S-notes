from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from src.database import (
    _connect,
    _ensure_directory_path,
    _plain_text_to_html,
    _set_note_ai_targets_with_connection,
    _set_note_tags_with_connection,
    initialize_database,
)
from src.services import ai_access_service as service
from src.time_utils import now_shanghai_iso


ROOT = Path(__file__).resolve().parents[1]
MCP_PYTHON = Path("/Users/s/.venvs/snotes-mcp/bin/python")
APPROVED_TOOLS = {
    "search_notes",
    "get_note",
    "list_directories",
    "list_tags",
    "create_note",
    "prepend_note",
    "append_note",
    "update_note_metadata",
    "get_operation_status",
}


class StderrCapture:
    def __init__(self) -> None:
        self._file = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
        self._cached = ""

    def fileno(self) -> int:
        return self._file.fileno()

    def write(self, value: str) -> int:
        return self._file.write(value)

    def flush(self) -> None:
        self._file.flush()

    def getvalue(self) -> str:
        if self._file.closed:
            return self._cached
        self._file.flush()
        position = self._file.tell()
        self._file.seek(0)
        value = self._file.read()
        self._file.seek(position)
        return value

    def close(self) -> None:
        self._cached = self.getvalue()
        self._file.close()


class McpProtocolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.database_path = self.root / "s_notes.db"
        self.rules_path = self.root / "redaction_rules.json"
        self.rules_path.write_text(
            json.dumps({"TopSecret": "[MASKED]"}, ensure_ascii=False),
            encoding="utf-8",
        )
        initialize_database(self.database_path)
        self.ids: dict[str, int] = {}
        self._seed_attack_fixture()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _env(self, **overrides: str) -> dict[str, str]:
        env = {
            "HOME": str(self.home),
            "PYTHONPYCACHEPREFIX": str(self.root / "pycache"),
            "SNOTES_MCP_DATABASE_PATH": str(self.database_path),
            "SNOTES_MCP_REDACTION_RULES_PATH": str(self.rules_path),
            "SNOTES_MCP_ACTOR_TARGET_ID": "gale",
        }
        env.update(overrides)
        return env

    @asynccontextmanager
    async def session(self, **env_overrides: str):
        stderr = StderrCapture()
        parameters = StdioServerParameters(
            command=str(ROOT / "scripts" / "run_snotes_mcp_stdio.sh"),
            args=[],
            cwd=str(ROOT),
            env=self._env(**env_overrides),
        )
        try:
            async with stdio_client(parameters, errlog=stderr) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    yield session, stderr
        finally:
            stderr.close()

    async def call(self, session: ClientSession, name: str, arguments: dict) -> dict:
        response = await session.call_tool(name, arguments)
        self.assertFalse(response.isError, [item.model_dump() for item in response.content])
        self.assertIsNotNone(response.structuredContent)
        return response.structuredContent

    def create_note(
        self,
        key: str,
        *,
        title: str,
        body: str,
        visibility: str,
        targets: list[str],
        tags: list[str],
        directory: tuple[str, str, str],
        deleted: bool = False,
    ) -> int:
        timestamp = now_shanghai_iso()
        with _connect(self.database_path) as connection:
            directory_id = _ensure_directory_path(connection, *directory)
            content_html = _plain_text_to_html(body)
            cursor = connection.execute(
                """
                INSERT INTO notes (
                    directory_id, title, body_rich_text, content_html,
                    main_category, sub_category, sub_sub_category, tags,
                    ai_visibility, is_pinned, is_deleted, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, 0, ?, ?, ?)
                """,
                (
                    directory_id, title, content_html, content_html,
                    directory[0], directory[1], directory[2], visibility,
                    1 if deleted else 0, timestamp, timestamp,
                ),
            )
            note_id = int(cursor.lastrowid)
            _set_note_tags_with_connection(connection, note_id, tags, timestamp)
            _set_note_ai_targets_with_connection(connection, note_id, targets, timestamp)
        self.ids[key] = note_id
        return note_id

    def _seed_attack_fixture(self) -> None:
        self.create_note(
            "common", title="Common visible", body="common-body-needle",
            visibility="full", targets=["common"], tags=["common-tag"],
            directory=("项目", "CommonVisible", "无"),
        )
        self.create_note(
            "gale", title="Gale visible", body="gale-body-needle",
            visibility="full", targets=["gale"], tags=["gale-tag"],
            directory=("项目", "GaleVisible", "无"),
        )
        self.create_note(
            "hidden", title="Hidden unique 7319", body="hidden-body-7319",
            visibility="hidden", targets=["gale"], tags=["hidden-tag-7319"],
            directory=("项目", "HiddenDirectory7319", "无"),
        )
        self.create_note(
            "codex", title="Codex unique 8420", body="codex-body-8420",
            visibility="full", targets=["codex"], tags=["codex-tag-8420"],
            directory=("项目", "CodexDirectory8420", "无"),
        )
        self.create_note(
            "redacted", title="TopSecret redacted title", body="TopSecret redacted-body",
            visibility="redacted", targets=["gale"], tags=["TopSecret-tag"],
            directory=("项目", "TopSecret-directory", "无"),
        )
        self.create_note(
            "unknown_visibility", title="Broken visibility 9531", body="broken-body-9531",
            visibility="future-value", targets=["gale"], tags=["broken-tag-9531"],
            directory=("项目", "BrokenDirectory9531", "无"),
        )
        with _connect(self.database_path) as connection:
            timestamp = now_shanghai_iso()
            connection.execute(
                """
                INSERT INTO ai_targets(
                    target_id, display_name, slug, is_system, is_enabled, created_at, updated_at
                ) VALUES ('mystery', 'Mystery', 'mystery', 0, 1, ?, ?)
                """,
                (timestamp, timestamp),
            )
        self.create_note(
            "unknown_target", title="Mystery target 1642", body="mystery-body-1642",
            visibility="full", targets=["mystery"], tags=["mystery-tag-1642"],
            directory=("项目", "MysteryDirectory1642", "无"),
        )
        self.create_note(
            "deleted", title="Deleted unique 2753", body="deleted-body-2753",
            visibility="full", targets=["gale"], tags=["deleted-tag-2753"],
            directory=("项目", "DeletedDirectory2753", "无"), deleted=True,
        )
        self.create_note(
            "long", title="Long visible", body="L" * 13_500,
            visibility="full", targets=["gale"], tags=["long-tag"],
            directory=("项目", "LongVisible", "无"),
        )

    async def test_initialize_list_tools_and_strict_approved_schemas(self) -> None:
        async with self.session() as (session, stderr):
            tools = (await session.list_tools()).tools
            self.assertEqual({tool.name for tool in tools}, APPROVED_TOOLS)
            self.assertEqual((await session.list_resources()).resources, [])
            self.assertEqual((await session.list_prompts()).prompts, [])
            for tool in tools:
                self.assertFalse(tool.inputSchema.get("additionalProperties", True), tool.name)
                self.assertNotIn("actor_target_id", tool.inputSchema.get("properties", {}))
                self.assertNotIn("database_path", tool.inputSchema.get("properties", {}))
            by_name = {tool.name: tool for tool in tools}
            self.assertEqual(
                by_name["search_notes"].inputSchema["properties"]["page_size"]["maximum"],
                20,
            )
            self.assertEqual(
                by_name["get_note"].inputSchema["properties"]["max_chars"]["maximum"],
                12_000,
            )
            for tool_name in ("prepend_note", "append_note", "update_note_metadata"):
                schema = by_name[tool_name].inputSchema
                self.assertIn("expected_version", schema["properties"])
                self.assertIn("expected_updated_at", schema["properties"])
                self.assertEqual(
                    schema["anyOf"],
                    [
                        {"required": ["expected_version"]},
                        {"required": ["expected_updated_at"]},
                    ],
                )
            oversized_page = await session.call_tool(
                "search_notes", {"query": "", "page_size": 21}
            )
            oversized_read = await session.call_tool(
                "get_note", {"note_id": self.ids["gale"], "max_chars": 12_001}
            )
            self.assertTrue(oversized_page.isError)
            self.assertTrue(oversized_read.isError)
        self.assertNotIn("Traceback", stderr.getvalue())

    async def test_get_note_version_credentials_round_trip_and_then_conflict(self) -> None:
        service.set_ai_setting(service.CREATE_CONFIRMATION_KEY, False, self.database_path)
        service.set_ai_setting(service.MODIFY_CONFIRMATION_KEY, False, self.database_path)
        async with self.session() as (session, stderr):
            created = await self.call(
                session,
                "create_note",
                {
                    "request_id": "roundtrip-create-1",
                    "title": "Round-trip version",
                    "content": "base",
                },
            )
            note_id = created["note_id"]
            first = await self.call(session, "get_note", {"note_id": note_id})
            self.assertRegex(first["version"], r"^snotes-v1-[0-9a-f]{64}$")

            appended = await self.call(
                session,
                "append_note",
                {
                    "request_id": "roundtrip-append-1",
                    "note_id": note_id,
                    "content": "-first",
                    "expected_updated_at": first["updated_at"],
                },
            )
            self.assertEqual(appended["status"], "updated")
            stale_timestamp = await self.call(
                session,
                "append_note",
                {
                    "request_id": "roundtrip-append-stale-timestamp",
                    "note_id": note_id,
                    "content": "-must-not-appear",
                    "expected_updated_at": first["updated_at"],
                },
            )
            self.assertEqual(stale_timestamp["status"], "conflict")

            second = await self.call(session, "get_note", {"note_id": note_id})
            version_append = await self.call(
                session,
                "append_note",
                {
                    "request_id": "roundtrip-append-version",
                    "note_id": note_id,
                    "content": "-second",
                    "expected_version": second["version"],
                },
            )
            self.assertEqual(version_append["status"], "updated")
            stale_version = await self.call(
                session,
                "append_note",
                {
                    "request_id": "roundtrip-append-stale-version",
                    "note_id": note_id,
                    "content": "-must-not-appear-either",
                    "expected_version": second["version"],
                },
            )
            self.assertEqual(stale_version["status"], "conflict")

            third = await self.call(session, "get_note", {"note_id": note_id})
            decorated_timestamp = f'  "{third["updated_at"]}"  '
            decorated_append = await self.call(
                session,
                "append_note",
                {
                    "request_id": "roundtrip-append-decorated-timestamp",
                    "note_id": note_id,
                    "content": "-third",
                    "expected_updated_at": decorated_timestamp,
                },
            )
            self.assertEqual(decorated_append["status"], "updated")
            final = await self.call(session, "get_note", {"note_id": note_id})
            self.assertEqual(final["content"], "base\n-first\n-second\n-third")
            self.assertNotIn("must-not-appear", final["content"])
        self.assertNotIn("Traceback", stderr.getvalue())

    async def test_protocol_visibility_attacks_redaction_facets_and_pagination(self) -> None:
        service.ai_create_note(
            "codex", "codex-private-operation", title="Other actor receipt",
            body="other actor private body", database_path=self.database_path,
        )
        async with self.session() as (session, _stderr):
            for query in (
                "Hidden unique 7319", "hidden-body-7319", "hidden-tag-7319",
                "HiddenDirectory7319", "Codex unique 8420", "codex-body-8420",
                "Broken visibility 9531", "Mystery target 1642", "Deleted unique 2753",
            ):
                result = await self.call(session, "search_notes", {"query": query})
                self.assertEqual(result["total_visible_matches"], 0, query)

            raw_redaction = await self.call(session, "search_notes", {"query": "TopSecret"})
            self.assertEqual(raw_redaction["total_visible_matches"], 0)
            safe_redaction = await self.call(session, "search_notes", {"query": "MASKED"})
            self.assertEqual(safe_redaction["total_visible_matches"], 1)
            self.assertNotIn("TopSecret", json.dumps(safe_redaction, ensure_ascii=False))
            multi_keyword = await self.call(
                session,
                "search_notes",
                {"query": "gale-tag visible"},
            )
            self.assertEqual(multi_keyword["total_visible_matches"], 1)
            self.assertEqual(
                multi_keyword["results"][0]["note_id"],
                self.ids["gale"],
            )

            directories = await self.call(session, "list_directories", {})
            tags = await self.call(session, "list_tags", {})
            facet_text = json.dumps([directories, tags], ensure_ascii=False)
            for forbidden in (
                "HiddenDirectory7319", "hidden-tag-7319", "CodexDirectory8420",
                "codex-tag-8420", "BrokenDirectory9531", "MysteryDirectory1642",
                "DeletedDirectory2753", "TopSecret",
            ):
                self.assertNotIn(forbidden, facet_text)
            self.assertIn("[MASKED]", facet_text)

            for key in (
                "hidden", "codex", "unknown_visibility", "unknown_target", "deleted"
            ):
                note = await self.call(session, "get_note", {"note_id": self.ids[key]})
                self.assertEqual(note["status"], "not_found", key)
            missing = await self.call(session, "get_note", {"note_id": 9_999_999})
            self.assertEqual(missing["status"], "not_found")
            other_operation = await self.call(
                session,
                "get_operation_status",
                {"request_id": "codex-private-operation"},
            )
            self.assertEqual(other_operation["status"], "not_found")
            self.assertNotIn("Other actor receipt", json.dumps(other_operation, ensure_ascii=False))

            redacted = await self.call(
                session, "get_note", {"note_id": self.ids["redacted"], "max_chars": 100}
            )
            self.assertEqual(redacted["status"], "ok")
            self.assertNotIn("TopSecret", json.dumps(redacted, ensure_ascii=False))
            self.assertIn("[MASKED]", json.dumps(redacted, ensure_ascii=False))

            first_page = await self.call(
                session, "search_notes", {"query": "", "page": 1, "page_size": 2}
            )
            second_page = await self.call(
                session, "search_notes", {"query": "", "page": 2, "page_size": 2}
            )
            self.assertEqual(first_page["total_visible_matches"], 4)
            self.assertTrue(first_page["has_more"])
            self.assertEqual(second_page["total_visible_matches"], 4)
            self.assertFalse(second_page["has_more"])

            long_note = await self.call(
                session,
                "get_note",
                {"note_id": self.ids["long"], "offset": 0, "max_chars": 4_000},
            )
            self.assertEqual(len(long_note["content"]), 4_000)
            self.assertTrue(long_note["has_more"])
            self.assertEqual(long_note["next_offset"], 4_000)
            self.assertEqual(long_note["total_safe_chars"], 13_500)

    async def test_protocol_writes_idempotency_conflicts_history_and_no_dangerous_fields(self) -> None:
        service.set_ai_setting(service.CREATE_CONFIRMATION_KEY, False, self.database_path)
        service.set_ai_setting(service.MODIFY_CONFIRMATION_KEY, False, self.database_path)
        async with self.session() as (session, stderr):
            created = await self.call(
                session,
                "create_note",
                {
                    "request_id": "protocol-create-1",
                    "title": "Protocol created",
                    "content": "middle <script>alert(1)</script>",
                    "tags": ["one"],
                },
            )
            self.assertEqual(created["status"], "created")
            note_id = created["note_id"]
            duplicate = await self.call(
                session,
                "create_note",
                {
                    "request_id": "protocol-create-1",
                    "title": "Must not duplicate",
                    "content": "ignored",
                },
            )
            self.assertEqual(duplicate["status"], "already_processed")
            self.assertEqual(duplicate["note_id"], note_id)

            status = await self.call(
                session, "get_operation_status", {"request_id": "protocol-create-1"}
            )
            self.assertEqual(status["operation_type"], "create")
            self.assertTrue(status["changed_formal_note"])
            self.assertTrue(status["created_at"])

            note = await self.call(session, "get_note", {"note_id": note_id})
            first_version = note["updated_at"]
            appended = await self.call(
                session,
                "append_note",
                {
                    "request_id": "protocol-append-1",
                    "note_id": note_id,
                    "content": "-end",
                    "expected_updated_at": first_version,
                },
            )
            self.assertEqual(appended["status"], "updated")
            after_append = await self.call(session, "get_note", {"note_id": note_id})
            prepended = await self.call(
                session,
                "prepend_note",
                {
                    "request_id": "protocol-prepend-1",
                    "note_id": note_id,
                    "content": "start-",
                    "expected_version": after_append["version"],
                },
            )
            self.assertEqual(prepended["status"], "updated")
            final_note = await self.call(session, "get_note", {"note_id": note_id})
            self.assertTrue(final_note["content"].startswith("start-\nmiddle"))
            self.assertTrue(final_note["content"].endswith("-end"))
            self.assertIn("<script>alert(1)</script>", final_note["content"])

            conflict = await self.call(
                session,
                "append_note",
                {
                    "request_id": "protocol-conflict-1",
                    "note_id": note_id,
                    "content": "must-not-appear",
                    "expected_updated_at": first_version,
                },
            )
            self.assertEqual(conflict["status"], "conflict")
            self.assertFalse(conflict["changed_formal_note"])

            no_change = await self.call(
                session,
                "update_note_metadata",
                {
                    "request_id": "protocol-no-change-1",
                    "note_id": note_id,
                    "expected_version": final_note["version"],
                    "title": "Protocol created",
                },
            )
            self.assertEqual(no_change["status"], "no_change")
            metadata = await self.call(
                session,
                "update_note_metadata",
                {
                    "request_id": "protocol-metadata-1",
                    "note_id": note_id,
                    "expected_version": final_note["version"],
                    "add_tags": ["two"],
                    "remove_tags": ["one"],
                },
            )
            self.assertEqual(metadata["status"], "updated")
            after_metadata = await self.call(session, "get_note", {"note_id": note_id})
            self.assertEqual(after_metadata["tags"], ["two"])

            unauthorized = await self.call(
                session,
                "append_note",
                {
                    "request_id": "protocol-codex-1",
                    "note_id": self.ids["codex"],
                    "content": "guess",
                    "expected_updated_at": "2026-01-01T00:00:00+08:00",
                },
            )
            self.assertEqual(unauthorized["status"], "not_found")

            for extra in ("ai_visibility", "actor_target_id", "database_path", "new_body"):
                response = await session.call_tool(
                    "create_note",
                    {
                        "request_id": f"bad-extra-{extra}",
                        "title": "Bad",
                        "content": "Bad",
                        extra: "forbidden",
                    },
                )
                self.assertTrue(response.isError, extra)

        with _connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM notes WHERE title='Protocol created'").fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM notes WHERE title='Must not duplicate'").fetchone()[0],
                0,
            )
            stored = connection.execute(
                "SELECT content_html, ai_visibility FROM notes WHERE id=?", (note_id,)
            ).fetchone()
            targets = connection.execute(
                "SELECT target_id FROM note_ai_targets WHERE note_id=?", (note_id,)
            ).fetchall()
            self.assertNotIn("<script>", stored["content_html"])
            self.assertIn("&lt;script&gt;", stored["content_html"])
            self.assertEqual(stored["ai_visibility"], "full")
            self.assertEqual([row["target_id"] for row in targets], ["gale"])
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM ai_note_history WHERE note_id=?", (note_id,)
                ).fetchone()[0],
                3,
            )
            self.assertGreater(
                connection.execute(
                    "SELECT COUNT(*) FROM ai_call_logs WHERE actor_target_id='gale'"
                ).fetchone()[0],
                0,
            )
        self.assertNotIn("middle <script>alert(1)</script>", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    async def test_pending_confirmation_and_already_processed_are_explicit(self) -> None:
        service.set_ai_setting(service.CREATE_CONFIRMATION_KEY, True, self.database_path)
        service.set_ai_setting(service.MODIFY_CONFIRMATION_KEY, True, self.database_path)
        async with self.session() as (session, _stderr):
            pending_create = await self.call(
                session,
                "create_note",
                {"request_id": "pending-create-1", "title": "Pending", "content": "body"},
            )
            self.assertEqual(pending_create["status"], "pending_confirmation")
            self.assertFalse(pending_create["changed_formal_note"])
            self.assertTrue(pending_create["pending_confirmation"])
            self.assertIsNotNone(pending_create["proposal_id"])
            duplicate = await self.call(
                session,
                "create_note",
                {"request_id": "pending-create-1", "title": "Ignored", "content": "ignored"},
            )
            self.assertEqual(duplicate["status"], "already_processed")
            self.assertEqual(duplicate["previous_status"], "pending_confirmation")

            target = await self.call(session, "get_note", {"note_id": self.ids["gale"]})
            pending_append = await self.call(
                session,
                "append_note",
                {
                    "request_id": "pending-append-1",
                    "note_id": self.ids["gale"],
                    "content": "not-yet-formal",
                    "expected_updated_at": target["updated_at"],
                },
            )
            self.assertEqual(pending_append["status"], "pending_confirmation")
            unchanged = await self.call(session, "get_note", {"note_id": self.ids["gale"]})
            self.assertNotIn("not-yet-formal", unchanged["content"])

    async def test_concurrent_duplicate_requests_execute_at_most_once(self) -> None:
        service.set_ai_setting(service.CREATE_CONFIRMATION_KEY, False, self.database_path)

        async def create_once():
            async with self.session() as (session, _stderr):
                return await self.call(
                    session,
                    "create_note",
                    {
                        "request_id": "concurrent-create-1",
                        "title": "Concurrent once",
                        "content": "body",
                    },
                )

        first, second = await asyncio.gather(create_once(), create_once())
        self.assertEqual({first["status"], second["status"]}, {"created", "already_processed"})
        self.assertEqual(first["note_id"], second["note_id"])
        with _connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM notes WHERE title='Concurrent once'").fetchone()[0],
                1,
            )

    async def test_database_busy_fails_safely_and_stdio_stays_clean(self) -> None:
        async with self.session() as (session, stderr):
            lock = sqlite3.connect(self.database_path)
            try:
                lock.execute("BEGIN EXCLUSIVE")
                result = await self.call(session, "search_notes", {"query": "gale"})
            finally:
                lock.rollback()
                lock.close()
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["results"], [])

            self.rules_path.write_text("{broken-json", encoding="utf-8")
            failed_redaction = await self.call(
                session, "search_notes", {"query": "gale-body-needle"}
            )
            self.assertEqual(failed_redaction["status"], "failed")
        error_text = stderr.getvalue()
        self.assertNotIn("gale-body-needle", error_text)
        self.assertNotIn("gale-body", error_text)
        self.assertNotIn("Traceback", error_text)

        with _connect(self.database_path) as connection:
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)

    async def test_read_only_mode_rejects_writes_with_logged_receipt(self) -> None:
        async with self.session(SNOTES_MCP_READ_ONLY="1") as (session, _stderr):
            rejected = await self.call(
                session,
                "create_note",
                {"request_id": "readonly-create-1", "title": "No write", "content": "body"},
            )
            self.assertEqual(rejected["status"], "rejected")
            self.assertFalse(rejected["changed_formal_note"])
            status = await self.call(
                session, "get_operation_status", {"request_id": "readonly-create-1"}
            )
            self.assertEqual(status["status"], "rejected")
        with _connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM notes WHERE title='No write'").fetchone()[0],
                0,
            )

    async def test_doctor_is_read_only_and_invalid_actor_startup_fails_closed(self) -> None:
        with _connect(self.database_path) as connection:
            before = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("notes", "ai_operations", "ai_call_logs", "ai_receipt_inbox")
            }
        doctor = subprocess.run(
            [str(MCP_PYTHON), "-m", "src.mcp_server.server", "doctor"],
            cwd=ROOT,
            env={**os.environ, **self._env()},
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(doctor.returncode, 0, doctor.stderr)
        report = json.loads(doctor.stdout)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["quick_check"], "ok")
        self.assertEqual(report["actor_target_status"], "ok")
        with _connect(self.database_path) as connection:
            after = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in before
            }
        self.assertEqual(before, after)

        failed_start = subprocess.run(
            [str(MCP_PYTHON), "-m", "src.mcp_server.server", "serve"],
            cwd=ROOT,
            env={**os.environ, **self._env(SNOTES_MCP_ACTOR_TARGET_ID="not-a-target")},
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        self.assertEqual(failed_start.returncode, 2)
        self.assertEqual(failed_start.stdout, "")
        self.assertIn("actor_unavailable", failed_start.stderr)
        self.assertNotIn(str(self.database_path), failed_start.stderr)
        self.assertNotIn("Traceback", failed_start.stderr)


if __name__ == "__main__":
    unittest.main()
