from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QHBoxLayout

from src.database import create_note, get_note, initialize_database
from src.main_window import (
    EDITOR_BOTTOM_MARGIN,
    TITLE_SEPARATOR_HEIGHT,
    MainWindow,
)
from src.theme import THEME_LABELS, THEME_SETTINGS_PATH, load_saved_theme


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
        self.assertEqual(
            self.window.title_separator.height(),
            TITLE_SEPARATOR_HEIGHT,
        )
        self.assertFalse(hasattr(self.window, "gale_menu_button"))

    def test_editor_body_uses_narrow_bottom_outer_margin(self) -> None:
        sheet_margins = self.window.editor_sheet.layout().contentsMargins()
        self.assertEqual(sheet_margins.bottom(), EDITOR_BOTTOM_MARGIN)
        body_margins = self.window.body_section.layout().contentsMargins()
        self.assertEqual(
            (
                body_margins.left(),
                body_margins.top(),
                body_margins.right(),
                body_margins.bottom(),
            ),
            (0, 0, 0, 0),
        )

    def test_real_theme_switch_keeps_persisted_editor_html_unchanged(self) -> None:
        self.window.body_edit.insertHtml(
            "<p><strong>虚构主题切换正文</strong></p>"
        )
        before_html = self.window.body_edit.note_content_html()
        for theme_name in ("dark", "light-slate", "light-clay"):
            self.window.set_theme(theme_name)
            self.app.processEvents()
            self.assertEqual(self.window.body_edit.note_content_html(), before_html)

    def test_theme_swatches_share_footer_row_and_replace_top_text_buttons(self) -> None:
        footer_layout = self.window.sidebar_footer.layout()
        self.assertIsInstance(footer_layout, QHBoxLayout)
        self.assertEqual(self.window.sidebar_footer.height(), 32)
        self.assertEqual(footer_layout.indexOf(self.window.trash_button), 0)
        swatches = list(self.window.theme_buttons.values())
        self.assertEqual(
            [button.toolTip() for button in swatches],
            ["夜间", "石板", "陶土"],
        )
        self.assertTrue(all(button.text() == "" for button in swatches))
        self.assertTrue(
            all(button.objectName() == "themeSwatch" for button in swatches)
        )
        self.assertTrue(all(button.size().width() == 20 for button in swatches))
        self.assertEqual(
            [footer_layout.indexOf(button) for button in swatches],
            [1, 2, 3],
        )
        theme_labels = {label for _name, label in THEME_LABELS}
        displayed_theme_text_buttons = [
            button
            for button in self.window.sidebar.findChildren(type(self.window.trash_button))
            if button.text() in theme_labels
        ]
        self.assertEqual(displayed_theme_text_buttons, [])

    def test_theme_swatches_switch_active_state_and_persist_existing_setting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            settings_path = Path(temporary_directory) / "ui_settings.ini"
            with patch("src.theme.THEME_SETTINGS_PATH", settings_path):
                for theme_name in ("dark", "light-slate", "light-clay"):
                    self.window.theme_buttons[theme_name].click()
                    self.app.processEvents()
                    self.assertEqual(self.window.current_theme_name, theme_name)
                    for candidate, button in self.window.theme_buttons.items():
                        self.assertEqual(
                            button.property("active"),
                            candidate == theme_name,
                        )
                self.assertEqual(load_saved_theme(), "light-clay")
                self.assertTrue(settings_path.is_file())
        self.assertNotEqual(THEME_SETTINGS_PATH, settings_path)

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
