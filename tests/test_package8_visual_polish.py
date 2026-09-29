from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QScrollBar

from src.snotes.ui.theme import THEMES, build_app_style


class Package8VisualPolishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_global_vertical_scrollbar_is_eight_with_six_pixel_handle(self) -> None:
        for theme_name, tokens in THEMES.items():
            with self.subTest(theme=theme_name):
                style = build_app_style(theme_name)
                self.assertIn(
                    "QScrollBar:vertical { width: 8px; background: transparent; }",
                    style,
                )
                self.assertIn(
                    "QScrollBar::handle:vertical { min-height: 24px; "
                    f"margin: 0 1px; border-radius: 3px; background: {tokens.scrollbar}; }}",
                    style,
                )
                self.assertIn(
                    "QScrollBar::handle:vertical:disabled { background: transparent; }",
                    style,
                )
                self.assertIn(
                    "QScrollBar[scrollable='false']::handle:vertical "
                    "{ background: transparent; }",
                    style,
                )
                self.assertIn(
                    "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical "
                    "{ height: 0; }",
                    style,
                )
                # 6a91c1d added horizontal styling with Reminder R1.
                self.assertIn(
                    "QScrollBar:horizontal { height: 8px; background: transparent; }",
                    style,
                )
                self.assertIn(
                    "QScrollBar::handle:horizontal { min-width: 24px; "
                    f"margin: 1px 0; border-radius: 3px; background: {tokens.scrollbar}; }}",
                    style,
                )
                self.assertIn(
                    "QScrollBar::handle:horizontal:disabled { background: transparent; }",
                    style,
                )
                self.assertIn(
                    "QScrollBar[scrollable='false']::handle:horizontal "
                    "{ background: transparent; }",
                    style,
                )

                self.application.setStyleSheet(style)
                bar = QScrollBar(Qt.Vertical)
                bar.setRange(0, 100)
                bar.show()
                self.application.processEvents()
                self.assertEqual(bar.width(), 8)
                bar.close()


if __name__ == "__main__":
    unittest.main()
