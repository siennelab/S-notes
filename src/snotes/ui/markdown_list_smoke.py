"""List/content-style gates driven only through the shipped Web UI."""
import re


def check_list_styles(editor, load, js, click, wait, passed):
    def caret(end=False):
        js("""(() => {
          const block=document.querySelector('.ProseMirror p,.ProseMirror h1,.ProseMirror h2,.ProseMirror h3');
          block.closest('.ProseMirror').focus();
          const range=document.createRange(); range.selectNodeContents(block);
          range.collapse(%s);
          const selection=window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
        })()""" % ('false' if end else 'true'))
        # Let ProseMirror's normal DOM selection observer see the selection.
        from PySide6.QtTest import QTest
        QTest.qWait(40)

    def has(selector):
        return js(f"Boolean(document.querySelector('.ProseMirror {selector}'))")

    def marker():
        return js("getComputedStyle(document.querySelector('.ProseMirror .label-wrapper')).fontSize")

    def stable():
        canonical = editor.latest_markdown
        assert load(canonical) == canonical
        return canonical

    for heading, kind, tag, px in [('H1', 'Ordered list', 'ol', '32px'), ('H2', 'Ordered list', 'ol', '26px'), ('H3', 'Bullet list', 'ul', '20px')]:
        level = int(heading[1])
        load('#' * level + ' Heading')
        caret()
        click(kind)
        assert has(f'{tag} h{level}') and marker() == px
        raw = stable()
        assert re.search(r'(?m)^\s*(?:\d+\.|[*+-])\s+(?:\n\s*)?#{1,3} Heading', raw), raw
        assert has(f'{tag} h{level}') and marker() == px
        passed(('ORDERED_' if tag == 'ol' else 'BULLET_') + heading)

    for kind, tag, gate in [('Ordered list', 'ol', 'ORDERED'), ('Bullet list', 'ul', 'BULLET')]:
        for size in ('15', '17', '20', '24'):
            body = 'Sized item' if size == '20' else f':size[Sized item]{{size="{size}"}}'
            load(body)
            caret()
            click(kind)
            assert has(f'{tag} p') and marker() == f'{size}px'
            raw = stable()
            assert marker() == f'{size}px'
            assert (':size[' not in raw) if size == '20' else (f'size="{size}"' in raw)
            passed(f'{gate}_{size}')

    # Existing list -> heading, then the previously approved direct size cycle.
    load('1. Original')
    caret()
    click('H1')
    assert has('ol h1') and marker() == '32px'
    click('20')
    assert has('ol p') and not has('h1') and marker() == '20px'
    assert ':size[' not in editor.latest_markdown
    passed('H1_LIST_TO_20_LIST')
    click('H2')
    assert has('ol h2') and marker() == '26px' and ':size[' not in editor.latest_markdown
    passed('20_LIST_TO_H2_LIST')
    click('17')
    assert has('ol p [data-body-size="17"]') and marker() == '17px' and 'size="17"' in editor.latest_markdown
    passed('H2_LIST_TO_17_LIST')

    for kind, prefix in [('Ordered list', '1.'), ('Bullet list', '-')]:
        load(f'{prefix} ## Heading')
        caret()
        click(kind)
        assert has('h2') and not has('ol') and not has('ul')
        assert editor.latest_markdown.strip() == '## Heading'
        load(f'{prefix} :size[Sized item]{{size="20"}}')
        caret()
        click(kind)
        assert has('p [data-body-size="20"]') and not has('ol') and not has('ul')
        assert editor.latest_markdown.strip() == ':size[Sized item]{size="20"}'
    passed('REMOVE_LIST_PRESERVES_HEADING')
    passed('REMOVE_LIST_PRESERVES_SIZE')

    for prefix in ('1.', '-'):
        load(f'{prefix} ordinary :size[large]{{size="24"}} word')
        assert marker() == '20px'
        load(f'{prefix} :size[small]{{size="15"}} :size[large]{{size="20"}}')
        assert marker() == '20px'
        # Uniform size across bold/italic spans still counts as one body size.
        load(f'{prefix} :size[**all** *large*]{{size="20"}}')
        assert marker() == '20px'
    passed('MARKER_FOLLOWS_BLOCK_STYLE')
    passed('LOCAL_INLINE_SIZE_DOES_NOT_RESIZE_MARKER')
    passed('RAW_MARKDOWN_STANDARD_LIST_HEADING')
    passed('NO_NEW_MARKDOWN_SYNTAX')

    # Adjacent item numbering and nested marker independence.
    load('1. ## Heading\n2. Next\n\n   - :size[Nested]{size="15"}')
    assert js("[...document.querySelectorAll('.label-wrapper')].map(e=>getComputedStyle(e).fontSize).join(',')") == '26px,20px,15px'
    caret()
    click('Bullet list')
    assert has('ul h2') and not has('ol')
    click('Ordered list')
    assert has('ol h2')
    assert js("document.querySelector('.label-wrapper').textContent.trim()") == '1.'
    stable()
    passed('LIST_KIND_SWITCH_AND_NESTED_MARKERS')

    load('## First\n\nSecond')
    js("""(() => { const e=document.querySelector('.ProseMirror'); e.focus();
      const r=document.createRange(); r.selectNodeContents(e);
      const s=window.getSelection(); s.removeAllRanges(); s.addRange(r); })()""")
    from PySide6.QtTest import QTest
    QTest.qWait(40)
    click('Ordered list')
    assert js("document.querySelectorAll('.ProseMirror ol > .milkdown-list-item-block').length") == 2
    assert has('ol h2') and has('ol p')
    click('Ordered list')
    assert has('h2') and has('p') and not has('ol')
    passed('MULTI_BLOCK_LIST_TOGGLE')

    load('1. ## Heading')
    caret(end=True)
    js("document.querySelector('.ProseMirror').dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',keyCode:13,bubbles:true,cancelable:true}))")
    wait(lambda: js("document.querySelectorAll('.ProseMirror ol > .milkdown-list-item-block').length") == 2)
    assert has('ol h2') and has('ol p')
    stable()
    passed('HEADING_LIST_ENTER')
