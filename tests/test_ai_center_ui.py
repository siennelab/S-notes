from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QCheckBox, QWidget

from src.ai_center_dialog import AiCenterDialog
from src.services.ai_access_service import (
    CREATE_CONFIRMATION_KEY,
    MODIFY_CONFIRMATION_KEY,
)
from src.theme import THEMES, apply_theme, build_app_style


class AiCenterUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def build_dialog(self, **kwargs) -> AiCenterDialog:
        patches = (
            patch("src.ai_center_dialog.list_ai_note_history", return_value=[]),
            patch("src.ai_center_dialog.list_ai_call_logs", return_value=[]),
            patch(
                "src.ai_center_dialog.get_ai_settings",
                return_value={
                    CREATE_CONFIRMATION_KEY: False,
                    MODIFY_CONFIRMATION_KEY: True,
                },
            ),
            patch(
                "src.receipt_inbox_dialog.list_ai_receipt_inbox_items",
                return_value=[],
            ),
        )
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)
        return AiCenterDialog(**kwargs)

    def test_settings_contains_remote_ai_access_without_adding_a_tab(self) -> None:
        dialog = self.build_dialog()
        self.assertEqual(dialog.tabs.count(), 4)
        self.assertEqual(
            [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())],
            ["待确认", "修改历史", "调用记录", "设置"],
        )
        remote_group = dialog.findChild(QWidget, "remoteAiAccessGroup")
        self.assertIsNotNone(remote_group)
        margins = remote_group.layout().contentsMargins()
        self.assertEqual(
            (margins.left(), margins.top(), margins.right(), margins.bottom()),
            (0, 0, 0, 0),
        )
        self.assertFalse(dialog.remote_auto_update.isChecked())
        self.assertEqual(dialog.remote_auto_update.text(), "自动更新")
        self.assertEqual(dialog.remote_update_now.text(), "立即更新")
        auto_update_controls = [
            child
            for child in remote_group.findChildren(QCheckBox)
            if child.text() == "自动更新"
        ]
        self.assertEqual(auto_update_controls, [dialog.remote_auto_update])
        self.assertEqual(
            remote_group.layout().indexOf(dialog.remote_auto_update),
            1,
        )
        self.assertEqual(dialog.remote_title.objectName(), "settingsSectionTitle")
        self.assertEqual(dialog.confirmation_note.objectName(), "settingsHelpText")
        self.assertEqual(dialog.remote_note.objectName(), "settingsHelpText")
        labels = [
            child.text()
            for child in dialog.findChildren(QWidget)
            if hasattr(child, "text")
        ]
        self.assertIn("远程 AI 访问", labels)
        self.assertIn("仅更新符合 AI 对象与 AI 可见性规则的内容", labels)

    def test_remote_controls_call_injected_existing_controllers(self) -> None:
        set_auto = unittest.mock.Mock()
        publish_now = unittest.mock.Mock()
        dialog = self.build_dialog(
            set_auto_publish_enabled=set_auto,
            publish_now=publish_now,
        )
        dialog.remote_auto_update.setChecked(True)
        dialog.remote_update_now.click()
        set_auto.assert_called_once_with(True)
        publish_now.assert_called_once_with()

    def test_primary_tabs_fill_the_row_in_four_equal_responsive_sections(self) -> None:
        apply_theme(self.app, "dark")
        for window_width in (720, 901, 1123):
            dialog = self.build_dialog()
            dialog.resize(window_width, 700)
            dialog.show()
            self.app.processEvents()
            tab_bar = dialog.tabs.tabBar()
            widths = [tab_bar.tabRect(index).width() for index in range(4)]
            self.assertEqual(widths, [widths[0]] * 4)
            self.assertEqual(tab_bar.tabRect(0).left(), 0)
            self.assertEqual(tab_bar.tabRect(3).right(), tab_bar.width() - 1)
            dialog.close()
            self.app.processEvents()

    def test_all_three_themes_cover_ai_center_window_tabs_and_close_button(self) -> None:
        for theme_name, tokens in THEMES.items():
            style = build_app_style(theme_name)
            self.assertIn("QWidget#aiCenterDialog", style)
            self.assertIn("QTabWidget#aiCenterTabs::pane", style)
            pane_style = style.split(
                "QTabWidget#aiCenterTabs::pane {", 1
            )[1].split("}", 1)[0]
            self.assertIn("border: 0;", pane_style)
            self.assertIn("top: 0;", pane_style)
            self.assertNotIn(tokens.sidebar_separator, pane_style)
            self.assertNotIn("#remoteAiAccessGroup {", style)
            self.assertIn(
                f"#settingsSectionTitle {{ color: {tokens.text};",
                style,
            )
            self.assertIn(
                f"#settingsHelpText {{ color: {tokens.text_subtle};",
                style,
            )
            self.assertIn("#aiCenterCloseButton", style)
            self.assertIn(f"background: {tokens.window};", style)
            self.assertIn(f"color: {tokens.text};", style)
            self.assertIn(f"color: {tokens.disabled};", style)

            apply_theme(self.app, theme_name)
            dialog = self.build_dialog()
            dialog.resize(900, 700)
            dialog.show()
            self.app.processEvents()
            self.assertEqual(dialog.objectName(), "aiCenterDialog")
            self.assertEqual(dialog.close_button.objectName(), "aiCenterCloseButton")
            self.assertEqual(dialog.tabs.tabBar().count(), 4)
            dialog.close()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
