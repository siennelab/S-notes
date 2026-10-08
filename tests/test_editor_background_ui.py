from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.snotes.bootstrap import build_application
from src.snotes.reminder_authority import LOCAL_REMINDER_AUTHORITY
from src.snotes.ui.theme import THEMES


class EditorBackgroundUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-editor-background-",
            dir="/private/tmp",
        )
        _application, self.window = build_application(
            [],
            data_root=Path(self.temporary.name) / "data",
            reminder_authority=LOCAL_REMINDER_AUTHORITY,
        )
        self.window.resize(1000, 700)
        self.window.show()
        QTest.qWait(20)

    def tearDown(self) -> None:
        self.window.has_unsaved_changes = False
        self.window.conflict_active = False
        self.window.close()
        self._wait(lambda: not self.window.isVisible())
        self.window.body_edit.page.deleteLater()
        self.window.deleteLater()
        QTest.qWait(20)
        self.temporary.cleanup()

    def _wait(self, predicate, timeout: float = 15.0) -> None:
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertTrue(predicate())

    def _javascript(self, code: str):
        results: list[object] = []
        self.window.body_edit.page.runJavaScript(code, results.append)
        self._wait(lambda: bool(results))
        return results[0]

    def _wait_for_document_background(self, color: QColor) -> None:
        expected = f"rgb({color.red()}, {color.green()}, {color.blue()})"
        self._wait(
            lambda: self._javascript(
                "getComputedStyle(document.documentElement).backgroundColor"
            )
            == expected
        )

    def _load_note(self) -> None:
        note = self.window.note_service.create_note()
        self.assertTrue(self.window.load_note(note.id))
        self._wait(lambda: self.window.body_edit.can_save)

    def test_page_background_starts_and_tracks_every_theme(self) -> None:
        editor = self.window.body_edit
        initial_tokens = THEMES[self.window.current_theme_name]
        self.assertEqual(
            editor.page.backgroundColor(),
            QColor(initial_tokens.editor_paper),
        )

        for theme_name, tokens in THEMES.items():
            with self.subTest(theme=theme_name):
                self.window.set_theme(theme_name)
                self.assertEqual(
                    editor.page.backgroundColor(),
                    QColor(tokens.editor_paper),
                )

    def test_resize_does_not_reveal_the_previous_dark_surface(self) -> None:
        self._load_note()
        editor = self.window.body_edit
        dark = QColor(THEMES["dark"].editor_paper)

        for theme_name in ("light-slate", "light-clay"):
            with self.subTest(theme=theme_name):
                self.window.set_theme("dark")
                self._wait_for_document_background(dark)
                self.window.resize(1500, 700)
                QTest.qWait(100)
                self.window.resize(1000, 700)
                QTest.qWait(100)

                light = QColor(THEMES[theme_name].editor_paper)
                self.window.set_theme(theme_name)
                self._wait_for_document_background(light)
                old_width = editor.view.width()

                self.window.resize(1500, 700)
                pixmap = editor.view.grab()
                image = pixmap.toImage()
                first_new_pixel = round(old_width * pixmap.devicePixelRatio())
                sampled = [
                    image.pixelColor(x, y)
                    for y in range(0, image.height(), 5)
                    for x in range(first_new_pixel, image.width(), 5)
                ]

                self.assertTrue(sampled)
                self.assertNotIn(dark, sampled)
                light_pixels = sum(color == light for color in sampled)
                self.assertGreater(light_pixels / len(sampled), 0.9)


if __name__ == "__main__":
    unittest.main()
