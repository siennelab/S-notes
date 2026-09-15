from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_temporary_data_root = None
if "S_NOTES_DATA_ROOT" not in os.environ:
    _temporary_data_root = tempfile.TemporaryDirectory(
        prefix="snotes-clear-format-tests-"
    )
    os.environ["S_NOTES_DATA_ROOT"] = _temporary_data_root.name

from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QFont, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.snotes.ui.editor import NotesEditor


class NotesEditorClearFormatInsertionStateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.editor = NotesEditor()
        self.editor.resize(520, 260)
        self.editor.show()
        self.editor.setFocus()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.editor.close()
        self.app.processEvents()

    def _paste_fictional_rich_text(self) -> None:
        mime = QMimeData()
        mime.setText("Fictional Alpha\nFictional body\n")
        mime.setHtml(
            '<h1><b style="font-size:26px;font-weight:700;color:#111111">'
            "Fictional Alpha</b></h1>"
            "<p>Fictional body</p>"
            "<p><br></p>"
        )
        self.editor.insertFromMimeData(mime)
        self.assertEqual(
            self.editor.toPlainText(),
            "Fictional Alpha\nFictional body\n",
        )
        self.large_pixel_size = self._format_at(0).font().pixelSize()
        self.assertGreaterEqual(self.large_pixel_size, 26)
        self.assertGreater(self._format_at(0).fontWeight(), QFont.Normal)
        self.assertFalse(
            self._format_at(0).hasProperty(QTextFormat.ForegroundBrush)
        )

    def _format_at(
        self,
        position: int,
        editor: NotesEditor | None = None,
    ) -> QTextCharFormat:
        editor = editor or self.editor
        cursor = QTextCursor(editor.document())
        cursor.setPosition(position)
        cursor.setPosition(position + 1, QTextCursor.KeepAnchor)
        return cursor.charFormat()

    def _assert_clean_format(self, char_format: QTextCharFormat) -> None:
        self.assertEqual(char_format.font().pixelSize(), 15)
        self.assertFalse(char_format.hasProperty(QTextFormat.FontPointSize))
        self.assertEqual(char_format.fontWeight(), QFont.Normal)
        self.assertFalse(char_format.fontItalic())
        self.assertFalse(char_format.fontStrikeOut())
        self.assertFalse(char_format.hasProperty(QTextFormat.ForegroundBrush))

    def _select_document_and_clear(self) -> None:
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.Document)
        self.editor.setTextCursor(cursor)
        self.assertTrue(self.editor.clear_formatting())
        self._assert_clean_format(self._format_at(0))

    def _return_twice_at_document_start(self) -> None:
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.Start)
        self.editor.setTextCursor(cursor)
        QTest.keyClick(self.editor, Qt.Key_Return)
        QTest.keyClick(self.editor, Qt.Key_Return)

    def _insert_at_document_start(self, text: str = "Z") -> QTextCharFormat:
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.editor.setTextCursor(cursor)
        cursor.movePosition(QTextCursor.Start)
        self.editor.setTextCursor(cursor)
        QTest.keyClicks(self.editor, text)
        return self._format_at(0)

    def test_clear_full_document_selection_prevents_return_resurrection(self) -> None:
        self._paste_fictional_rich_text()
        self._select_document_and_clear()

        self._return_twice_at_document_start()

        self._assert_clean_format(self._insert_at_document_start())
        self._assert_clean_format(self.editor.document().firstBlock().charFormat())

    def test_clear_whole_document_without_selection_resets_current_input(self) -> None:
        self._paste_fictional_rich_text()
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.Start)
        self.editor.setTextCursor(cursor)

        self.assertTrue(self.editor.clear_formatting())

        self._assert_clean_format(self._format_at(0))
        self._assert_clean_format(self.editor.currentCharFormat())
        self._return_twice_at_document_start()
        self._assert_clean_format(self._insert_at_document_start())

    def test_clear_complete_selected_paragraph_prevents_resurrection(self) -> None:
        self._paste_fictional_rich_text()
        second_block = self.editor.document().findBlockByNumber(1)
        cursor = QTextCursor(self.editor.document())
        cursor.setPosition(0)
        cursor.setPosition(second_block.position(), QTextCursor.KeepAnchor)
        self.editor.setTextCursor(cursor)

        self.assertTrue(self.editor.clear_formatting())
        self._assert_clean_format(self._format_at(0))
        self._return_twice_at_document_start()

        self._assert_clean_format(self._insert_at_document_start())

    def test_body_size_after_resurrection_does_not_resurrect_again(self) -> None:
        self._paste_fictional_rich_text()
        self._select_document_and_clear()
        self._return_twice_at_document_start()
        self._insert_at_document_start()

        cursor = QTextCursor(self.editor.document())
        cursor.setPosition(0)
        cursor.setPosition(1, QTextCursor.KeepAnchor)
        self.editor.setTextCursor(cursor)
        self.editor.apply_font_size(15)
        self.assertEqual(self._format_at(0).font().pixelSize(), 15)
        cursor = self.editor.textCursor()
        cursor.setPosition(1)
        self.editor.setTextCursor(cursor)
        QTest.keyClick(self.editor, Qt.Key_Return)

        self._assert_clean_format(self._insert_at_document_start("Y"))

    def test_cleared_html_round_trip_prevents_resurrection(self) -> None:
        self._paste_fictional_rich_text()
        self._select_document_and_clear()
        saved_html = self.editor.note_content_html()
        self.assertNotIn(
            f"font-size:{self.large_pixel_size}px",
            saved_html.replace(" ", "").lower(),
        )
        self.assertNotIn("font-weight:700", saved_html.replace(" ", "").lower())
        self.assertNotIn("#111111", saved_html.lower())

        restored = NotesEditor()
        try:
            restored.set_note_content(saved_html)
            cursor = restored.textCursor()
            cursor.movePosition(QTextCursor.Start)
            restored.setTextCursor(cursor)
            restored.show()
            restored.setFocus()
            QTest.keyClick(restored, Qt.Key_Return)
            QTest.keyClick(restored, Qt.Key_Return)
            cursor = restored.textCursor()
            cursor.movePosition(QTextCursor.Start)
            restored.setTextCursor(cursor)
            QTest.keyClicks(restored, "R")

            self._assert_clean_format(self._format_at(0, restored))
        finally:
            restored.close()

    def test_mouse_click_back_to_first_line_uses_clean_format(self) -> None:
        self._paste_fictional_rich_text()
        self._select_document_and_clear()
        self._return_twice_at_document_start()
        first_line_cursor = QTextCursor(self.editor.document())
        first_line_cursor.setPosition(0)
        first_line_point = self.editor.cursorRect(first_line_cursor).center()
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.editor.setTextCursor(cursor)

        QTest.mouseClick(self.editor.viewport(), Qt.LeftButton, pos=first_line_point)
        QTest.keyClicks(self.editor, "M")

        self._assert_clean_format(self._format_at(0))

    def test_arrow_back_to_first_line_uses_clean_format(self) -> None:
        self._paste_fictional_rich_text()
        self._select_document_and_clear()
        self._return_twice_at_document_start()
        QTest.keyClick(self.editor, Qt.Key_Up)
        QTest.keyClick(self.editor, Qt.Key_Up)
        QTest.keyClicks(self.editor, "A")

        self.assertEqual(self.editor.textCursor().blockNumber(), 0)
        self._assert_clean_format(self._format_at(0))

    def test_cleared_paragraph_start_end_and_empty_block_use_clean_format(
        self,
    ) -> None:
        for block_number, edge in (
            (0, "start"),
            (0, "end"),
            (2, "start"),
        ):
            with self.subTest(block_number=block_number, edge=edge):
                self.editor.close()
                self.editor = NotesEditor()
                self.editor.show()
                self.editor.setFocus()
                self._paste_fictional_rich_text()
                self._select_document_and_clear()
                block = self.editor.document().findBlockByNumber(block_number)
                position = block.position()
                if edge == "end":
                    position += len(block.text())
                cursor = QTextCursor(self.editor.document())
                cursor.setPosition(position)
                self.editor.setTextCursor(cursor)

                QTest.keyClicks(self.editor, "B")

                self._assert_clean_format(self._format_at(position))

    def test_save_switch_and_reload_keep_clean_insertion_state(self) -> None:
        from pathlib import Path

        from src.snotes.bootstrap import build_application

        data_root = Path(os.environ["S_NOTES_DATA_ROOT"])
        _application, window = build_application([], data_root=data_root)
        first = window.note_service.create_note()
        second = window.note_service.create_note()
        restored_window = None
        try:
            window.load_note(first.id)
            mime = QMimeData()
            mime.setText("Fictional Alpha\nFictional body\n")
            mime.setHtml(
                "<h1><b>Fictional Alpha</b></h1>"
                "<p>Fictional body</p><p><br></p>"
            )
            window.body_edit.insertFromMimeData(mime)
            cursor = window.body_edit.textCursor()
            cursor.select(QTextCursor.Document)
            window.body_edit.setTextCursor(cursor)
            self.assertTrue(window.body_edit.clear_formatting())
            self.assertTrue(window.force_save_current_note())

            window.load_note(second.id)
            window.load_note(first.id)
            self._assert_clean_format(self._format_at(0, window.body_edit))
            window.close()
            self.app.processEvents()

            _application, restored_window = build_application([], data_root=data_root)
            restored_window.load_note(first.id)
            cursor = restored_window.body_edit.textCursor()
            cursor.movePosition(QTextCursor.Start)
            restored_window.body_edit.setTextCursor(cursor)
            QTest.keyClick(restored_window.body_edit, Qt.Key_Return)
            QTest.keyClick(restored_window.body_edit, Qt.Key_Return)
            cursor = restored_window.body_edit.textCursor()
            cursor.movePosition(QTextCursor.Start)
            restored_window.body_edit.setTextCursor(cursor)
            QTest.keyClicks(restored_window.body_edit, "S")

            self._assert_clean_format(
                self._format_at(0, restored_window.body_edit)
            )
        finally:
            window.close()
            if restored_window is not None:
                restored_window.close()
            self.app.processEvents()

    def test_clear_formatting_undo_redo_is_one_edit(self) -> None:
        self._paste_fictional_rich_text()
        self.editor.document().clearUndoRedoStacks()
        self._select_document_and_clear()

        self.editor.undo()
        self.assertEqual(
            self._format_at(0).font().pixelSize(),
            self.large_pixel_size,
        )
        self.assertGreater(self._format_at(0).fontWeight(), QFont.Normal)
        self.editor.redo()

        self._assert_clean_format(self._format_at(0))
        self._assert_clean_format(self.editor.document().firstBlock().charFormat())

    def test_internal_32px_bold_clear_does_not_resurrect(self) -> None:
        self.editor.setPlainText("虚构内部大字号")
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.Document)
        self.editor.setTextCursor(cursor)
        self.editor.apply_font_size(32)
        self.editor.apply_bold()
        self.assertEqual(self._format_at(0).font().pixelSize(), 32)
        self.assertGreater(self._format_at(0).fontWeight(), QFont.Normal)

        self.assertTrue(self.editor.clear_formatting())
        self._return_twice_at_document_start()
        cursor = self.editor.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.editor.setTextCursor(cursor)
        QTest.keyClick(self.editor, Qt.Key_Up)
        QTest.keyClick(self.editor, Qt.Key_Down)
        QTest.keyClicks(self.editor, "safe")

        insertion = self.editor.textCursor().position() - 1
        self._assert_clean_format(self._format_at(insertion))
        self._assert_clean_format(self.editor.currentCharFormat())


if __name__ == "__main__":
    unittest.main()
