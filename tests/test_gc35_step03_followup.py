"""Human Gate follow-up: stale proposals and AI Center feedback on fictional notes."""

from __future__ import annotations

import os
from dataclasses import replace
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_audit_service import AiAuditService
from src.snotes.application.ai_receipt_inbox_service import AiReceiptInboxService
from src.snotes.application.ai_safe_read_service import AiSafeReadService
from src.snotes.application.directory_service import DirectoryService
from src.snotes.application.local_mcp_write_service import LocalMcpWriteService, note_version_token
from src.snotes.application.note_service import NoteService
from src.snotes.application.tag_service import TagService
from src.snotes.domain.models import NoteLocation
from src.snotes.storage.repositories.ai_audit import SQLiteAiAuditRepository
from src.snotes.storage.repositories.ai_mcp_operations import SQLiteAiMcpOperationRepository
from src.snotes.storage.repositories.ai_receipts import SQLiteAiReceiptInboxRepository
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.ui.ai_center_dialog import ReceiptCard, ReceiptInboxView


class Step03FollowupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.home = tempfile.TemporaryDirectory(prefix="snotes-step03-followup-home-")
        cls.environment = patch.dict(os.environ, {
            "HOME": cls.home.name,
            "S_NOTES_DATA_ROOT": cls.home.name,
            "QT_QPA_PLATFORM": "offscreen",
            "QTWEBENGINE_CHROMIUM_FLAGS": "--disable-gpu",
            "PATH": "/usr/bin:/bin",
        })
        cls.environment.start()
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    @classmethod
    def tearDownClass(cls) -> None:
        QTimer.singleShot(0, cls.app.quit)
        cls.app.exec()
        cls.environment.stop()
        cls.home.cleanup()

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="snotes-step03-followup-db-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.connections = initialize_database(root / "fixture.db")
        directory_repository = SQLiteDirectoriesRepository(self.connections)
        tag_repository = SQLiteTagsRepository(self.connections)
        note_repository = SQLiteNotesRepository(self.connections, directory_repository)
        self.notes = NoteService(note_repository)
        self.tags = TagService(tag_repository)
        self.metadata = AiAccessMetadataService(note_repository)
        self.receipts = AiReceiptInboxService(
            SQLiteAiReceiptInboxRepository(self.connections, directory_repository, tag_repository),
            notes=self.notes,
            tags=self.tags,
            ai_metadata=self.metadata,
            mcp_operations=SQLiteAiMcpOperationRepository(self.connections, tag_repository),
        )
        self.writes = LocalMcpWriteService(
            actor_target_id="clawbot",
            notes=self.notes,
            directories=DirectoryService(directory_repository),
            tags=self.tags,
            ai_metadata=self.metadata,
            safe_reads=AiSafeReadService(root / "unused-rules.json"),
            audit=AiAuditService(SQLiteAiAuditRepository(self.connections)),
            receipt_inbox=self.receipts,
        )
        with self.connections.write() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO app_settings(setting_key, setting_value, updated_at) VALUES (?, ?, ?)",
                ("ai_modify_requires_confirmation", "1", "2026-01-01T00:00:00"),
            )

    def _note(self, body: str = "Before\n"):
        created = self.notes.create_note(NoteLocation("项目", "虚构"))
        saved = self.notes.save_note(
            created.id, title="Follow-up fixture", content_markdown=body,
            location=created.location, expected_revision=created.revision,
        )
        self.metadata.set_note_visibility(saved.id, "full")
        self.metadata.set_note_targets(saved.id, ("clawbot",))
        return self.notes.get_note(saved.id)

    @staticmethod
    def _version(note):
        return note_version_token(note.id, note.updated_at)

    def _view(self, receipt_id: int, *, expanded: bool = True):
        view = ReceiptInboxView(self.receipts, self.metadata)
        self.addCleanup(view.close)
        view.resize(1000, 800)
        view.show()
        if expanded:
            view.toggle_card(receipt_id)
        return view, view.cards[receipt_id]

    def _edit(self, note, request_id: str):
        result = self.writes.edit_note(
            request_id=request_id, note_id=note.id,
            expected_version=self._version(note),
            edits=[{"old_markdown": "Before", "new_markdown": "After"}],
        )
        self.assertEqual(result["status"], "pending_confirmation", result)
        return result["proposal_id"]

    def _formal_state(self):
        with self.connections.read() as connection:
            return tuple(tuple(row) for row in connection.execute("SELECT * FROM notes ORDER BY id"))

    def test_stale_edit_accept_records_conflict_and_keeps_stored_diff(self) -> None:
        for expanded in (True, False):
            with self.subTest(expanded=expanded):
                note = self._note()
                request_id = f"followup-stale-{expanded}"
                receipt_id = self._edit(note, request_id)
                if expanded:
                    view, card = self._view(receipt_id)
                    card.refresh_final_preview()
                    self.assertTrue(card.final_preview_ready)
                edited = self.notes.save_note(
                    note.id, title=note.title, content_markdown="S later edit\n",
                    location=note.location, expected_revision=note.revision,
                )
                if not expanded:
                    # The collapsed card was never previewed. Accept still needs
                    # to reach the service and persist the version conflict.
                    view, card = self._view(receipt_id, expanded=False)
                    self.assertTrue(card.accept_button.isEnabled())
                formal_state = self._formal_state()
                with self.connections.read() as connection:
                    history_before = connection.execute(
                        "SELECT COUNT(*) FROM ai_note_history WHERE note_id = ?",
                        (note.id,),
                    ).fetchone()[0]
                with patch.object(QMessageBox, "warning") as warning:
                    QTest.mouseClick(card.accept_button, Qt.LeftButton)
                warning.assert_called_once()
                popup = warning.call_args.args[2]
                self.assertIn("版本冲突", popup)
                self.assertIn("AI 修改未写入", popup)
                self.assertIn("笔记已被更新", popup)
                self.assertEqual(self._formal_state(), formal_state)
                self.assertEqual(self.notes.get_note(note.id), edited)
                with self.connections.read() as connection:
                    history_after = connection.execute(
                        "SELECT COUNT(*) FROM ai_note_history WHERE note_id = ?",
                        (note.id,),
                    ).fetchone()[0]
                self.assertEqual(history_after, history_before)
                operation = self.writes.get_operation_status(request_id)
                self.assertEqual(operation["status"], "conflict", operation)
                self.assertTrue(operation["reason"])
                with self.connections.read() as connection:
                    stored = connection.execute(
                        "SELECT status, result_json FROM ai_operations WHERE request_id = ?",
                        (request_id,),
                    ).fetchone()
                self.assertEqual(stored["status"], "conflict")
                self.assertIn(operation["reason"], stored["result_json"])
                self.assertEqual(self.receipts.get_receipt(receipt_id).status, "rejected")
                if view.list_filter != "processed":
                    view.set_filter("processed")
                if not view.cards[receipt_id].details.isVisible():
                    view.toggle_card(receipt_id)
                processed = view.cards[receipt_id]
                self.assertIn("状态：版本冲突", processed.summary_label.text())
                self.assertNotIn("已拒绝", processed.summary_label.text())
                self.assertEqual(operation["reason"], "笔记已被更新。")
                self.assertIn("Before", processed.edit_diff.toPlainText())
                self.assertIn("After", processed.edit_diff.toPlainText())

    def test_nonconflict_rejection_keeps_rejected_label(self) -> None:
        note = self._note()
        receipt_id = self._edit(note, "ordinary-rejection-label")
        receipt = replace(self.receipts.get_receipt(receipt_id), status="rejected")
        with patch.object(self.receipts, "mcp_get_operation", return_value={
            "status": "rejected", "reason": "用户已拒绝。",
        }):
            card = ReceiptCard(receipt, self.receipts, self.metadata)
        self.addCleanup(card.close)
        self.assertIn("状态：已拒绝", card.summary_label.text())
        self.assertNotIn("版本冲突", card.summary_label.text())

    def test_stale_preview_keeps_pair_visible_without_final_diff(self) -> None:
        note = self._note()
        receipt_id = self._edit(note, "followup-stale-preview")
        view, card = self._view(receipt_id)
        self.notes.save_note(
            note.id, title=note.title, content_markdown="S later edit\n",
            location=note.location, expected_revision=note.revision,
        )
        card.refresh_final_preview()
        self.assertIn("Before", card.edit_diff.toPlainText())
        self.assertIn("After", card.edit_diff.toPlainText())
        self.assertIn("笔记已被更新", card.preview_status.text())
        self.assertFalse(card.final_preview_ready)
        self.assertFalse(card.final_preview.toPlainText())
        self.assertTrue(card.accept_button.isEnabled())
        # A stale preview must still leave an actionable Accept that records conflict.
        with patch.object(QMessageBox, "warning") as warning:
            QTest.mouseClick(card.accept_button, Qt.LeftButton)
        warning.assert_called_once()
        self.assertEqual(self.writes.get_operation_status("followup-stale-preview")["status"], "conflict")

    def test_revoked_grant_keeps_stored_pair_but_blocks_final_preview(self) -> None:
        note = self._note()
        receipt_id = self._edit(note, "followup-revoked-preview")
        view, card = self._view(receipt_id)
        # Isolate the grant check from the version check. The public setter
        # advances the note version, which would stop preview even earlier.
        with self.connections.write() as connection:
            connection.execute(
                "DELETE FROM note_ai_targets WHERE note_id = ? AND target_id = ?",
                (note.id, "clawbot"),
            )
        card.refresh_final_preview()
        self.assertIn("Before", card.edit_diff.toPlainText())
        self.assertIn("After", card.edit_diff.toPlainText())
        self.assertIn("无权", card.preview_status.text())
        self.assertFalse(card.final_preview_ready)
        self.assertFalse(card.final_preview.toPlainText())

    def test_normal_edit_accept_shows_processing_before_service_and_succeeds(self) -> None:
        note = self._note()
        receipt_id = self._edit(note, "followup-normal-edit")
        view, card = self._view(receipt_id)
        card.refresh_final_preview()
        original = self.receipts.accept_receipt
        observed = []

        def observe(*args, **kwargs):
            observed.append((card.accept_button.text(), card.accept_button.isEnabled()))
            return original(*args, **kwargs)

        with patch.object(self.receipts, "accept_receipt", side_effect=observe):
            QTest.mouseClick(card.accept_button, Qt.LeftButton)
        self.assertEqual(observed, [("处理中…", False)])
        self.assertEqual(self.receipts.get_receipt(receipt_id).status, "accepted")
        self.assertEqual(self.notes.get_note(note.id).content_markdown, "After\n")
        self.assertNotIn(receipt_id, view.cards)

    def test_append_accept_shows_processing_and_refreshes(self) -> None:
        note = self._note()
        result = self.writes.append_note(
            request_id="followup-append", note_id=note.id,
            expected_version=self._version(note), content="Later",
        )
        self.assertEqual(result["status"], "pending_confirmation", result)
        receipt_id = result["proposal_id"]
        view, card = self._view(receipt_id)
        card.refresh_final_preview()
        original = self.receipts.accept_receipt
        observed = []

        def observe(*args, **kwargs):
            observed.append((card.accept_button.text(), card.accept_button.isEnabled()))
            return original(*args, **kwargs)

        with patch.object(self.receipts, "accept_receipt", side_effect=observe):
            QTest.mouseClick(card.accept_button, Qt.LeftButton)
        self.assertEqual(observed, [("处理中…", False)])
        self.assertEqual(self.receipts.get_receipt(receipt_id).status, "accepted")
        self.assertIn("Later", self.notes.get_note(note.id).content_markdown)
        self.assertNotIn(receipt_id, view.cards)

    def test_metadata_add_remove_labels_and_stale_fallback(self) -> None:
        note = self._note()
        self.tags.set_note_tags(note.id, ("old-tag", "keep-tag"))
        note = self.notes.get_note(note.id)
        result = self.writes.update_note_metadata(
            request_id="followup-metadata", note_id=note.id,
            expected_version=self._version(note),
            add_tags=["new-tag"], remove_tags=["old-tag"],
        )
        self.assertEqual(result["status"], "pending_confirmation", result)
        receipt_id = result["proposal_id"]
        view, card = self._view(receipt_id)
        card.refresh_metadata_preview()
        self.assertIn("新增标签：new-tag", card.metadata_preview.text())
        self.assertIn("移除标签：old-tag", card.metadata_preview.text())
        self.assertNotIn("keep-tag", card.metadata_preview.text())
        self.notes.save_note(
            note.id, title=note.title, content_markdown="S later edit\n",
            location=note.location, expected_revision=note.revision,
        )
        card.refresh_metadata_preview()
        self.assertIn("标签将设为（完整列表）：", card.metadata_preview.text())
        self.assertNotIn("新增标签：", card.metadata_preview.text())
        self.assertNotIn("移除标签：", card.metadata_preview.text())


if __name__ == "__main__":
    unittest.main()
