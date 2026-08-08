from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QTextCursor, QTextFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.snotes.ui.editor import NotesEditor


class Package2ContinuousEnterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.editor = NotesEditor()
        self.editor.resize(520, 240)
        self.editor.show()
        self.editor.setFocus()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.editor.close()
        self.app.processEvents()

    def _move_to_end(self) -> None:
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.editor.setTextCursor(cursor)
        self.editor.setFocus()

    def _snapshot(self) -> tuple:
        cursor = self.editor.textCursor()
        char_format = cursor.charFormat()
        block_char_format = cursor.block().charFormat()
        return (
            self.editor.document().blockCount(),
            cursor.position(),
            cursor.blockNumber(),
            cursor.block().text(),
            cursor.block().position(),
            char_format.property(QTextFormat.FontPixelSize),
            block_char_format.property(QTextFormat.FontPixelSize),
            char_format.fontWeight(),
            char_format.fontItalic(),
            char_format.fontUnderline(),
            char_format.fontStrikeOut(),
            cursor.blockFormat().headingLevel(),
        )

    def _assert_three_returns_progress(self) -> list[tuple]:
        snapshots = [self._snapshot()]
        for press_index in range(1, 4):
            QTest.keyClick(self.editor, Qt.Key_Return)
            self.app.processEvents()
            current = self._snapshot()
            previous = snapshots[-1]
            self.assertEqual(
                current[0],
                previous[0] + 1,
                f"Enter #{press_index} did not create one block: {snapshots + [current]}",
            )
            self.assertGreater(
                current[1],
                previous[1],
                f"Enter #{press_index} did not advance cursor position: {snapshots + [current]}",
            )
            self.assertEqual(
                current[2],
                previous[2] + 1,
                f"Enter #{press_index} did not advance block: {snapshots + [current]}",
            )
            snapshots.append(current)
        return snapshots

    def test_default_paragraph_three_returns_all_progress(self) -> None:
        self.editor.setPlainText("虚构默认正文")
        self._move_to_end()
        self._assert_three_returns_progress()

    def test_32px_bold_paragraph_three_returns_keep_inline_format(self) -> None:
        self.editor.setPlainText("虚构 32px 粗体正文")
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.Document)
        self.editor.setTextCursor(cursor)
        self.editor.apply_font_size(32)
        self.editor.apply_bold()
        self._move_to_end()

        snapshots = self._assert_three_returns_progress()

        for snapshot in snapshots[1:]:
            self.assertEqual(snapshot[5], 32)
            self.assertGreater(snapshot[7], QFont.Normal)
        QTest.keyClicks(self.editor, "X")
        self.assertEqual(
            self.editor.textCursor().charFormat().property(
                QTextFormat.FontPixelSize
            ),
            32,
        )
        self.assertGreater(
            self.editor.textCursor().charFormat().fontWeight(),
            QFont.Normal,
        )

    def test_bold_paragraph_three_returns_all_progress(self) -> None:
        self.editor.setPlainText("虚构粗体正文")
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.Document)
        self.editor.setTextCursor(cursor)
        self.editor.apply_bold()
        self._move_to_end()
        self.assertGreater(self.editor.currentCharFormat().fontWeight(), QFont.Normal)

        snapshots = self._assert_three_returns_progress()

        for snapshot in snapshots[1:]:
            self.assertGreater(snapshot[7], QFont.Normal)
        QTest.keyClicks(self.editor, "X")
        self.assertGreater(
            self.editor.textCursor().charFormat().fontWeight(),
            QFont.Normal,
        )

    def test_loaded_italic_underline_three_returns_all_progress(self) -> None:
        self.editor.set_note_content("<p><i><u>虚构斜体下划线正文</u></i></p>")
        self._move_to_end()
        current_format = self.editor.currentCharFormat()
        self.assertTrue(current_format.fontItalic())
        self.assertTrue(current_format.fontUnderline())

        snapshots = self._assert_three_returns_progress()

        for snapshot in snapshots[1:]:
            self.assertTrue(snapshot[8])
            self.assertTrue(snapshot[9])
        QTest.keyClicks(self.editor, "X")
        self.assertTrue(self.editor.textCursor().charFormat().fontItalic())
        self.assertTrue(self.editor.textCursor().charFormat().fontUnderline())

    def test_strikethrough_three_returns_keep_inline_format(self) -> None:
        self.editor.setPlainText("虚构删除线正文")
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.Document)
        self.editor.setTextCursor(cursor)
        self.editor.apply_strike()
        self._move_to_end()

        snapshots = self._assert_three_returns_progress()

        for snapshot in snapshots[1:]:
            self.assertTrue(snapshot[10])
        QTest.keyClicks(self.editor, "X")
        self.assertTrue(self.editor.textCursor().charFormat().fontStrikeOut())

    def test_heading_exit_then_following_returns_all_progress(self) -> None:
        self.editor.setPlainText("虚构标题")
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.Document)
        self.editor.setTextCursor(cursor)
        self.editor.apply_heading(1)
        self._move_to_end()
        self.assertEqual(self.editor.textCursor().blockFormat().headingLevel(), 1)

        snapshots = self._assert_three_returns_progress()

        self.assertEqual(self.editor.textCursor().blockFormat().headingLevel(), 0)
        self.assertEqual([snapshot[2] for snapshot in snapshots], [0, 1, 2, 3])
        self.assertTrue(all(snapshot[11] == 0 for snapshot in snapshots[1:]))
        self.assertTrue(
            all(snapshot[7] == QFont.Normal for snapshot in snapshots[1:])
        )
        QTest.keyClicks(self.editor, "X")
        self.assertEqual(self.editor.textCursor().blockFormat().headingLevel(), 0)
        self.assertEqual(
            self.editor.textCursor().charFormat().fontWeight(),
            QFont.Normal,
        )

    def test_clear_format_then_three_returns_progress_without_resurrection(self) -> None:
        self.editor.setPlainText("虚构清格式正文")
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.Document)
        self.editor.setTextCursor(cursor)
        self.editor.apply_font_size(32)
        self.editor.apply_bold()
        self.assertTrue(self.editor.clear_formatting())
        self._move_to_end()

        snapshots = self._assert_three_returns_progress()

        for snapshot in snapshots[1:]:
            self.assertIn(snapshot[5], (None, 15))
            self.assertEqual(snapshot[7], QFont.Normal)
            self.assertFalse(snapshot[8])
            self.assertFalse(snapshot[9])
            self.assertFalse(snapshot[10])
        QTest.keyClicks(self.editor, "X")

        current_format = self.editor.currentCharFormat()
        self.assertEqual(
            current_format.font().pixelSize(),
            15,
        )
        self.assertEqual(current_format.fontWeight(), QFont.Normal)


if __name__ == "__main__":
    unittest.main()
