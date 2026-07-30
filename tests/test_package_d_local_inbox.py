from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.ai_safe_export import AiSafePackageExporter
from src.database import (
    _connect,
    accept_ai_receipt_inbox_item,
    create_gale_submission,
    create_note,
    get_note,
    get_note_ai_targets,
    initialize_database,
    list_ai_visible_notes,
    list_deleted_notes,
    list_notes,
    list_notes_by_tag,
    list_notes_for_export,
    list_recent_notes,
    reject_ai_receipt_inbox_item,
    search_notes,
    update_note,
)
from src.receipt_inbox_dialog import ReceiptCard
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

    def test_pending_is_excluded_from_every_formal_read_and_export_path(self) -> None:
        pending = self._submission("ISOLATION")
        marker = pending["external_submission_id"]
        self.assertEqual(search_notes(marker, self.database_path), [])
        self.assertEqual(search_notes("fictional Gale submission", self.database_path), [])
        self.assertEqual(list_notes(self.database_path), [])
        self.assertEqual(list_recent_notes(database_path=self.database_path), [])
        self.assertEqual(list_notes_by_tag("虚构投稿", self.database_path), [])
        self.assertEqual(list_ai_visible_notes(self.database_path), [])
        self.assertEqual(list_deleted_notes(self.database_path), [])
        self.assertEqual(list_notes_for_export(self.database_path), [])

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
