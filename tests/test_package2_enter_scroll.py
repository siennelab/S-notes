from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QTextCursor, QTextFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.snotes.ui.editor import NotesEditor
from src.snotes.application.note_service import NoteService
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.ui.main_window import MainWindow


class Package2EnterScrollTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.editor = NotesEditor()
        self.editor.resize(560, 220)
        self.editor.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.editor.close()
        self.app.processEvents()

    def _prepare_long_document(self, pixel_size: int | None = None) -> None:
        self.editor.set_note_content(
            "".join(
                f"<p>虚构长文第 {index:02d} 段：用于 Return 滚动回归。</p>"
                for index in range(1, 81)
            )
        )
        if pixel_size is not None:
            cursor = self.editor.textCursor()
            cursor.select(QTextCursor.Document)
            self.editor.setTextCursor(cursor)
            self.editor.apply_font_size(pixel_size)
        block = self.editor.document().findBlockByNumber(55)
        self.assertTrue(block.isValid())
        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.EndOfBlock)
        self.editor.setTextCursor(cursor)
        self.editor.setFocus()
        self.editor.ensureCursorVisible()
        self.app.processEvents()

    def test_vertical_scrollbar_reserves_constant_viewport_width(self) -> None:
        self.editor.setPlainText("虚构短文本")
        self.app.processEvents()
        scroll_bar = self.editor.verticalScrollBar()
        self.assertEqual(
            self.editor.verticalScrollBarPolicy(),
            Qt.ScrollBarAlwaysOn,
        )
        self.assertEqual(scroll_bar.maximum(), scroll_bar.minimum())
        self.assertTrue(scroll_bar.isVisible())
        self.assertFalse(scroll_bar.property("scrollable"))
        short_viewport_width = self.editor.viewport().width()

        self.editor.setPlainText(
            "\n".join(
                f"虚构长文本第 {index:03d} 行"
                for index in range(250)
            )
        )
        self.app.processEvents()
        self.assertGreater(scroll_bar.maximum(), scroll_bar.minimum())
        self.assertTrue(scroll_bar.isEnabled())
        self.assertTrue(scroll_bar.property("scrollable"))
        long_viewport_width = self.editor.viewport().width()
        self.assertEqual(short_viewport_width, long_viewport_width)

        scroll_bar.setValue(scroll_bar.maximum())
        self.app.processEvents()
        self.assertGreater(scroll_bar.value(), scroll_bar.minimum())

    def test_return_does_not_reset_non_top_viewport(self) -> None:
        self._prepare_long_document()
        scroll_bar = self.editor.verticalScrollBar()
        before = scroll_bar.value()
        self.assertGreater(before, scroll_bar.minimum())
        self.assertGreater(self.editor.textCursor().blockNumber(), 0)

        QTest.keyClick(self.editor, Qt.Key_Return)
        self.app.processEvents()

        after = scroll_bar.value()
        cursor_rect = self.editor.cursorRect(self.editor.textCursor())
        self.assertGreater(after, scroll_bar.minimum())
        self.assertGreater(self.editor.textCursor().blockNumber(), 0)
        self.assertGreaterEqual(cursor_rect.top(), self.editor.viewport().rect().top())
        self.assertLessEqual(cursor_rect.bottom(), self.editor.viewport().rect().bottom())

    def test_mouse_positioned_return_does_not_reset_viewport(self) -> None:
        self.editor.set_note_content(
            "".join(
                f"<p>虚构鼠标长文第 {index:02d} 段：用于 Return 滚动回归。</p>"
                for index in range(1, 101)
            )
        )
        scroll_bar = self.editor.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum() * 2 // 3)
        self.app.processEvents()
        self.assertGreater(scroll_bar.value(), scroll_bar.minimum())
        click_point = QPoint(
            self.editor.viewport().width() // 2,
            self.editor.viewport().height() * 3 // 4,
        )
        QTest.mouseClick(self.editor.viewport(), Qt.LeftButton, pos=click_point)
        self.app.processEvents()
        before = scroll_bar.value()
        before_block = self.editor.textCursor().blockNumber()
        self.assertGreater(before, scroll_bar.minimum())
        self.assertGreater(before_block, 0)

        QTest.keyClick(self.editor, Qt.Key_Return)
        self.app.processEvents()

        after = scroll_bar.value()
        cursor_rect = self.editor.cursorRect(self.editor.textCursor())
        self.assertGreater(after, scroll_bar.minimum())
        self.assertGreaterEqual(self.editor.textCursor().blockNumber(), before_block)
        self.assertGreaterEqual(cursor_rect.top(), self.editor.viewport().rect().top())
        self.assertLessEqual(cursor_rect.bottom(), self.editor.viewport().rect().bottom())

    def test_return_in_one_long_wrapped_block_does_not_reset_viewport(self) -> None:
        self.editor.setPlainText(
            " ".join(f"虚构长段落词语{index:03d}" for index in range(700))
        )
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.Document)
        self.editor.setTextCursor(cursor)
        self.editor.apply_font_size(20)
        scroll_bar = self.editor.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum() * 2 // 3)
        self.app.processEvents()
        click_point = QPoint(
            self.editor.viewport().width() // 2,
            self.editor.viewport().height() * 3 // 4,
        )
        QTest.mouseClick(self.editor.viewport(), Qt.LeftButton, pos=click_point)
        self.app.processEvents()
        before = scroll_bar.value()
        self.assertGreater(before, scroll_bar.minimum())

        QTest.keyClick(self.editor, Qt.Key_Return)
        self.app.processEvents()

        after = scroll_bar.value()
        cursor_rect = self.editor.cursorRect(self.editor.textCursor())
        self.assertGreater(after, scroll_bar.minimum())
        self.assertGreater(self.editor.textCursor().position(), 0)
        self.assertGreaterEqual(cursor_rect.top(), self.editor.viewport().rect().top())
        self.assertLessEqual(cursor_rect.bottom(), self.editor.viewport().rect().bottom())

    def test_return_with_internal_pixel_size_does_not_reset_viewport(self) -> None:
        self._prepare_long_document(pixel_size=20)
        scroll_bar = self.editor.verticalScrollBar()
        before = scroll_bar.value()
        self.assertGreater(before, scroll_bar.minimum())

        QTest.keyClick(self.editor, Qt.Key_Return)
        self.app.processEvents()

        after = scroll_bar.value()
        cursor_rect = self.editor.cursorRect(self.editor.textCursor())
        self.assertGreater(after, scroll_bar.minimum())
        self.assertGreater(self.editor.textCursor().blockNumber(), 0)
        self.assertGreaterEqual(cursor_rect.top(), self.editor.viewport().rect().top())
        self.assertLessEqual(cursor_rect.bottom(), self.editor.viewport().rect().bottom())

    def test_return_and_size_inheritance_remain_one_undo_step(self) -> None:
        self.editor.setPlainText("虚构撤销边界")
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.Document)
        self.editor.setTextCursor(cursor)
        self.editor.apply_font_size(23)
        cursor.movePosition(QTextCursor.End)
        self.editor.setTextCursor(cursor)
        self.editor.document().clearUndoRedoStacks()

        QTest.keyClick(self.editor, Qt.Key_Return)
        self.assertEqual(self.editor.document().blockCount(), 2)
        self.assertEqual(
            self.editor.currentCharFormat().property(QTextFormat.FontPixelSize),
            23,
        )
        self.editor.undo()

        self.assertEqual(self.editor.document().blockCount(), 1)
        self.assertEqual(self.editor.toPlainText(), "虚构撤销边界")

    def test_main_window_return_and_autosave_do_not_reset_viewport(self) -> None:
        with tempfile.TemporaryDirectory(prefix="snotes-scroll-window-") as temporary:
            root = Path(temporary)
            connections = initialize_database(root / "fictional.db")
            directories = SQLiteDirectoriesRepository(connections)
            service = NoteService(SQLiteNotesRepository(connections, directories))
            note = service.create_note()
            long_html = "".join(
                f"<p>虚构窗口长文第 {index:02d} 段。</p>" for index in range(1, 101)
            )
            service.save_note(
                note.id,
                title="虚构 Enter 回归",
                content_html=long_html,
                location=note.location,
                expected_revision=note.revision,
            )
            window = MainWindow(
                service,
                theme_settings_path=root / "config" / "ui_settings.ini",
            )
            try:
                window.resize(900, 520)
                window.show()
                window.load_note(note.id)
                self.app.processEvents()
                editor = window.body_edit
                block = editor.document().findBlockByNumber(70)
                cursor = QTextCursor(block)
                cursor.movePosition(QTextCursor.EndOfBlock)
                editor.setTextCursor(cursor)
                editor.setFocus()
                editor.ensureCursorVisible()
                self.app.processEvents()
                scroll_bar = editor.verticalScrollBar()
                before = scroll_bar.value()
                self.assertGreater(before, scroll_bar.minimum())

                QTest.keyClick(editor, Qt.Key_Return)
                self.app.processEvents()
                immediate = scroll_bar.value()
                self.assertGreater(immediate, scroll_bar.minimum())
                QTest.qWait(window.AUTOSAVE_DELAY_MS + 50)
                self.app.processEvents()

                after_save = scroll_bar.value()
                cursor_rect = editor.cursorRect(editor.textCursor())
                self.assertGreater(after_save, scroll_bar.minimum())
                self.assertGreater(editor.textCursor().blockNumber(), 0)
                self.assertGreaterEqual(cursor_rect.top(), editor.viewport().rect().top())
                self.assertLessEqual(cursor_rect.bottom(), editor.viewport().rect().bottom())
            finally:
                window.has_unsaved_changes = False
                window.close()

if __name__ == "__main__":
    unittest.main()
