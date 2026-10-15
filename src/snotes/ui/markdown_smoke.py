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
            js(f"[...document.querySelectorAll('#toolbar button')].find(b=>b.dataset.command==={json.dumps(label)}).click()")
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
            # One ProseMirror view for the whole session: loads replace the document, never the editor.
            assert js("document.querySelectorAll('.ProseMirror').length") == 1
            js("document.querySelector('.ProseMirror').dataset.smokeInstance='kept'")
            load('reuse check')
            assert js("document.querySelectorAll('.ProseMirror').length") == 1
            assert js("document.querySelector('.ProseMirror').dataset.smokeInstance") == 'kept'
            click('Undo')
            assert js("document.querySelector('.ProseMirror').textContent") == 'reuse check'
            passed('MILKDOWN_INSTANCE_REUSED')
            load(FIXTURES['未知扩展'])
            visible = js("document.querySelector('.ProseMirror').textContent")
            for literal in (':::note', ':tada:', ':xxx[test]{foo="bar"}', ':size[非法字号]{size="99"}', ':size[未闭合'):
                assert literal in visible, (literal, visible)
            canonical = editor.latest_markdown
            assert load(canonical) == canonical
            passed('UNKNOWN_DIRECTIVE_SAFE')
            # Inject a document-install failure in the real Web path, without any test API.
            count = len(events)
            js("""window.originalDom={};
            for (const name of ['insertBefore','appendChild','removeChild','replaceChild']) {
              window.originalDom[name]=Node.prototype[name];
              Node.prototype[name]=function(...args){
                if(this.classList?.contains('ProseMirror')) throw Error('Injected install failure');
                return window.originalDom[name].apply(this,args);
              };
            }""")
            editor.setMarkdown('must not replace prior document', uuid4().hex)
            wait(lambda: editor.state == 'failed')
            assert not editor.can_save and editor.latest_markdown == '' and len(events) == count
            assert editor.view.isVisible()  # The view is never hidden; the Web body is discarded and locked.
            assert js("document.getElementById('editor').textContent") == ''
            assert js("!document.querySelector('.ProseMirror') || document.querySelector('.ProseMirror').contentEditable === 'false'")
            js("for (const [name, fn] of Object.entries(window.originalDom)) Node.prototype[name]=fn; delete window.originalDom")
            passed('LOAD_FAILURE_SAFE')
            for size in ('15', '20', '24'):
                load('字号 roundtrip')
                select_all()
                click(size)
                wait(lambda: f'size="{size}"' in editor.latest_markdown)
                canonical = editor.latest_markdown
                assert load(canonical) == canonical
                assert js(f"getComputedStyle(document.querySelector('[data-body-size=\"{size}\"]')).fontSize") == f'{size}px'
                passed(f'FONT_{size}_ROUNDTRIP')
                js("""(() => { const block=document.querySelector('.ProseMirror p'); block.closest('.ProseMirror').focus();
                const range=document.createRange(); range.selectNodeContents(block); range.collapse(false);
                const selection=window.getSelection(); selection.removeAllRanges(); selection.addRange(range); })()""")
                QTest.qWait(30)
                js("document.execCommand('insertText',false,' continued')")
                wait(lambda: 'continued' in editor.latest_markdown)
                assert js(f"document.querySelector('[data-body-size=\"{size}\"]').textContent") == '字号 roundtrip continued'
                passed(f'FONT_{size}_CONTINUATION')
            select_all()
            click('17')
            wait(lambda: ':size[' not in editor.latest_markdown)
            assert not js("document.querySelector('[data-body-size]') !== null")
            passed('BACK_TO_17_CLEANS_MARKUP')
            click('Undo')
            wait(lambda: 'size="24"' in editor.latest_markdown)
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

            for heading, size, gate in [('H1', '17', 'H1_TO_17'), ('H1', '15', 'H1_TO_15'), ('H2', '20', 'H2_TO_20'), ('H3', '24', 'H3_TO_24')]:
                load('#' * int(heading[1]) + ' heading text')
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
            load(':size[sized text]{size="24"}')
            select_all()
            click('H3')
            assert editor.latest_markdown.startswith('### sized text') and ':size[' not in editor.latest_markdown
            passed('24_TO_H3_CLEARS_SIZE')
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
            assert not js("[...document.querySelectorAll('#toolbar button')].some(b=>b.dataset.command==='正文')")
            assert js("[...document.querySelectorAll('#toolbar button')].slice(0,7).map(b=>b.textContent).join('|')") == 'H1|H2|H3|15|17|20|24'
            passed('BODY_BUTTON_REMOVED')
            check_code_button(editor, load, js, click, select_all, passed)
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


def check_code_button(editor, load, js, click, select_all, passed):
    """Actual toolbar, DOM selection and final Markdown; no private editor API."""
    def select(selector, start, end=None, end_selector=None):
        end = start if end is None else end
        js(f"""(() => {{
          const root=document.querySelector('.ProseMirror'); root.focus();
          function point(selector, offset) {{
            const el=root.querySelector(selector), walker=document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
            let node; while (node=walker.nextNode()) {{
              if (offset <= node.length) return [node, offset]; offset-=node.length;
            }}
            return [el, 0];
          }}
          const r=document.createRange();
          r.setStart(...point({json.dumps(selector)}, {start}));
          r.setEnd(...point({json.dumps(end_selector or selector)}, {end}));
          const s=window.getSelection(); s.removeAllRanges(); s.addRange(r);
        }})()""")
        QTest.qWait(40)

    def exact(expected):
        assert editor.latest_markdown == expected, (editor.latest_markdown, expected)

    def toggle_case(name, source, expected, selector=None, start=0, end=None, end_selector=None):
        before = load(source)
        if selector is None:
            select_all()
        else:
            select(selector, start, end, end_selector)
        click('Code block')
        exact(expected)
        click('Undo')
        exact(before)
        passed(name + '_AND_SINGLE_UNDO')

    source = 'alpha\n\n```js\none\ntwo\n```\n\nomega\n'
    load(source)
    for size in ('15', '20', '17'):
        select_all()
        click(size)
        text = lambda s: s if size == '17' else f':size[{s}]{{size="{size}"}}'
        exact(text('alpha') + '\n\n```js\none\ntwo\n```\n\n' + text('omega') + '\n')
    for size in ('15', '20', '17'):
        select('pre code', 2)
        click(size)
        exact(source)
    passed('CODE_SKIPPED_BY_SIZE_15_20_17_AND_CARET_NOOP')

    toggle_case('TEXT_SELECTION', '**alpha**\n\n## beta', '```\nalpha\nbeta\n```\n', 'p', 2, 2, 'h2')
    toggle_case('CODE_SELECTION', '```js\none\ntwo\n```', 'one\n\ntwo\n')
    toggle_case('PARTIAL_CODE', '```js\none\ntwo\nthree\n```', '```js\none\n```\n\ntwo\n\n```js\nthree\n```\n', 'pre code', 5, 6)
    toggle_case('UNSELECTED_BLANK_CODE_LINES', '```js\n\ntwo\n\n```', '```js\n```\n\ntwo\n\n```js\n```\n', 'pre code', 2, 3)
    toggle_case('CODE_LINE_BOUNDARY', '```js\none\ntwo\nthree\n```', 'one\n\n```js\ntwo\nthree\n```\n', 'pre code', 1, 4)
    toggle_case('MIXED_SELECTION', '**alpha**\n\n```js\none\ntwo\n```\n\n## omega', '**alpha**\n\none\n\ntwo\n\n## omega\n')
    toggle_case('MULTIPLE_CODE', '```js\none\n```\n\nnormal\n\n```py\ntwo\n```', 'one\n\nnormal\n\ntwo\n')
    for offset, expected in [(0, '```\nabcdef\n```\n'), (3, 'abc\n\n```\ndef\n```\n'), (6, 'abcdef\n\n```\n```\n')]:
        toggle_case(f'BODY_CARET_{offset}', 'abcdef', expected, 'p', offset)
    for offset, expected in [(0, 'one\n\ntwo\n'), (2, '```js\non\n```\n\ne\n\ntwo\n'), (7, '```js\none\ntwo\n```\n\n')]:
        toggle_case(f'CODE_CARET_{offset}', '```js\none\ntwo\n```', expected, 'pre code', offset)
    toggle_case('HEADING_CARET', '## abcdef', '## abc\n\n```\ndef\n```\n', 'h2', 3)
    toggle_case('EMPTY_BODY', '', '```\n```\n', 'p')
    toggle_case('EMPTY_CODE', '```\n```', '', 'pre code')

    # Each consecutive click is its own history event, and the new right block receives typing.
    load('tail')
    select('p', 4)
    click('Code block')
    first = editor.latest_markdown
    click('Code block')
    second = editor.latest_markdown
    click('Undo')
    exact(first)
    click('Redo')
    exact(second)
    js("document.execCommand('insertText',false,'outside')")
    QTest.qWait(80)
    exact('tail\n\noutside\n')
    passed('CONSECUTIVE_BUTTON_UNDO_AND_RIGHT_CARET')

    load('```js\none\n```')
    select('pre code', 3)
    js("document.querySelector('.ProseMirror').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true,cancelable:true}))")
    QTest.qWait(80)
    exact('```js\none\n\n```\n')
    passed('CODE_ENTER_REMAINS_NEWLINE')
    for source in ('- one\n- two', '> one\n\noutside', '> one\n\n> > two'):
        before = load(source)
        select_all()
        click('Code block')
        exact(before)
    passed('CROSS_CONTAINER_NOOP')
