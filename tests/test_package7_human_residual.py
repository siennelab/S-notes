from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QPalette, QTextCursor, QTextFormat
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from src.snotes.bootstrap import build_application
from src.snotes.domain.models import NoteLocation
from src.snotes.domain.relations import RelatedNote
from src.snotes.ui.link_drawer import LinkDrawer
from src.snotes.ui.shell import SnotesShell
from src.snotes.ui.theme import THEMES, apply_theme, build_app_style


class Package7HumanResidualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self) -> None:
        apply_theme(self.app, "light-slate")
        self.app.processEvents()


    def test_related_drawer_uses_three_theme_surfaces_and_selection_tokens(
        self,
    ) -> None:
        drawer = LinkDrawer()
        drawer.resize(320, 260)
        drawer.set_results(
            [
                RelatedNote(
                    note_id=1,
                    title="Fictional related note",
                    path="Fictional / Path",
                    updated_at="2026-08-10T00:00:00",
                    reasons=("同目录",),
                )
            ]
        )
        drawer.show()
        self.addCleanup(drawer.close)

        for theme_name, tokens in THEMES.items():
            with self.subTest(theme=theme_name):
                apply_theme(self.app, theme_name)
                self.app.processEvents()
                self.assertEqual(
                    drawer.palette().color(QPalette.Window),
                    QColor(tokens.sidebar),
                )
                self.assertEqual(
                    drawer.results.viewport().palette().color(QPalette.Base),
                    QColor(tokens.sidebar),
                )
                self.assertEqual(
                    drawer.results.palette().color(QPalette.Text),
                    QColor(tokens.sidebar_text),
                )
                style = build_app_style(theme_name)
                self.assertIn(
                    f"background: {tokens.sidebar_hover}; color: {tokens.sidebar_text};",
                    style,
                )
                self.assertIn(
                    f"background: {tokens.sidebar_selected}; color: {tokens.sidebar_text};",
                    style,
                )


if __name__ == "__main__":
    unittest.main()
