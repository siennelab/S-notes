from __future__ import annotations

import io
import os
import sqlite3
import stat
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from scripts import inject_fictional_gale_submissions as injection_script
from src import database as database_module
from src.ai_safe_export import AiSafePackageExporter
from src.database import (
    SCHEMA_SQL,
    _connect,
    accept_ai_receipt_inbox_item,
    create_gale_submission,
    create_note,
    get_ai_receipt_inbox_item,
    get_note,
    get_note_ai_targets,
    initialize_database,
    list_ai_visible_notes,
    list_deleted_notes,
    list_notes,
    list_notes_for_link_drawer,
    list_notes_by_tag,
    list_notes_for_export,
    list_pinned_notes,
    list_recent_notes,
    list_todo_notes,
    reject_ai_receipt_inbox_item,
    search_notes,
    update_note,
)
from src.receipt_inbox_dialog import ReceiptCard
from src.services import ai_access_service
from src.services.gale_mirror_service import export_gale_mirror


class PackageDLocalInboxTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="snotes-r1b-package-d-"
        )
        self.root = Path(self.temporary_directory.name)
        self.database_path = self.root / "s_notes.db"
        self.rules_path = self.root / "redaction_rules.json"
        self.rules_path.write_text("{}", encoding="utf-8")
        initialize_database(self.database_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _submission(
        self,
        suffix: str,
        *,
        idempotency_key: str | None = None,
    ) -> dict:
        return create_gale_submission(
            external_submission_id=f"FICTIONAL_GALE_SUBMISSION_{suffix}",
            idempotency_key=idempotency_key or f"fictional-r1b-key-{suffix}",
            title=f"虚构 Gale 投稿 {suffix}",
            body=f"A fictional Gale submission for R1B local inbox testing {suffix}.",
            main_category="虚构目录",
            sub_category="投稿",
            sub_sub_category="待确认",
            tags=["虚构投稿", f"tag-{suffix}"],
            database_path=self.database_path,
        )

    def _note_count(self) -> int:
        with _connect(self.database_path) as connection:
            return int(connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0])

    def _create_schema_v1_database(self, name: str) -> tuple[Path, dict]:
        database_path = self.root / name
        schema_v1 = SCHEMA_SQL.replace(
            "    note_uuid TEXT,\n    revision INTEGER NOT NULL DEFAULT 1,\n",
            "",
        ).replace(
            "    result_status TEXT,\n    external_submission_id TEXT,\n"
            "    idempotency_key TEXT,\n    accepted_note_uuid TEXT\n",
            "    result_status TEXT\n",
        )
        created_at = "2026-01-02T03:04:05+08:00"
        updated_at = "2026-01-03T04:05:06+08:00"
        connection = sqlite3.connect(database_path)
        try:
            for statement in schema_v1.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.executemany(
                """
                INSERT INTO directories (
                    id, name, parent_id, sort_order, created_at, updated_at, is_deleted
                ) VALUES (?, ?, ?, 0, ?, ?, 0)
                """,
                (
                    (1, "虚构主目录", None, created_at, updated_at),
                    (2, "虚构子目录", 1, created_at, updated_at),
                    (3, "虚构末级目录", 2, created_at, updated_at),
                ),
            )
            connection.execute(
                """
                INSERT INTO notes (
                    directory_id, title, body_rich_text, content_html,
                    main_category, sub_category, sub_sub_category, tags,
                    ai_visibility, is_pinned, is_deleted, created_at, updated_at
                ) VALUES (3, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
                """,
                (
                    "虚构 schema v1 标题",
                    "<p>虚构 schema v1 正文</p>",
                    "<p>虚构 schema v1 正文</p>",
                    "虚构主目录",
                    "虚构子目录",
                    "虚构末级目录",
                    "虚构标签",
                    "hidden",
                    created_at,
                    updated_at,
                ),
            )
            connection.execute(
                "INSERT INTO schema_meta(meta_key, meta_value) VALUES (?, ?)",
                ("ai_foundation_version", "1"),
            )
            connection.commit()
        finally:
            connection.close()
        expected = {
            "directory_id": 3,
            "title": "虚构 schema v1 标题",
            "body_rich_text": "<p>虚构 schema v1 正文</p>",
            "content_html": "<p>虚构 schema v1 正文</p>",
            "main_category": "虚构主目录",
            "sub_category": "虚构子目录",
            "sub_sub_category": "虚构末级目录",
            "tags": "虚构标签",
            "ai_visibility": "hidden",
            "created_at": created_at,
            "updated_at": updated_at,
        }
        return database_path, expected

    def test_note_uuid_migration_is_repeatable_and_revision_is_monotonic(self) -> None:
        note = create_note(self.database_path)
        original_uuid = note["note_uuid"]
        self.assertEqual(uuid.UUID(original_uuid).version, 4)
        self.assertEqual(note["revision"], 1)

        with _connect(self.database_path) as connection:
            before = dict(
                connection.execute(
                    """
                    SELECT title, body_rich_text, content_html, main_category,
                           sub_category, sub_sub_category, tags, ai_visibility,
                           updated_at
                    FROM notes WHERE id=?
                    """,
                    (note["id"],),
                ).fetchone()
            )
            connection.execute(
                "UPDATE notes SET note_uuid=NULL, revision=0 WHERE id=?",
                (note["id"],),
            )

        initialize_database(self.database_path)
        migrated = get_note(note["id"], self.database_path)
        self.assertIsNotNone(migrated)
        migrated_uuid = migrated["note_uuid"]
        self.assertEqual(uuid.UUID(migrated_uuid).version, 4)
        self.assertEqual(migrated["revision"], 1)

        initialize_database(self.database_path)
        restarted = get_note(note["id"], self.database_path)
        self.assertEqual(restarted["note_uuid"], migrated_uuid)
        self.assertEqual(restarted["revision"], 1)
        with _connect(self.database_path) as connection:
            after = dict(
                connection.execute(
                    """
                    SELECT title, body_rich_text, content_html, main_category,
                           sub_category, sub_sub_category, tags, ai_visibility,
                           updated_at
                    FROM notes WHERE id=?
                    """,
                    (note["id"],),
                ).fetchone()
            )
        self.assertEqual(after, before)

        updated = update_note(
            note["id"],
            title="版本递增",
            content_html="<p>虚构正文</p>",
            database_path=self.database_path,
        )
        self.assertEqual(updated["note_uuid"], migrated_uuid)
        self.assertEqual(updated["revision"], 2)

        with _connect(self.database_path) as connection:
            cursor = connection.execute(
                """
                INSERT INTO notes (title, created_at, updated_at)
                VALUES ('触发器虚构笔记', '2026-01-01T00:00:00+08:00',
                        '2026-01-01T00:00:00+08:00')
                """
            )
            triggered = connection.execute(
                "SELECT note_uuid, revision FROM notes WHERE id=?",
                (cursor.lastrowid,),
            ).fetchone()
        self.assertEqual(uuid.UUID(triggered["note_uuid"]).version, 4)
        self.assertEqual(triggered["revision"], 1)

    def test_real_schema_v1_upgrade_is_stable_private_and_one_time(self) -> None:
        database_path, expected = self._create_schema_v1_database("schema-v1.db")

        initialize_database(database_path)
        with _connect(database_path) as connection:
            first = dict(
                connection.execute(
                    """
                    SELECT directory_id, title, body_rich_text, content_html,
                           main_category, sub_category, sub_sub_category, tags,
                           ai_visibility, created_at, updated_at, note_uuid, revision
                    FROM notes WHERE id = 1
                    """
                ).fetchone()
            )
            version = connection.execute(
                "SELECT meta_value FROM schema_meta "
                "WHERE meta_key = ?",
                ("ai_foundation_version",),
            ).fetchone()["meta_value"]
        self.assertEqual(version, "2")
        self.assertEqual(
            {key: first[key] for key in expected},
            expected,
        )
        self.assertEqual(uuid.UUID(first["note_uuid"]).version, 4)
        self.assertEqual(first["revision"], 1)

        backup_dir = database_path.parent / "backups" / "migrations"
        backups = list(backup_dir.glob("*.db"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(stat.S_IMODE(backup_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o600)

        initialize_database(database_path)
        with _connect(database_path) as connection:
            second = dict(
                connection.execute(
                    """
                    SELECT directory_id, title, body_rich_text, content_html,
                           main_category, sub_category, sub_sub_category, tags,
                           ai_visibility, created_at, updated_at, note_uuid, revision
                    FROM notes WHERE id = 1
                    """
                ).fetchone()
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    """
                    INSERT INTO notes (note_uuid, title, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        first["note_uuid"],
                        "重复 UUID 虚构笔记",
                        expected["created_at"],
                        expected["updated_at"],
                    ),
                )
        self.assertEqual(second, first)
        self.assertEqual(len(list(backup_dir.glob("*.db"))), 1)

    def test_schema_v1_failure_rolls_back_all_schema_changes(self) -> None:
        database_path, expected = self._create_schema_v1_database(
            "schema-v1-failure.db"
        )
        with patch.object(
            database_module,
            "_ensure_r1b_indexes_and_triggers",
            side_effect=RuntimeError("forced migration failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "forced migration failure"):
                initialize_database(database_path)

        with _connect(database_path) as connection:
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(notes)")
            }
            version = connection.execute(
                "SELECT meta_value FROM schema_meta WHERE meta_key = ?",
                ("ai_foundation_version",),
            ).fetchone()["meta_value"]
            unchanged = dict(
                connection.execute(
                    """
                    SELECT directory_id, title, body_rich_text, content_html,
                           main_category, sub_category, sub_sub_category, tags,
                           ai_visibility, created_at, updated_at
                    FROM notes WHERE id = 1
                    """
                ).fetchone()
            )
        self.assertNotIn("note_uuid", columns)
        self.assertNotIn("revision", columns)
        self.assertEqual(version, "1")
        self.assertEqual(unchanged, expected)

    def test_submission_defaults_and_idempotency(self) -> None:
        first = self._submission("001", idempotency_key="stable-key-001")
        duplicate_external = create_gale_submission(
            external_submission_id=first["external_submission_id"],
            idempotency_key="different-key",
            title="不应覆盖",
            body="不应覆盖",
            database_path=self.database_path,
        )
        duplicate_key = create_gale_submission(
            external_submission_id="FICTIONAL_GALE_SUBMISSION_OTHER",
            idempotency_key="stable-key-001",
            title="不应产生第二条",
            body="不应产生第二条",
            database_path=self.database_path,
        )
        self.assertEqual(first["id"], duplicate_external["id"])
        self.assertEqual(first["id"], duplicate_key["id"])
        self.assertEqual(first["status"], "pending")
        self.assertEqual(first["source"], "Gale")
        self.assertEqual(first["ai_visibility"], "hidden")
        self.assertEqual(first["ai_targets"], "")
        self.assertIsNone(first["accepted_note_uuid"])
        with _connect(self.database_path) as connection:
            logical_count = connection.execute(
                "SELECT COUNT(*) FROM ai_receipt_inbox WHERE source='Gale'"
            ).fetchone()[0]
        self.assertEqual(logical_count, 1)
        self.assertEqual(self._note_count(), 0)

    def test_concurrent_duplicate_submission_creates_one_logical_item(self) -> None:
        def write_submission(_index: int) -> dict:
            return create_gale_submission(
                external_submission_id="FICTIONAL_GALE_CONCURRENT_001",
                idempotency_key="fictional-concurrent-key-001",
                title="虚构并发投稿",
                body="A fictional concurrent Gale submission.",
                database_path=self.database_path,
            )

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(write_submission, range(8)))
        self.assertEqual(len({item["id"] for item in results}), 1)
        with _connect(self.database_path) as connection:
            count = connection.execute(
                """
                SELECT COUNT(*) FROM ai_receipt_inbox
                WHERE source = ? AND (external_submission_id = ? OR idempotency_key = ?)
                """,
                (
                    "Gale",
                    "FICTIONAL_GALE_CONCURRENT_001",
                    "fictional-concurrent-key-001",
                ),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_pending_is_excluded_from_every_formal_read_and_export_path(self) -> None:
        pending = self._submission("ISOLATION")
        marker = pending["external_submission_id"]
        self.assertEqual(search_notes(marker, self.database_path), [])
        self.assertEqual(search_notes("fictional Gale submission", self.database_path), [])
        self.assertEqual(list_notes(self.database_path), [])
        self.assertEqual(list_notes_for_link_drawer(self.database_path), [])
        self.assertEqual(list_pinned_notes(self.database_path), [])
        self.assertEqual(list_todo_notes(self.database_path), [])
        self.assertEqual(list_recent_notes(database_path=self.database_path), [])
        self.assertEqual(list_notes_by_tag("虚构投稿", self.database_path), [])
        self.assertEqual(list_ai_visible_notes(self.database_path), [])
        self.assertEqual(list_deleted_notes(self.database_path), [])
        self.assertEqual(list_notes_for_export(self.database_path), [])

        mcp_search = ai_access_service.safe_search_notes(
            "fictional Gale submission",
            "gale",
            database_path=self.database_path,
            redaction_rules_path=self.rules_path,
        )
        self.assertEqual(mcp_search["total_count"], 0)
        self.assertEqual(mcp_search["items"], [])
        self.assertIsNone(
            ai_access_service.safe_get_note_by_id(
                pending["id"],
                "gale",
                database_path=self.database_path,
                redaction_rules_path=self.rules_path,
            )
        )
        self.assertNotIn(
            "虚构投稿",
            ai_access_service.safe_list_tags(
                "gale",
                database_path=self.database_path,
                redaction_rules_path=self.rules_path,
            ),
        )
        self.assertNotIn(
            "虚构目录 / 投稿 / 待确认",
            ai_access_service.safe_list_directories(
                "gale",
                database_path=self.database_path,
                redaction_rules_path=self.rules_path,
            ),
        )

        plan = AiSafePackageExporter(
            database_path=self.database_path,
            output_root=self.root / "exports",
            redaction_rules_path=self.rules_path,
        ).build_plan("Gale")
        self.assertEqual(plan.note_count, 0)

        mirror_result = export_gale_mirror(
            source_database=self.database_path,
            output_path=self.root / "gale-mirror.db",
            redaction_rules_path=self.rules_path,
        )
        self.assertEqual(mirror_result.note_count, 0)

    def test_accept_creates_exactly_one_hidden_note_and_is_restart_safe(self) -> None:
        submission = self._submission("ACCEPT")
        first = accept_ai_receipt_inbox_item(
            submission["id"],
            {
                "title": "接受后的虚构笔记",
                "body": "编辑后的虚构正文",
                "main_category": "已确认",
                "sub_category": "Gale",
                "sub_sub_category": "投稿",
                "tags": "已确认, 虚构",
            },
            self.database_path,
        )
        self.assertEqual(first["receipt"]["status"], "accepted")
        self.assertEqual(first["note"]["revision"], 1)
        self.assertEqual(first["note"]["ai_visibility"], "hidden")
        self.assertEqual(get_note_ai_targets(first["note"]["id"], self.database_path), [])
        self.assertEqual(
            first["receipt"]["accepted_note_uuid"],
            first["note"]["note_uuid"],
        )
        self.assertEqual(self._note_count(), 1)
        with _connect(self.database_path) as connection:
            linked = connection.execute(
                """
                SELECT COUNT(*) FROM ai_receipt_inbox AS receipt
                JOIN notes AS note
                  ON note.id = receipt.note_id
                 AND note.note_uuid = receipt.accepted_note_uuid
                WHERE receipt.id = ? AND receipt.status = ?
                """,
                (submission["id"], "accepted"),
            ).fetchone()[0]
        self.assertEqual(linked, 1)

        second = accept_ai_receipt_inbox_item(
            submission["id"], {}, self.database_path
        )
        self.assertEqual(second["note"]["id"], first["note"]["id"])
        self.assertEqual(second["note"]["note_uuid"], first["note"]["note_uuid"])
        initialize_database(self.database_path)
        after_restart = create_gale_submission(
            external_submission_id=submission["external_submission_id"],
            idempotency_key=submission["idempotency_key"],
            title="重启后的重复投稿",
            body="不应再创建",
            database_path=self.database_path,
        )
        self.assertEqual(after_restart["status"], "accepted")
        self.assertEqual(after_restart["accepted_note_uuid"], first["note"]["note_uuid"])
        self.assertEqual(self._note_count(), 1)

    def test_accept_failure_rolls_back_receipt_note_and_directories(self) -> None:
        submission = self._submission("ROLLBACK")
        with patch.object(
            database_module,
            "_set_note_ai_targets_with_connection",
            side_effect=RuntimeError("forced acceptance failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "forced acceptance failure"):
                accept_ai_receipt_inbox_item(
                    submission["id"],
                    {
                        "title": "不应保留的编辑标题",
                        "main_category": "不应保留的目录",
                        "sub_category": "回滚",
                        "sub_sub_category": "验证",
                    },
                    self.database_path,
                )

        receipt = get_ai_receipt_inbox_item(submission["id"], self.database_path)
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt["status"], "pending")
        self.assertEqual(receipt["title"], submission["title"])
        self.assertIsNone(receipt["note_id"])
        self.assertIsNone(receipt["accepted_note_uuid"])
        self.assertEqual(self._note_count(), 0)
        with _connect(self.database_path) as connection:
            directory_count = connection.execute(
                "SELECT COUNT(*) FROM directories WHERE name = ?",
                ("不应保留的目录",),
            ).fetchone()[0]
        self.assertEqual(directory_count, 0)

    def test_explicit_permissions_are_saved_only_on_accept(self) -> None:
        submission = self._submission("PERMISSIONS")
        result = accept_ai_receipt_inbox_item(
            submission["id"],
            {
                "ai_visibility": "AI完整可见",
                "ai_targets": "Gale",
            },
            self.database_path,
        )
        self.assertEqual(result["note"]["ai_visibility"], "full")
        self.assertEqual(
            get_note_ai_targets(result["note"]["id"], self.database_path),
            ["Gale"],
        )

    def test_discard_is_idempotent_and_creates_no_note_or_trash(self) -> None:
        submission = self._submission("DISCARD")
        first = reject_ai_receipt_inbox_item(submission["id"], self.database_path)
        second = reject_ai_receipt_inbox_item(submission["id"], self.database_path)
        self.assertEqual(first["status"], "discarded")
        self.assertEqual(second["status"], "discarded")
        self.assertEqual(self._note_count(), 0)
        self.assertEqual(list_deleted_notes(self.database_path), [])

    def test_injection_script_rejects_real_roots_and_is_repeatable(self) -> None:
        formal_root = Path.home() / "Library" / "Application Support" / "S-notes"
        with patch.object(injection_script, "DATA_ROOT_OVERRIDDEN", True), patch.object(
            injection_script, "USER_DATA_ROOT", formal_root
        ):
            with self.assertRaisesRegex(SystemExit, "必须位于 /private/tmp"):
                injection_script._require_isolated_data_root()

        project_data_root = Path(__file__).resolve().parents[1] / "data"
        with patch.object(injection_script, "DATA_ROOT_OVERRIDDEN", True), patch.object(
            injection_script, "USER_DATA_ROOT", project_data_root
        ):
            with self.assertRaisesRegex(SystemExit, "必须位于 /private/tmp"):
                injection_script._require_isolated_data_root()

        safe_name = Path("/private/tmp/snotes-r1b-fictional-guard")
        with patch.object(injection_script, "DATA_ROOT_OVERRIDDEN", False), patch.object(
            injection_script, "USER_DATA_ROOT", safe_name
        ):
            with self.assertRaisesRegex(SystemExit, "必须显式设置"):
                injection_script._require_isolated_data_root()

        with tempfile.TemporaryDirectory(
            prefix="snotes-r1b-injection-",
            dir="/private/tmp",
        ) as temporary_root:
            root = Path(temporary_root)
            database_path = root / "s_notes.db"
            output = io.StringIO()
            with patch.object(
                injection_script, "DATA_ROOT_OVERRIDDEN", True
            ), patch.object(
                injection_script, "USER_DATA_ROOT", root
            ), patch.object(
                injection_script, "DATABASE_PATH", database_path
            ), redirect_stdout(output):
                self.assertEqual(injection_script.main(), 0)
                self.assertEqual(injection_script.main(), 0)
            with _connect(database_path) as connection:
                rows = connection.execute(
                    """
                    SELECT external_submission_id, idempotency_key, status
                    FROM ai_receipt_inbox ORDER BY external_submission_id
                    """
                ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row["status"] == "pending" for row in rows))
            self.assertEqual(
                len({row["idempotency_key"] for row in rows}),
                2,
            )
            self.assertNotIn(
                "A fictional Gale submission for R1B local inbox testing.",
                output.getvalue(),
            )

    def test_existing_receipt_inbox_card_exposes_gale_review_fields(self) -> None:
        submission = self._submission("GUI")
        card = ReceiptCard(submission)
        try:
            self.assertTrue(card.is_gale_submission)
            self.assertEqual(card.accept_button.text(), "接受")
            self.assertEqual(card.reject_button.text(), "丢弃")
            summary = card.summary_label.full_text
            self.assertIn("来源: Gale", summary)
            self.assertIn("投稿时间:", summary)
            self.assertIn("建议目录:", summary)
            self.assertIn("建议标签:", summary)
            self.assertTrue(card.title_input.isEnabled())
            self.assertTrue(card.body_edit.isEnabled())
            self.assertTrue(card.ai_visibility_combo.isEnabled())
            self.assertTrue(all(box.isEnabled() for box in card.ai_target_checks.values()))
        finally:
            card.deleteLater()
