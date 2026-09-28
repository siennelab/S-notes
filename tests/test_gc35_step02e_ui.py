"""Step 2E AI Center acceptance with fictional notes in a temporary database."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox

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
from src.snotes.ui.ai_center_dialog import ReceiptInboxView
from src.snotes.ui.markdown_canonicalizer import CanonicalizationError, MarkdownCanonicalizer
from tests.canonicalizer_test_double import CanonicalizerTestDouble


class PendingModificationUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.test_home = tempfile.TemporaryDirectory(prefix="snotes-2e-ui-home-")
        cls.environment = patch.dict(os.environ, {
            "HOME": cls.test_home.name,
            "S_NOTES_DATA_ROOT": cls.test_home.name,
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
        cls.test_home.cleanup()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-2e-ui-db-")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.connections = initialize_database(root / "fixture.db")
        directory_repository = SQLiteDirectoriesRepository(self.connections)
        tag_repository = SQLiteTagsRepository(self.connections)
        note_repository = SQLiteNotesRepository(self.connections, directory_repository)
        self.notes = NoteService(note_repository)
        self.directories = DirectoryService(directory_repository)
        self.metadata = AiAccessMetadataService(note_repository)
        self.canonicalizer = CanonicalizerTestDouble(
            lambda markdown: self._canonicalize(markdown)
        )
        self.receipts = AiReceiptInboxService(
            SQLiteAiReceiptInboxRepository(self.connections, directory_repository, tag_repository),
            notes=self.notes,
            tags=TagService(tag_repository),
            ai_metadata=self.metadata,
            mcp_operations=SQLiteAiMcpOperationRepository(self.connections, tag_repository),
            canonicalizer=self.canonicalizer,
        )
        self.writes = LocalMcpWriteService(
            actor_target_id="clawbot",
            notes=self.notes,
            directories=self.directories,
            tags=TagService(tag_repository),
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

    @staticmethod
    def _canonicalize(markdown: str) -> str:
        with MarkdownCanonicalizer() as milkdown:
            return milkdown.canonicalize_markdown(markdown)

    def _note(self, body: str = "base\n"):
        created = self.notes.create_note(NoteLocation("项目", "原分类"))
        note = self.notes.save_note(
            created.id, title="原标题", content_markdown=body,
            location=created.location, expected_revision=created.revision,
        )
        self.metadata.set_note_visibility(note.id, "full")
        self.metadata.set_note_targets(note.id, ("clawbot",))
        return self.notes.get_note(note.id)

    def _proposal(self, operation: str, note, *, request_id: str):
        arguments = {
            "request_id": request_id,
            "note_id": note.id,
            "expected_version": note_version_token(note.id, note.updated_at),
        }
        if operation == "prepend":
            result = self.writes.prepend_note(**arguments, content="- new")
        else:
            destination = self.notes.create_note(NoteLocation("项目", "新分类"))
            self.metadata.set_note_visibility(destination.id, "full")
            self.metadata.set_note_targets(destination.id, ("clawbot",))
            directory_id = self.directories.find_active_location_id(destination.location)
            self.assertIsNotNone(directory_id)
            result = self.writes.update_note_metadata(
                **arguments,
                title="新标题",
                directory_id=directory_id,
                add_tags=["新标签"],
            )
        self.assertEqual(result["status"], "pending_confirmation", result)
        return result["proposal_id"]

    def _view(self, receipt_id: int):
        view = ReceiptInboxView(self.receipts, self.metadata)
        self.addCleanup(view.close)
        view.resize(1000, 800)
        view.show()
        view.toggle_card(receipt_id)
        return view, view.cards[receipt_id]

    def test_prepend_accept_uses_bundled_final_preview(self) -> None:
        note = self._note()
        receipt_id = self._proposal("prepend", note, request_id="ui-prepend-accept")
        view, card = self._view(receipt_id)
        self.assertTrue(card.has_final_preview)
        self.assertFalse(card.accept_button.isEnabled())
        card.refresh_final_preview()
        self.assertTrue(card.final_preview_ready)
        self.assertIn("+* new", card.final_preview.toPlainText())
        with patch.object(QMessageBox, "warning", side_effect=AssertionError("accept failed")):
            QTest.mouseClick(card.accept_button, Qt.LeftButton)
        accepted = self.receipts.get_receipt(receipt_id)
        self.assertEqual(accepted.status, "accepted")
        self.assertEqual(self.notes.get_note(note.id).content_markdown, "* new\n\nbase\n")
        self.assertNotIn(receipt_id, view.cards)

    def test_prepend_discard_and_failed_preview_gate(self) -> None:
        note = self._note()
        receipt_id = self._proposal("prepend", note, request_id="ui-prepend-discard")
        view, card = self._view(receipt_id)
        with patch.object(
            self.canonicalizer,
            "canonicalize",
            side_effect=CanonicalizationError("fixture failure"),
        ):
            card.refresh_final_preview()
        self.assertFalse(card.final_preview_ready)
        self.assertFalse(card.accept_button.isEnabled())
        view.accept_receipt(receipt_id)
        self.assertEqual(self.receipts.get_receipt(receipt_id).status, "pending")
        QTest.mouseClick(card.discard_button, Qt.LeftButton)
        self.assertEqual(self.receipts.get_receipt(receipt_id).status, "discarded")
        self.assertEqual(self.notes.get_note(note.id), note)

    def test_metadata_accept_shows_only_stored_proposed_changes(self) -> None:
        note = self._note("- legacy")
        receipt_id = self._proposal("metadata", note, request_id="ui-metadata-accept")
        view, card = self._view(receipt_id)
        self.assertFalse(card.accept_button.isEnabled())
        card.refresh_metadata_preview()
        self.assertTrue(card.metadata_preview_ready)
        shown = card.metadata_preview.text()
        self.assertIn("标题：新标题", shown)
        self.assertIn("分类：项目 / 新分类", shown)
        self.assertIn("标签：新标签", shown)
        all_labels = "\n".join(label.text() for label in card.details.findChildren(QLabel))
        self.assertNotIn("AI 可见性", all_labels)
        self.assertNotIn("AI 对象", all_labels)
        self.assertNotIn("正文", all_labels)
        self.assertFalse(hasattr(card, "body_edit"))
        self.assertFalse(hasattr(card, "visibility_combo"))
        self.assertFalse(hasattr(card, "target_checks"))
        with patch.object(QMessageBox, "warning", side_effect=AssertionError("accept failed")):
            QTest.mouseClick(card.accept_button, Qt.LeftButton)
        accepted = self.receipts.get_receipt(receipt_id)
        self.assertEqual(accepted.status, "accepted")
        updated = self.notes.get_note(note.id)
        self.assertEqual(updated.title, "新标题")
        self.assertEqual(updated.location, NoteLocation("项目", "新分类"))
        self.assertEqual(updated.content_markdown, note.content_markdown)
        self.assertNotIn(receipt_id, view.cards)

    def test_metadata_discard_and_failed_preview_gate(self) -> None:
        note = self._note()
        receipt_id = self._proposal("metadata", note, request_id="ui-metadata-discard")
        view, card = self._view(receipt_id)
        with patch.object(self.receipts, "pending_metadata_changes", side_effect=ValueError("bad payload")):
            card.refresh_metadata_preview()
        self.assertFalse(card.metadata_preview_ready)
        self.assertFalse(card.accept_button.isEnabled())
        view.accept_receipt(receipt_id)
        self.assertEqual(self.receipts.get_receipt(receipt_id).status, "pending")
        QTest.mouseClick(card.discard_button, Qt.LeftButton)
        self.assertEqual(self.receipts.get_receipt(receipt_id).status, "discarded")
        self.assertEqual(self.notes.get_note(note.id), note)


if __name__ == "__main__":
    unittest.main()
