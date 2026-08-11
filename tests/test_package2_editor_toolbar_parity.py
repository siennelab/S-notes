from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QTextCursor, QTextFormat
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QToolButton

from src.snotes.ui.editor import NotesEditor
from src.snotes.ui.editor_toolbar import FONT_SIZE_OPTIONS, EditorToolbar


class EditorToolbarFormatMenuTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.editor = NotesEditor()
        self.toolbar = EditorToolbar(self.editor)
        self.toolbar.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.toolbar.close()
        self.editor.close()
        self.app.processEvents()

    def test_format_button_is_an_instant_popup_tool_button(self) -> None:
        button = self.toolbar.format_button

        self.assertIsInstance(button, QToolButton)
        self.assertEqual(button.text(), "标题")
        self.assertEqual(button.objectName(), "toolbarButton")
        self.assertEqual(button.toolTip(), "标题层级")
        self.assertEqual((button.width(), button.height()), (37, 29))
        self.assertEqual(button.toolButtonStyle(), Qt.ToolButtonTextOnly)
        self.assertEqual(button.popupMode(), QToolButton.InstantPopup)

    def test_click_shows_the_complete_format_menu_once(self) -> None:
        button = self.toolbar.format_button
        menu = button.menu()
        self.assertIsNotNone(menu)
        assert menu is not None
        shown = QSignalSpy(menu.aboutToShow)
        menu.aboutToShow.connect(lambda: QTimer.singleShot(0, menu.close))

        QTest.mouseClick(button, Qt.LeftButton)
        self.app.processEvents()

        self.assertEqual(shown.count(), 1)
        self.assertEqual(
            [action.text() for action in menu.actions() if not action.isSeparator()],
            ["正文", "标题 1", "标题 2"],
        )

    def test_font_size_menu_has_the_complete_ordered_pixel_options(self) -> None:
        menu = self.toolbar.font_size_button.menu()
        self.assertIsNotNone(menu)
        assert menu is not None
        self.assertEqual(
            [action.text() for action in menu.actions()],
            [label for label, _pixel_size in FONT_SIZE_OPTIONS],
        )
        self.assertEqual(
            FONT_SIZE_OPTIONS,
            (
                ("11", 11),
                ("13", 13),
                ("15(正文)", 15),
                ("17", 17),
                ("20", 20),
                ("23", 23),
                ("26", 26),
                ("32", 32),
            ),
        )

    def test_approved_toolbar_actions_and_cleanup_labels(self) -> None:
        group_tooltips: list[list[str]] = []
        for group in self.toolbar.toolbar_groups:
            layout = group.layout()
            group_tooltips.append(
                [
                    layout.itemAt(index).widget().toolTip()
                    for index in range(layout.count())
                    if layout.itemAt(index).widget() is not None
                ]
            )
        self.assertEqual(
            group_tooltips,
            [
                ["撤销", "还原"],
                ["文字大小", "加粗", "斜体", "删除线"],
                ["清除选区或全文格式", "清除 Markdown 标记", "压缩空行"],
                ["标题层级", "代码块"],
                ["展开或收起查找替换"],
            ],
        )
        self.assertEqual(self.toolbar.clear_format_button.text(), "清格式")
        self.assertEqual(self.toolbar.clear_markdown_button.text(), "清MD")
        self.assertEqual(self.toolbar.compress_blank_lines_button.text(), "空行")
        self.assertTrue(self.toolbar.clear_format_button.icon().isNull())
        self.assertTrue(self.toolbar.clear_markdown_button.icon().isNull())
        self.assertTrue(self.toolbar.compress_blank_lines_button.icon().isNull())
        self.assertEqual(
            [action.text() for action in self.toolbar.compress_blank_lines_button.menu().actions()],
            ["保留一个空行", "移除所有空行"],
        )

    def test_heading_one_action_applies_real_text_format(self) -> None:
        self.editor.setPlainText("heading")
        selection = self.editor.textCursor()
        selection.select(QTextCursor.Document)
        self.editor.setTextCursor(selection)
        menu = self.toolbar.format_button.menu()
        self.assertIsNotNone(menu)
        assert menu is not None
        heading_one = next(action for action in menu.actions() if action.text() == "标题 1")

        heading_one.trigger()

        char_cursor = QTextCursor(self.editor.document())
        char_cursor.setPosition(0)
        char_cursor.setPosition(1, QTextCursor.KeepAnchor)
        char_format = char_cursor.charFormat()
        self.assertEqual(
            self.editor.document().firstBlock().blockFormat().headingLevel(),
            1,
        )
        self.assertEqual(char_format.property(QTextFormat.FontPixelSize), 26)
        self.assertEqual(char_format.fontWeight(), QFont.Bold)


if __name__ == "__main__":
    unittest.main()
