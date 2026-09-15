from __future__ import annotations

import unittest
from PySide6.QtCore import QMimeData
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication
from src.snotes.ui.editor import NotesEditor


class PasteNormalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.editors = []

    def tearDown(self):
        for editor in self.editors:
            editor.close()

    def editor(self):
        editor = NotesEditor()
        self.editors.append(editor)
        return editor

    def paste(self, editor, markup, text):
        mime = QMimeData()
        mime.setHtml(markup)
        mime.setText(text)
        editor.insertFromMimeData(mime)

    def blocks(self, editor):
        result = []
        block = editor.document().begin()
        while block.isValid():
            fmt = block.blockFormat()
            result.append((block.text(), fmt.topMargin(), fmt.bottomMargin(), bool(block.textList())))
            block = block.next()
        return result

    def test_equivalent_clipboard_whitespace_keeps_rich_semantics(self):
        for plain in ('标题\n正文', '标题\n\n正文', ' 标题\n正文 ', '标\n题\n正文'):
            with self.subTest(plain=plain):
                editor = self.editor()
                self.paste(editor, '<h2>标题</h2><p><b>正文</b></p>', plain)
                self.assertEqual(editor.document().firstBlock().blockFormat().headingLevel(), 2)
                self.assertIn('font-weight:700', editor.toHtml().replace(' ', ''))

    def test_single_line_list_fragment_pastes_three_times_in_place(self):
        editor = self.editor()
        editor.setPlainText('第一行\n第二行\n第三行\n4. 其他 ¥64461,')
        editor.moveCursor(QTextCursor.End)
        before = editor.toPlainText()
        for _ in range(3):
            self.paste(editor, '<ol><li><b>到期日 2026-12-29</b></li></ol>', '到期日 2026-12-29')
        self.assertEqual(editor.toPlainText(), before + '到期日 2026-12-29' * 3)
        self.assertEqual(len(self.blocks(editor)), 4)
        self.assertFalse(any(b[3] for b in self.blocks(editor)))

    def test_clear_copy_paste_preserves_text_blank_blocks_and_gap(self):
        editor = self.editor()
        self.paste(editor, '<p><b>甲</b></p><p>乙</p>', '甲\n乙')
        before = editor.toPlainText()
        blanks = sum(not b[0] for b in self.blocks(editor))
        editor.clear_formatting()
        self.assertEqual(editor.toPlainText(), before)
        self.assertEqual(sum(not b[0] for b in self.blocks(editor)), blanks)
        editor.selectAll()
        mime = editor.createMimeDataFromSelection()
        other = self.editor()
        other.insertFromMimeData(mime)
        self.assertEqual(other.toPlainText(), before)
        self.assertEqual(self.blocks(other), self.blocks(editor))

    def test_inline_containers_preserve_target_list_and_undo(self):
        for container in ('p', 'div', 'h1', 'h2', 'h3', 'blockquote', 'ol', 'ul'):
            with self.subTest(container=container):
                editor = self.editor()
                editor.setHtml('<ol><li>甲</li><li>其他 ¥64461,</li></ol>')
                editor.moveCursor(QTextCursor.End)
                before = editor.toHtml()
                original = self.blocks(editor)
                body = '<b><i><s>到期日 2026-12-29</s></i></b>'
                if container in ('ol', 'ul'):
                    body = '<li>' + body + '</li>'
                self.paste(editor, f'<{container}>{body}</{container}>', '到期日 2026-12-29')
                self.assertEqual(len(self.blocks(editor)), len(original))
                self.assertTrue(editor.document().lastBlock().textList())
                editor.undo()
                self.assertEqual(editor.toHtml(), before)
                editor.redo()
                self.assertTrue(editor.toPlainText().endswith('到期日 2026-12-29'))

    def test_style_encoded_emphasis_and_safe_link_survive_without_source_layout(self):
        editor = self.editor()
        self.paste(editor, '<p class="web" style="margin:90px">'
                   '<span style="font-family:Papyrus;font-size:50px;color:red;'
                   'background:blue;font-weight:700;font-style:italic;text-decoration:line-through">语义</span>'
                   '<a href="https://example.com">安全</a><a href="javascript:alert(1)">危险</a></p>',
                   '语义安全危险')
        cursor = QTextCursor(editor.document())
        cursor.setPosition(0)
        cursor.setPosition(1, QTextCursor.KeepAnchor)
        fmt = cursor.charFormat()
        self.assertGreater(fmt.fontWeight(), 400)
        self.assertTrue(fmt.fontItalic())
        self.assertTrue(fmt.fontStrikeOut())
        self.assertEqual(fmt.font().pixelSize(), 15)
        self.assertNotIn('Papyrus', editor.toHtml())
        self.assertNotIn('javascript:', editor.toHtml())
        self.assertIn('https://example.com', editor.toHtml())
        self.assertEqual(self.blocks(editor)[0][1:3], (0, 0))

    def test_real_blanks_and_soft_break_survive_clear_copy_reload(self):
        editor = self.editor()
        self.paste(editor, '<p><b>甲<br>乙</b></p><p><br></p><p>丙</p>', '甲\n乙\n\n丙')
        before = self.blocks(editor)
        self.assertEqual(len(before), 3)
        self.assertEqual(sum(not b[0] for b in before), 1)
        editor.clear_formatting()
        self.assertEqual(self.blocks(editor), before)
        editor.selectAll()
        other = self.editor()
        other.insertFromMimeData(editor.createMimeDataFromSelection())
        self.assertEqual(self.blocks(other), before)
        restored = self.editor()
        restored.set_note_content(other.note_content_html())
        self.assertEqual(self.blocks(restored), before)
        editor.document().setTextWidth(400)
        other.document().setTextWidth(400)
        self.assertAlmostEqual(editor.document().size().height(), other.document().size().height(), delta=1)

    def test_whole_blocks_keep_heading_list_quote_and_code(self):
        for markup in ('<h2>标题</h2>', '<ol><li>列表</li></ol>',
                       '<blockquote>引用</blockquote>', '<pre><code>code()</code></pre>'):
            editor = self.editor()
            self.paste(editor, markup, '')
            block = editor.document().firstBlock()
            if markup.startswith('<h2>'):
                self.assertEqual(block.blockFormat().headingLevel(), 2)
            elif markup.startswith('<ol>'):
                self.assertIsNotNone(block.textList())
            elif markup.startswith('<blockquote>'):
                self.assertGreater(block.blockFormat().leftMargin(), 0)
            else:
                self.assertTrue(block.blockFormat().nonBreakableLines())

    def test_real_copy_clipboard_list_fragment_three_pastes(self):
        from PySide6.QtGui import QTextListFormat

        source = self.editor()
        fragment = '到期日 2026-12-29'
        source.setPlainText('第一项\n前缀 ' + fragment + ' 后缀\n第三项')
        source.selectAll()
        source.textCursor().createList(QTextListFormat.ListDecimal)
        selection = source.document().find(fragment)
        source.setTextCursor(selection)
        source.copy()
        self.app.processEvents()
        clipboard = QApplication.clipboard().mimeData()
        self.assertTrue(clipboard.hasHtml())
        self.assertEqual(selection.selectedText(), fragment)
        self.assertEqual(clipboard.text().lstrip("\n"), fragment)

        target = self.editor()
        target.setPlainText('第一项\n第二项\n第三项\n其他 ¥64461,')
        target.selectAll()
        target.textCursor().createList(QTextListFormat.ListDecimal)
        target.moveCursor(QTextCursor.End)
        before = target.toPlainText()
        original_list = target.textCursor().currentList()
        self.assertEqual(original_list.count(), 4)
        for repeat in range(1, 4):
            target.paste()
            self.app.processEvents()
            with self.subTest(repeat=repeat):
                self.assertEqual(target.document().blockCount(), 4)
                self.assertEqual(original_list.count(), 4)
                self.assertEqual(target.toPlainText(), before + fragment * repeat)

    def test_real_copy_clipboard_heading_vs_body_size(self):
        for name, heading, size in (('A', 1, 32), ('B', 0, 32), ('C', 0, 15)):
            with self.subTest(source=name):
                source = self.editor()
                source.setPlainText('样本文字')
                source.selectAll()
                source.apply_font_size(size)
                source.apply_bold()
                cursor = source.textCursor()
                block_format = cursor.blockFormat()
                block_format.setHeadingLevel(heading)
                cursor.setBlockFormat(block_format)
                source.copy()
                self.app.processEvents()
                self.assertTrue(QApplication.clipboard().mimeData().hasHtml())
                target = self.editor()
                target.paste()
                self.app.processEvents()
                target.selectAll()
                fmt = target.textCursor().charFormat()
                self.assertEqual(target.document().firstBlock().blockFormat().headingLevel(), heading)
                self.assertGreater(fmt.fontWeight(), 400)
                if not heading:
                    self.assertEqual(fmt.font().pixelSize(), 15)

    def test_real_copy_preserves_intentionally_selected_blank_before_list(self):
        from PySide6.QtGui import QTextListFormat

        source = self.editor()
        source.setPlainText('\n到期日 2026-12-29')
        source.moveCursor(QTextCursor.End)
        source.textCursor().createList(QTextListFormat.ListDecimal)
        source.selectAll()
        self.assertTrue(source.textCursor().selectedText().startswith('\u2029'))
        source.copy()
        target = self.editor()
        target.paste()
        self.assertEqual(target.toPlainText(), source.toPlainText())
        self.assertEqual(target.document().blockCount(), source.document().blockCount())
