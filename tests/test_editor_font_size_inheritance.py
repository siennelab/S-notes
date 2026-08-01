from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFontMetricsF, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.editor import NotesEditor, QUOTE_BLOCK_PROPERTY


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

    def _move_to_document_end(self, editor: NotesEditor | None = None) -> None:
        editor = editor or self.editor
        cursor = QTextCursor(editor.document())
        cursor.setPosition(editor.document().characterCount() - 1)
        editor.setTextCursor(cursor)
        editor.setFocus()

    def _assert_input_size(
        self,
        editor: NotesEditor,
        expected: int,
    ) -> None:
        cursor = editor.textCursor()
        self._assert_pixel_size(cursor.block().charFormat(), expected)
        self._assert_pixel_size(cursor.charFormat(), expected)
        self._assert_pixel_size(editor.currentCharFormat(), expected)

    def _prepare_long_visible_editor(
        self,
        pixel_size: int | None = 20,
    ) -> NotesEditor:
        self.editor.close()
        self.editor = NotesEditor()
        self.editor.resize(500, 180)
        self.editor.show()
        self.app.processEvents()
        self.editor.setPlainText(
            "\n\n".join(
                f"Fictional paragraph {index:02d} for scroll regression testing."
                for index in range(40)
            )
        )
        if pixel_size is not None:
            cursor = self.editor.textCursor()
            cursor.select(QTextCursor.Document)
            self.editor.setTextCursor(cursor)
            self.editor.apply_font_size(pixel_size)
        self._move_to_document_end()
        self.editor.ensureCursorVisible()
        self.app.processEvents()
        scroll_bar = self.editor.verticalScrollBar()
        self.assertGreater(scroll_bar.maximum(), 0)
        self.assertGreater(scroll_bar.value(), 0)
        self.assertLessEqual(
            scroll_bar.maximum() - scroll_bar.value(),
            max(4, scroll_bar.pageStep() // 4),
        )
        return self.editor

    def _assert_cursor_visible_near_scroll_end(
        self,
        editor: NotesEditor,
    ) -> None:
        self.app.processEvents()
        cursor = editor.textCursor()
        cursor_rect = editor.cursorRect(cursor)
        scroll_bar = editor.verticalScrollBar()
        self.assertEqual(cursor.block(), editor.document().lastBlock())
        self.assertEqual(
            cursor.position(),
            editor.document().characterCount() - 1,
        )
        self.assertGreaterEqual(cursor_rect.top(), 0)
        self.assertLessEqual(cursor_rect.bottom(), editor.viewport().height())
        self.assertGreater(scroll_bar.maximum(), 0)
        self.assertGreater(scroll_bar.value(), 0)
        self.assertGreaterEqual(
            scroll_bar.value(),
            int(scroll_bar.maximum() * 0.75),
        )
        self.assertLessEqual(
            scroll_bar.maximum() - scroll_bar.value(),
            max(4, scroll_bar.pageStep() // 4),
        )

    def _position_cursor_in_view(
        self,
        editor: NotesEditor,
        location: str,
        block_number: int = 20,
    ) -> dict[str, float | int]:
        block = editor.document().findBlockByNumber(block_number)
        self.assertTrue(block.isValid())
        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.EndOfBlock)
        editor.setTextCursor(cursor)
        editor.setFocus()
        editor.ensureCursorVisible()
        self.app.processEvents()

        line_spacing = QFontMetricsF(cursor.charFormat().font()).lineSpacing()
        cursor_rect = editor.cursorRect(cursor)
        if location == "upper":
            target_top = round(line_spacing)
        elif location == "middle":
            target_top = round(
                (editor.viewport().height() - cursor_rect.height()) / 2
            )
        elif location == "lower":
            target_top = round(
                editor.viewport().height()
                - cursor_rect.height()
                - max(2.0, line_spacing * 0.25)
            )
        else:
            self.fail(f"Unknown viewport location: {location}")

        scroll_bar = editor.verticalScrollBar()
        scroll_bar.setValue(
            scroll_bar.value() + round(cursor_rect.top() - target_top)
        )
        self.app.processEvents()
        snapshot = self._cursor_view_snapshot(editor)
        self.assertGreater(snapshot["scroll"], 0)
        self.assertLess(snapshot["scroll"], snapshot["maximum"])
        self.assertAlmostEqual(
            snapshot["top"],
            target_top,
            delta=max(3.0, line_spacing * 0.25),
        )
        return snapshot

    def _cursor_view_snapshot(
        self,
        editor: NotesEditor,
    ) -> dict[str, float | int]:
        cursor = editor.textCursor()
        cursor_rect = editor.cursorRect(cursor)
        scroll_bar = editor.verticalScrollBar()
        return {
            "block": cursor.blockNumber(),
            "position": cursor.position(),
            "top": cursor_rect.top(),
            "bottom": cursor_rect.bottom(),
            "height": cursor_rect.height(),
            "scroll": scroll_bar.value(),
            "maximum": scroll_bar.maximum(),
            "line_spacing": QFontMetricsF(
                cursor.charFormat().font()
            ).lineSpacing(),
        }

    def _wait_for_layout_and_callbacks(self) -> None:
        self.app.processEvents()
        QTest.qWait(1)
        self.app.processEvents()

    def _assert_one_line_return(
        self,
        editor: NotesEditor,
        before: dict[str, float | int],
        location: str,
    ) -> dict[str, float | int]:
        self._wait_for_layout_and_callbacks()
        after = self._cursor_view_snapshot(editor)
        line_spacing = float(before["line_spacing"])
        scroll_delta = int(after["scroll"]) - int(before["scroll"])
        document_delta = (
            float(after["top"])
            + int(after["scroll"])
            - float(before["top"])
            - int(before["scroll"])
        )

        self.assertEqual(after["block"], int(before["block"]) + 1)
        self.assertEqual(after["position"], int(before["position"]) + 1)
        self.assertEqual(editor.textCursor().block().text(), "")
        self.assertAlmostEqual(
            document_delta,
            line_spacing,
            delta=max(4.0, line_spacing * 0.35),
        )
        self.assertGreater(int(after["scroll"]), 0)
        self.assertLess(int(after["scroll"]), int(after["maximum"]))
        self.assertLessEqual(
            abs(scroll_delta),
            round(line_spacing * 1.5) + 4,
        )

        expected_visual_top = (
            float(before["top"]) + line_spacing - scroll_delta
        )
        self.assertAlmostEqual(
            float(after["top"]),
            expected_visual_top,
            delta=max(4.0, line_spacing * 0.35),
        )
        if location in {"upper", "middle"}:
            self.assertLessEqual(abs(scroll_delta), 2)
            self.assertGreater(
                editor.viewport().height() - float(after["bottom"]),
                2,
            )
        else:
            viewport_bottom = editor.viewport().rect().bottom() - 2
            predicted_bottom = (
                float(before["top"])
                + line_spacing
                + float(after["height"])
                - 1
            )
            minimum_scroll = max(0.0, predicted_bottom - viewport_bottom)
            self.assertGreaterEqual(scroll_delta, 0)
            self.assertAlmostEqual(
                scroll_delta,
                minimum_scroll,
                delta=max(4.0, line_spacing * 0.35),
            )
            self.assertLessEqual(
                float(after["bottom"]),
                editor.viewport().rect().bottom(),
            )
        return after

    def _set_heading_document_and_apply_size(self) -> None:
        self.editor.close()
        self.editor = NotesEditor()
        self.editor.setPlainText("alpha\nbravo\ncharlie")
        self._move_to_document_end()
        self.editor.apply_heading(1)
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.Document)
        self.editor.setTextCursor(cursor)
        self.editor.apply_font_size(20)
        self._move_to_document_end()
        self.assertEqual(
            self.editor.textCursor().blockFormat().headingLevel(),
            1,
        )

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

    def test_return_after_select_all_heading_keeps_20px_for_new_paragraphs(
        self,
    ) -> None:
        self._set_heading_document_and_apply_size()

        QTest.keyClick(self.editor, Qt.Key_Return)

        first_new_block = self.editor.textCursor().block()
        self.assertEqual(first_new_block.blockFormat().headingLevel(), 0)
        self._assert_input_size(self.editor, 20)
        insertion_position = self.editor.textCursor().position()
        QTest.keyClicks(self.editor, "not_ok")
        self._assert_pixel_size(self._format_at(insertion_position), 20)
        self._assert_pixel_size(first_new_block.charFormat(), 20)

        QTest.keyClick(self.editor, Qt.Key_Return)

        second_new_block = self.editor.textCursor().block()
        self.assertEqual(second_new_block.blockNumber(), 4)
        self.assertEqual(second_new_block.text(), "")
        self._assert_input_size(self.editor, 20)

    def test_consecutive_returns_and_arrow_into_empty_block_keep_20px(
        self,
    ) -> None:
        self._reset_with_fictional_document()
        self._move_to_document_end()

        QTest.keyClick(self.editor, Qt.Key_Return)
        first_empty = self.editor.textCursor().block()
        self._assert_input_size(self.editor, 20)
        QTest.keyClick(self.editor, Qt.Key_Return)
        second_empty = self.editor.textCursor().block()
        self.assertEqual(second_empty.text(), "")
        self._assert_input_size(self.editor, 20)

        QTest.keyClick(self.editor, Qt.Key_Up)
        self.assertEqual(
            self.editor.textCursor().blockNumber(),
            first_empty.blockNumber(),
        )
        insertion_position = self.editor.textCursor().position()
        QTest.keyClicks(self.editor, "arrow")
        self._assert_pixel_size(self._format_at(insertion_position), 20)
        self._assert_pixel_size(first_empty.charFormat(), 20)

    def test_return_preserves_list_quote_and_code_block_semantics(self) -> None:
        for structure in ("list", "quote", "code"):
            with self.subTest(structure=structure):
                self.editor.close()
                self.editor = NotesEditor()
                self.editor.setPlainText("alpha\nbravo\ncharlie")
                self._move_to_document_end()
                if structure == "list":
                    self.editor.apply_list(False)
                elif structure == "quote":
                    self.editor.apply_quote()
                else:
                    self.editor.apply_code_block()
                cursor = self.editor.textCursor()
                cursor.select(QTextCursor.Document)
                self.editor.setTextCursor(cursor)
                self.editor.apply_font_size(20)
                self._move_to_document_end()

                QTest.keyClick(self.editor, Qt.Key_Return)

                block = self.editor.textCursor().block()
                if structure == "list":
                    self.assertIsNotNone(block.textList())
                elif structure == "quote":
                    self.assertTrue(
                        bool(
                            block.blockFormat().property(
                                QUOTE_BLOCK_PROPERTY
                            )
                        )
                    )
                else:
                    self.assertTrue(
                        self.editor._is_code_block_format(block.blockFormat())
                    )
                self._assert_input_size(self.editor, 20)

                QTest.keyClick(self.editor, Qt.Key_Return)

                exited_block = self.editor.textCursor().block()
                if structure == "list":
                    self.assertIsNone(exited_block.textList())
                elif structure == "quote":
                    self.assertFalse(
                        bool(
                            exited_block.blockFormat().property(
                                QUOTE_BLOCK_PROPERTY
                            )
                        )
                    )
                else:
                    self.assertFalse(
                        self.editor._is_code_block_format(
                            exited_block.blockFormat()
                        )
                    )
                self._assert_input_size(self.editor, 20)
                insertion_position = self.editor.textCursor().position()
                QTest.keyClicks(self.editor, "exit")
                self._assert_pixel_size(
                    self._format_at(insertion_position),
                    20,
                )

    def test_html_reload_then_return_from_heading_keeps_20px(self) -> None:
        self._set_heading_document_and_apply_size()
        html = self.editor.note_content_html()
        restored = NotesEditor()
        try:
            restored.set_note_content(html)
            self._move_to_document_end(restored)
            self.assertEqual(
                restored.textCursor().blockFormat().headingLevel(),
                1,
            )

            QTest.keyClick(restored, Qt.Key_Return)

            self.assertEqual(
                restored.textCursor().blockFormat().headingLevel(),
                0,
            )
            self._assert_input_size(restored, 20)
            insertion_position = restored.textCursor().position()
            QTest.keyClicks(restored, "reload")
            inserted = QTextCursor(restored.document())
            inserted.setPosition(insertion_position)
            inserted.setPosition(
                insertion_position + 1,
                QTextCursor.KeepAnchor,
            )
            self._assert_pixel_size(inserted.charFormat(), 20)
        finally:
            restored.close()

    def test_single_return_from_middle_moves_one_line_without_scrolling(
        self,
    ) -> None:
        editor = self._prepare_long_visible_editor(20)
        before = self._position_cursor_in_view(editor, "middle")

        QTest.keyClick(editor, Qt.Key_Return)

        after = self._assert_one_line_return(editor, before, "middle")
        self.assertGreater(
            editor.viewport().height() - float(after["bottom"]),
            float(before["line_spacing"]) * 0.5,
        )

    def test_consecutive_returns_each_move_one_line_without_scroll_jump(
        self,
    ) -> None:
        editor = self._prepare_long_visible_editor(20)
        before = self._position_cursor_in_view(editor, "middle")

        QTest.keyClick(editor, Qt.Key_Return)
        after_first = self._assert_one_line_return(editor, before, "middle")
        QTest.keyClick(editor, Qt.Key_Return)
        after_second = self._assert_one_line_return(
            editor,
            after_first,
            "middle",
        )

        self.assertEqual(after_second["block"], int(before["block"]) + 2)
        self.assertEqual(after_second["position"], int(before["position"]) + 2)

    def test_return_scroll_is_minimal_at_three_viewport_positions(self) -> None:
        for location in ("upper", "middle", "lower"):
            with self.subTest(location=location):
                editor = self._prepare_long_visible_editor(20)
                before = self._position_cursor_in_view(editor, location)

                QTest.keyClick(editor, Qt.Key_Return)

                after = self._assert_one_line_return(
                    editor,
                    before,
                    location,
                )
                if location in {"upper", "middle"}:
                    self.assertGreater(
                        editor.viewport().height() - float(after["bottom"]),
                        float(before["line_spacing"]) * 0.5,
                    )

    def test_pending_correction_does_not_override_mouse_cursor_move(
        self,
    ) -> None:
        editor = self._prepare_long_visible_editor(20)
        self._position_cursor_in_view(editor, "middle")
        QTest.keyClick(editor, Qt.Key_Return)
        expected_enter_position = editor.textCursor().position()

        QTest.mouseClick(
            editor.viewport(),
            Qt.LeftButton,
            Qt.NoModifier,
            QPoint(20, 20),
        )
        mouse_position = editor.textCursor().position()
        mouse_scroll = editor.verticalScrollBar().value()
        self.assertNotEqual(mouse_position, expected_enter_position)

        self._wait_for_layout_and_callbacks()

        self.assertEqual(editor.textCursor().position(), mouse_position)
        self.assertLessEqual(
            abs(editor.verticalScrollBar().value() - mouse_scroll),
            2,
        )

    def test_pending_correction_does_not_override_arrow_navigation(
        self,
    ) -> None:
        editor = self._prepare_long_visible_editor(20)
        self._position_cursor_in_view(editor, "middle")
        QTest.keyClick(editor, Qt.Key_Return)
        QTest.keyClick(editor, Qt.Key_Up)
        navigated_position = editor.textCursor().position()
        navigated_scroll = editor.verticalScrollBar().value()

        self._wait_for_layout_and_callbacks()

        self.assertEqual(editor.textCursor().position(), navigated_position)
        self.assertLessEqual(
            abs(editor.verticalScrollBar().value() - navigated_scroll),
            2,
        )

    def test_pending_correction_does_not_override_document_reload(
        self,
    ) -> None:
        editor = self._prepare_long_visible_editor(20)
        self._position_cursor_in_view(editor, "middle")
        QTest.keyClick(editor, Qt.Key_Return)
        editor.set_note_content("<p>Replacement fictional note.</p>")
        replacement_position = editor.textCursor().position()
        replacement_scroll = editor.verticalScrollBar().value()

        self._wait_for_layout_and_callbacks()

        self.assertEqual(editor.toPlainText(), "Replacement fictional note.")
        self.assertEqual(editor.textCursor().position(), replacement_position)
        self.assertEqual(
            editor.verticalScrollBar().value(),
            replacement_scroll,
        )

    def test_long_20px_document_return_keeps_cursor_visible_at_bottom(
        self,
    ) -> None:
        editor = self._prepare_long_visible_editor(20)

        for index in range(5):
            with self.subTest(index=index):
                before = editor.verticalScrollBar().value()
                before_maximum = editor.verticalScrollBar().maximum()
                self.assertGreaterEqual(
                    before,
                    int(before_maximum * 0.75),
                )

                QTest.keyClick(editor, Qt.Key_Return)
                self._assert_cursor_visible_near_scroll_end(editor)
                QTest.keyClicks(editor, f" fictional continuation {index}")
                self._assert_cursor_visible_near_scroll_end(editor)

    def test_long_default_15px_document_return_keeps_cursor_visible_at_bottom(
        self,
    ) -> None:
        editor = self._prepare_long_visible_editor(None)
        self.assertEqual(editor.textCursor().charFormat().font().pixelSize(), 15)

        QTest.keyClick(editor, Qt.Key_Return)

        self._assert_cursor_visible_near_scroll_end(editor)

    def test_long_structured_blocks_keep_scroll_follow_when_exiting(self) -> None:
        for structure in ("heading", "list", "quote", "code"):
            with self.subTest(structure=structure):
                editor = self._prepare_long_visible_editor(20)
                if structure == "heading":
                    editor.apply_heading(1)
                elif structure == "list":
                    editor.apply_list(False)
                elif structure == "quote":
                    editor.apply_quote()
                else:
                    editor.apply_code_block()
                editor.ensureCursorVisible()
                self.app.processEvents()

                QTest.keyClick(editor, Qt.Key_Return)
                self._assert_cursor_visible_near_scroll_end(editor)
                if structure != "heading":
                    QTest.keyClick(editor, Qt.Key_Return)
                    self._assert_cursor_visible_near_scroll_end(editor)

                block = editor.textCursor().block()
                if structure == "heading":
                    self.assertEqual(block.blockFormat().headingLevel(), 0)
                elif structure == "list":
                    self.assertIsNone(block.textList())
                elif structure == "quote":
                    self.assertFalse(
                        bool(
                            block.blockFormat().property(QUOTE_BLOCK_PROPERTY)
                        )
                    )
                else:
                    self.assertFalse(
                        editor._is_code_block_format(block.blockFormat())
                    )

    def test_long_html_reload_then_return_keeps_cursor_visible_at_bottom(
        self,
    ) -> None:
        source = self._prepare_long_visible_editor(20)
        html = source.note_content_html()
        restored = NotesEditor()
        try:
            restored.resize(500, 180)
            restored.show()
            self.app.processEvents()
            restored.set_note_content(html)
            self._move_to_document_end(restored)
            restored.ensureCursorVisible()
            self.app.processEvents()
            self.assertGreater(restored.verticalScrollBar().maximum(), 0)
            self.assertGreater(restored.verticalScrollBar().value(), 0)

            QTest.keyClick(restored, Qt.Key_Return)

            self._assert_cursor_visible_near_scroll_end(restored)
        finally:
            restored.close()

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
