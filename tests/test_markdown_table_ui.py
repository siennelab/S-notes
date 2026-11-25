"""Drive the bundled editor with Qt mouse and keyboard events in isolation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
import unittest
from uuid import uuid4

from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit

from src.snotes.ui.markdown_demo import FIXTURES, MarkdownDemo
from src.snotes.ui.theme import THEMES


TABLE = FIXTURES["表格与拖选"]
CAMERA_VALUE = "tr:nth-child(4) td:nth-child(3) p"
OPERATIONS_TABLE = """| Left | Center | Right |
| :--- | :---: | ---: |
| A1 | B1 | C1 |
| A2 | B2 | C2 |
"""


class MarkdownTableUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Path(os.environ["HOME"]).name.startswith("snotes-test-home-"):
            raise RuntimeError("Run with a temporary snotes-test-home-* HOME")
        if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            raise RuntimeError("Use Qt's offscreen platform for an isolated clipboard")
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    @classmethod
    def tearDownClass(cls):
        QTimer.singleShot(0, cls.app.quit)
        cls.app.exec()

    def setUp(self):
        self.window = MarkdownDemo()
        self.paste_target = QLineEdit(self.window)
        self.window.centralWidget().layout().addWidget(self.paste_target)
        self.window.resize(1050, 650)
        self.window.show()
        self.editor = self.window.editor
        self.target = self.editor.view.focusProxy() or self.editor.view
        self.load(TABLE)

    def tearDown(self):
        self.window.close()
        self.wait(lambda: not self.window.isVisible())
        self.editor.page.deleteLater()

    def wait(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                self.fail(f"Timeout: {self.editor.state}: {self.editor.error}")
            QTest.qWait(20)

    def js(self, source):
        result = []
        self.editor.page.runJavaScript(source, result.append)
        self.wait(lambda: bool(result))
        return result[0]

    def load(self, body):
        self.editor.setMarkdown(body, uuid4().hex)
        self.wait(lambda: self.editor.can_save)
        self.wait(lambda: self.js("document.querySelector('.ProseMirror')?.contentEditable") == "true")

    def text_point(self, selector, offset, *, dx=0):
        # Read glyph coordinates without touching the editor selection.
        point = json.loads(self.js("""(() => {
            const text = document.querySelector(%s).firstChild;
            const range = new Range();
            range.setStart(text, %d);
            range.collapse(true);
            const rect = range.getBoundingClientRect();
            return JSON.stringify([rect.x, rect.y + rect.height / 2]);
        })()""" % (json.dumps(selector), offset)))
        return QPoint(round(point[0] + dx), round(point[1]))

    def drag(self, start, end, *, hold=80):
        # Allow separate drags at nearby points to remain single clicks.
        QTest.qWait(520)
        QTest.mousePress(self.target, Qt.LeftButton, pos=start)
        try:
            QTest.mouseMove(self.target, (start + end) / 2, 30)
            QTest.mouseMove(self.target, end, 30)
            QTest.qWait(hold)
        finally:
            QTest.mouseRelease(self.target, Qt.LeftButton, pos=end)
        QTest.qWait(100)

    def selector_point(self, selector):
        rect = json.loads(self.js("""JSON.stringify((() => {
            const rect = document.querySelector(%s).getBoundingClientRect();
            return [rect.x + rect.width / 2, rect.y + rect.height / 2];
        })())""" % json.dumps(selector)))
        return QPoint(round(rect[0]), round(rect[1]))

    def click_selector(self, selector):
        QTest.mouseClick(self.target, Qt.LeftButton, pos=self.selector_point(selector))
        QTest.qWait(80)

    def place_caret(self, selector, offset=1):
        # A fresh document often puts the intended cell at the same viewport
        # coordinates as the prior one.  Keep those clicks outside Qt/WebEngine's
        # multi-click interval so they cannot become a CellSelection.
        QTest.qWait(520)
        length = self.js("document.querySelector(%s).textContent.length" % json.dumps(selector))
        self.click_selector(selector) if not length else self.click_selector_at_text(selector, min(offset, length))

    def click_selector_at_text(self, selector, offset):
        QTest.mouseClick(self.target, Qt.LeftButton, pos=self.text_point(selector, offset))
        QTest.qWait(80)

    def table_matrix(self):
        return json.loads(self.js("""JSON.stringify([...document.querySelectorAll('table tr')]
            .map(row => [...row.cells].map(cell => cell.textContent)))"""))

    def selection_cell(self):
        return json.loads(self.js("""JSON.stringify((() => {
            const node = getSelection().anchorNode;
            const cell = (node?.nodeType === Node.ELEMENT_NODE ? node : node?.parentElement)
                ?.closest('td, th');
            return cell ? [cell.parentElement.rowIndex, cell.cellIndex] : null;
        })())"""))

    def button_disabled(self, selector):
        return self.js("document.querySelector(%s).disabled" % json.dumps(selector))

    def open_table_operations(self, cell_selector):
        self.place_caret(cell_selector)
        self.wait(lambda: not self.button_disabled('[data-command="Table"]'))
        self.click_selector('[data-command="Table"]')
        self.wait(lambda: self.js("document.querySelector('#table-operations').offsetParent !== null"))

    def run_table_operation(self, operation, cell_selector="tr:nth-child(2) td:nth-child(2) p"):
        self.open_table_operations(cell_selector)
        selector = f'[data-table-operation="{operation}"]'
        self.assertFalse(self.button_disabled(selector))
        self.click_selector(selector)
        self.wait(lambda: self.editor.dirty)
        self.assertTrue(self.js("document.activeElement.closest('.ProseMirror') !== null"))

    def assert_copies_text(self, expected):
        self.assertEqual(self.js("getSelection().toString()"), expected)
        self.assertEqual(self.js("document.querySelectorAll('.selectedCell').length"), 0)
        self.app.clipboard().clear()
        QTest.keyClick(self.target, Qt.Key_C, Qt.ControlModifier)
        self.wait(lambda: self.app.clipboard().text() == expected)
        self.paste_target.clear()
        self.paste_target.setFocus()
        QTest.keyClick(self.paste_target, Qt.Key_V, Qt.ControlModifier)
        self.assertEqual(self.paste_target.text(), expected)

    def test_mouse_and_keyboard_copy_cell_text(self):
        baseline = self.editor.baseline
        changes = []
        self.editor.changed.connect(lambda *args: changes.append(args))

        self.drag(self.text_point(CAMERA_VALUE, 2), self.text_point(CAMERA_VALUE, 5))
        self.assert_copies_text("n't")
        for selector in ("tr:nth-child(2) td:nth-child(2) p", "tr:nth-child(3) td:nth-child(2) p",
                         CAMERA_VALUE):
            with self.subTest(cell=selector):
                # A small overshoot of the last glyph must still select only text.
                self.drag(self.text_point(selector, 0), self.text_point(selector, 11, dx=4))
                self.assert_copies_text("Don't allow")

        QTest.qWait(520)
        QTest.mouseClick(self.target, Qt.LeftButton, pos=self.text_point(CAMERA_VALUE, 11))
        for _ in "Don't allow":
            QTest.keyClick(self.target, Qt.Key_Left, Qt.ShiftModifier)
        QTest.qWait(100)
        self.assert_copies_text("Don't allow")
        self.assertFalse(self.editor.dirty)
        self.assertEqual(self.editor.latest_markdown, baseline)
        self.assertEqual(changes, [])

    def test_table_borders_and_cell_selection_follow_theme(self):
        self.drag(self.text_point("tr:nth-child(2) td:nth-child(2) p", 2),
                  self.text_point(CAMERA_VALUE, 8))
        selected = json.loads(self.js("""JSON.stringify([...document.querySelectorAll('.selectedCell')]
            .map(cell => [cell.parentElement.rowIndex, cell.cellIndex]))"""))
        self.assertEqual(selected, [[1, 1], [1, 2], [2, 1], [2, 2], [3, 1], [3, 2]])
        self.app.clipboard().clear()
        QTest.keyClick(self.target, Qt.Key_C, Qt.ControlModifier)
        self.wait(lambda: self.app.clipboard().mimeData() and self.app.clipboard().mimeData().hasHtml())
        self.assertIn("<table", self.app.clipboard().mimeData().html())

        for theme in THEMES.values():
            with self.subTest(theme=theme.name):
                self.editor.setTheme(theme)
                self.wait(lambda: self.js("""document.documentElement.style
                    .getPropertyValue('--editor-border')""").lower() == theme.editor_border.lower())
                styles = json.loads(self.js("""JSON.stringify((() => {
                    const emptyCell = document.querySelector('tr:nth-child(4) td:nth-child(2)');
                    const style = getComputedStyle(emptyCell);
                    return [emptyCell.textContent, style.borderTopWidth, style.borderTopStyle,
                            style.borderTopColor, style.backgroundColor,
                            getComputedStyle(emptyCell.querySelector('p'), '::selection').backgroundColor];
                })())"""))
                self.assertEqual(styles, ["", "1px", "solid", self.rgb(theme.editor_border),
                                          self.rgb(theme.surface_selected), "rgba(0, 0, 0, 0)"])
        self.assertFalse(self.editor.dirty)

    @staticmethod
    def rgb(color):
        return "rgb({}, {}, {})".format(*QColor(color).getRgb()[:3])

    def test_plain_text_drag_and_bottom_edge_autoscroll(self):
        self.load("\n\n".join(f"Plain line {number:02}" for number in range(60)))
        self.drag(self.text_point(".ProseMirror p:first-child", 0),
                  self.text_point(".ProseMirror p:first-child", 5))
        self.assert_copies_text("Plain")

        start = self.text_point(".ProseMirror p:nth-child(2)", 0)
        bottom = self.js("document.querySelector('.panes > section').getBoundingClientRect().bottom")
        self.drag(start, QPoint(start.x() + 40, round(bottom) - 4), hold=950)
        self.assertGreater(self.js("document.querySelector('.panes > section').scrollTop"), 0)
        self.assertTrue(self.js("getSelection().toString().startsWith('Plain line 01')"))
        self.assertFalse(self.editor.dirty)

    def test_all_table_operations_use_current_cell_and_place_caret(self):
        cases = (
            ("row-before", [["Left", "Center", "Right"], ["", "", ""],
                            ["A1", "B1", "C1"], ["A2", "B2", "C2"]], [1, 1]),
            ("row-after", [["Left", "Center", "Right"], ["A1", "B1", "C1"],
                           ["", "", ""], ["A2", "B2", "C2"]], [2, 1]),
            ("row-delete", [["Left", "Center", "Right"], ["A2", "B2", "C2"]], [1, 1]),
            ("col-before", [["Left", "", "Center", "Right"],
                            ["A1", "", "B1", "C1"], ["A2", "", "B2", "C2"]], [1, 1]),
            ("col-after", [["Left", "Center", "", "Right"],
                           ["A1", "B1", "", "C1"], ["A2", "B2", "", "C2"]], [1, 2]),
            ("col-delete", [["Left", "Right"], ["A1", "C1"], ["A2", "C2"]], [1, 1]),
        )
        for operation, expected, caret in cases:
            with self.subTest(operation=operation):
                self.load(OPERATIONS_TABLE)
                baseline = self.editor.latest_markdown
                self.run_table_operation(operation)
                self.wait(lambda: self.table_matrix() == expected)
                self.assertEqual(self.selection_cell(), caret)
                changed = self.editor.latest_markdown
                QTest.keyClick(self.target, Qt.Key_Z, Qt.MetaModifier)
                self.wait(lambda: self.editor.latest_markdown == baseline)
                self.assertEqual(self.table_matrix(), [
                    ["Left", "Center", "Right"], ["A1", "B1", "C1"],
                    ["A2", "B2", "C2"],
                ])
                QTest.keyClick(self.target, Qt.Key_Z, Qt.MetaModifier | Qt.ShiftModifier)
                self.wait(lambda: self.editor.latest_markdown == changed)
                self.assertEqual(self.table_matrix(), expected)

        # The operation must re-read the caret after the menu was opened.
        self.load(OPERATIONS_TABLE)
        self.open_table_operations("tr:nth-child(2) td:nth-child(2) p")
        QTest.keyClick(self.target, Qt.Key_Down)
        self.wait(lambda: self.selection_cell() == [2, 1])
        self.click_selector('[data-table-operation="row-before"]')
        self.wait(lambda: len(self.table_matrix()) == 4)
        self.assertEqual(self.table_matrix()[1], ["A1", "B1", "C1"])
        self.assertEqual(self.table_matrix()[2], ["", "", ""])
        self.assertEqual(self.selection_cell(), [2, 1])

    def test_table_operation_guards_and_single_cell_text_selection(self):
        self.load("Outside\n\n" + OPERATIONS_TABLE)
        self.assertEqual(
            self.js("document.querySelector('[data-command=\"Table\"]').textContent.trim()"),
            "表格",
        )
        self.assertEqual(
            json.loads(self.js("""JSON.stringify([...document.querySelectorAll(
                '#table-operations [data-table-operation]')].map(button => button.dataset.tableOperation))""")),
            ["row-before", "row-after", "row-delete", "col-before", "col-after", "col-delete"],
        )
        self.assertFalse(self.js("document.querySelector('#table-operations').offsetParent !== null"))
        self.place_caret(".ProseMirror > p:first-child")
        self.assertTrue(self.button_disabled('[data-command="Table"]'))
        self.assertFalse(self.js("document.querySelector('#table-operations').offsetParent !== null"))

        self.open_table_operations("tr:first-child th:nth-child(2) p")
        self.assertTrue(self.button_disabled('[data-table-operation="row-before"]'))
        self.assertFalse(self.button_disabled('[data-table-operation="row-after"]'))
        self.assertTrue(self.button_disabled('[data-table-operation="row-delete"]'))
        self.click_selector('[data-table-operation="row-after"]')
        self.wait(lambda: len(self.table_matrix()) == 4)
        self.assertEqual(self.table_matrix()[0], ["Left", "Center", "Right"])
        self.assertEqual(self.table_matrix()[1], ["", "", ""])
        self.assertEqual(self.selection_cell(), [1, 1])

        self.load("| Only |\n| --- |\n| body |\n")
        self.open_table_operations("tr:nth-child(2) td p")
        self.assertTrue(self.button_disabled('[data-table-operation="row-delete"]'))
        self.assertTrue(self.button_disabled('[data-table-operation="col-delete"]'))

        self.load(OPERATIONS_TABLE)
        self.drag(self.text_point("tr:nth-child(2) td:nth-child(2) p", 0),
                  self.text_point("tr:nth-child(3) td:nth-child(3) p", 2))
        self.assertGreater(self.js("document.querySelectorAll('.selectedCell').length"), 1)
        self.assertTrue(self.button_disabled('[data-command="Table"]'))

        self.load(OPERATIONS_TABLE)
        cell = "tr:nth-child(2) td:nth-child(2) p"
        self.drag(self.text_point(cell, 0), self.text_point(cell, 2))
        self.assertEqual(self.js("getSelection().toString()"), "B1")
        self.assertEqual(self.js("document.querySelectorAll('.selectedCell').length"), 0)
        self.assertFalse(self.button_disabled('[data-command="Table"]'))
        self.click_selector('[data-command="Table"]')
        self.assertFalse(self.button_disabled('[data-table-operation="col-after"]'))

    def test_empty_column_undo_redo_and_canonical_reload(self):
        source = """| Left | | | Right |
| :--- | :---: | :---: | ---: |
| one | | | three |
| two | | | four |
"""
        self.load(source)
        self.run_table_operation("col-delete", "tr:nth-child(3) td:nth-child(2) p")
        self.wait(lambda: self.table_matrix() == [
            ["Left", "", "Right"], ["one", "", "three"], ["two", "", "four"]])
        once_deleted = self.editor.latest_markdown

        # The command leaves the caret in the nearest surviving cell: reopen the
        # compact panel and delete the next blank column without locating the
        # caret again.
        self.assertEqual(self.selection_cell(), [2, 1])
        self.click_selector('[data-command="Table"]')
        self.wait(lambda: self.js("document.querySelector('#table-operations').offsetParent !== null"))
        self.click_selector('[data-table-operation="col-delete"]')
        self.wait(lambda: self.table_matrix() == [
            ["Left", "Right"], ["one", "three"], ["two", "four"]])
        twice_deleted = self.editor.latest_markdown

        QTest.keyClick(self.target, Qt.Key_Z, Qt.MetaModifier)
        self.wait(lambda: len(self.table_matrix()[0]) == 3)
        self.assertEqual(self.table_matrix(), [
            ["Left", "", "Right"], ["one", "", "three"], ["two", "", "four"]])
        self.assertEqual(self.editor.latest_markdown, once_deleted)
        QTest.keyClick(self.target, Qt.Key_Z, Qt.MetaModifier)
        self.wait(lambda: len(self.table_matrix()[0]) == 4)
        self.assertEqual(self.table_matrix(), [
            ["Left", "", "", "Right"], ["one", "", "", "three"],
            ["two", "", "", "four"]])
        QTest.keyClick(self.target, Qt.Key_Z, Qt.MetaModifier | Qt.ShiftModifier)
        self.wait(lambda: self.editor.latest_markdown == once_deleted)
        self.assertEqual(len(self.table_matrix()[0]), 3)
        QTest.keyClick(self.target, Qt.Key_Z, Qt.MetaModifier | Qt.ShiftModifier)
        self.wait(lambda: self.editor.latest_markdown == twice_deleted)
        self.assertEqual(len(self.table_matrix()[0]), 2)

        self.load(OPERATIONS_TABLE)
        self.run_table_operation("row-before")
        QTest.keyClicks(self.target, "typed")
        self.wait(lambda: "typed" in self.editor.latest_markdown)
        canonical = self.editor.latest_markdown
        self.load(canonical)
        self.assertEqual(self.editor.latest_markdown, canonical)
        self.assertEqual(self.table_matrix()[1], ["", "typed", ""])
        aligns = json.loads(self.js("""JSON.stringify([...document.querySelectorAll('tr:nth-child(2) td')]
            .map(cell => getComputedStyle(cell).textAlign))"""))
        self.assertEqual(aligns, ["left", "center", "right"])


if __name__ == "__main__":
    unittest.main()
