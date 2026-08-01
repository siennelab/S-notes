from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QTextCursor, QTextFormat
from PySide6.QtWidgets import QApplication

from src.editor import NotesEditor
from src.editor_font_size_fix import install_font_size_cursor_fix


class EditorFontSizeCursorFixTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        install_font_size_cursor_fix()

    def setUp(self) -> None:
        self.editor = NotesEditor()
        self.editor.setPlainText("第一行\n\n第三行\n\n第五行")

    def tearDown(self) -> None:
        self.editor.close()
        self.app.processEvents()

    def _select_all(self) -> None:
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.Document)
        self.editor.setTextCursor(cursor)

    def _assert_inserted_size(self, position: int, expected: int = 20) -> None:
        cursor = self.editor.textCursor()
        cursor.clearSelection()
        cursor.setPosition(position)
        self.editor.setTextCursor(cursor)
        self.editor.insertPlainText("测")

        inserted = QTextCursor(self.editor.document())
        inserted.setPosition(position)
        inserted.setPosition(position + 1, QTextCursor.KeepAnchor)
        self.assertEqual(
            inserted.charFormat().property(QTextFormat.FontPixelSize),
            expected,
        )

    def test_select_all_size_survives_cursor_at_first_line_and_later_blocks(self) -> None:
        self._select_all()
        self.editor.apply_font_size(20)

        first = self.editor.document().firstBlock()
        blank = first.next()
        third = blank.next()

        self._assert_inserted_size(first.position())
        self._assert_inserted_size(blank.position())
        self._assert_inserted_size(third.position())

    def test_partial_selection_does_not_change_unselected_block_input_format(self) -> None:
        second_text_block = self.editor.document().findBlockByNumber(2)
        cursor = self.editor.textCursor()
        cursor.setPosition(second_text_block.position())
        cursor.setPosition(
            second_text_block.position() + len(second_text_block.text()),
            QTextCursor.KeepAnchor,
        )
        self.editor.setTextCursor(cursor)

        self.editor.apply_font_size(20)

        first_block_format = self.editor.document().firstBlock().charFormat()
        self.assertFalse(first_block_format.hasProperty(QTextFormat.FontPixelSize))
        self.assertEqual(
            second_text_block.charFormat().property(QTextFormat.FontPixelSize),
            20,
        )


if __name__ == "__main__":
    unittest.main()
