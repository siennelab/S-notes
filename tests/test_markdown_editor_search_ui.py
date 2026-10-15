"""Real pointer regressions for the bundled Markdown editor search controls.

The fixture owns no database. Run with a temporary HOME so Qt WebEngine also
keeps its profile and caches away from the user's real application state.
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

from src.snotes.ui.markdown_demo import MarkdownDemo


class MarkdownEditorSearchUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Path(os.environ["HOME"]).name.startswith("snotes-search-ui-"):
            raise RuntimeError("Run with a temporary snotes-search-ui-* HOME")
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.window = MarkdownDemo()
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self.editor = self.window.editor
        self.wait(lambda: self.editor.ready and self.editor.state == "loaded")
        self.wait(self.window.isActiveWindow)

    def tearDown(self):
        self.window.confirmed_close = True
        self.window.close()
        self.wait(lambda: not self.window.isVisible())
        self.editor.page.deleteLater()

    @classmethod
    def tearDownClass(cls):
        QTimer.singleShot(0, cls.app.quit)
        cls.app.exec()

    def wait(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                self.fail(f"Timeout: {self.editor.state}: {self.editor.error}")
            QTest.qWait(20)

    def js(self, source):
        result = []
        self.editor.page.runJavaScript(source, lambda value: result.append(value))
        self.wait(lambda: bool(result))
        return result[0]

    def load(self, markdown):
        self.editor.setMarkdown(markdown, uuid4().hex)
        self.wait(lambda: self.editor.can_save)
        self.wait(lambda: self.js("document.querySelector('.ProseMirror')?.textContent") is not None)

    def point(self, selector):
        rect = json.loads(self.js(
            "JSON.stringify(document.querySelector(" + json.dumps(selector) + ").getBoundingClientRect())"
        ))
        return QPoint(round(rect["x"] + rect["width"] / 2), round(rect["y"] + rect["height"] / 2))

    def mouse_click(self, selector):
        target = self.editor.view.focusProxy() or self.editor.view
        QTest.mouseClick(target, Qt.LeftButton, pos=self.point(selector))
        QTest.qWait(80)

    def search(self, needle):
        self.js("""(() => {
            if (document.getElementById('find-bar').hidden)
                document.querySelector('[data-command="Search"]').click();
            const root = document.querySelector('.ProseMirror');
            if (!root.dataset.searchTestInstrumented) {
                const desc = root.pmViewDesc;
                const setSelection = desc.setSelection;
                desc.setSelection = function(anchor, head, view, force) {
                    window.__snotesSearchTestView = view;
                    return setSelection.call(this, anchor, head, view, force);
                };
                root.dataset.searchTestInstrumented = 'true';
            }
            const input = document.getElementById('find');
            input.focus();
            input.value = %s;
            input.dispatchEvent(new Event('input', {bubbles: true}));
            return true;
        })()""" % json.dumps(needle))
        self.assertFalse(self.js("document.getElementById('find-bar').hidden"))

    def selection(self):
        return json.loads(self.js("""JSON.stringify((() => {
            const root = document.querySelector('.ProseMirror');
            const desc = root.pmViewDesc;
            const view = window.__snotesSearchTestView;
            const selection = window.getSelection();
            const range = selection.rangeCount ? selection.getRangeAt(0) : null;
            const rect = range?.getBoundingClientRect();
            const viewport = document.querySelector('.panes > section').getBoundingClientRect();
            return {
                pmFrom: view?.state.selection.from ?? null,
                pmTo: view?.state.selection.to ?? null,
                domPmAnchor: selection.rangeCount
                    ? desc.posFromDOM(selection.anchorNode, selection.anchorOffset, 1) : null,
                domPmHead: selection.rangeCount
                    ? desc.posFromDOM(selection.focusNode, selection.focusOffset, -1) : null,
                domText: selection.toString(),
                rangeCount: selection.rangeCount,
                visible: Boolean(rect && rect.width > 0 && rect.height > 0 &&
                    rect.bottom > viewport.top && rect.top < viewport.bottom),
                focused: document.activeElement === root,
                scrollTop: document.querySelector('.panes > section').scrollTop,
            };
        })())"""))

    def test_real_find_next_selects_first_hit_then_wraps(self):
        # Put the first hit below the initial viewport so scrollIntoView is also
        # exercised by the same real pointer path that users trigger.
        prefix_lines = [f"line {index}" for index in range(70)]
        prefix = "\n\n".join(prefix_lines)
        self.load(f"{prefix}\n\ntarget first\n\ntarget second\n\ntarget third")
        self.search("target")

        first = sum(len(line) + 2 for line in prefix_lines) + 1
        second = first + len("target first") + 2
        third = second + len("target second") + 2
        expected = [(first, first + 6), (second, second + 6), (third, third + 6), (first, first + 6)]
        previous_scroll = 0
        for index, positions in enumerate(expected):
            with self.subTest(click=index + 1):
                self.mouse_click("#find-next")
                selected = self.selection()
                self.assertEqual(
                    (selected["pmFrom"], selected["pmTo"]), positions, selected
                )
                self.assertEqual(
                    (selected["domPmAnchor"], selected["domPmHead"]), positions, selected
                )
                self.assertEqual(selected["domText"], "target")
                self.assertEqual(selected["rangeCount"], 1)
                self.assertTrue(selected["visible"])
                self.assertTrue(selected["focused"])
                if index == 0:
                    self.assertGreater(selected["scrollTop"], previous_scroll)

    def test_find_next_matches_ascii_case_insensitively(self):
        for needle in ("abc", "Abc", "ABC", "aBc"):
            with self.subTest(needle=needle):
                self.load("abc ABC Abc")
                self.search(needle)
                self.assertEqual(self.js("document.getElementById('find-status').textContent"), "3 处匹配")
                for text in ("abc", "ABC", "Abc", "abc"):
                    self.mouse_click("#find-next")
                    self.assertEqual(self.selection()["domText"], text)

    def test_replace_and_replace_all_share_case_insensitive_literal_matches(self):
        self.load("abc ABC Abc \n\n中文. [a-z] 中文. [a-z]")
        self.search("AbC")
        self.js("document.getElementById('replacement').value='one'")
        self.mouse_click("#find-next")
        self.mouse_click("#replace")
        self.wait(lambda: self.js("document.querySelector('.ProseMirror').textContent").startswith("one ABC Abc"))
        self.wait(lambda: self.editor.latest_markdown.startswith("one ABC Abc"))
        self.mouse_click("#replace-all")
        self.wait(lambda: self.js("document.querySelector('.ProseMirror').textContent").startswith("one one one"))
        self.wait(lambda: self.editor.latest_markdown.startswith("one one one"))

        # Ordinary CJK and regexp-looking characters stay literal.
        self.js("""(() => {
            document.getElementById('find').value='[a-z]';
            document.getElementById('replacement').value='符号';
            document.getElementById('find').dispatchEvent(new Event('input', {bubbles:true}));
            return true;
        })()""")
        self.mouse_click("#replace-all")
        self.wait(lambda: "符号" in self.js("document.querySelector('.ProseMirror').textContent"))
        text = self.js("document.querySelector('.ProseMirror').textContent")
        self.assertEqual(text.count("符号"), 2)
        self.assertEqual(text.count("中文"), 2)
        self.wait(lambda: self.editor.latest_markdown.count("符号") == 2)

    def test_search_count_refreshes_when_switching_documents_without_next(self):
        self.load(" ".join(["keyword"] * 9))
        self.search("keyword")
        self.assertEqual(self.js("document.getElementById('find-status').textContent"), "9 处匹配")
        self.assertEqual(self.js("document.activeElement.id"), "find")

        self.load("a different note without the term")

        self.assertEqual(self.js("document.getElementById('find').value"), "keyword")
        self.assertEqual(self.js("document.getElementById('find-status').textContent"), "0 处匹配")
        self.assertEqual(self.js("document.activeElement.id"), "find")
        self.assertEqual(self.js("window.getSelection().toString()"), "")

    def test_search_count_tracks_edits_and_clears_with_empty_query(self):
        self.load("hit hit")
        self.search("hit")
        self.assertEqual(self.js("document.getElementById('find-status').textContent"), "2 处匹配")

        self.js("""(() => {
            const root = document.querySelector('.ProseMirror');
            const text = root.querySelector('p').firstChild;
            const range = document.createRange();
            range.setStart(text, text.length); range.collapse(true);
            const selection = window.getSelection();
            selection.removeAllRanges(); selection.addRange(range);
            root.focus();
            document.execCommand('insertText', false, ' hit');
            return true;
        })()""")
        self.wait(lambda: self.editor.latest_markdown.strip() == "hit hit hit")
        self.wait(lambda: self.js("document.getElementById('find-status').textContent") == "3 处匹配")

        self.js("""(() => {
            const root = document.querySelector('.ProseMirror');
            const range = document.createRange(); range.selectNodeContents(root.querySelector('p'));
            const selection = window.getSelection();
            selection.removeAllRanges(); selection.addRange(range);
            root.focus();
            document.execCommand('insertText', false, 'hit');
            return true;
        })()""")
        self.wait(lambda: self.editor.latest_markdown.strip() == "hit")
        self.wait(lambda: self.js("document.getElementById('find-status').textContent") == "1 处匹配")

        self.js("""(() => {
            const input = document.getElementById('find');
            input.value = '';
            input.dispatchEvent(new Event('input', {bubbles: true}));
            return true;
        })()""")
        self.assertEqual(self.js("document.getElementById('find-status').textContent"), "")


if __name__ == "__main__":
    unittest.main()
