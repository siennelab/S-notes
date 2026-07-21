from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QTextCursor, QTextFormat
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QToolButton

from src.editor import NotesEditor
from src.editor_toolbar import EditorToolbar


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
        self.assertEqual(button.toolTip(), "标题和字号")
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
            ["正文", "标题 1", "标题 2", "小字", "正文大小", "大字"],
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
