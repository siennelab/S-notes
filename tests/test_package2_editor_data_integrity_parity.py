from __future__ import annotations

import os
import unittest
import unicodedata

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData
from PySide6.QtWidgets import QApplication

from src.snotes.ui.editor import NotesEditor


class EditorDataIntegrityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def paste_editor(markup: str, plain_text: str) -> NotesEditor:
        mime = QMimeData()
        mime.setHtml(markup)
        mime.setText(plain_text)
        editor = NotesEditor()
        editor.insertFromMimeData(mime)
        return editor

    def test_clear_formatting_marks_model_modified_and_serializes_stably(self) -> None:
        editor = NotesEditor()
        editor.set_note_content(
            '<p><span style="color:#ff0000; font-weight:700">'
            "虚构正文 1、</span></p>"
        )
        text_changes: list[bool] = []
        editor.textChanged.connect(lambda: text_changes.append(True))

        self.assertTrue(editor.clear_formatting())
        self.assertEqual(editor.toPlainText(), "虚构正文 1、")
        self.assertTrue(editor.document().isModified())
        self.assertTrue(text_changes)
        saved_html = editor.note_content_html()
        self.assertNotIn("#ff0000", saved_html.lower())
        self.assertNotIn("font-weight:700", saved_html.replace(" ", "").lower())

        restored = NotesEditor()
        restored.set_note_content(saved_html)
        self.assertEqual(restored.note_content_html(), saved_html)
        self.assertEqual(restored.toPlainText(), editor.toPlainText())

    def test_rich_text_paste_keeps_text_order_and_safe_inline_emphasis(self) -> None:
        expected = "标题：虚构计划\n普通中文段落\n1、甲\n2、乙\n3、丙"
        mime = QMimeData()
        mime.setText(expected)
        mime.setHtml(
            '<h2 style="color:#ff0000">标题：虚构计划</h2>'
            "<p>普通中文段落</p>"
            '<p><b style="font-weight:900">1、甲</b></p>'
            '<p><span style="color:blue">2、乙</span></p>'
            "<p>3、丙</p>"
        )
        editor = NotesEditor()
        editor.insertFromMimeData(mime)

        self.assertEqual(editor.toPlainText(), expected)
        html = editor.toHtml().replace(" ", "").lower()
        self.assertIn("font-weight:700", html)
        self.assertNotIn("#ff0000", html)
        self.assertNotIn("color:blue", html)

    def test_external_blocks_keep_semantics_without_extra_blank_lines(self) -> None:
        expected = "第一段 1、\n第二段 2、\n\n第三段 3、"
        mime = QMimeData()
        mime.setText(expected)
        mime.setHtml(
            '<div style="line-height:2">'
            "<p>第一段 1、</p>"
            '<p><b style="color:red">第二段 2、</b></p>'
            "<p><br></p>"
            "<div>第三段 3、</div>"
            "</div>"
        )
        editor = NotesEditor()
        editor.insertFromMimeData(mime)

        self.assertEqual(editor.toPlainText(), expected)
        for fragment in ("第一段 1、", "第二段 2、", "第三段 3、"):
            self.assertEqual(editor.toPlainText().count(fragment), 1)

    def test_paste_normalizes_entities_nbsp_breaks_and_unicode_equivalence(self) -> None:
        equivalent_cases = (
            ("<p><b>甲 &amp; 乙</b></p>", "甲 & 乙", "甲 & 乙"),
            ("<p><b>甲&nbsp;乙</b></p>", "甲 乙", "甲 乙"),
            ("<p><b>甲<br>乙</b></p>", "甲\n乙", "甲\n乙"),
            ("<p><b>甲</b></p>", "甲\n", "甲"),
        )
        for markup, plain_text, expected in equivalent_cases:
            with self.subTest(markup=markup):
                editor = self.paste_editor(markup, plain_text)
                self.assertEqual(editor.toPlainText(), expected)
                self.assertIn(
                    "font-weight:700",
                    editor.toHtml().replace(" ", "").lower(),
                )

        decomposed = "Cafe\u0301"
        editor = self.paste_editor(f"<p><b>{decomposed}</b></p>", "Café")
        self.assertEqual(editor.toPlainText(), decomposed)
        self.assertEqual(unicodedata.normalize("NFC", editor.toPlainText()), "Café")
        self.assertIn(
            "font-weight:700",
            editor.toHtml().replace(" ", "").lower(),
        )

    def test_paste_keeps_all_visible_text_from_incomplete_mime_representations(self) -> None:
        html_has_extra = self.paste_editor(
            "<p>虚构第一段</p><p><b>虚构第二段 2、</b></p>",
            "虚构第一段",
        )
        self.assertEqual(
            html_has_extra.toPlainText(),
            "虚构第一段\n虚构第二段 2、",
        )
        self.assertIn(
            "font-weight:700",
            html_has_extra.toHtml().replace(" ", "").lower(),
        )

        plain_has_extra = self.paste_editor(
            "<p><b>虚构第一段</b></p>",
            "虚构第一段\n虚构第二段 2、",
        )
        self.assertEqual(
            plain_has_extra.toPlainText(),
            "虚构第一段\n虚构第二段 2、",
        )

        conflicting = self.paste_editor(
            "<p>虚构共同段</p><p>虚构 HTML 独有段</p>",
            "虚构共同段\n虚构 plain 独有段",
        )
        self.assertEqual(
            conflicting.toPlainText(),
            "虚构共同段\n虚构 plain 独有段",
        )

        paragraph_boundary = self.paste_editor(
            "<p><b>虚构甲</b></p><p>虚构乙</p>",
            "虚构甲虚构乙",
        )
        self.assertEqual(paragraph_boundary.toPlainText(), "虚构甲\n虚构乙")

    def test_paste_preserves_lists_nested_divs_empty_lines_and_empty_fallbacks(self) -> None:
        list_editor = self.paste_editor(
            "<ol><li><b>虚构甲</b></li><li>虚构乙</li></ol>",
            "1. 虚构甲\n2. 虚构乙",
        )
        self.assertEqual(list_editor.toPlainText(), "虚构甲\n虚构乙")
        self.assertIsNotNone(list_editor.document().begin().textList())
        self.assertIn(
            "font-weight:700",
            list_editor.toHtml().replace(" ", "").lower(),
        )

        numbered_plain = self.paste_editor(
            "<p>虚构甲</p>",
            "1、虚构甲",
        )
        self.assertEqual(numbered_plain.toPlainText(), "1、虚构甲")

        nested = self.paste_editor(
            "<div><div>虚构第一段</div><div><br></div>"
            "<div>虚构第三段</div></div>",
            "虚构第一段\n\n虚构第三段",
        )
        self.assertEqual(nested.toPlainText(), "虚构第一段\n\n虚构第三段")

        empty_html = self.paste_editor("", "虚构纯文本保留\n\n虚构末段")
        self.assertEqual(empty_html.toPlainText(), "虚构纯文本保留\n\n虚构末段")

        empty_plain = self.paste_editor(
            "<h2>虚构标题</h2><p><b>虚构 HTML 正文</b></p>",
            "",
        )
        self.assertEqual(empty_plain.toPlainText(), "虚构标题\n虚构 HTML 正文")
        self.assertIn(
            "font-weight:700",
            empty_plain.toHtml().replace(" ", "").lower(),
        )

    def test_paste_conflicts_choose_one_complete_representation_without_duplication(
        self,
    ) -> None:
        cases = (
            (
                "reversed_order",
                "<p>第一段</p><p>第二段</p>",
                "第二段\n第一段",
                "第二段\n第一段",
            ),
            (
                "inline_replace",
                "<p>版本 1.0</p>",
                "版本1.0",
                "版本 1.0",
            ),
            (
                "cross_overlap",
                "<p>甲</p><p>乙</p><p>丙</p>",
                "乙\n丙\n丁",
                "乙\n丙\n丁",
            ),
            (
                "equal_information_conflict",
                "<p>共同段</p><p>HTML 独有段</p>",
                "共同段\nplain 独有段",
                "共同段\nplain 独有段",
            ),
            (
                "html_superset",
                "<p>甲</p><p>乙</p><p>丙</p>",
                "甲\n乙",
                "甲\n乙\n丙",
            ),
            (
                "plain_superset",
                "<p>甲</p><p>乙</p>",
                "甲\n乙\n丙",
                "甲\n乙\n丙",
            ),
        )
        for name, markup, plain_text, expected in cases:
            with self.subTest(name=name):
                editor = self.paste_editor(markup, plain_text)
                self.assertEqual(editor.toPlainText(), expected)
                self.assertEqual(editor.toPlainText().split("\n"), expected.split("\n"))
                for paragraph in expected.split("\n"):
                    if paragraph:
                        self.assertEqual(
                            editor.toPlainText().split("\n").count(paragraph),
                            expected.split("\n").count(paragraph),
                        )



if __name__ == "__main__":
    unittest.main()
