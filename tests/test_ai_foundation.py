from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.ai_targets import (
    AI_VISIBILITY_FULL,
    AI_VISIBILITY_HIDDEN,
    AI_VISIBILITY_REDACTED,
)
from src.ai_safe_export import AiSafePackageExporter
from src.database import (
    _connect,
    _ensure_directory_path,
    _migrate_note_ai_targets_table,
    _plain_text_to_html,
    _set_note_ai_targets_with_connection,
    _set_note_tags_with_connection,
    get_last_migration_report,
    accept_ai_receipt_inbox_item,
    create_ai_receipt_inbox_item,
    create_note,
    initialize_database,
)
from src.memory_receipt_importer import parse_memory_receipt
from src.services import ai_access_service as service
from src.time_utils import now_shanghai_iso


class AiFoundationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "s_notes.db"
        self.rules_path = self.root / "redaction_rules.json"
        initialize_database(self.database_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def create_note(
        self,
        *,
        title: str,
        body: str,
        visibility: str,
        targets: list[str],
        tags: list[str] | None = None,
        directory: tuple[str, str, str] = ("临时", "无", "无"),
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
            _set_note_tags_with_connection(connection, note_id, tags or [], timestamp)
            _set_note_ai_targets_with_connection(connection, note_id, targets, timestamp)
        return note_id

    def test_hidden_note_is_indistinguishable_from_missing_on_every_read_path(self) -> None:
        note_id = self.create_note(
            title="隐藏的灯塔", body="unique-hidden-sentence-7319",
            visibility=AI_VISIBILITY_HIDDEN, targets=["gale"],
            tags=["secret-hidden-tag"], directory=("隐藏目录", "无", "无"),
        )
        for query in ("隐藏的灯塔", "unique-hidden-sentence-7319", "secret-hidden-tag", "隐藏目录"):
            result = service.safe_search_notes(
                query, "gale", database_path=self.database_path,
                redaction_rules_path=self.rules_path,
            )
            self.assertEqual(result["items"], [])
            self.assertEqual(result["total_count"], 0)
        self.assertIsNone(
            service.safe_get_note_by_id(
                note_id, "gale", database_path=self.database_path,
                redaction_rules_path=self.rules_path,
            )
        )
        self.assertNotIn(
            "secret-hidden-tag",
            service.safe_list_tags(
                "gale", database_path=self.database_path,
                redaction_rules_path=self.rules_path,
            ),
        )
        self.assertNotIn(
            "隐藏目录 / 无 / 无",
            service.safe_list_directories(
                "gale", database_path=self.database_path,
                redaction_rules_path=self.rules_path,
            ),
        )

    def test_stable_target_permissions_fail_closed(self) -> None:
        common = self.create_note(title="common", body="common-body", visibility="full", targets=["common"])
        gale = self.create_note(title="gale", body="gale-body", visibility="full", targets=["gale"])
        codex = self.create_note(title="codex", body="codex-body", visibility="full", targets=["codex"])
        keden = self.create_note(title="keden", body="keden-body", visibility="full", targets=["ke-den"])
        for visible_id in (common, gale):
            self.assertIsNotNone(service.safe_get_note_by_id(visible_id, "gale", database_path=self.database_path, redaction_rules_path=self.rules_path))
        for hidden_id in (codex, keden):
            self.assertIsNone(service.safe_get_note_by_id(hidden_id, "gale", database_path=self.database_path, redaction_rules_path=self.rules_path))
        self.assertIsNone(service.safe_get_note_by_id(common, "unknown-actor", database_path=self.database_path, redaction_rules_path=self.rules_path))

    def test_redacted_note_is_redacted_before_matching_and_snippet_generation(self) -> None:
        self.rules_path.write_text(
            json.dumps({"TopSecret": "[已脱敏]"}, ensure_ascii=False), encoding="utf-8"
        )
        note_id = self.create_note(
            title="TopSecret 标题", body="正文 TopSecret 内容",
            visibility=AI_VISIBILITY_REDACTED, targets=["gale"],
            tags=["TopSecret-tag"], directory=("TopSecret-目录", "无", "无"),
        )
        raw_search = service.safe_search_notes(
            "TopSecret", "gale", database_path=self.database_path,
            redaction_rules_path=self.rules_path,
        )
        self.assertEqual(raw_search["total_count"], 0)
        safe_search = service.safe_search_notes(
            "已脱敏", "gale", database_path=self.database_path,
            redaction_rules_path=self.rules_path,
        )
        self.assertEqual(safe_search["total_count"], 1)
        self.assertNotIn("TopSecret", json.dumps(safe_search, ensure_ascii=False))
        note = service.safe_get_note_by_id(
            note_id, "gale", database_path=self.database_path,
            redaction_rules_path=self.rules_path,
        )
        self.assertNotIn("TopSecret", json.dumps(note, ensure_ascii=False))
        self.assertNotIn("TopSecret-tag", service.safe_list_tags("gale", database_path=self.database_path, redaction_rules_path=self.rules_path))

    def test_search_ranking_pagination_and_one_result_per_note(self) -> None:
        self.create_note(title="needle", body="needle " * 30, visibility="full", targets=["gale"])
        self.create_note(title="other", body="needle", visibility="full", targets=["gale"], tags=["needle"])
        result = service.safe_search_notes("needle", "gale", limit=1, database_path=self.database_path, redaction_rules_path=self.rules_path)
        self.assertEqual(result["total_count"], 2)
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["matched_in"], "title")
        self.assertEqual(result["limit"], 1)
        capped = service.safe_search_notes("needle", "gale", limit=999, offset=1, database_path=self.database_path, redaction_rules_path=self.rules_path)
        self.assertEqual(capped["limit"], 20)
        self.assertEqual(len(capped["items"]), 1)

    def test_write_defaults_pending_idempotency_conflict_history_and_restore(self) -> None:
        created = service.ai_create_note(
            "gale", "create-1", title="AI note", body="original",
            database_path=self.database_path,
        )
        self.assertEqual(created["status"], "created")
        note_id = created["note_id"]
        note = service.safe_get_note_by_id(note_id, "gale", database_path=self.database_path, redaction_rules_path=self.rules_path)
        self.assertEqual(note["body"], "original")
        with _connect(self.database_path) as connection:
            stored = connection.execute("SELECT ai_visibility, updated_at FROM notes WHERE id=?", (note_id,)).fetchone()
            targets = connection.execute("SELECT target_id FROM note_ai_targets WHERE note_id=?", (note_id,)).fetchall()
        self.assertEqual(stored["ai_visibility"], AI_VISIBILITY_FULL)
        self.assertEqual([row["target_id"] for row in targets], ["gale"])

        pending = service.ai_append_note(
            "gale", "append-1", note_id, "-pending",
            expected_updated_at=stored["updated_at"], database_path=self.database_path,
        )
        self.assertEqual(pending["status"], "pending_confirmation")
        unchanged = service.safe_get_note_by_id(note_id, "gale", database_path=self.database_path, redaction_rules_path=self.rules_path)
        self.assertEqual(unchanged["body"], "original")
        with _connect(self.database_path) as connection:
            inbox_id = connection.execute("SELECT id FROM ai_receipt_inbox WHERE request_id='append-1'").fetchone()["id"]
        accepted = service.accept_ai_proposal(inbox_id, database_path=self.database_path)
        self.assertEqual(accepted["status"], "updated")
        self.assertEqual(service.safe_get_note_by_id(note_id, "gale", database_path=self.database_path, redaction_rules_path=self.rules_path)["body"], "original\n-pending")

        duplicate = service.ai_append_note(
            "gale", "append-1", note_id, "-duplicate",
            expected_updated_at=stored["updated_at"], database_path=self.database_path,
        )
        self.assertEqual(duplicate["status"], "already_processed")
        self.assertNotIn("duplicate", service.safe_get_note_by_id(note_id, "gale", database_path=self.database_path, redaction_rules_path=self.rules_path)["body"])

        conflict = service.ai_append_note(
            "gale", "append-conflict", note_id, "bad",
            expected_updated_at="stale-version", database_path=self.database_path,
        )
        self.assertEqual(conflict["status"], "conflict")
        history = service.list_ai_note_history(database_path=self.database_path)
        self.assertEqual(len(history), 1)
        restored = service.restore_ai_note_history(history[0]["id"], database_path=self.database_path)
        self.assertEqual(restored["status"], "updated")
        self.assertEqual(service.safe_get_note_by_id(note_id, "gale", database_path=self.database_path, redaction_rules_path=self.rules_path)["body"], "original")
        self.assertEqual(len(service.list_ai_note_history(database_path=self.database_path)), 2)

    def test_confirmation_settings_persist_and_direct_prepend_append_preserve_body(self) -> None:
        service.set_ai_setting(service.CREATE_CONFIRMATION_KEY, True, self.database_path)
        pending = service.ai_create_note("gale", "create-pending", title="P", body="B", database_path=self.database_path)
        self.assertEqual(pending["status"], "pending_confirmation")
        self.assertFalse(pending["modified_formal_note"])
        service.set_ai_setting(service.CREATE_CONFIRMATION_KEY, False, self.database_path)
        service.set_ai_setting(service.MODIFY_CONFIRMATION_KEY, False, self.database_path)
        self.assertEqual(service.get_ai_settings(self.database_path), {
            service.CREATE_CONFIRMATION_KEY: False,
            service.MODIFY_CONFIRMATION_KEY: False,
        })
        created = service.ai_create_note("gale", "direct-body", title="Body", body="middle", database_path=self.database_path)
        with _connect(self.database_path) as connection:
            version = connection.execute("SELECT updated_at FROM notes WHERE id=?", (created["note_id"],)).fetchone()["updated_at"]
        prepend = service.ai_prepend_note("gale", "prepend-direct", created["note_id"], "start-", expected_updated_at=version, database_path=self.database_path)
        self.assertEqual(prepend["status"], "updated")
        with _connect(self.database_path) as connection:
            version = connection.execute("SELECT updated_at FROM notes WHERE id=?", (created["note_id"],)).fetchone()["updated_at"]
        append = service.ai_append_note("gale", "append-direct", created["note_id"], "-end", expected_updated_at=version, database_path=self.database_path)
        self.assertEqual(append["status"], "updated")
        body = service.safe_get_note_by_id(created["note_id"], "gale", database_path=self.database_path, redaction_rules_path=self.rules_path)["body"]
        self.assertEqual(body, "start-\nmiddle\n-end")

    def test_logs_cover_reads_writes_pending_duplicate_conflict_without_full_body(self) -> None:
        service.ai_create_note("gale", "log-create", title="Log", body="FULL-BODY-MUST-NOT-BE-IN-LOG", database_path=self.database_path)
        service.safe_search_notes("Log", "gale", request_id="log-search", database_path=self.database_path, redaction_rules_path=self.rules_path)
        service.ai_create_note("gale", "log-create", title="ignored", body="ignored", database_path=self.database_path)
        logs = service.list_ai_call_logs(database_path=self.database_path)
        self.assertTrue(any(row["operation_type"] == "search" for row in logs))
        self.assertTrue(any(row["is_duplicate"] for row in logs))
        self.assertNotIn("FULL-BODY-MUST-NOT-BE-IN-LOG", json.dumps(logs, ensure_ascii=False))
        self.assertTrue(all(row["request_id"] and row["status"] for row in logs))

    def test_pending_create_accept_reject_failure_and_log_statuses(self) -> None:
        service.set_ai_setting(service.CREATE_CONFIRMATION_KEY, True, self.database_path)
        pending = service.ai_create_note(
            "gale", "pending-create-accept", title="Pending", body="body",
            database_path=self.database_path,
        )
        self.assertEqual(pending["status"], "pending_confirmation")
        with _connect(self.database_path) as connection:
            accept_id = connection.execute(
                "SELECT id FROM ai_receipt_inbox WHERE request_id='pending-create-accept'"
            ).fetchone()["id"]
        accepted = service.accept_ai_proposal(accept_id, database_path=self.database_path)
        self.assertEqual(accepted["status"], "created")

        rejected_pending = service.ai_create_note(
            "gale", "pending-create-reject", title="Rejected", body="body",
            database_path=self.database_path,
        )
        with _connect(self.database_path) as connection:
            reject_id = connection.execute(
                "SELECT id FROM ai_receipt_inbox WHERE request_id='pending-create-reject'"
            ).fetchone()["id"]
        rejected = service.reject_ai_proposal(reject_id, self.database_path)
        self.assertEqual(rejected["status"], "rejected")

        failed = service.ai_create_note(
            "unknown", "unknown-create", title="No", body="No",
            database_path=self.database_path,
        )
        self.assertEqual(failed["status"], "failed")
        logs = service.list_ai_call_logs(database_path=self.database_path)
        statuses = {row["status"] for row in logs}
        self.assertTrue({"pending_confirmation", "created", "rejected", "failed"}.issubset(statuses))
        self.assertFalse(rejected_pending["modified_formal_note"])

    def test_metadata_move_requires_existing_directory_and_logs_failure(self) -> None:
        service.set_ai_setting(service.MODIFY_CONFIRMATION_KEY, False, self.database_path)
        created = service.ai_create_note(
            "gale", "metadata-base", title="Metadata", body="body",
            database_path=self.database_path,
        )
        with _connect(self.database_path) as connection:
            version = connection.execute(
                "SELECT updated_at FROM notes WHERE id=?", (created["note_id"],)
            ).fetchone()["updated_at"]
        failed = service.ai_update_note_metadata(
            "gale", "metadata-missing-dir", created["note_id"],
            expected_updated_at=version, directory=("不存在", "无", "无"),
            database_path=self.database_path,
        )
        self.assertEqual(failed["status"], "failed")
        with _connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM directories WHERE name='不存在'").fetchone()[0],
                0,
            )
        logs = service.list_ai_call_logs(operation_type="metadata", database_path=self.database_path)
        self.assertEqual(logs[0]["status"], "failed")

    def test_legacy_receipt_acceptance_writes_codes_and_stable_target_ids(self) -> None:
        receipt = create_ai_receipt_inbox_item(
            {
                "raw_text": "legacy", "parse_status": "parsed", "source": "test",
                "action": "新增", "title": "Legacy receipt", "body": "body",
                "ai_visibility": "可直接给 AI", "ai_targets": "G经理",
            },
            self.database_path,
        )
        result = accept_ai_receipt_inbox_item(receipt["id"], {}, self.database_path)
        note_id = result["note"]["id"]
        with _connect(self.database_path) as connection:
            visibility = connection.execute(
                "SELECT ai_visibility FROM notes WHERE id=?", (note_id,)
            ).fetchone()["ai_visibility"]
            target_id = connection.execute(
                "SELECT target_id FROM note_ai_targets WHERE note_id=?", (note_id,)
            ).fetchone()["target_id"]
        self.assertEqual(visibility, "full")
        self.assertEqual(target_id, "gale")

    def test_ordinary_new_note_and_receipt_parser_only_write_visibility_codes(self) -> None:
        note = create_note(self.database_path)
        self.assertEqual(note["ai_visibility"], "hidden")
        parsed = parse_memory_receipt(
            """[S-notes 记忆回执]\n标题：Parsed\nAI可见性：可直接给 AI\n正文：body\n[/S-notes 记忆回执]"""
        )
        self.assertEqual(parsed.ai_visibility, "full")

    def test_ai_safe_export_uses_stable_target_relation_and_fail_closed_visibility(self) -> None:
        visible_id = self.create_note(
            title="Visible", body="visible body", visibility="full", targets=["gale"]
        )
        self.create_note(
            title="Other", body="other body", visibility="full", targets=["codex"]
        )
        self.create_note(
            title="Broken", body="broken body", visibility="corrupt", targets=["gale"]
        )
        exporter = AiSafePackageExporter(
            database_path=self.database_path,
            output_root=self.root / "exports",
            redaction_rules_path=self.rules_path,
        )
        plan = exporter.build_plan("Gale")
        self.assertEqual([item.note["id"] for item in plan.rendered_notes], [visible_id])
        self.assertIn("AI 可见性：AI完整可见", plan.rendered_notes[0].markdown)

    def test_no_full_body_overwrite_delete_or_permission_mutation_api_exists(self) -> None:
        forbidden = {
            "ai_replace_note", "ai_overwrite_note", "ai_delete_note",
            "ai_set_visibility", "ai_set_targets",
        }
        self.assertTrue(forbidden.isdisjoint(set(dir(service))))


class AiFoundationMigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "legacy.db"
        self._create_legacy_database()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _create_legacy_database(self) -> None:
        timestamp = now_shanghai_iso()
        connection = sqlite3.connect(self.database_path)
        connection.executescript(
            """
            CREATE TABLE directories (id INTEGER PRIMARY KEY, name TEXT NOT NULL, parent_id INTEGER, sort_order INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, is_deleted INTEGER NOT NULL DEFAULT 0, deleted_at TEXT, trash_group_id TEXT);
            CREATE TABLE notes (id INTEGER PRIMARY KEY, directory_id INTEGER, title TEXT NOT NULL DEFAULT '', body_rich_text TEXT NOT NULL DEFAULT '', content_html TEXT NOT NULL DEFAULT '', main_category TEXT NOT NULL DEFAULT '临时', sub_category TEXT NOT NULL DEFAULT '无', sub_sub_category TEXT NOT NULL DEFAULT '无', tags TEXT NOT NULL DEFAULT '', ai_visibility TEXT NOT NULL DEFAULT '不导出给 AI', is_pinned INTEGER NOT NULL DEFAULT 0, is_deleted INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT, trash_group_id TEXT);
            CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
            CREATE TABLE note_tags (note_id INTEGER NOT NULL, tag_id INTEGER NOT NULL, PRIMARY KEY(note_id, tag_id));
            CREATE TABLE note_ai_targets (id INTEGER PRIMARY KEY, note_id INTEGER NOT NULL, target_name TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE memory_receipts (id INTEGER PRIMARY KEY, note_id INTEGER, receipt_text TEXT NOT NULL, imported_at TEXT NOT NULL);
            CREATE TABLE ai_receipt_inbox (id INTEGER PRIMARY KEY, raw_text TEXT NOT NULL, parse_status TEXT NOT NULL DEFAULT 'failed', status TEXT NOT NULL DEFAULT 'pending', source TEXT NOT NULL DEFAULT '未知', action TEXT NOT NULL DEFAULT '新增', title TEXT NOT NULL DEFAULT '', main_category TEXT NOT NULL DEFAULT '临时', sub_category TEXT NOT NULL DEFAULT '无', sub_sub_category TEXT NOT NULL DEFAULT '无', tags TEXT NOT NULL DEFAULT '', ai_visibility TEXT NOT NULL DEFAULT '不导出给 AI', ai_targets TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '', note_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, decided_at TEXT, parse_error TEXT);
            """
        )
        connection.execute("INSERT INTO directories VALUES (1, '临时', NULL, 0, ?, ?, 0, NULL, NULL)", (timestamp, timestamp))
        visibilities = ["不导出给 AI", "可直接给 AI", "脱敏后给 AI", "broken-value"]
        for note_id, visibility in enumerate(visibilities, start=1):
            connection.execute(
                "INSERT INTO notes(id, directory_id, title, ai_visibility, created_at, updated_at) VALUES (?, 1, ?, ?, ?, ?)",
                (note_id, f"n{note_id}", visibility, timestamp, timestamp),
            )
        targets = [(1, "G经理"), (2, "微信 clawde"), (3, "ke-den Claude"), (4, "mystery-ai")]
        for note_id, name in targets:
            connection.execute("INSERT INTO note_ai_targets(note_id, target_name, created_at) VALUES (?, ?, ?)", (note_id, name, timestamp))
        connection.commit()
        connection.close()

    def test_legacy_values_migrate_to_codes_and_stable_ids_idempotently_with_backup(self) -> None:
        initialize_database(self.database_path)
        report = get_last_migration_report(self.database_path)
        self.assertTrue(Path(report["backup_path"]).is_file())
        self.assertEqual(report["before"]["notes"], report["after"]["notes"])
        self.assertEqual(report["before"]["target_relations"], 4)
        self.assertEqual(report["after"]["target_relations"], 3)
        self.assertEqual(report["unknown_visibility_values"], ["broken-value"])
        self.assertEqual(report["unknown_target_values"], ["mystery-ai"])
        with _connect(self.database_path) as connection:
            values = [row["ai_visibility"] for row in connection.execute("SELECT ai_visibility FROM notes ORDER BY id")]
            targets = [row["target_id"] for row in connection.execute("SELECT target_id FROM note_ai_targets ORDER BY note_id")]
            unknown_common = connection.execute("SELECT COUNT(*) FROM note_ai_targets WHERE note_id=4 AND target_id='common'").fetchone()[0]
        self.assertEqual(values, ["hidden", "full", "redacted", "hidden"])
        self.assertEqual(targets, ["gale", "clawbot", "ke-den"])
        self.assertEqual(unknown_common, 0)
        initialize_database(self.database_path)
        with _connect(self.database_path) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM note_ai_targets").fetchone()[0], 3)

    def test_mid_migration_failure_rolls_back_every_change(self) -> None:
        def migrate_then_fail(connection):
            _migrate_note_ai_targets_table(connection)
            raise RuntimeError("injected migration failure")

        with patch("src.database._migrate_note_ai_targets_table", migrate_then_fail):
            with self.assertRaisesRegex(RuntimeError, "injected migration failure"):
                initialize_database(self.database_path)
        connection = sqlite3.connect(self.database_path)
        try:
            visibility = connection.execute("SELECT ai_visibility FROM notes WHERE id=1").fetchone()[0]
            columns = [row[1] for row in connection.execute("PRAGMA table_info(note_ai_targets)")]
            schema_meta = connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='schema_meta'").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(visibility, "不导出给 AI")
        self.assertIn("target_name", columns)
        self.assertEqual(schema_meta, 0)


if __name__ == "__main__":
    unittest.main()
