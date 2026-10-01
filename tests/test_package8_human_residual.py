from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.snotes.application.ai_receipt_inbox_service import AiReceiptInboxService
from src.snotes.bootstrap import build_application
from src.snotes.reminder_authority import LOCAL_REMINDER_AUTHORITY
from src.snotes.domain.ai_receipts import GaleReceiptImport
from src.snotes.domain.models import NoteLocation
from src.snotes.storage.repositories.ai_receipts import SQLiteAiReceiptInboxRepository
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database


class Package8HumanResidualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-r2-p8-residual-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "data"
        _application, self.window = build_application([], data_root=self.root, reminder_authority=LOCAL_REMINDER_AUTHORITY)
        self.addCleanup(self.window.close)

    def test_single_and_multi_soft_delete_skip_confirmation(self) -> None:
        notes = [
            self.window.note_service.create_note(
                NoteLocation("临时", "Package 8")
            )
            for _index in range(3)
        ]
        with patch.object(
            self.window,
            "confirm",
            side_effect=AssertionError("ordinary soft delete must not confirm"),
        ):
            self.window.delete_notes([notes[0].id])
            self.window.delete_notes([notes[1].id, notes[2].id])

        for note in notes:
            deleted = self.window.note_service.get_note(
                note.id, include_deleted=True
            )
            self.assertIsNotNone(deleted)
            self.assertTrue(deleted.is_deleted)
            restored = self.window.note_service.restore_note(note.id)
            self.assertFalse(restored.is_deleted)

    def test_permanent_delete_and_empty_trash_still_confirm(self) -> None:
        note = self.window.note_service.create_note(
            NoteLocation("临时", "Package 8")
        )
        self.window.note_service.soft_delete_note(note.id)
        with patch.object(self.window, "confirm", return_value=False) as confirm:
            self.window.permanently_delete_trash_items([note.id], [])
            self.window.empty_trash()
        self.assertEqual(confirm.call_count, 2)
        self.assertIsNotNone(
            self.window.note_service.get_note(note.id, include_deleted=True)
        )

    def test_external_pending_refreshes_open_badge_and_page(self) -> None:
        button = self.window.shell.sidebar.ai_center_button
        self.assertEqual(button.text(), "AI中心")
        self.window.show()
        self.window.open_ai_center()
        self.application.processEvents()
        self.assertEqual(
            self.window.ai_center_dialog.receipt_view.pending_button.text(),
            "待确认（0）",
        )

        connections = initialize_database(self.root / "s_notes.db")
        external = AiReceiptInboxService(
            SQLiteAiReceiptInboxRepository(
                connections,
                SQLiteDirectoriesRepository(connections),
                SQLiteTagsRepository(connections),
            )
        )
        for number in (1, 2):
            _receipt, created = external.import_gale_submission(
                GaleReceiptImport(
                    external_submission_id=f"fictional-p8-external-{number}",
                    idempotency_key=f"fictional-p8-key-{number}",
                    title=f"Fictional Package 8 receipt {number}",
                    body="Fictional body",
                    location=NoteLocation("临时", "Package 8"),
                    tags=("package8",),
                )
            )
            self.assertTrue(created)

        self.window.receipt_refresh_timer.setInterval(1)
        QTest.qWait(20)
        self.application.processEvents()
        self.assertEqual(button.text(), "AI中心 (2)")
        self.assertEqual(
            self.window.ai_center_dialog.receipt_view.pending_button.text(),
            "待确认（2）",
        )
        self.assertEqual(
            len(self.window.ai_center_dialog.receipt_view.cards), 2
        )


if __name__ == "__main__":
    unittest.main()
