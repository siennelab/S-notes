"""Canonicalizer foundation: fictional text, real bundled Milkdown, no Notes DB."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication

from src.snotes.ui.markdown_canonicalizer import (
    MAX_PASSES, CanonicalizationError, MarkdownCanonicalizer,
    canonicalization_changes, canonicalize_markdown,
)


# Expected bytes are the formal editor's dialect, not another serializer.
FIXTURES = (
    ("paragraph", "first\n\nsecond", "first\n\nsecond\n"),
    ("code", "```js\none\ntwo\n```", "```js\none\ntwo\n```\n"),
    ("mixed_code", "before\n\n```py\nx = 1\n```\n\nafter", "before\n\n```py\nx = 1\n```\n\nafter\n"),
    ("heading", "# Heading", "# Heading\n"),
    # The shipped text handler encodes spaces at text-node boundaries.
    ("marks", "**bold** *italic* ~~strike~~", "**bold**&#32;*italic*&#32;~~strike~~\n"),
    ("link", "[label](https://example.test)", "[label](https://example.test)\n"),
    ("unordered", "- one\n- two", "* one\n* two\n"),
    ("ordered", "1. one\n2. two", "1. one\n2. two\n"),
    ("task", "- [ ] todo\n- [x] done", "* [ ] todo\n* [x] done\n"),
    ("table", "| A | B |\n| --- | --- |\n| x | y |", "| A | B |\n| - | - |\n| x | y |\n"),
    ("escaping", r"\# literal \*star\* \[brackets\]", "\\# literal \\*star\\* \\[brackets]" + "\n"),
    ("trailing_newline", "text\n\n", "text\n"),
    ("size15", ':size[small]{size="15"}', ':size[small]{size="15"}\n'),
    ("size20", ':size[large]{size="20"}', ':size[large]{size="20"}\n'),
    ("list_heading", "- ## Heading", "* ## Heading\n"),
)


class CanonicalizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.home = tempfile.TemporaryDirectory(prefix="snotes-canonicalizer-test-")
        cls.environment = patch.dict(os.environ, {
            "HOME": cls.home.name,
            "S_NOTES_DATA_ROOT": cls.home.name,
            "QT_QPA_PLATFORM": "offscreen",
            "QTWEBENGINE_CHROMIUM_FLAGS": "--disable-gpu",
            # The runtime must work without a Node executable on PATH.
            "PATH": "/usr/bin:/bin",
        })
        cls.environment.start()
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    @classmethod
    def tearDownClass(cls):
        QTimer.singleShot(0, cls.app.quit)
        cls.app.exec()
        cls.environment.stop()
        cls.home.cleanup()

    def test_dialect_golden_outputs_and_fixed_points(self):
        with MarkdownCanonicalizer() as canonicalizer:
            for name, source, expected in FIXTURES:
                with self.subTest(fixture=name):
                    actual = canonicalizer.canonicalize_markdown(source)
                    self.assertEqual(actual, expected)
                    self.assertEqual(canonicalizer._serialize_once(actual), actual)
                    self.assertEqual(canonicalizer.canonicalize_markdown(actual), actual)

    def test_tail_empty_block_needs_more_than_one_pass(self):
        with MarkdownCanonicalizer() as canonicalizer:
            self.assertEqual(canonicalizer._serialize_once("text\n\n<br>\n"), "text\n\n")
            with patch.object(canonicalizer, "_serialize_once", wraps=canonicalizer._serialize_once) as serialize:
                result = canonicalizer.canonicalize_markdown("text\n\n<br>\n")
                self.assertEqual(result, "text\n")
                self.assertEqual(serialize.call_count, 3)
            self.assertEqual(canonicalizer.canonicalize_markdown(result), result)

    def test_baseline_detection_and_convenience_api(self):
        self.assertEqual(canonicalize_markdown("text"), "text\n")
        self.assertFalse(canonicalization_changes("text\n"))
        self.assertTrue(canonicalization_changes("text"))
        with MarkdownCanonicalizer() as canonicalizer:
            self.assertFalse(canonicalizer.canonicalization_changes("text\n"))
            self.assertTrue(canonicalizer.canonicalization_changes("text\n\n<br>\n"))

    def test_unknown_and_invalid_size_remain_literal(self):
        with MarkdownCanonicalizer() as canonicalizer:
            for source in (':size[keep]{size="17"}', ':future[keep]{mode="x"}'):
                with self.subTest(source=source):
                    result = canonicalizer.canonicalize_markdown(source)
                    self.assertEqual(canonicalizer.canonicalize_markdown(result), result)
                    rendered = []
                    canonicalizer._editor.page.runJavaScript(
                        'document.querySelector(".ProseMirror").textContent',
                        lambda text: rendered.append(text),
                    )
                    canonicalizer._wait(lambda: bool(rendered), operation="fixture render")
                    self.assertEqual(rendered[0], source)

    def test_nonconvergence_and_load_failure_are_errors(self):
        with MarkdownCanonicalizer() as canonicalizer:
            with patch.object(canonicalizer, "_serialize_once", side_effect=lambda s: "b" if s == "a" else "a") as serialize:
                with self.assertRaises(CanonicalizationError):
                    canonicalizer.canonicalize_markdown("a")
                self.assertEqual(serialize.call_count, MAX_PASSES)
                with self.assertRaises(CanonicalizationError):
                    canonicalizer.canonicalization_changes("a")
            with patch.object(canonicalizer, "_serialize_once", side_effect=RuntimeError("fictional load failure")):
                with self.assertRaises(CanonicalizationError):
                    canonicalizer.canonicalize_markdown("text")
            with patch.object(canonicalizer, "_serialize_once", return_value=None):
                with self.assertRaises(CanonicalizationError):
                    canonicalizer.canonicalize_markdown("text")

    def test_bridge_load_failure_is_not_a_successful_empty_body(self):
        with MarkdownCanonicalizer() as canonicalizer:
            editor = canonicalizer._editor
            original_load = editor.setMarkdown

            def failing_load(markdown, load_id):
                original_load(markdown, load_id)
                editor.bridge.loadFailed(load_id, "fictional parser failure")

            with patch.object(editor, "setMarkdown", side_effect=failing_load):
                with self.assertRaisesRegex(CanonicalizationError, "load failed"):
                    canonicalizer.canonicalize_markdown("text")

    def test_helper_is_invisible_and_rejects_worker_thread(self):
        with MarkdownCanonicalizer() as canonicalizer:
            self.assertTrue(canonicalizer._editor.testAttribute(Qt.WA_DontShowOnScreen))
            self.assertFalse(canonicalizer._editor.testAttribute(Qt.WA_QuitOnClose))
            errors = []

            def worker():
                try:
                    canonicalizer.canonicalize_markdown("text")
                except Exception as error:
                    errors.append(error)

            thread = threading.Thread(target=worker)
            thread.start()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], CanonicalizationError)

    def test_standalone_process_owns_qt_without_node(self):
        result = subprocess.run(
            [sys.executable, "-c", """
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from src.snotes.ui.markdown_canonicalizer import canonicalize_markdown
assert QApplication.instance() is None
assert canonicalize_markdown('text') == 'text\\n'
assert canonicalize_markdown('text\\n\\n<br>\\n') == 'text\\n'
app = QApplication.instance()
QTimer.singleShot(0, app.quit)
app.exec()
"""],
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
