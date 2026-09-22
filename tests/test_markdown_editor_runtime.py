"""Real bundled Milkdown → MainWindow → synthetic SQLite save boundaries."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
import tempfile
import time
import unittest
from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from src.snotes.bootstrap import build_application


class MarkdownEditorRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.windows = []

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='snotes-markdown-ui-')
        self.root = Path(self.temp.name)
        _, self.window = build_application([], data_root=self.root)
        self.windows.append(self.window)
        self.window.show()
        self.editor = self.window.body_edit
        self.service = self.window.note_service
        self.assertEqual(self.editor._theme.name, self.window.current_theme_name)

    def wait(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() > deadline:
                self.fail(f'Timeout: {self.editor.state}: {self.editor.error}')
            QTest.qWait(20)

    def js(self, code):
        result = []
        self.editor.page.runJavaScript(code, lambda value: result.append(value))
        self.wait(lambda: bool(result))
        return result[0]

    def create(self, body=''):
        note = self.service.create_note()
        if body:
            note = self.service.save_note(note.id, title='fixture', content_markdown=body, location=note.location, expected_revision=note.revision)
        return note

    def load(self, note):
        self.window.load_note(note.id)
        self.wait(lambda: self.editor.can_save)

    def insert(self, text):
        self.js('document.querySelector(".ProseMirror").focus(); document.execCommand("insertText", false, ' + json.dumps(text) + ')')
        self.wait(lambda: text in self.editor.latest_markdown)

    def tearDown(self):
        self.window.has_unsaved_changes = False
        self.window.conflict_active = False
        self.window.close()
        self.wait(lambda: not self.window.isVisible())
        self.temp.cleanup()

    @classmethod
    def tearDownClass(cls):
        for window in cls.windows:
            window.body_edit.page.deleteLater()
        QTimer.singleShot(0, cls.app.quit)
        cls.app.exec()
        cls.windows.clear()

    def test_load_baseline_identical_empty_stale_failed_and_autosave(self):
        one = self.create('**body**')
        two = self.create('**body**')
        self.load(one)
        first_id = self.editor.current_load_id
        self.assertFalse(self.window.has_unsaved_changes)
        self.assertEqual(self.service.get_note(one.id).revision, one.revision)
        baseline = self.editor.baseline
        self.editor.bridge.markdownChanged('temporary edit', first_id)
        self.editor.bridge.markdownChanged(baseline, first_id)
        self.assertFalse(self.window.has_unsaved_changes)
        self.assertFalse(self.window.save_timer.isActive())
        self.window.switch_note(two.id)
        self.wait(lambda: self.window.current_note.id == two.id and self.editor.can_save)
        self.editor.bridge.markdownChanged('stale', first_id)
        self.editor.bridge.loadFailed(first_id, 'stale failure')
        self.assertNotEqual(self.editor.latest_markdown, 'stale')
        self.assertFalse(self.window.has_unsaved_changes)
        self.assertEqual(self.service.get_note(two.id).revision, two.revision)
        empty = self.create()
        self.load(empty)
        self.insert('中文 autosave')
        self.wait(lambda: self.service.get_note(empty.id).revision > empty.revision)
        self.assertIn('中文 autosave', self.service.get_note(empty.id).content_markdown)
        saved = self.service.get_note(empty.id)
        self.editor.bridge.loadFailed(self.editor.current_load_id, 'synthetic load failure')
        self.window.title_input.setText('must not save')
        self.assertFalse(self.window.force_save_current_note())
        self.assertEqual(self.service.get_note(empty.id), saved)
        self.window.load_note(empty.id)
        self.wait(lambda: self.editor.can_save)

    def test_switch_flush_exit_save_and_revision_conflict(self):
        first, second = self.create(), self.create()
        self.load(first)
        self.insert('switch save')
        self.window.switch_note(second.id)
        self.wait(lambda: self.window.current_note.id == second.id and self.editor.can_save)
        self.assertIn('switch save', self.service.get_note(first.id).content_markdown)
        self.insert('local unsaved')
        remote = self.service.save_note(second.id, title='remote', content_markdown='remote Markdown', location=second.location, expected_revision=second.revision)
        self.assertFalse(self.window.force_save_current_note())
        self.assertTrue(self.window.conflict_active)
        self.assertEqual(self.service.get_note(second.id), remote)
        # A failed close must leave the editor usable and reloadable.
        self.window.close()
        self.wait(lambda: not self.window._close_pending)
        self.assertTrue(self.window.isVisible())
        self.window.reload_after_conflict()
        self.wait(lambda: self.editor.can_save)
        self.insert('exit save')
        self.window.close()
        self.wait(lambda: not self.window.isVisible())
        self.assertIn('exit save', self.service.get_note(second.id).content_markdown)

    def test_loading_prohibits_save_and_metadata_survives(self):
        note = self.create('# Title\n\nbody')
        self.window.load_note(note.id)
        self.window.title_input.setText('edited during load')
        self.assertFalse(self.window.force_save_current_note())
        self.assertEqual(self.service.get_note(note.id).revision, note.revision)
        self.wait(lambda: self.editor.can_save)
        self.assertTrue(self.window.force_save_current_note())
        saved = self.service.get_note(note.id)
        self.assertEqual(saved.title, 'edited during load')
        self.assertIn('# Title', saved.content_markdown)

    def test_close_cancels_pending_switch_and_saves_current_note(self):
        first, second = self.create(), self.create()
        self.load(first)
        self.insert('last edit before rapid close')
        self.window.switch_note(second.id)
        self.window.close()
        self.wait(lambda: not self.window.isVisible())
        self.assertIsNone(self.window._pending_switch)
        self.assertEqual(self.window.current_note.id, first.id)
        self.assertIn('last edit before rapid close', self.service.get_note(first.id).content_markdown)

    def test_desktop_refuses_unmarked_database_before_writes(self):
        path = self.root / 's_notes.db'
        with sqlite3.connect(path) as connection:
            connection.execute("DELETE FROM schema_meta WHERE meta_key='body_format'")
        before = path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, 'Markdown migration'):
            build_application([], data_root=self.root)
        self.assertEqual(path.read_bytes(), before)
