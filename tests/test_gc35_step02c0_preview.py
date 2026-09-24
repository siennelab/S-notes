"""Step 2C0: read-only final Markdown preview using fictional temporary data."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from src.snotes.application.ai_access_metadata_service import AiAccessMetadataService
from src.snotes.application.ai_receipt_inbox_service import AiReceiptInboxService
from src.snotes.application.note_canonical_candidate import build_canonical_note_candidate
from src.snotes.application.note_service import NoteService
from src.snotes.application.tag_service import TagService
from src.snotes.domain.models import NoteLocation
from src.snotes.storage.repositories.ai_mcp_operations import SQLiteAiMcpOperationRepository
from src.snotes.storage.repositories.ai_receipts import SQLiteAiReceiptInboxRepository
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.ui.ai_center_dialog import ReceiptCard
from src.snotes.ui.markdown_canonicalizer import (
    CanonicalizationError,
    MarkdownCanonicalizer,
    canonicalize_markdown,
)


class FinalCanonicalPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.test_home = tempfile.TemporaryDirectory(prefix="snotes-gc35-2c0-home-")
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
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-gc35-2c0-db-")
        self.addCleanup(self.temporary.cleanup)
        self.connections = initialize_database(Path(self.temporary.name) / "fixture.db")
        directories = SQLiteDirectoriesRepository(self.connections)
        tags_repository = SQLiteTagsRepository(self.connections)
        notes_repository = SQLiteNotesRepository(self.connections, directories)
        self.notes = NoteService(notes_repository)
        self.metadata = AiAccessMetadataService(notes_repository)
        self.receipts = AiReceiptInboxService(
            SQLiteAiReceiptInboxRepository(self.connections, directories, tags_repository),
            notes=self.notes,
            tags=TagService(tags_repository),
            ai_metadata=self.metadata,
            mcp_operations=SQLiteAiMcpOperationRepository(self.connections, tags_repository),
        )

    def _pending_append(self, old: str, fragment: str = "- new"):
        created = self.notes.create_note(NoteLocation("项目", "虚构"))
        note = self.notes.save_note(
            created.id, title="Fictional note", content_markdown=old,
            location=created.location, expected_revision=created.revision,
        )
        result = self.receipts.mcp_create_pending(
            actor_target_id="clawbot", actor_display="Clawbot",
            request_id=f"fixture-preview-{note.id}", operation_type="append",
            title=note.title, note_id=note.id,
            expected_updated_at=note.updated_at,
            payload={"note_id": note.id, "content": fragment,
                     "expected_revision": note.revision},
            location=note.location, tags=(), body=fragment,
            visibility="hidden", target_ids=(),
        )
        self.assertEqual(result["status"], "pending_confirmation")
        return note, result["proposal_id"]

    def _database_dump(self) -> tuple[str, ...]:
        with self.connections.read() as connection:
            return tuple(connection.iterdump())

    def test_canonical_note_preview_is_fixed_point_and_read_only(self) -> None:
        note, receipt_id = self._pending_append("base\n", "- new")
        before_db = self._database_dump()
        with patch.object(self.connections, "write", side_effect=AssertionError("preview wrote DB")):
            preview = self.receipts.build_pending_note_preview(receipt_id)
        self.assertEqual(self._database_dump(), before_db)
        self.assertEqual(preview.before_markdown, note.content_markdown)
        self.assertEqual(preview.note_id, note.id)
        self.assertEqual(preview.note_revision, note.revision)
        self.assertEqual(preview.expected_updated_at, note.updated_at)
        self.assertFalse(preview.legacy_canonicalization_changed)
        self.assertEqual(preview.final_markdown, canonicalize_markdown(preview.final_markdown))
        self.assertEqual(
            preview.final_markdown,
            build_canonical_note_candidate(note.content_markdown, "- new").final_markdown,
        )
        self.assertIn("* new", preview.final_markdown)
        card = ReceiptCard(self.receipts.get_receipt(receipt_id), self.receipts, self.metadata)
        self.addCleanup(card.close)
        card.resize(900, 700)
        card.show()
        card.set_expanded(True)
        card.refresh_final_preview()
        self.app.processEvents()
        self.assertFalse(card.legacy_warning.isVisible())
        self.assertIn("+* new", card.final_preview.toPlainText())

    def test_legacy_preview_shows_full_diff_warning_and_fragment_invalidation(self) -> None:
        note, receipt_id = self._pending_append("- old", "- new")
        before_db = self._database_dump()
        preview = self.receipts.build_pending_note_preview(receipt_id)
        self.assertEqual(preview.before_markdown, "- old")
        self.assertTrue(preview.legacy_canonicalization_changed)
        self.assertIn("* old", preview.final_markdown)
        self.assertIn("* new", preview.final_markdown)
        self.assertEqual(self._database_dump(), before_db)

        card = ReceiptCard(self.receipts.get_receipt(receipt_id), self.receipts, self.metadata)
        self.addCleanup(card.close)
        card.resize(900, 800)
        card.show()
        card.set_expanded(True)
        card.refresh_final_preview()
        self.app.processEvents()
        self.assertTrue(card.final_preview.isReadOnly())
        self.assertTrue(card.legacy_warning.isVisible())
        self.assertIn("规范化这篇旧笔记", card.legacy_warning.text())
        diff = card.final_preview.toPlainText()
        self.assertIn("--- 当前完整正文", diff)
        self.assertIn("+++ 最终 canonical 正文", diff)
        self.assertIn("- old", diff)
        self.assertIn("+* old", diff)
        self.assertIn("+* new", diff)
        # A disposable visual artifact for review; never assert its persistence.
        card.grab().save("/tmp/snotes-step02c0-preview.png")

        card.body_edit.setPlainText("- edited")
        self.assertEqual(card.final_preview.toPlainText(), "")
        self.assertFalse(card.legacy_warning.isVisible())
        card.refresh_final_preview()
        self.assertIn("* edited", card.final_preview.toPlainText())
        self.assertNotIn("* new", card.final_preview.toPlainText())
        self.assertEqual(self._database_dump(), before_db)

    def test_canonicalizer_failure_returns_no_final_body_and_no_write(self) -> None:
        _note, receipt_id = self._pending_append("base\n")
        before_db = self._database_dump()
        with patch(
            "src.snotes.ui.markdown_canonicalizer.MarkdownCanonicalizer.canonicalize_markdown",
            side_effect=CanonicalizationError("fixture Milkdown failure"),
        ):
            with self.assertRaisesRegex(CanonicalizationError, "fixture Milkdown failure"):
                self.receipts.build_pending_note_preview(receipt_id)
        self.assertEqual(self._database_dump(), before_db)

    def test_stale_expected_version_refuses_preview(self) -> None:
        note, receipt_id = self._pending_append("base\n")
        changed = self.notes.save_note(
            note.id, title=note.title, content_markdown="changed\n",
            location=note.location, expected_revision=note.revision,
        )
        self.assertNotEqual(changed.updated_at, note.updated_at)
        before_db = self._database_dump()
        with self.assertRaisesRegex(ValueError, "笔记已被更新"):
            self.receipts.build_pending_note_preview(receipt_id)
        self.assertEqual(self._database_dump(), before_db)

    def test_builder_deterministic_and_preview_uses_edited_payload(self) -> None:
        note, receipt_id = self._pending_append("- old", "- original")
        first = build_canonical_note_candidate(note.content_markdown, "- edited")
        second = build_canonical_note_candidate(note.content_markdown, "- edited")
        preview = self.receipts.build_pending_note_preview(
            receipt_id, addition_markdown="- edited",
        )
        self.assertEqual(first, second)
        self.assertEqual(preview.final_markdown, first.final_markdown)
        self.assertEqual(
            preview.legacy_canonicalization_changed,
            first.legacy_canonicalization_changed,
        )
        self.assertIn("* edited", preview.final_markdown)
        self.assertNotIn("original", preview.final_markdown)

    def test_note_change_during_canonicalization_is_rejected(self) -> None:
        note, receipt_id = self._pending_append("base\n")
        original = MarkdownCanonicalizer.canonicalize_markdown

        def mutate_during_preview(canonicalizer: MarkdownCanonicalizer, markdown: str) -> str:
            if markdown == note.content_markdown:
                self.notes.save_note(
                    note.id, title="Changed while previewing",
                    content_markdown="changed\n", location=note.location,
                    expected_revision=note.revision,
                )
            return original(canonicalizer, markdown)

        # The injected canonicalizer simulates a concurrent save in Qt's
        # nested event loop, so the post-canonicalization read must reject it.
        with patch(
            "src.snotes.ui.markdown_canonicalizer.MarkdownCanonicalizer.canonicalize_markdown",
            side_effect=mutate_during_preview,
            autospec=True,
        ):
            with self.assertRaisesRegex(ValueError, "提案或笔记已被更新"):
                self.receipts.build_pending_note_preview(receipt_id)


if __name__ == "__main__":
    unittest.main()
