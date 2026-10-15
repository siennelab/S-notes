"""Real bundled-editor regressions for body-size Markdown and typing continuity.

Run with a temporary HOME.  The component is exercised directly and never
opens the S-notes database.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
import unittest
from uuid import uuid4

from PySide6.QtCore import QPoint, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from src.snotes.ui.markdown_editor import MarkdownEditorWidget


class MarkdownBodySizeUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Path(os.environ["HOME"]).name.startswith("snotes-body-size-ui-"):
            raise RuntimeError("Run with a temporary snotes-body-size-ui-* HOME")
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.editor = MarkdownEditorWidget()
        cls.editor.resize(900, 650)
        cls.editor.show()
        cls.wait(lambda: cls.editor.ready)

    @classmethod
    def wait(cls, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                raise AssertionError(
                    f"Timeout: state={cls.editor.state}, error={cls.editor.error}"
                )
            QTest.qWait(20)

    @classmethod
    def js(cls, source):
        result = []
        cls.editor.page.runJavaScript(source, lambda value: result.append(value))
        cls.wait(lambda: len(result) == 1)
        return result[0]

    @classmethod
    def load(cls, markdown):
        load_id = uuid4().hex
        cls.editor.setMarkdown(markdown, load_id)
        cls.wait(lambda: cls.editor.current_load_id == load_id and cls.editor.can_save)
        cls.wait(lambda: cls.js("document.querySelector('.ProseMirror')?.contentEditable") == "true")
        return cls.editor.latest_markdown

    @classmethod
    def click(cls, command):
        cls.js(
            "document.querySelector(%s).click()"
            % json.dumps(f'[data-command="{command}"]')
        )
        QTest.qWait(50)

    @classmethod
    def select_text(cls, selector, start, end=None):
        if end is None:
            end = start
        cls.js(
            """(() => {
                const element = document.querySelector(%s);
                const text = element.firstChild;
                const range = document.createRange();
                range.setStart(text, %d); range.setEnd(text, %d);
                const selection = window.getSelection();
                selection.removeAllRanges(); selection.addRange(range);
                element.closest('.ProseMirror').focus();
                return true;
            })()"""
            % (json.dumps(selector), start, end)
        )
        QTest.qWait(30)

    @classmethod
    def insert(cls, text):
        cls.js(
            "document.execCommand('insertText', false, %s)" % json.dumps(text)
        )
        cls.wait(lambda: text in cls.js("document.querySelector('.ProseMirror').textContent"))

    @classmethod
    def computed_size_for_text(cls, needle):
        return cls.js(
            """(() => {
                const walker = document.createTreeWalker(
                    document.querySelector('.ProseMirror'), NodeFilter.SHOW_TEXT
                );
                while (walker.nextNode()) {
                    if (walker.currentNode.data.includes(%s))
                        return getComputedStyle(walker.currentNode.parentElement).fontSize;
                }
                return null;
            })()"""
            % json.dumps(needle)
        )

    @classmethod
    def mouse_click_text_end(cls, selector):
        rect = json.loads(
            cls.js(
                """JSON.stringify((() => {
                    const element = document.querySelector(%s);
                    const range = document.createRange();
                    range.selectNodeContents(element);
                    return range.getBoundingClientRect();
                })())"""
                % json.dumps(selector)
            )
        )
        point = QPoint(round(rect["right"] + 2), round(rect["y"] + rect["height"] / 2))
        target = cls.editor.view.focusProxy() or cls.editor.view
        if target is not cls.editor.view:
            point = target.mapFrom(cls.editor.view, point)
        QTest.mouseClick(target, Qt.LeftButton, pos=point)
        QTest.qWait(50)

    def set_paragraph_size(self, size, text="alpha"):
        self.load(text)
        self.select_text(".ProseMirror p", 0, len(text))
        self.click(str(size))
        self.wait(lambda: f'size="{size}"' in self.editor.latest_markdown)

    def test_toolbar_roundtrip_default_and_list_markers(self):
        self.load("plain")
        commands = json.loads(
            self.js(
                "JSON.stringify([...document.querySelectorAll('#toolbar button')].slice(0,7).map(b=>b.dataset.command))"
            )
        )
        self.assertEqual(commands, ["H1", "H2", "H3", "15", "17", "20", "24"])
        self.assertEqual(self.js("getComputedStyle(document.querySelector('.ProseMirror')).fontSize"), "17px")

        for size in (15, 20, 24):
            with self.subTest(roundtrip=size):
                source = f':size[roundtrip]{chr(123)}size="{size}"{chr(125)}'
                canonical = self.load(source)
                self.assertEqual(self.load(canonical), canonical)
                self.assertEqual(
                    self.js(
                        f"getComputedStyle(document.querySelector('[data-body-size=\"{size}\"]')).fontSize"
                    ),
                    f"{size}px",
                )

            with self.subTest(list_marker=size):
                self.load(f'- :size[item]{chr(123)}size="{size}"{chr(125)}')
                marker = f'.milkdown-list-item-block[data-marker-style="body{size}"] .label-wrapper'
                self.assertTrue(self.js(f"Boolean(document.querySelector({json.dumps(marker)}))"))
                self.assertEqual(
                    self.js(f"getComputedStyle(document.querySelector({json.dumps(marker)})).fontSize"),
                    f"{size}px",
                )

        self.set_paragraph_size(24)
        self.select_text('.ProseMirror [data-body-size="24"]', 0, 5)
        self.click("17")
        self.wait(lambda: ":size[" not in self.editor.latest_markdown)
        self.assertFalse(self.js("Boolean(document.querySelector('[data-body-size]'))"))
        self.assertEqual(self.computed_size_for_text("alpha"), "17px")

    def test_full_paragraph_tail_typing_inherits_after_right_key_and_reload_click(self):
        for size in (15, 20, 24):
            with self.subTest(size=size):
                self.set_paragraph_size(size)
                target = self.editor.view.focusProxy() or self.editor.view
                target.setFocus()
                QTest.keyClick(target, Qt.Key_Right)
                QTest.qWait(30)
                self.insert("R")
                self.wait(lambda: "alphaR" in self.editor.latest_markdown)
                self.assertIn(f'size="{size}"', self.editor.latest_markdown)
                self.assertEqual(self.computed_size_for_text("alphaR"), f"{size}px")

                self.load(f':size[alpha]{chr(123)}size="{size}"{chr(125)}')
                self.mouse_click_text_end(f'.ProseMirror [data-body-size="{size}"]')
                self.insert("C")
                self.wait(lambda: "alphaC" in self.editor.latest_markdown)
                self.assertIn(f'size="{size}"', self.editor.latest_markdown)
                self.assertEqual(self.computed_size_for_text("alphaC"), f"{size}px")

    def test_local_run_internal_and_trailing_boundary_typing_inherit(self):
        for size in (20, 24):
            for offset, expected in ((1, "cXd"), (2, "cdX")):
                with self.subTest(size=size, offset=offset):
                    self.load(f'ab:size[cd]{chr(123)}size="{size}"{chr(125)}ef')
                    self.select_text(f'.ProseMirror [data-body-size="{size}"]', offset)
                    self.insert("X")
                    self.wait(lambda: expected in self.editor.latest_markdown)
                    self.assertIn(f':size[{expected}]', self.editor.latest_markdown)
                    self.assertEqual(self.computed_size_for_text(expected), f"{size}px")

    def test_caret_17_explicitly_returns_to_default_but_plain_typing_stays_default(self):
        self.set_paragraph_size(20)
        self.select_text('.ProseMirror [data-body-size="20"]', 5)
        self.click("17")
        self.insert("D")
        self.wait(lambda: self.editor.latest_markdown.strip().endswith("D"))
        self.assertEqual(self.editor.latest_markdown.strip(), ':size[alpha]{size="20"}D')
        self.assertEqual(self.computed_size_for_text("D"), "17px")

        self.load("plain")
        self.select_text(".ProseMirror p", 5)
        self.insert("D")
        self.wait(lambda: self.editor.latest_markdown.strip() == "plainD")
        self.assertEqual(self.editor.latest_markdown.strip(), "plainD")
        self.assertNotIn(":size[", self.editor.latest_markdown)
        self.assertEqual(self.computed_size_for_text("plainD"), "17px")

    def test_heading_body_size_semantics_remain_intact(self):
        self.load("## heading")
        self.select_text(".ProseMirror h2", 7)
        self.click("24")
        self.wait(lambda: 'size="24"' in self.editor.latest_markdown)
        self.assertFalse(self.editor.latest_markdown.startswith("#"))
        self.assertEqual(self.js("document.querySelector('.ProseMirror').firstElementChild.tagName"), "P")
        self.assertEqual(self.computed_size_for_text("heading"), "24px")
        self.insert("Z")
        self.wait(lambda: "headingZ" in self.editor.latest_markdown)
        self.assertEqual(self.computed_size_for_text("headingZ"), "24px")

        self.click("H2")
        self.wait(lambda: self.editor.latest_markdown.startswith("## headingZ"))
        self.assertNotIn(":size[", self.editor.latest_markdown)
        self.assertFalse(self.js("Boolean(document.querySelector('[data-body-size]'))"))

    @classmethod
    def tearDownClass(cls):
        cls.editor.close()
        cls.editor.page.deleteLater()
        QTimer.singleShot(0, cls.app.quit)
        cls.app.exec()


if __name__ == "__main__":
    unittest.main()
