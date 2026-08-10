from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from src.snotes.application.ai_receipt_inbox_service import AiReceiptAcceptFields
from src.snotes.bootstrap import build_application
from src.snotes.storage.connection import SQLiteConnectionFactory


class Package4AiCenterReceiptLifecycleUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-r2-package4-ai-center-ui-"
        )
        self.addCleanup(self.temporary.cleanup)
        self.data_root = Path(self.temporary.name) / "data"
        _application, self.window = build_application([], data_root=self.data_root)
        self.addCleanup(self.window.close)
        self.connections = SQLiteConnectionFactory(self.data_root / "s_notes.db")

    def test_pending_detail_accept_and_processed_refresh_stay_in_one_window(self) -> None:
        self._insert_receipt(1, "pending", title="Pending A")
        self.window.show()
        QTest.mouseClick(self.window.shell.sidebar.ai_center_button, Qt.LeftButton)
        self.app.processEvents()

        dialog = self.window.ai_center_dialog
        self.assertIsNotNone(dialog)
        self.assertTrue(dialog.isVisible())
        view = dialog.receipt_view
        self.assertEqual(set(view.cards), {1})
        QTest.mouseClick(view.cards[1], Qt.LeftButton)
        self.app.processEvents()
        card = view.cards[1]
        self.assertTrue(card.details.isVisible())
        self.assertEqual(
            tuple(
                (target_id, checkbox.text())
                for target_id, checkbox in card.target_checks.items()
            ),
            (
                ("common", "通用"),
                ("gale", "Gale"),
                ("clawbot", "Clawbot"),
                ("claude-code", "Claude Code"),
                ("ke-den", "Ke"),
            ),
        )
        card.title_input.setText("Accepted from UI")
        card.body_edit.setPlainText("UI line one\nUI line two")
        card.main_input.setText("项目")
        card.sub_input.setText("UI验收")
        card.tags_input.setText("ui, accepted")
        card.visibility_combo.setCurrentIndex(
            card.visibility_combo.findData("full")
        )
        card.target_checks["claude-code"].setChecked(True)
        QTest.mouseClick(card.accept_button, Qt.LeftButton)
        self.app.processEvents()

        receipt = self.window.receipt_inbox_service.get_receipt(1)
        self.assertEqual(receipt.status, "accepted")
        note = self.window.note_service.get_note(receipt.note_id)
        self.assertEqual(note.title, "Accepted from UI")
        self.assertEqual(note.content_html, "<p>UI line one<br>UI line two</p>")
        self.assertEqual(note.main_category, "项目")
        self.assertEqual(note.sub_category, "UI验收")
        self.assertIn(note.id, [item.id for item in self.window.visible_notes])
        self.assertTrue(dialog.isVisible())
        QTest.mouseClick(view.processed_button, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(set(view.cards), {1})
        self.assertEqual(view.cards[1].receipt.status_label, "已接受")

    def test_discard_creates_no_note_or_trash_and_appears_processed(self) -> None:
        self._insert_receipt(2, "pending", title="Pending B")
        counts_before = self._note_counts()
        self.window.open_ai_center()
        self.app.processEvents()
        dialog = self.window.ai_center_dialog
        view = dialog.receipt_view

        QTest.mouseClick(view.cards[2].discard_button, Qt.LeftButton)
        self.app.processEvents()

        receipt = self.window.receipt_inbox_service.get_receipt(2)
        self.assertEqual(receipt.status, "discarded")
        self.assertEqual(self._note_counts(), counts_before)
        self.assertTrue(dialog.isVisible())
        QTest.mouseClick(view.processed_button, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(set(view.cards), {2})
        self.assertEqual(view.cards[2].receipt.status_label, "已丢弃")

    def test_processed_delete_rule_keeps_dialog_and_note_state(self) -> None:
        self._insert_receipt(3, "pending", title="Linked acceptance")
        accepted = self.window.receipt_inbox_service.accept_receipt(
            3,
            self._accept_fields("Linked formal note"),
        )
        self._insert_receipt(4, "discarded", title="Deletable processed")
        note_before = self.window.note_service.get_note(accepted.note.id)
        metadata_before = self.window.ai_access_metadata_service.get_note_metadata(
            accepted.note.id
        )
        self.window.open_ai_center()
        self.app.processEvents()
        dialog = self.window.ai_center_dialog
        view = dialog.receipt_view
        QTest.mouseClick(view.processed_button, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(set(view.cards), {3, 4})
        finished = QSignalSpy(dialog.finished)

        with patch(
            "src.snotes.ui.ai_center_dialog.QMessageBox.warning"
        ) as warning:
            self._confirm_delete(view, 3)
        warning.assert_called_once()
        self.assertIn("保留用于查账", str(warning.call_args))
        self.assertIsNotNone(self.window.receipt_inbox_service.get_receipt(3))
        self.assertEqual(
            self.window.note_service.get_note(accepted.note.id),
            note_before,
        )
        self.assertEqual(
            self.window.ai_access_metadata_service.get_note_metadata(
                accepted.note.id
            ),
            metadata_before,
        )
        self.assertTrue(dialog.isVisible())
        self.assertEqual(finished.count(), 0)

        self._confirm_delete(view, 4)
        self.assertIsNone(self.window.receipt_inbox_service.get_receipt(4))
        self.assertEqual(set(view.cards), {3})
        self.assertTrue(dialog.isVisible())
        self.assertEqual(finished.count(), 0)

    def test_reopen_and_repeated_open_reuse_one_ai_center_window(self) -> None:
        self._insert_receipt(5, "pending", title="Single window")
        self.window.open_ai_center()
        self.app.processEvents()
        first = self.window.ai_center_dialog
        self.window.open_ai_center()
        self.app.processEvents()
        self.assertIs(self.window.ai_center_dialog, first)
        first.close()
        self.app.processEvents()
        self.assertIsNone(self.window.ai_center_dialog)
        self.window.open_ai_center()
        self.app.processEvents()
        self.assertIsNot(self.window.ai_center_dialog, first)
        self.assertEqual(set(self.window.ai_center_dialog.receipt_view.cards), {5})

    def _confirm_delete(self, view, receipt_id: int) -> None:
        view.confirm_delete_receipt(receipt_id)
        self.app.processEvents()
        box = view.delete_confirmation_box
        self.assertIsNotNone(box)
        delete_button = next(
            button
            for button in box.buttons()
            if box.buttonRole(button) == QMessageBox.AcceptRole
        )
        QTest.mouseClick(delete_button, Qt.LeftButton)
        self.app.processEvents()

    @staticmethod
    def _accept_fields(title: str) -> AiReceiptAcceptFields:
        return AiReceiptAcceptFields(
            title=title,
            body="FICTIONAL_LINKED_BODY",
            main_category="项目",
            sub_category="虚构",
            sub_sub_category="无",
            tags="linked",
            visibility="full",
            target_ids=("gale",),
        )

    def _insert_receipt(self, receipt_id: int, status: str, *, title: str) -> None:
        with self.connections.write() as connection:
            connection.execute(
                """
                INSERT INTO ai_receipt_inbox (
                    id, raw_text, parse_status, status, source, action, title,
                    main_category, sub_category, sub_sub_category, tags,
                    ai_visibility, ai_targets, body, created_at, updated_at
                ) VALUES (
                    ?, 'FICTIONAL_UI_RAW', 'parsed', ?, 'Fictional UI', '新增',
                    ?, '项目', '虚构', '无', 'fixture', 'hidden', '',
                    'FICTIONAL_UI_BODY', '2026-08-10T07:00:00',
                    '2026-08-10T08:00:00'
                )
                """,
                (receipt_id, status, title),
            )

    def _note_counts(self) -> tuple[int, int]:
        with self.connections.read() as connection:
            return (
                int(connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0]),
                int(
                    connection.execute(
                        "SELECT COUNT(*) FROM notes WHERE is_deleted = 1"
                    ).fetchone()[0]
                ),
            )


if __name__ == "__main__":
    unittest.main()
