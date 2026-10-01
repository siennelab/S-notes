"""Real bundled Milkdown → MainWindow → synthetic SQLite save boundaries."""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from src.snotes.bootstrap import build_application
from src.snotes.reminder_authority import LOCAL_REMINDER_AUTHORITY
from src.snotes.storage.repositories.tags import SQLiteTagsRepository


class MarkdownEditorRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.windows = []

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='snotes-markdown-ui-')
        self.root = Path(self.temp.name)
        _, self.window = build_application([], data_root=self.root, reminder_authority=LOCAL_REMINDER_AUTHORITY)
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

    def test_compact_toolbar_and_on_demand_panels(self):
        def click(key):
            selector = '[data-command="' + key + '"]'
            self.js('document.querySelector(' + json.dumps(selector) + ').click()')
            QTest.qWait(40)

        def select(selector, start=0, end=None):
            self.js('''(() => {
                const e = document.querySelector(%s); e.closest('.ProseMirror').focus();
                const text = e.firstChild, range = document.createRange();
                range.setStart(text, %d); range.setEnd(text, %d);
                const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
            })()''' % (json.dumps(selector), start, start if end is None else end))
            QTest.qWait(50)

        self.load(self.create('OpenAI and OpenAI'))
        self.assertFalse(hasattr(self.editor, 'status'))
        self.assertTrue(self.js("!document.getElementById('status')"))
        self.assertTrue(self.js("['raw-pane','link-field','find-bar'].every(id=>document.getElementById(id).hidden)"))
        self.assertTrue(self.js("document.getElementById('raw').readOnly"))
        self.assertTrue(self.js("document.querySelector('.panes > section').getBoundingClientRect().width === document.querySelector('.panes').getBoundingClientRect().width"))
        self.assertTrue(self.js("getComputedStyle(document.getElementById('toolbar')).flexWrap === 'nowrap'"))
        self.assertTrue(self.js("[...document.querySelectorAll('#toolbar button')].every(b=>b.textContent.length<=2 && b.title && b.getBoundingClientRect().height<=30)"))
        baseline = self.editor.latest_markdown
        click('Raw Markdown')
        self.assertFalse(self.js("document.getElementById('raw-pane').hidden"))
        self.assertEqual(self.js("document.getElementById('raw').value"), baseline)
        click('Raw Markdown')
        self.assertTrue(self.js("document.getElementById('raw-pane').hidden"))
        self.assertFalse(self.window.has_unsaved_changes)
        self.assertEqual(self.editor.latest_markdown, baseline)
        select('.ProseMirror p', 0, 6)
        click('Link')
        self.assertFalse(self.js("document.getElementById('link-field').hidden"))
        self.js("document.getElementById('link-url').value='https://openai.com'; document.getElementById('link-field').requestSubmit()")
        self.wait(lambda: '[OpenAI](https://openai.com)' in self.editor.latest_markdown)
        self.assertTrue(self.js("document.getElementById('link-field').hidden"))
        click('Undo')
        self.wait(lambda: 'https://openai.com' not in self.editor.latest_markdown)
        click('Redo')
        self.wait(lambda: 'https://openai.com' in self.editor.latest_markdown)
        select('.ProseMirror a', 2)
        click('Link')
        self.assertEqual(self.js("document.getElementById('link-url').value"), 'https://openai.com')
        self.js("document.getElementById('link-url').value='https://example.com'; document.getElementById('link-field').requestSubmit()")
        self.wait(lambda: '[OpenAI](https://example.com)' in self.editor.latest_markdown)
        click('Link')
        self.js("document.getElementById('link-url').dispatchEvent(new KeyboardEvent('keydown', {key:'Escape',bubbles:true}))")
        self.assertTrue(self.js("document.getElementById('link-field').hidden"))
        self.load(self.create('find me and find me'))
        click('Search')
        self.assertFalse(self.js("document.getElementById('find-bar').hidden"))
        self.js("document.getElementById('find').value='find me'; document.getElementById('replacement').value='found'; document.getElementById('find-next').click(); document.getElementById('replace').click()")
        self.wait(lambda: 'found' in self.editor.latest_markdown)
        self.js("document.getElementById('replace-all').click()")
        self.wait(lambda: self.js("document.querySelector('.ProseMirror').textContent") == 'found and found')
        click('Search')
        self.assertTrue(self.js("document.getElementById('find-bar').hidden"))
        self.js("document.dispatchEvent(new KeyboardEvent('keydown', {key:'f',metaKey:true,bubbles:true}))")
        self.assertFalse(self.js("document.getElementById('find-bar').hidden"))
        self.js("document.dispatchEvent(new KeyboardEvent('keydown', {key:'Escape',bubbles:true}))")
        self.assertTrue(self.js("document.getElementById('find-bar').hidden"))
        self.load(self.create(':size[字号状态]{size="20"}'))
        select('.ProseMirror [data-body-size]', 1)
        self.assertEqual(self.js("document.querySelector('[data-command=\"20\"]').getAttribute('aria-pressed')"), 'true')
        click('H2')
        self.assertEqual(self.js("document.querySelector('[data-command=\"H2\"]').getAttribute('aria-pressed')"), 'true')
        self.assertEqual(self.js("document.querySelector('[data-command=\"20\"]').getAttribute('aria-pressed')"), 'false')
        self.load(self.create('# 紧凑编辑器\n\n正文默认占满宽度，工具栏按需展开。\n\n- 列表样例\n- **重点**与链接'))
        self.wait(lambda: self.js("document.querySelector('.ProseMirror').textContent.includes('紧凑编辑器')"))
        QTest.qWait(100)
        self.window.grab().save('/tmp/snotes-p3-ui-default.png')
        click('Raw Markdown')
        self.window.grab().save('/tmp/snotes-p3-ui-raw.png')
        click('Raw Markdown')

    def test_serialization_preserves_literal_bullets_and_space_paragraphs(self):
        from src.snotes.domain.markdown import markdown_to_plain_text
        body = 'before\n\n&#32;&#32;\n\n\\- literal&#32;\n\n&#32;&#32;indented'
        note = self.create(body)
        self.load(note)
        first = self.editor.latest_markdown
        expected = markdown_to_plain_text(body)
        self.assertEqual(markdown_to_plain_text(first), expected)
        second = self.create(first)
        self.load(second)
        self.assertEqual(self.editor.latest_markdown, first)

    def test_normalized_paragraph_heading_targets_only_selected_blocks(self):
        from src.snotes.migration.legacy_qt import legacy_qt_html_to_markdown
        body = legacy_qt_html_to_markdown(
            '<p>A line 1<br>A line 2<br><br>B line<br><br>C line 1<br>C line 2</p>'
        ).markdown

        def select(start_block, end_block, cursor=False):
            self.js('''(() => {
                const root=document.querySelector('.ProseMirror'); root.focus();
                const range=document.createRange();
                range.setStart(root.children[%d].firstChild, 1);
                range.setEnd(root.children[%d].firstChild, %d);
                const s=window.getSelection();s.removeAllRanges();s.addRange(range);
            })()''' % (start_block, end_block, 1 if cursor else 4))
            QTest.qWait(50)

        def blocks():
            return json.loads(self.js("JSON.stringify(document.querySelector('.ProseMirror').pmViewDesc.node.toJSON().content)"))

        for level in (1, 2, 3):
            for cursor in (True, False):
                with self.subTest(level=level, cursor=cursor):
                    self.load(self.create(body))
                    before = blocks()
                    self.assertEqual([b['type'] for b in before], ['paragraph'] * 3)
                    self.assertEqual(sum(n['type'] == 'hardbreak' for b in before for n in b['content']), 2)
                    select(1, 1, cursor)
                    self.js('document.querySelector(\'[data-command="H%d"]\').click()' % level)
                    after = blocks()
                    self.assertEqual(after[0], before[0])
                    self.assertEqual(after[2], before[2])
                    self.assertEqual(after[1]['type'], 'heading')
                    self.assertEqual(after[1]['attrs']['level'], level)
                    self.assertEqual(after[1]['content'], before[1]['content'])
        self.load(self.create(body))
        before = blocks()
        select(1, 2)
        self.js('document.querySelector(\'[data-command="H2"]\').click()')
        after = blocks()
        self.assertEqual(after[0], before[0])
        self.assertEqual([b['type'] for b in after], ['paragraph', 'heading', 'heading'])
        self.assertEqual([b['content'] for b in after], [b['content'] for b in before])

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

    def test_tag_write_failure_keeps_ui_revision_and_retry_does_not_conflict(self):
        note = self.create('before body')
        note = self.service.save_note(
            note.id,
            title=note.title,
            content_markdown=note.content_markdown,
            location=note.location,
            expected_revision=note.revision,
            tags_text='before, keep',
        )
        self.load(note)
        self.insert('local edit')
        self.window.shell.tags_input.setText('after, keep')
        self.window.save_timer.stop()
        original_replace = SQLiteTagsRepository.replace_for_note_in_connection

        def fail_after_replacing(connection, note_id, tags, timestamp):
            original_replace(connection, note_id, tags, timestamp)
            raise RuntimeError('synthetic tag write failure')

        with patch.object(
            SQLiteTagsRepository,
            'replace_for_note_in_connection',
            side_effect=fail_after_replacing,
        ):
            self.assertFalse(self.window.force_save_current_note())

        failed = self.service.get_note(note.id)
        self.assertEqual(failed, note)
        self.assertEqual(self.window.current_note.revision, note.revision)
        self.assertFalse(self.window.conflict_active)
        self.window.save_timer.stop()

        self.assertTrue(self.window.force_save_current_note())
        saved = self.service.get_note(note.id)
        self.assertIn('local edit', saved.content_markdown)
        self.assertEqual(saved.tags, 'after, keep')
        self.assertEqual(saved.revision, note.revision + 2)
        self.assertFalse(self.window.conflict_active)

    def test_loading_prohibits_save_and_pending_note_commits_atomically(self):
        first = self.create('# First\n\nfirst body')
        self.service.rename_note(first.id, 'First title')
        self.load(self.service.get_note(first.id))
        self.assertEqual(self.window.title_input.text(), 'First title')
        note = self.create('# Title\n\nbody')
        self.service.rename_note(note.id, 'Second title')
        note = self.service.get_note(note.id)
        # Hold loadComplete on the Python side so the pending phase can be observed.
        held, original_loaded = [], self.editor._loaded
        self.editor._loaded = lambda *args: held.append(args)
        self.window.load_note(note.id)
        # Pending: the committed view is still the first note, fully consistent and locked.
        self.assertEqual(self.window.current_note.id, first.id)
        self.assertEqual(self.window.title_input.text(), 'First title')
        self.assertTrue(self.window.title_input.isReadOnly())
        self.assertFalse(self.editor.can_save)
        self.window.title_input.setText('typed while pending')  # read-only for users; programmatic
        self.assertTrue(self.window.force_save_current_note())  # nothing dirty; no write
        self.assertEqual(self.service.get_note(first.id).title, 'First title')
        self.assertEqual(self.service.get_note(note.id).revision, note.revision)
        self.wait(lambda: bool(held))
        self.assertEqual(self.window.title_input.text(), 'typed while pending')
        self.assertEqual(self.window.current_note.id, first.id)
        self.editor._loaded = original_loaded
        original_loaded(*held[0])
        self.wait(lambda: self.editor.can_save)
        # Committed together: title, body, save target.
        self.assertEqual(self.window.current_note.id, note.id)
        self.assertEqual(self.window.title_input.text(), 'Second title')
        self.assertFalse(self.window.title_input.isReadOnly())
        self.assertIn('body', self.js("document.querySelector('.ProseMirror').textContent"))
        self.assertNotIn('first body', self.js("document.querySelector('.ProseMirror').textContent"))
        self.assertEqual(self.window._note_load, (note.id, self.editor.current_load_id))
        self.window.title_input.setText('edited after load')
        self.assertTrue(self.window.force_save_current_note())
        saved = self.service.get_note(note.id)
        self.assertEqual(saved.title, 'edited after load')
        self.assertIn('# Title', saved.content_markdown)

    def test_note_switch_reuses_one_editor_and_resets_history(self):
        a, b = self.create('BodyA'), self.create('BodyB')
        self.load(a)
        self.assertEqual(self.js("document.querySelectorAll('.ProseMirror').length"), 1)
        self.js("document.querySelector('.ProseMirror').dataset.instance = 'kept'")
        self.insert('编辑A')
        self.window.switch_note(b.id)
        self.wait(lambda: self.window.current_note.id == b.id and self.editor.can_save)
        self.assertEqual(self.js("document.querySelectorAll('.ProseMirror').length"), 1)
        self.assertEqual(self.js("document.querySelector('.ProseMirror').dataset.instance"), 'kept')
        self.assertEqual(self.js("document.querySelector('.ProseMirror').textContent"), 'BodyB')
        self.js('document.querySelector(\'[data-command="Undo"]\').click()')
        QTest.qWait(60)
        self.assertEqual(self.js("document.querySelector('.ProseMirror').textContent"), 'BodyB')
        self.assertFalse(self.window.has_unsaved_changes)
        self.assertIn('编辑A', self.service.get_note(a.id).content_markdown)
        self.assertEqual(self.service.get_note(b.id).content_markdown, 'BodyB')

    def test_first_note_skips_hidden_web_composition_barrier(self):
        note = self.create('Cold first body')
        self.wait(lambda: self.editor.ready)
        barriers = []
        self.editor.bridge.prepareClose.connect(lambda: barriers.append(True))
        self.window.switch_note(note.id)
        self.wait(lambda: self.window.current_note is not None
                  and self.window.current_note.id == note.id
                  and self.editor.can_save)
        self.assertEqual(barriers, [])
        self.assertEqual(self.editor.latest_markdown.strip(), 'Cold first body')

    def test_sidebar_mouse_switches_have_one_barrier_and_correct_save_target(self):
        notes = [self.create('Body' + name) for name in ('A', 'B', 'C', 'D')]
        notes = [self.service.set_note_pinned(n.id, i < 2) for i, n in enumerate(notes)]
        for i, n in enumerate(notes):
            self.service.rename_note(n.id, 'Title' + 'ABCD'[i])
        notes = [self.service.get_note(n.id) for n in notes]
        self.window.refresh_note_list()
        barriers = []
        self.editor.bridge.prepareClose.connect(lambda: barriers.append(True))
        loaded = []
        self.editor.loaded.connect(lambda lid, md: loaded.append((self.window.current_note.id, lid, md)))
        saves = []
        original_save = self.service.save_note

        def save(nid, **kwargs):
            self.assertEqual(self.window._note_load, (nid, self.editor.current_load_id))
            self.assertEqual(self.window.current_note.id, nid)
            expected = next(n for n in notes if n.id == nid)
            self.assertEqual(kwargs['title'], expected.title)
            self.assertIn(expected.content_markdown, kwargs['content_markdown'])
            saves.append(nid)
            return original_save(nid, **kwargs)

        self.service.save_note = save
        self.load(notes[0])
        # pinned -> unpinned -> same pinned (A/B/A), pinned -> pinned,
        # pinned -> unpinned -> unpinned -> pinned.
        for index in (2, 0, 1, 2, 3, 1):
            note = notes[index]
            previous = self.window.current_note.id
            self.insert('切换保存')
            count = len(barriers)
            listing = self.window.shell.sidebar.note_list
            item = next(listing.item(i) for i in range(listing.count())
                        if listing.item(i).data(Qt.UserRole) == note.id)
            listing.scrollToItem(item)
            pos = listing.visualItemRect(item).center()
            # A real mouse holds the button between currentItemChanged and
            # itemClicked. Immediate mouseClick used to hide overlapping flushes.
            QTest.mousePress(listing.viewport(), Qt.LeftButton, pos=pos)
            QTest.qWait(20)
            QTest.mouseRelease(listing.viewport(), Qt.LeftButton, pos=pos)
            self.wait(lambda: self.window.current_note.id == note.id and self.editor.can_save)
            self.assertEqual(len(barriers), count + 1)
            self.assertEqual(saves[-1], previous)
            self.assertEqual(loaded[-1][:2], (note.id, self.editor.current_load_id))
            self.assertEqual(self.window._note_load, (note.id, self.editor.current_load_id))
            self.assertEqual(self.window.title_input.text(), note.title)
            self.assertIn(note.content_markdown, self.js("document.querySelector('.ProseMirror').textContent"))
            self.assertIn(note.content_markdown, self.editor.latest_markdown)
        self.insert('自动保存')
        self.wait(lambda: not self.window.has_unsaved_changes)
        self.assertEqual(saves[-1], notes[1].id)
        self.insert('关闭保存')
        self.window.close()
        self.wait(lambda: not self.window.isVisible())
        self.assertEqual(saves[-1], notes[1].id)

    def test_pending_switch_retargets_to_original_and_title_body_never_mismatch(self):
        a, b = self.create('BodyA'), self.create('BodyB')
        self.service.rename_note(a.id, 'TitleA')
        self.service.rename_note(b.id, 'TitleB')
        a, b = self.service.get_note(a.id), self.service.get_note(b.id)
        self.load(a)
        barriers = []
        self.editor.bridge.prepareClose.connect(lambda: barriers.append(True))
        self.window.switch_note(b.id)
        self.window.switch_note(a.id)
        self.wait(lambda: self.window._pending_switch is None and self.editor.can_save)
        self.assertEqual(len(barriers), 1)
        self.assertEqual(self.window.current_note.id, a.id)
        self.assertEqual(self.editor.latest_markdown.strip(), 'BodyA')
        old_id = self.editor.current_load_id
        held, original_loaded = [], self.editor._loaded
        self.editor._loaded = lambda lid, md: (held.append((lid, md)) if lid == self.editor.current_load_id
                                               else original_loaded(lid, md))
        self.window.load_note(b.id)
        # Pending: title A stays committed and locked; the view is never hidden.
        self.assertTrue(self.editor.view.isVisible())
        self.assertEqual(self.window.title_input.text(), 'TitleA')
        self.assertEqual(self.window.current_note.id, a.id)
        self.assertFalse(self.editor.can_save)
        self.assertEqual(self.editor.latest_markdown, '')
        self.editor.bridge.loadComplete(old_id, 'BodyA')
        self.editor.bridge.markdownChanged('BodyA', old_id)
        self.assertEqual(self.editor.latest_markdown, '')
        self.wait(lambda: bool(held))
        self.assertEqual(held[0][0], self.editor.current_load_id)
        self.assertEqual(self.window.title_input.text(), 'TitleA')
        self.assertEqual(self.window.current_note.id, a.id)
        self.editor._loaded = original_loaded
        original_loaded(*held[0])
        self.wait(lambda: self.editor.can_save)
        self.assertEqual(self.window.title_input.text(), 'TitleB')
        self.assertEqual(self.window.current_note.id, b.id)
        self.assertEqual(self.js("document.querySelector('.ProseMirror').textContent"), 'BodyB')
        self.assertEqual(self.js("document.querySelector('.ProseMirror').contentEditable"), 'true')
        self.assertEqual(self.editor.baseline.strip(), 'BodyB')
        self.editor.bridge.loadFailed(self.editor.current_load_id, 'controlled failure')
        self.assertFalse(self.editor.can_save)
        self.assertIn('加载失败', self.window.status_label.text())
        self.window.title_input.setText('must not save')
        self.assertFalse(self.window.force_save_current_note())
        self.assertEqual(self.service.get_note(b.id).content_markdown, 'BodyB')
        self.window.load_note(b.id)
        self.wait(lambda: self.editor.can_save)
        self.assertEqual(self.window.title_input.text(), 'TitleB')

    def test_toolbar_and_panels_stay_outside_body_scroll(self):
        self.load(self.create('\n\n'.join('正文 paragraph %d' % i for i in range(100))))
        self.assertEqual(self.js("getComputedStyle(document.getElementById('toolbar')).borderBottomWidth"), '0px')
        self.assertIn('inset', self.js("getComputedStyle(document.querySelector('.panes > section')).boxShadow"))
        geometry = '''JSON.stringify((() => {
            const top = id => document.getElementById(id).getBoundingClientRect().top;
            const scroller=document.querySelector('.panes > section');
            return {toolbar:top('toolbar'),link:top('link-field'),search:top('find-bar'),
                    body:document.querySelector('.ProseMirror').getBoundingClientRect().top,
                    scroll:scroller.scrollTop,window:window.scrollY};
        })())'''
        for command in (None, 'Raw Markdown', 'Search', 'Link'):
            if command:
                self.js('document.querySelector(\'[data-command="%s"]\').click()' % command)
            before = json.loads(self.js(geometry))
            self.js("document.querySelector('.panes > section').scrollTop += 250")
            after = json.loads(self.js(geometry))
            self.assertEqual(after['toolbar'], before['toolbar'])
            self.assertEqual(after['search'], before['search'])
            self.assertEqual(after['link'], before['link'])
            self.assertEqual(after['window'], 0)
            self.assertGreater(after['scroll'], before['scroll'])
            self.assertLess(after['body'], before['body'])
        self.assertTrue(self.js("document.getElementById('raw').readOnly"))

    def test_chinese_ime_commit_is_flushed_to_original_note_on_switch(self):
        a, b = self.create('Original A'), self.create('Original B')
        self.load(a)
        self.editor.view.setFocus()
        self.js("document.querySelector('.ProseMirror').focus()")
        self.wait(lambda: self.js("document.querySelector('.ProseMirror').contentEditable") == 'true')
        QTest.qWait(50)  # Let Qt deliver the native input-method focus update.
        target = self.editor.view.focusProxy()
        self.assertIsNotNone(target)
        QApplication.sendEvent(target, QInputMethodEvent('中文输入', []))
        commit = QInputMethodEvent()
        commit.setCommitString('中文输入')
        QApplication.sendEvent(target, commit)
        self.window.switch_note(b.id)
        self.wait(lambda: self.window.current_note.id == b.id and self.editor.can_save)
        self.assertIn('中文输入', self.service.get_note(a.id).content_markdown)
        self.assertEqual(self.service.get_note(b.id).content_markdown, 'Original B')
        self.assertNotIn('中文输入', self.editor.latest_markdown)

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
            build_application([], data_root=self.root, reminder_authority=LOCAL_REMINDER_AUTHORITY)
        self.assertEqual(path.read_bytes(), before)
