from __future__ import annotations

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

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

    def build_dialog(self) -> AiCenterDialog:
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
        return AiCenterDialog()

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
