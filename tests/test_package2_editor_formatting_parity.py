from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFont, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import QApplication

from src.snotes.ui.editor import NotesEditor


class NotesEditorFormattingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.editor = NotesEditor()

    def tearDown(self) -> None:
        self.editor.close()
        self.app.processEvents()

    def _select_all(self) -> None:
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.Document)
        self.editor.setTextCursor(cursor)

    def _char_format_at(self, position: int) -> QTextCharFormat:
        cursor = QTextCursor(self.editor.document())
        cursor.setPosition(position)
        cursor.setPosition(position + 1, QTextCursor.KeepAnchor)
        return cursor.charFormat()

    def _assert_pixel_size(self, char_format: QTextCharFormat, pixel_size: int) -> None:
        self.assertEqual(
            char_format.property(QTextFormat.FontPixelSize),
            pixel_size,
        )
        self.assertEqual(char_format.font().pixelSize(), pixel_size)

    def test_apply_heading_sets_real_block_and_character_formats(self) -> None:
        for level, pixel_size, weight in (
            (1, 26, QFont.Bold),
            (2, 15, QFont.Bold),
            (0, 15, QFont.Normal),
        ):
            with self.subTest(level=level):
                self.editor.setPlainText("heading")
                self._select_all()

                self.editor.apply_heading(level)

                block_format = self.editor.document().firstBlock().blockFormat()
                char_format = self._char_format_at(0)
                self.assertEqual(block_format.headingLevel(), level)
                self._assert_pixel_size(char_format, pixel_size)
                self.assertEqual(char_format.fontWeight(), weight)

    def test_apply_font_size_sets_real_selected_text_pixel_size(self) -> None:
        for pixel_size in (11, 15, 20, 26):
            with self.subTest(pixel_size=pixel_size):
                self.editor.setPlainText("sized text")
                self._select_all()
                point_format = QTextCharFormat()
                point_format.setFontPointSize(12)
                self.editor.textCursor().mergeCharFormat(point_format)
                self._select_all()

                self.editor.apply_font_size(pixel_size)

                char_format = self._char_format_at(0)
                self._assert_pixel_size(char_format, pixel_size)
                self.assertFalse(
                    char_format.hasProperty(QTextFormat.FontPointSize)
                )
                self.assertEqual(char_format.font().pointSizeF(), -1.0)

    def test_font_size_changes_only_the_selected_text(self) -> None:
        self.editor.setPlainText("before target after")
        cursor = self.editor.textCursor()
        cursor.setPosition(7)
        cursor.setPosition(13, QTextCursor.KeepAnchor)
        self.editor.setTextCursor(cursor)

        self.editor.apply_font_size(20)

        before_format = self._char_format_at(0)
        self.assertEqual(before_format.font().pixelSize(), 15)
        self.assertFalse(before_format.hasProperty(QTextFormat.FontPixelSize))
        self._assert_pixel_size(self._char_format_at(7), 20)
        after_format = self._char_format_at(14)
        self.assertEqual(after_format.font().pixelSize(), 15)
        self.assertFalse(after_format.hasProperty(QTextFormat.FontPixelSize))

    def test_heading_without_selection_updates_block_and_future_input(self) -> None:
        self.editor.setPlainText("paragraph")
        cursor = self.editor.textCursor()
        cursor.setPosition(4)
        self.editor.setTextCursor(cursor)

        self.editor.apply_heading(1)
        insertion_position = self.editor.textCursor().position()
        self.editor.insertPlainText("X")

        block_format = self.editor.document().firstBlock().blockFormat()
        char_format = self._char_format_at(insertion_position)
        self.assertEqual(block_format.headingLevel(), 1)
        self._assert_pixel_size(char_format, 26)
        self.assertEqual(char_format.fontWeight(), QFont.Bold)

    def test_font_size_without_selection_updates_future_input(self) -> None:
        self.editor.setPlainText("paragraph")
        cursor = self.editor.textCursor()
        cursor.setPosition(4)
        self.editor.setTextCursor(cursor)

        self.editor.apply_font_size(11)
        insertion_position = self.editor.textCursor().position()
        self.editor.insertPlainText("X")

        self._assert_pixel_size(self._char_format_at(insertion_position), 11)

    def test_font_size_change_is_undoable(self) -> None:
        self.editor.setPlainText("undo size")
        self._select_all()
        point_format = QTextCharFormat()
        point_format.setFontPointSize(12)
        self.editor.textCursor().mergeCharFormat(point_format)
        self.editor.document().clearUndoRedoStacks()
        self._select_all()

        self.editor.apply_font_size(26)
        self._assert_pixel_size(self._char_format_at(0), 26)

        self.editor.undo()

        restored = self._char_format_at(0)
        self.assertFalse(restored.hasProperty(QTextFormat.FontPixelSize))
        self.assertEqual(restored.property(QTextFormat.FontPointSize), 12.0)

    def test_heading_change_is_undoable_as_one_edit(self) -> None:
        self.editor.setPlainText("undo heading")
        self._select_all()
        point_format = QTextCharFormat()
        point_format.setFontPointSize(12)
        self.editor.textCursor().mergeCharFormat(point_format)
        self.editor.document().clearUndoRedoStacks()
        self._select_all()

        self.editor.apply_heading(1)
        self.assertEqual(
            self.editor.document().firstBlock().blockFormat().headingLevel(),
            1,
        )
        self._assert_pixel_size(self._char_format_at(0), 26)

        self.editor.undo()

        restored = self._char_format_at(0)
        self.assertEqual(
            self.editor.document().firstBlock().blockFormat().headingLevel(),
            0,
        )
        self.assertFalse(restored.hasProperty(QTextFormat.FontPixelSize))
        self.assertEqual(restored.property(QTextFormat.FontPointSize), 12.0)

    def test_heading_pixel_format_survives_html_round_trip(self) -> None:
        self.editor.setPlainText("saved heading")
        self._select_all()
        self.editor.apply_heading(1)

        html = self.editor.toHtml()
        self.assertIn("font-size:26px", html.replace(" ", "").lower())
        restored_editor = NotesEditor()
        try:
            restored_editor.setHtml(html)
            cursor = QTextCursor(restored_editor.document())
            cursor.setPosition(0)
            cursor.setPosition(1, QTextCursor.KeepAnchor)
            char_format = cursor.charFormat()
            self.assertEqual(
                restored_editor.document().firstBlock().blockFormat().headingLevel(),
                1,
            )
            self.assertEqual(char_format.property(QTextFormat.FontPixelSize), 26)
            self.assertEqual(char_format.fontWeight(), QFont.Bold)
        finally:
            restored_editor.close()


if __name__ == "__main__":
    unittest.main()
