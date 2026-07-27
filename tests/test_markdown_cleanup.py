from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import QApplication

from src.editor import NotesEditor, strip_markdown_markup
from src.editor_toolbar import EditorToolbar
from src.theme import THEMES, apply_theme, build_app_style


def rendered_foreground(editor: NotesEditor, position: int) -> str | None:
    for selection in reversed(editor.extraSelections()):
        if selection.cursor.selectionStart() <= position < selection.cursor.selectionEnd():
            brush = selection.format.foreground()
            if brush.style() != Qt.NoBrush:
                return brush.color().name().upper()
    return None


class MarkdownConversionTest(unittest.TestCase):
    def test_headings_support_spaced_levels_one_through_six(self) -> None:
        source = "\n".join(
            [*("#" * level + f" Heading {level}" for level in range(1, 7)), "#标签", "#giffgaff"]
        )
        expected = "\n".join(
            [*(f"Heading {level}" for level in range(1, 7)), "#标签", "#giffgaff"]
        )
        self.assertEqual(strip_markdown_markup(source), expected)

    def test_heading_cleanup_preserves_numbering_and_ordinary_hashes(self) -> None:
        source = (
            "# 1、\n"
            "## 1.\n"
            "### 1. 外部粘贴与清除格式\n"
            "###1、\n"
            "###1、第一项\n"
            "###2、第二项\n"
            "### 1)\n"
            "#### 第一章\n"
            "正文中的 # 号保留\n"
            "#标签\n"
            "C#\n"
            "A#B\n"
            "https://example.test/page#section\n"
        )
        self.assertEqual(
            strip_markdown_markup(source),
            (
                "1、\n"
                "1.\n"
                "1. 外部粘贴与清除格式\n"
                "1、\n"
                "1、第一项\n"
                "2、第二项\n"
                "1)\n"
                "第一章\n"
                "正文中的 # 号保留\n"
                "#标签\n"
                "C#\n"
                "A#B\n"
                "https://example.test/page#section\n"
            ),
        )

    def test_fenced_code_drops_matched_fences_and_preserves_code(self) -> None:
        source = (
            "before\n"
            "```bash\n"
            "echo **literal**\n"
            "```\n"
            "~~~python\n"
            "value = '''not a fence'''\n"
            "~~~\n"
            "after\n"
        )
        self.assertEqual(
            strip_markdown_markup(source),
            "before\necho **literal**\nvalue = '''not a fence'''\nafter\n",
        )
        self.assertEqual(strip_markdown_markup("```\nunclosed\n"), "```\nunclosed\n")
        self.assertEqual(
            strip_markdown_markup(
                "```\n###1、围栏内保留\n### 1. 围栏内也保留\n```\n"
            ),
            "###1、围栏内保留\n### 1. 围栏内也保留\n",
        )

    def test_inline_formatting_is_removed_conservatively(self) -> None:
        source = "**bold** __strong__ *italic* _em_ ~~strike~~ `code`"
        self.assertEqual(
            strip_markdown_markup(source),
            "bold strong italic em strike code",
        )

    def test_lists_quotes_tasks_and_horizontal_rules(self) -> None:
        source = (
            "- first\n+ second\n* third\n"
            "1. ordered\n2) ordered too\n"
            "- [ ] todo\n- [x] done\n- [X] done too\n"
            "> quote\n---\n***\n___\n"
        )
        self.assertEqual(
            strip_markdown_markup(source),
            "first\nsecond\nthird\nordered\nordered too\ntodo\ndone\ndone too\nquote\n",
        )

    def test_links_images_and_autolinks_keep_destinations(self) -> None:
        source = (
            "[OpenAI](https://openai.com) "
            "![diagram](https://example.com/a.png) "
            "<https://example.com>"
        )
        self.assertEqual(
            strip_markdown_markup(source),
            "OpenAI (https://openai.com) diagram (https://example.com/a.png) "
            "https://example.com",
        )

    def test_plain_symbols_identifiers_paths_and_tables_are_unchanged(self) -> None:
        source = (
            "a-b snake_case foo*bar 2 * 3 /tmp/a_b\n"
            "    - indented code marker\n"
            "| name | value |\n| --- | --- |\n"
            "'''python multiline marker'''\n"
        )
        self.assertEqual(strip_markdown_markup(source), source)


class MarkdownEditorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_selection_only_is_replaced_and_outside_rich_text_is_preserved(self) -> None:
        editor = NotesEditor()
        editor.set_note_content("outside **inside** tail")
        outside_cursor = QTextCursor(editor.document())
        outside_cursor.setPosition(0)
        outside_cursor.setPosition(len("outside"), QTextCursor.KeepAnchor)
        bold = QTextCharFormat()
        bold.setFontWeight(QFont.Bold)
        outside_cursor.mergeCharFormat(bold)

        source = editor.toPlainText()
        start = source.index("**inside**")
        selection = QTextCursor(editor.document())
        selection.setPosition(start)
        selection.setPosition(start + len("**inside**"), QTextCursor.KeepAnchor)
        editor.setTextCursor(selection)

        self.assertTrue(editor.clear_markdown_markup())
        self.assertEqual(editor.toPlainText(), "outside inside tail")
        self.assertEqual(editor.textCursor().selectedText(), "inside")
        preserved = QTextCursor(editor.document())
        preserved.setPosition(1)
        self.assertGreater(preserved.charFormat().fontWeight(), QFont.Normal)

    def test_no_selection_processes_the_full_document(self) -> None:
        editor = NotesEditor()
        editor.set_note_content("# Heading\n\n- item\nplain")
        cursor = editor.textCursor()
        cursor.setPosition(4)
        editor.setTextCursor(cursor)

        self.assertTrue(editor.clear_markdown_markup())
        self.assertEqual(editor.toPlainText(), "Heading\n\nitem\nplain")
        self.assertLessEqual(editor.textCursor().position(), len(editor.toPlainText()))

    def test_conversion_is_one_undo_command(self) -> None:
        editor = NotesEditor()
        editor.set_note_content("before **bold** and [link](https://example.com)")
        before_text = editor.toPlainText()
        before_html = editor.toHtml()

        self.assertTrue(editor.clear_markdown_markup())
        self.assertNotEqual(editor.toPlainText(), before_text)
        editor.undo()

        self.assertEqual(editor.toPlainText(), before_text)
        self.assertEqual(editor.toHtml(), before_html)
        self.assertFalse(editor.document().isUndoAvailable())

    def test_plain_text_is_a_true_no_op(self) -> None:
        editor = NotesEditor()
        editor.set_note_content("plain text\nsnake_case\na-b")
        before_html = editor.toHtml()
        editor.document().setModified(False)
        text_changes = []
        editor.textChanged.connect(lambda: text_changes.append(True))

        self.assertFalse(editor.clear_markdown_markup())
        self.assertEqual(editor.toHtml(), before_html)
        self.assertFalse(editor.document().isModified())
        self.assertEqual(text_changes, [])

    def test_toolbar_button_uses_shared_three_theme_states_and_icon_rules(self) -> None:
        for theme_name in THEMES:
            apply_theme(self.app, theme_name)
            editor = NotesEditor()
            toolbar = EditorToolbar(editor)
            toolbar.refresh_theme()
            button = toolbar.clear_markdown_button

            self.assertEqual(button.toolTip(), "清除 Markdown 标记")
            self.assertEqual(button.iconSize().width(), 14)
            self.assertEqual(button.iconSize().height(), 14)
            self.assertFalse(button.icon().isNull())
            style = build_app_style(theme_name)
            self.assertIn("#toolbarButton:hover", style)
            self.assertIn("#toolbarButton:pressed", style)
            self.assertIn("#toolbarButton:focus", style)

            editor.set_note_content("**text**")
            button.click()
            self.assertEqual(editor.toPlainText(), "text")

            editor.set_note_content(
                "### 1. 外部粘贴与清除格式\n"
                "###1、第一项\n"
                "###2、第二项\n"
                "#标签\n"
                "C#\n"
                "A#B\n"
                "https://example.test/page#section"
            )
            button.click()
            self.assertEqual(
                editor.toPlainText(),
                "1. 外部粘贴与清除格式\n"
                "1、第一项\n"
                "2、第二项\n"
                "#标签\n"
                "C#\n"
                "A#B\n"
                "https://example.test/page#section",
            )
            toolbar.close()
            editor.close()

    def test_dark_legacy_note_remains_readable_without_stored_theme_color(self) -> None:
        apply_theme(self.app, "dark")
        editor = NotesEditor()
        editor.set_note_content(
            '<p><span style="color:#333333">旧正文 **mixed** 😀</span></p>'
        )
        source = editor.toPlainText()
        start = source.index("**mixed**")
        selection = QTextCursor(editor.document())
        selection.setPosition(start)
        selection.setPosition(start + len("**mixed**"), QTextCursor.KeepAnchor)
        editor.setTextCursor(selection)

        self.assertTrue(editor.clear_markdown_markup())
        self.app.processEvents()
        text = editor.toPlainText()
        position = text.index("mixed")
        self.assertEqual(
            rendered_foreground(editor, position),
            THEMES["dark"].document_text.upper(),
        )
        html = editor.toHtml().lower()
        self.assertNotIn("#333333", html)
        self.assertNotIn(THEMES["dark"].document_text.lower(), html)
        cursor = QTextCursor(editor.document())
        cursor.setPosition(position)
        self.assertFalse(cursor.charFormat().hasProperty(QTextFormat.ForegroundBrush))


if __name__ == "__main__":
    unittest.main()
