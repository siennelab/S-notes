from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.snotes.bootstrap import build_application
from src.snotes.domain.models import NoteLocation
from src.snotes.ui.sidebar import PAYLOAD_ROLE


class Package8FinalUiParityResidualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-r2-p8-final-ui-"
        )
        self.addCleanup(self.temporary.cleanup)
        _application, self.window = build_application(
            [], data_root=Path(self.temporary.name) / "data"
        )
        self.addCleanup(self.window.close)

    def _trash_item(self, item_type: str, item_id: int):
        tree = self.window.shell.sidebar.trash_tree

        def visit(item):
            payload = item.data(0, PAYLOAD_ROLE) or {}
            if payload.get("type") == item_type and payload.get("id") == item_id:
                return item
            for index in range(item.childCount()):
                if match := visit(item.child(index)):
                    return match
            return None

        for index in range(tree.topLevelItemCount()):
            if match := visit(tree.topLevelItem(index)):
                return match
        self.fail(f"trash item not found: {item_type}/{item_id}")

    def test_deleted_note_click_previews_full_content_without_writes(self) -> None:
        note = self.window.note_service.create_note(
            NoteLocation("项目", "Package 8", "只读预览")
        )
        note = self.window.note_service.save_note(
            note.id,
            title="虚构回收站预览",
            content_html="<p>虚构完整正文</p>",
            location=note.location,
            expected_revision=note.revision,
        )
        self.window.tag_service.set_note_tags(note.id, ("preview", "fictional"))
        metadata = self.window.ai_access_metadata_service
        metadata.set_note_visibility(note.id, "redacted")
        metadata.set_note_targets(note.id, ("common", "gale"))
        self.window.delete_notes([note.id])
        before = self.window.note_service.get_note(note.id, include_deleted=True)
        self.assertIsNotNone(before)

        self.window.show_trash()
        self.window.show()
        self.application.processEvents()
        tree = self.window.shell.sidebar.trash_tree
        item = self._trash_item("deleted_note", note.id)
        QTest.mouseClick(
            tree.viewport(),
            Qt.LeftButton,
            pos=tree.visualItemRect(item).center(),
        )
        self.application.processEvents()

        shell = self.window.shell
        self.assertEqual(self.window.status_label.text(), "回收站中 · 只读")
        self.assertEqual(self.window.title_input.text(), "虚构回收站预览")
        self.assertEqual(self.window.body_edit.toPlainText(), "虚构完整正文")
        self.assertIn("#preview", shell.metadata_summary_button.text())
        self.assertIn("项目 / Package 8", shell.metadata_summary_button.text())
        self.assertEqual(shell.ai_visibility_combo.currentData(), "redacted")
        self.assertEqual(shell.selected_ai_target_ids(), ("common", "gale"))

        self.assertIsNone(self.window.current_note)
        self.assertTrue(self.window.title_input.isReadOnly())
        self.assertTrue(self.window.body_edit.isReadOnly())
        self.assertTrue(shell.tags_input.isReadOnly())
        self.assertFalse(shell.editor_toolbar.isEnabled())
        self.assertFalse(shell.main_combo.isEnabled())
        self.assertFalse(shell.sub_combo.isEnabled())
        self.assertFalse(shell.ai_visibility_combo.isEnabled())
        self.assertTrue(
            all(
                not checkbox.isEnabled()
                for checkbox in shell.ai_target_checks.values()
            )
        )
        self.assertFalse(shell.delete_button.isEnabled())
        self.assertFalse(self.window.save_timer.isActive())
        self.assertFalse(self.window.has_unsaved_changes)
        self.assertFalse(self.window.body_edit.document().isModified())

        original_title = self.window.title_input.text()
        original_body = self.window.body_edit.toPlainText()
        self.window.title_input.setFocus()
        QTest.keyClicks(self.window.title_input, "blocked")
        self.window.body_edit.setFocus()
        QTest.keyClicks(self.window.body_edit, "blocked")
        mime = QMimeData()
        mime.setText("blocked paste")
        self.window.body_edit.insertFromMimeData(mime)
        self.application.processEvents()
        self.assertEqual(self.window.title_input.text(), original_title)
        self.assertEqual(self.window.body_edit.toPlainText(), original_body)
        self.assertTrue(self.window.save_current_note())

        after = self.window.note_service.get_note(note.id, include_deleted=True)
        self.assertEqual(after.revision, before.revision)
        self.assertEqual(after.updated_at, before.updated_at)
        self.assertEqual(after.content_html, before.content_html)

    def test_restored_note_is_editable_when_reopened_normally(self) -> None:
        note = self.window.note_service.create_note()
        self.window.delete_notes([note.id])
        self.window.show_trash()
        self.assertTrue(self.window.load_deleted_note(note.id))

        self.window.restore_trash_items([note.id], [])
        self.window.show_directory()
        self.assertTrue(self.window.load_note(note.id))

        restored = self.window.note_service.get_note(note.id)
        self.assertIsNotNone(restored)
        self.assertEqual(self.window.current_mode, "directory")
        self.assertEqual(self.window.current_note.id, note.id)
        self.assertFalse(self.window.title_input.isReadOnly())
        self.assertFalse(self.window.body_edit.isReadOnly())
        self.assertTrue(self.window.shell.editor_toolbar.isEnabled())

    def test_deleted_directory_click_only_toggles_tree(self) -> None:
        root = self.window.directory_service.create_directory("虚构删除目录")
        self.window.directory_service.soft_delete_directory(root.id)
        self.window.show_trash()
        item = self._trash_item("deleted_directory", root.id)
        was_expanded = item.isExpanded()

        self.window.shell.sidebar.trash_tree.itemClicked.emit(item, 0)

        self.assertEqual(item.isExpanded(), not was_expanded)
        self.assertIsNone(self.window.current_note)

    def test_destructive_trash_actions_still_require_confirmation(self) -> None:
        note = self.window.note_service.create_note()
        self.window.note_service.soft_delete_note(note.id)
        with patch.object(self.window, "confirm", return_value=False) as confirm:
            self.window.permanently_delete_trash_items([note.id], [])
            self.window.empty_trash()
        self.assertEqual(confirm.call_count, 2)
        self.assertIsNotNone(
            self.window.note_service.get_note(note.id, include_deleted=True)
        )


if __name__ == "__main__":
    unittest.main()
