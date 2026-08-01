from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.editor import NotesEditor


class NotesEditorFontSizeInheritanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.editor = NotesEditor()

    def tearDown(self) -> None:
        self.editor.close()
        self.app.processEvents()

    def _reset_with_fictional_document(self, pixel_size: int = 20) -> None:
        self.editor.close()
        self.editor = NotesEditor()
        self.editor.setPlainText("alpha\n\ncharlie\ndelta")
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.Document)
        self.editor.setTextCursor(cursor)
        self.editor.apply_font_size(pixel_size)

    def _format_at(self, position: int) -> QTextCharFormat:
        cursor = QTextCursor(self.editor.document())
        cursor.setPosition(position)
        cursor.setPosition(position + 1, QTextCursor.KeepAnchor)
        return cursor.charFormat()

    def _assert_pixel_size(
        self,
        char_format: QTextCharFormat,
        expected: int,
    ) -> None:
        self.assertEqual(
            char_format.property(QTextFormat.FontPixelSize),
            expected,
        )
        self.assertEqual(char_format.font().pixelSize(), expected)

    def _insert_at(self, position: int) -> QTextCharFormat:
        cursor = QTextCursor(self.editor.document())
        cursor.setPosition(position)
        self.editor.setTextCursor(cursor)
        self.editor.setFocus()
        QTest.keyClick(self.editor, Qt.Key_X)
        return self._format_at(position)

    def test_select_all_controls_input_at_every_paragraph_boundary(self) -> None:
        for block_number, edge in (
            (0, "start"),
            (1, "start"),
            (2, "start"),
            (2, "end"),
            (3, "start"),
            (3, "end"),
        ):
            with self.subTest(block_number=block_number, edge=edge):
                self._reset_with_fictional_document()
                block = self.editor.document().findBlockByNumber(block_number)
                self._assert_pixel_size(block.charFormat(), 20)
                position = block.position()
                if edge == "end":
                    position += len(block.text())

                self._assert_pixel_size(self._insert_at(position), 20)

    def test_arrow_navigation_across_blocks_keeps_document_size(self) -> None:
        self._reset_with_fictional_document()
        first = self.editor.document().firstBlock()
        cursor = QTextCursor(self.editor.document())
        cursor.setPosition(first.position() + len(first.text()))
        self.editor.setTextCursor(cursor)
        self.editor.setFocus()

        QTest.keyClick(self.editor, Qt.Key_Right)
        insertion_position = self.editor.textCursor().position()
        QTest.keyClick(self.editor, Qt.Key_X)

        self.assertEqual(
            self.editor.document().findBlock(insertion_position).blockNumber(),
            1,
        )
        self._assert_pixel_size(self._format_at(insertion_position), 20)

    def test_partial_selection_only_changes_selected_text(self) -> None:
        self.editor.setPlainText("before target after")
        cursor = QTextCursor(self.editor.document())
        cursor.setPosition(7)
        cursor.setPosition(13, QTextCursor.KeepAnchor)
        self.editor.setTextCursor(cursor)

        self.editor.apply_font_size(20)

        block_format = self.editor.document().firstBlock().charFormat()
        self.assertFalse(block_format.hasProperty(QTextFormat.FontPixelSize))
        self._assert_pixel_size(self._format_at(7), 20)
        inserted_format = self._insert_at(0)
        self.assertEqual(inserted_format.font().pixelSize(), 15)
        self.assertFalse(inserted_format.hasProperty(QTextFormat.FontPixelSize))

    def test_cross_block_partial_selection_does_not_pollute_edge_blocks(self) -> None:
        self.editor.setPlainText("alpha\nbravo\ncharlie")
        first = self.editor.document().findBlockByNumber(0)
        middle = self.editor.document().findBlockByNumber(1)
        last = self.editor.document().findBlockByNumber(2)
        cursor = QTextCursor(self.editor.document())
        cursor.setPosition(first.position() + 2)
        cursor.setPosition(last.position() + 3, QTextCursor.KeepAnchor)
        self.editor.setTextCursor(cursor)

        self.editor.apply_font_size(20)

        self.assertFalse(
            first.charFormat().hasProperty(QTextFormat.FontPixelSize)
        )
        self._assert_pixel_size(middle.charFormat(), 20)
        self.assertFalse(
            last.charFormat().hasProperty(QTextFormat.FontPixelSize)
        )
        self.assertEqual(self._format_at(0).font().pixelSize(), 15)
        self._assert_pixel_size(self._format_at(first.position() + 2), 20)
        self._assert_pixel_size(self._format_at(last.position()), 20)
        self.assertEqual(
            self._format_at(last.position() + 4).font().pixelSize(),
            15,
        )

    def test_all_four_font_sizes_keep_empty_paragraph_input_size(self) -> None:
        for pixel_size in (11, 15, 20, 26):
            with self.subTest(pixel_size=pixel_size):
                self._reset_with_fictional_document(pixel_size)
                blank = self.editor.document().findBlockByNumber(1)
                self._assert_pixel_size(
                    self._insert_at(blank.position()),
                    pixel_size,
                )

    def test_multiblock_change_is_undoable_as_one_edit(self) -> None:
        self.editor.setPlainText("alpha\n\ncharlie")
        self.editor.document().clearUndoRedoStacks()
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.Document)
        self.editor.setTextCursor(cursor)

        self.editor.apply_font_size(20)
        self._assert_pixel_size(self._format_at(0), 20)
        self._assert_pixel_size(
            self.editor.document().findBlockByNumber(1).charFormat(),
            20,
        )

        self.editor.undo()

        self.assertFalse(
            self._format_at(0).hasProperty(QTextFormat.FontPixelSize)
        )
        self.assertFalse(
            self.editor.document()
            .findBlockByNumber(1)
            .charFormat()
            .hasProperty(QTextFormat.FontPixelSize)
        )

    def test_inheritance_survives_html_round_trip(self) -> None:
        for block_number, edge in (
            (0, "start"),
            (1, "start"),
            (2, "start"),
            (3, "end"),
        ):
            with self.subTest(block_number=block_number, edge=edge):
                self._reset_with_fictional_document()
                html = self.editor.note_content_html()
                restored = NotesEditor()
                try:
                    restored.set_note_content(html)
                    block = restored.document().findBlockByNumber(block_number)
                    position = block.position()
                    if edge == "end":
                        position += len(block.text())
                    cursor = QTextCursor(restored.document())
                    cursor.setPosition(position)
                    restored.setTextCursor(cursor)
                    restored.setFocus()
                    QTest.keyClick(restored, Qt.Key_X)

                    inserted = QTextCursor(restored.document())
                    inserted.setPosition(position)
                    inserted.setPosition(position + 1, QTextCursor.KeepAnchor)
                    self._assert_pixel_size(inserted.charFormat(), 20)
                finally:
                    restored.close()


if __name__ == "__main__":
    unittest.main()
