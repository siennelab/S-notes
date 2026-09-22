"""Engineering gates for the fixture entry, including the actual frozen App.

No database is opened. Invoke with a temporary HOME and --markdown-smoke.
DOM interactions exercise the shipped Web toolbar, not an additional bridge API.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
import traceback
from uuid import uuid4

from PySide6.QtCore import QObject, QTimer, QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtTest import QTest

from src.snotes.ui.markdown_demo import FIXTURES
from src.snotes.ui.markdown_editor import BUNDLE
from src.snotes.ui.theme import THEMES


def run_smoke(app, window):
    def execute():
        editor = window.editor
        report = []

        def passed(name):
            line = f"{name}=PASS"
            report.append(line)
            print(line, flush=True)

        def wait(predicate, timeout=12):
            deadline = time.monotonic() + timeout
            while not predicate():
                if time.monotonic() > deadline:
                    raise AssertionError(f"Timeout: state={editor.state}, error={editor.error}")
                QTest.qWait(20)

        def js(source):
            result = []
            editor.page.runJavaScript(source, lambda value: result.append(value))
            wait(lambda: bool(result))
            return result[0]

        def load(body):
            load_id = uuid4().hex
            editor.setMarkdown(body, load_id)
            assert not editor.can_save
            wait(lambda: editor.state in {'loaded', 'failed'})
            assert editor.state == 'loaded', editor.error
            assert editor.current_load_id == load_id and not editor.dirty and editor.can_save
            wait(lambda: js("document.querySelector('.ProseMirror')?.contentEditable") == 'true')
            return editor.latest_markdown

        def select_all():
            js("""(() => {const e=document.querySelector('.ProseMirror'); e.focus();
            const r=document.createRange(); r.selectNodeContents(e);
            const s=window.getSelection(); s.removeAllRanges(); s.addRange(r); return true;})()""")
            QTest.qWait(30)

        def click(label):
            js(f"[...document.querySelectorAll('#toolbar button')].find(b=>b.textContent==={json.dumps(label)}).click()")
            QTest.qWait(80)

        try:
            assert Path(os.environ['HOME']).name.startswith('snotes-md-check-'), 'Use a temporary HOME for smoke checks'
            assert BUNDLE.is_file()
            wait(lambda: editor.ready and editor.state == 'loaded')
            assert editor.page.url().scheme() == 'file'
            assert editor.profile.isOffTheRecord()
            passed('LOCAL_STATIC_BUNDLE')
            wait(lambda: editor.ready and editor.state == 'loaded')
            passed('EDITOR_READY')
            events = []
            changes = []
            editor.loaded.connect(lambda load_id, body: events.append((load_id, body)))
            editor.changed.connect(lambda body, load_id: changes.append((load_id, body)))
            load('**baseline**')
            assert len(events) == 1 and not changes
            passed('LOAD_COMPLETE')
            load('')
            assert events[-1][1].strip() == ''
            js("document.querySelector('.ProseMirror').focus(); document.execCommand('insertText',false,'空白笔记 input')")
            wait(lambda: editor.dirty)
            assert '空白笔记 input' in editor.latest_markdown
            window.save_snapshot()
            assert window.snapshots[editor.current_load_id] == editor.latest_markdown
            passed('EMPTY_NOTE_LOAD_COMPLETE')
            first = load('identical body')
            first_id = editor.current_load_id
            second = load('identical body')
            assert first == second and editor.current_load_id != first_id
            assert events[-1][0] != events[-2][0]
            passed('IDENTICAL_BODY_RELOAD')
            editor.bridge.markdownChanged('stale edit', first_id)
            editor.bridge.loadComplete(first_id, 'stale baseline')
            editor.bridge.loadFailed(first_id, 'stale failure')
            assert editor.latest_markdown == second and editor.can_save and not editor.dirty
            passed('STALE_LOAD_ID_IGNORED')
            stale_id, newest_id = uuid4().hex, uuid4().hex
            editor.setMarkdown('rapid old', stale_id)
            editor.setMarkdown('rapid current', newest_id)
            wait(lambda: editor.state == 'loaded')
            assert editor.current_load_id == newest_id and 'rapid current' in editor.latest_markdown
            assert not editor.dirty
            passed('RAPID_LOAD_SWITCH')
            load(FIXTURES['未知扩展'])
            visible = js("document.querySelector('.ProseMirror').textContent")
            for literal in (':::note', ':tada:', ':xxx[test]{foo="bar"}', ':size[非法字号]{size="99"}', ':size[未闭合'):
                assert literal in visible, (literal, visible)
            canonical = editor.latest_markdown
            assert load(canonical) == canonical
            passed('UNKNOWN_DIRECTIVE_SAFE')
            # Inject a mount failure in the real Web path, without any test API.
            before = editor.latest_markdown
            count = len(events)
            js("""window.originalAppendChild=Element.prototype.appendChild;
            Element.prototype.appendChild=function(node){
              if(node.classList?.contains('ProseMirror')) throw Error('Injected mount failure');
              return window.originalAppendChild.call(this,node);
            };""")
            editor.setMarkdown('must not replace prior document', uuid4().hex)
            wait(lambda: editor.state == 'failed')
            assert not editor.can_save and editor.latest_markdown == before and len(events) == count
            assert ':::note' in js("document.querySelector('.ProseMirror').textContent")
            js("Element.prototype.appendChild=window.originalAppendChild; delete window.originalAppendChild")
            passed('LOAD_FAILURE_SAFE')
            for size in ('15', '20'):
                load('字号 roundtrip')
                select_all()
                click(size)
                wait(lambda: f'size="{size}"' in editor.latest_markdown)
                canonical = editor.latest_markdown
                assert load(canonical) == canonical
                assert js(f"getComputedStyle(document.querySelector('[data-body-size=\"{size}\"]')).fontSize") == f'{size}px'
                passed(f'FONT_{size}_ROUNDTRIP')
            select_all()
            click('17')
            wait(lambda: ':size[' not in editor.latest_markdown)
            assert not js("document.querySelector('[data-body-size]') !== null")
            passed('BACK_TO_17_CLEANS_MARKUP')
            click('Undo')
            wait(lambda: 'size="20"' in editor.latest_markdown)
            click('Redo')
            wait(lambda: ':size[' not in editor.latest_markdown)
            passed('UNDO_REDO')
            def select_text(start, end=None):
                end = start if end is None else end
                js(f"""(() => {{ const e=document.querySelector('.ProseMirror'); e.focus();
                const text=e.querySelector('p,h1,h2,h3').firstChild;
                const range=document.createRange(); range.setStart(text,{start}); range.setEnd(text,{end});
                const selection=window.getSelection(); selection.removeAllRanges(); selection.addRange(range); }})()""")
                QTest.qWait(30)

            for heading, size, gate in [('H1', '17', 'H1_TO_17'), ('H1', '15', 'H1_TO_15'), ('H2', '20', 'H2_TO_20')]:
                load(('#' if heading == 'H1' else '##') + ' heading text')
                select_text(3)
                click(size)
                assert js("document.querySelector('.ProseMirror').firstElementChild.tagName") == 'P'
                assert not editor.latest_markdown.startswith('#')
                if size == '17':
                    assert ':size[' not in editor.latest_markdown
                else:
                    assert js(f"document.querySelector('[data-body-size=\"{size}\"]').textContent") == 'heading text'
                passed(gate)
            load('plain text')
            select_text(2)
            click('H1')
            assert editor.latest_markdown.startswith('# plain text')
            passed('17_TO_H1')
            load(':size[sized text]{size="20"}')
            select_all()
            click('H2')
            assert editor.latest_markdown.startswith('## sized text') and ':size[' not in editor.latest_markdown
            passed('20_TO_H2_CLEARS_SIZE')
            load('# heading text')
            select_text(2)
            click('20')
            assert 'size="20"' in editor.latest_markdown
            click('H1')
            assert editor.latest_markdown.startswith('# heading text') and ':size[' not in editor.latest_markdown
            passed('H1_TO_20_TO_H1_CLEARS_SIZE')
            click('H3')
            assert editor.latest_markdown.startswith('### heading text')
            passed('HEADING_DIRECT_SWITCH')
            load('abcdef')
            select_text(2, 4)
            click('15')
            assert js("document.querySelector('[data-body-size]').textContent") == 'cd'
            assert js("document.querySelector('.ProseMirror').textContent") == 'abcdef'
            passed('PARTIAL_BODY_SIZE_PRESERVED')
            load('# abcdef')
            select_text(2, 4)
            click('20')
            assert js("document.querySelector('.ProseMirror').firstElementChild.tagName") == 'P'
            assert js("document.querySelector('[data-body-size]').textContent") == 'cd'
            click('H1')
            assert ':size[' not in editor.latest_markdown
            passed('HEADING_SELECTION_SIZE_AND_BLOCK_CLEANUP')
            assert not js("[...document.querySelectorAll('#toolbar button')].some(b=>b.textContent==='正文')")
            assert js("[...document.querySelectorAll('#toolbar button')].slice(0,6).map(b=>b.textContent).join('|')") == 'H1|H2|H3|15|17|20'
            passed('BODY_BUTTON_REMOVED')
            from src.snotes.ui.markdown_list_smoke import check_list_styles
            check_list_styles(editor, load, js, click, wait, passed)
            load('find **me** and find me')
            js("document.getElementById('find').value='find me'; document.getElementById('replacement').value='found'; document.getElementById('replace-all').click()")
            wait(lambda: 'found' in editor.latest_markdown)
            assert js("document.querySelector('.ProseMirror').textContent") == 'found and found'
            passed('FIND_REPLACE')
            editor.setReadOnly(True)
            wait(lambda: js("document.querySelector('.ProseMirror').contentEditable") == 'false')
            assert not editor.can_save
            assert js("document.getElementById('replace-all').disabled")
            frozen = editor.latest_markdown
            js("document.querySelector('.ProseMirror').focus(); document.execCommand('insertText',false,'forbidden')")
            QTest.qWait(50)
            assert editor.latest_markdown == frozen
            assert js("document.querySelector('.ProseMirror').textContent") == 'found and found'
            editor.setReadOnly(False)
            wait(lambda: js("document.querySelector('.ProseMirror').contentEditable") == 'true')
            passed('READ_ONLY')
            for theme in THEMES.values():
                editor.setTheme(theme)
                wait(lambda: js("getComputedStyle(document.documentElement).getPropertyValue('--text').trim()") == theme.text)
            passed('THEMES')
            assert js("document.getElementById('raw').readOnly")
            assert js("getComputedStyle(document.querySelector('.ProseMirror')).fontSize") == '17px'
            passed('RAW_READ_ONLY')
            class UrlReceiver(QObject):
                def __init__(self):
                    super().__init__()
                    self.urls = []

                @Slot(QUrl)
                def receive(self, url):
                    self.urls.append(url.toString())

            receiver = UrlReceiver()
            QDesktopServices.setUrlHandler('https', receiver, 'receive')
            try:
                load('[external](https://example.com/package1)')
                js("document.querySelector('.ProseMirror a').click()")
                wait(lambda: bool(receiver.urls))
                assert receiver.urls[-1] == 'https://example.com/package1'
                assert editor.page.url() == editor.page.editor_url
                assert not editor.page.acceptNavigationRequest(QUrl.fromLocalFile('/private/tmp/drop.md'), QWebEnginePage.NavigationType.NavigationTypeOther, True)
                assert not editor.view.acceptDrops()
            finally:
                QDesktopServices.unsetUrlHandler('https')
            passed('EXTERNAL_NAVIGATION_AND_DROP')
            load(FIXTURES['列表与内容样式'])
            if os.environ.get('SNOTES_MD_SCREENSHOT'):
                window.grab().save(os.environ['SNOTES_MD_SCREENSHOT'])
            assert 'src.snotes.bootstrap' not in sys.modules
            for name in ('HTML_INTERMEDIATE', 'EDITOR_JSON_PERSISTED', 'RUNTIME_NODE_REQUIRED', 'RUNTIME_LOCALHOST_REQUIRED', 'FORMAL_DATABASE_TOUCHED', 'NOTE_SERVICE_BODY_CONTRACT_CHANGED'):
                report.append(f'{name}=NO')
                print(report[-1], flush=True)
            # A last edit immediately followed by the real close handshake.
            select_all()
            js("document.execCommand('insertText',false,'last edit before close')")
            closed = []
            editor.closeReady.connect(lambda: closed.append(True))
            editor.prepareClose()
            wait(lambda: bool(closed))
            assert 'last edit before close' in editor.latest_markdown
            passed('CLOSE_HANDSHAKE')
            code = 0
        except Exception:
            traceback.print_exc()
            report.append('SMOKE=FAIL')
            code = 1
        if os.environ.get('SNOTES_MD_REPORT'):
            Path(os.environ['SNOTES_MD_REPORT']).write_text('\n'.join(report)+'\n')
        window.confirmed_close = True
        window.close()
        app.exit(code)

    QTimer.singleShot(0, execute)
