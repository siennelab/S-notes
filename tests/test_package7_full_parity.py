from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.snotes.application.ai_receipt_inbox_service import AiReceiptInboxService
from src.snotes.bootstrap import build_application
from src.snotes.domain.ai_receipts import GaleReceiptImport
from src.snotes.domain.models import NoteLocation
from src.snotes.storage.repositories.ai_receipts import SQLiteAiReceiptInboxRepository
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.tags import SQLiteTagsRepository
from src.snotes.storage.schema import initialize_database


class Package7FullParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-r2-p7-")
        self.addCleanup(self.temporary.cleanup)
        self.data_root = Path(self.temporary.name) / "data"

    def test_sidebar_pending_badge_starts_and_refreshes_from_receipt_owner(
        self,
    ) -> None:
        connections = initialize_database(self.data_root / "s_notes.db")
        directories = SQLiteDirectoriesRepository(connections)
        receipts = AiReceiptInboxService(
            SQLiteAiReceiptInboxRepository(
                connections,
                directories,
                SQLiteTagsRepository(connections),
            )
        )
        receipt, created = receipts.import_gale_submission(
            GaleReceiptImport(
                external_submission_id="fictional-package7-submission",
                idempotency_key="fictional-package7-idempotency",
                title="Fictional Package 7 pending",
                body="Fictional Package 7 body",
                location=NoteLocation("临时", "Package 7", "待确认"),
                tags=("package7",),
            )
        )
        self.assertTrue(created)

        _application, window = build_application([], data_root=self.data_root)
        self.addCleanup(window.close)
        button = window.shell.sidebar.ai_center_button
        self.assertEqual(button.text(), "AI中心 (1)")

        window.open_ai_center()
        self.app.processEvents()
        view = window.ai_center_dialog.receipt_view
        QTest.mouseClick(view.cards[receipt.id].discard_button, Qt.LeftButton)
        self.app.processEvents()

        self.assertEqual(button.text(), "AI中心")
        self.assertEqual(
            window.receipt_inbox_service.get_receipt(receipt.id).status,
            "discarded",
        )

if __name__ == "__main__":
    unittest.main()
