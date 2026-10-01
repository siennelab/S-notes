from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from src.snotes.application.ai_receipt_inbox_service import AiReceiptAcceptFields
from src.snotes.bootstrap import build_application
from src.snotes.reminder_authority import LOCAL_REMINDER_AUTHORITY
from src.snotes.domain.models import NoteLocation
from src.snotes.storage.connection import SQLiteConnectionFactory


class Package4AiCenterReceiptLifecycleUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _close_window(self):
        self.window.has_unsaved_changes = False
        self.window.conflict_active = False
        self.window.close()
        deadline = time.monotonic() + 15
        while (not self.window._close_ready or self.window.receipt_refresh_timer.isActive()) and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertTrue(self.window._close_ready)
        QTest.qWait(20)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-r2-package4-ai-center-ui-"
        )
        self.addCleanup(self.temporary.cleanup)
        self.data_root = Path(self.temporary.name) / "data"
        _application, self.window = build_application([], data_root=self.data_root, reminder_authority=LOCAL_REMINDER_AUTHORITY)
        self.addCleanup(self._close_window)
        self.connections = SQLiteConnectionFactory(self.data_root / "s_notes.db")

    def _wait(self, predicate, timeout: float = 15) -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("Timed out waiting for AI Center UI state")
            QTest.qWait(20)

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
                ("yan", "砚"),
                ("clawbot", "Clawbot"),
                ("antigravity", "Antigravity"),
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
        self.assertEqual(note.content_markdown, "UI line one\nUI line two\n")
        self.assertEqual(note.main_category, "项目")
        self.assertEqual(note.sub_category, "UI验收")
        self.assertIn(note.id, [item.id for item in self.window.visible_notes])
        self.assertTrue(dialog.isVisible())
        QTest.mouseClick(view.processed_button, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(set(view.cards), {1})
        self.assertEqual(view.cards[1].receipt.status_label, "已接受")

    def test_accept_reload_current_clean_note(self) -> None:
        note = self.window.note_service.create_note(NoteLocation("项目", "虚构"))
        note = self.window.note_service.save_note(
            note.id,
            title="Before AI",
            content_markdown="before\n",
            location=note.location,
            expected_revision=note.revision,
        )
        self.window.load_note(note.id)
        self._wait(lambda: self.window.current_note is not None and self.window.body_edit.can_save)

        accepted = self.window.note_service.save_note(
            note.id,
            title="After AI",
            content_markdown="after\n",
            location=note.location,
            expected_revision=note.revision,
        )
        self.window._receipt_note_created(note.id)

        self._wait(
            lambda: self.window.current_note is not None
            and self.window.current_note.revision == accepted.revision
            and self.window.body_edit.can_save
            and self.window.body_edit.latest_markdown == accepted.content_markdown
        )
        self.assertEqual(self.window.title_input.text(), "After AI")
        self.assertFalse(self.window.has_unsaved_changes)
        self.assertFalse(self.window.conflict_active)

    def test_accept_preserves_dirty_current_note_and_offers_reload(self) -> None:
        note = self.window.note_service.create_note(NoteLocation("项目", "虚构"))
        note = self.window.note_service.save_note(
            note.id,
            title="Before AI",
            content_markdown="before\n",
            location=note.location,
            expected_revision=note.revision,
        )
        self.window.load_note(note.id)
        self._wait(lambda: self.window.current_note is not None and self.window.body_edit.can_save)
        self.window.title_input.setText("LOCAL UNSAVED TITLE")
        self._wait(lambda: self.window.has_unsaved_changes)
        self.window.save_timer.stop()

        self.window.note_service.save_note(
            note.id,
            title="After AI",
            content_markdown="after\n",
            location=note.location,
            expected_revision=note.revision,
        )
        self.window._receipt_note_created(note.id)
        self.app.processEvents()

        self.assertEqual(self.window.title_input.text(), "LOCAL UNSAVED TITLE")
        self.assertEqual(self.window.body_edit.latest_markdown, "before\n")
        self.assertEqual(self.window.current_note.revision, note.revision)
        self.assertTrue(self.window.has_unsaved_changes)
        self.assertTrue(self.window.conflict_active)
        self.assertFalse(self.window.save_timer.isActive())
        self.assertFalse(self.window.shell.reload_button.isHidden())
        self.assertIn("AI 修改已接受", self.window.status_label.text())

    def test_accept_other_note_does_not_reload_current_note(self) -> None:
        current = self.window.note_service.create_note(NoteLocation("项目", "虚构"))
        current = self.window.note_service.save_note(
            current.id,
            title="Current",
            content_markdown="current\n",
            location=current.location,
            expected_revision=current.revision,
        )
        other = self.window.note_service.create_note(NoteLocation("项目", "虚构"))
        self.window.load_note(current.id)
        self._wait(lambda: self.window.current_note is not None and self.window.body_edit.can_save)

        updated_other = self.window.note_service.save_note(
            other.id,
            title="Other after AI",
            content_markdown="other\n",
            location=other.location,
            expected_revision=other.revision,
        )
        self.window._receipt_note_created(other.id)
        self.app.processEvents()

        self.assertEqual(self.window.current_note.id, current.id)
        self.assertEqual(self.window.title_input.text(), "Current")
        self.assertEqual(self.window.body_edit.latest_markdown, "current\n")
        self.assertFalse(self.window.has_unsaved_changes)
        self.assertIn(
            (updated_other.id, "Other after AI"),
            [(item.id, item.title) for item in self.window.visible_notes],
        )

    def test_accept_create_refreshes_list_without_stealing_current_note(self) -> None:
        current = self.window.note_service.create_note(NoteLocation("项目", "虚构"))
        current = self.window.note_service.save_note(
            current.id,
            title="Stay here",
            content_markdown="current\n",
            location=current.location,
            expected_revision=current.revision,
        )
        self.window.load_note(current.id)
        self._wait(lambda: self.window.current_note is not None and self.window.body_edit.can_save)
        self._insert_receipt(7, "pending", title="New from AI")
        self.window.open_ai_center()
        self.app.processEvents()
        view = self.window.ai_center_dialog.receipt_view

        QTest.mouseClick(view.cards[7].accept_button, Qt.LeftButton)
        self.app.processEvents()

        receipt = self.window.receipt_inbox_service.get_receipt(7)
        self.assertEqual(receipt.status, "accepted")
        self.assertEqual(self.window.current_note.id, current.id)
        self.assertEqual(self.window.title_input.text(), "Stay here")
        self.assertIn(receipt.note_id, [item.id for item in self.window.visible_notes])

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

    def test_request_id_receipt_defaults_and_final_ui_metadata_are_persisted(self) -> None:
        self.window.note_service.create_note(NoteLocation("项目", "虚构"))
        self._insert_receipt(
            6,
            "pending",
            title="AI metadata choice",
            visibility="full",
            targets="gale",
            request_id="fictional-ui-request-6",
            actor_target_id="gale",
            operation_type="create",
        )
        self.window.open_ai_center()
        self.app.processEvents()
        view = self.window.ai_center_dialog.receipt_view
        QTest.mouseClick(view.cards[6], Qt.LeftButton)
        self.app.processEvents()
        card = view.cards[6]

        self.assertEqual(card.visibility_combo.currentData(), "full")
        self.assertTrue(card.target_checks["gale"].isChecked())
        self.assertFalse(card.target_checks["ke-den"].isChecked())

        card.visibility_combo.setCurrentIndex(
            card.visibility_combo.findData("redacted")
        )
        card.target_checks["gale"].setChecked(False)
        card.target_checks["ke-den"].setChecked(True)
        QTest.mouseClick(card.accept_button, Qt.LeftButton)
        self.app.processEvents()

        receipt = self.window.receipt_inbox_service.get_receipt(6)
        self.assertEqual(receipt.status, "accepted")
        metadata = self.window.ai_access_metadata_service.get_note_metadata(
            receipt.note_id
        )
        self.assertEqual(metadata.visibility, "redacted")
        self.assertEqual(metadata.target_ids, ("ke-den",))
        self.assertNotIn("gale", metadata.target_ids)

    def test_ai_center_tooltip_has_no_package_phase_copy(self) -> None:
        button = self.window.shell.sidebar.ai_center_button
        self.assertEqual(button.toolTip(), "打开 AI 中心")
        self.window.shell.sidebar.set_ai_center_available(False)
        self.assertFalse(button.isEnabled())
        self.assertEqual(button.toolTip(), "AI 中心当前不可用")
        self.assertNotIn("Package 4", button.toolTip())
        self.window.shell.sidebar.set_ai_center_available(True)
        self.assertTrue(button.isEnabled())
        self.assertEqual(button.toolTip(), "打开 AI 中心")

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
            tags="linked",
            visibility="full",
            target_ids=("gale",),
        )

    def _insert_receipt(
        self,
        receipt_id: int,
        status: str,
        *,
        title: str,
        visibility: str = "hidden",
        targets: str = "",
        request_id: str | None = None,
        actor_target_id: str | None = None,
        operation_type: str | None = None,
    ) -> None:
        with self.connections.write() as connection:
            connection.execute(
                """
                INSERT INTO ai_receipt_inbox (
                    id, raw_text, parse_status, status, source, action, title,
                    main_category, sub_category, tags,
                    ai_visibility, ai_targets, body, created_at, updated_at,
                    request_id, actor_target_id, operation_type
                ) VALUES (
                    ?, 'FICTIONAL_UI_RAW', 'parsed', ?, 'Fictional UI', '新增',
                    ?, '项目', '虚构', 'fixture', ?, ?,
                    'FICTIONAL_UI_BODY', '2026-08-10T07:00:00',
                    '2026-08-10T08:00:00', ?, ?, ?
                )
                """,
                (
                    receipt_id,
                    status,
                    title,
                    visibility,
                    targets,
                    request_id,
                    actor_target_id,
                    operation_type,
                ),
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
