from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QHBoxLayout

from src.database import create_note, get_note, initialize_database
from src.main_window import MainWindow


class EditorExperienceUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        initialize_database()

    def setUp(self) -> None:
        self.first = create_note()
        self.second = create_note()
        self.window = MainWindow()
        self.window.show()
        self.window.refresh_directory_tree(self.first["id"])
        self.window.load_note(self.first["id"])
        self.window.refresh_directory_tree(self.first["id"])
        self.app.processEvents()

    def tearDown(self) -> None:
        self.window.close()
        self.app.processEvents()

    def test_title_actions_share_one_row_above_full_width_separator(self) -> None:
        title_layout = self.window.title_band.layout()
        self.assertIsInstance(title_layout, QHBoxLayout)
        self.assertEqual(title_layout.indexOf(self.window.title_input), 0)
        self.assertEqual(title_layout.indexOf(self.window.editor_action_bar), 1)
        self.assertEqual(
            [
                self.window.pin_button.toolTip(),
                self.window.link_drawer_button.toolTip(),
                self.window.export_button.toolTip(),
            ],
            ["固定笔记", "打开关联", "导出笔记"],
        )
        self.assertEqual(self.window.title_separator.objectName(), "titleSeparator")
        self.assertFalse(hasattr(self.window, "gale_menu_button"))

    def test_real_theme_switch_keeps_persisted_editor_html_unchanged(self) -> None:
        self.window.body_edit.insertHtml(
            "<p><strong>虚构主题切换正文</strong></p>"
        )
        before_html = self.window.body_edit.note_content_html()
        for theme_name in ("dark", "light-slate", "light-clay"):
            self.window.set_theme(theme_name)
            self.app.processEvents()
            self.assertEqual(self.window.body_edit.note_content_html(), before_html)

    def test_normal_delete_saves_then_moves_to_trash_without_confirmation(self) -> None:
        visible_before = self.window.visible_note_ids()
        target_id = self.first["id"]
        target_index = visible_before.index(target_id)
        remaining = [note_id for note_id in visible_before if note_id != target_id]
        expected_adjacent = remaining[min(target_index, len(remaining) - 1)]
        self.window.body_edit.insertPlainText("虚构删除前未保存正文")
        self.app.processEvents()

        with patch("src.main_window.QMessageBox") as message_box:
            self.window.confirm_delete_note(target_id)

        message_box.assert_not_called()
        deleted = get_note(target_id, include_deleted=True)
        self.assertIsNotNone(deleted)
        self.assertTrue(deleted["is_deleted"])
        self.assertIn("虚构删除前未保存正文", deleted["content_html"])
        self.assertEqual(self.window.current_note["id"], expected_adjacent)
        self.assertEqual(self.window.status_label.text(), "已移入回收站")
        self.assertIn(
            expected_adjacent,
            self.window.selected_note_ids(),
        )

    def test_normal_delete_aborts_when_presave_fails(self) -> None:
        target_id = self.first["id"]
        self.window.body_edit.insertPlainText("虚构保存失败正文")
        self.app.processEvents()

        with (
            patch(
                "src.main_window.save_editor_state",
                side_effect=RuntimeError("虚构保存失败"),
            ),
            patch("src.main_window.soft_delete_note") as soft_delete,
        ):
            self.window.confirm_delete_note(target_id)

        soft_delete.assert_not_called()
        self.assertFalse(get_note(target_id, include_deleted=True)["is_deleted"])
        self.assertTrue(self.window.has_unsaved_changes)
        self.assertIn("保存失败", self.window.status_label.text())

    def test_permanent_delete_still_requires_explicit_confirmation(self) -> None:
        target_id = self.first["id"]
        with (
            patch.object(self.window, "confirm_continue", return_value=False) as confirm,
            patch("src.main_window.permanently_delete_note") as permanent_delete,
        ):
            self.window.confirm_permanent_delete_note(target_id)

        confirm.assert_called_once()
        self.assertEqual(confirm.call_args.args[0], "永久删除笔记")
        self.assertIn("不可恢复", confirm.call_args.args[1])
        permanent_delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
