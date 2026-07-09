from __future__ import annotations

import html
import re

from PySide6.QtCore import QMimeData
from PySide6.QtGui import (
    QColor,
    QFont,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
    QTextListFormat,
)
from PySide6.QtWidgets import QTextEdit


CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
HTML_MARKER_RE = re.compile(r"</?(html|body|p|div|br|ul|ol|li|h[1-6]|blockquote|pre|span)\b", re.I)
ALLOWED_HTML_TAGS = (
    "p",
    "br",
    "b",
    "strong",
    "i",
    "em",
    "ul",
    "ol",
    "li",
    "pre",
    "code",
    "blockquote",
    "h1",
    "h2",
    "h3",
)
UNSAFE_HTML_BLOCK_RE = re.compile(
    r"(?is)<(script|style|object|iframe|embed|link|meta)[^>]*>.*?</\1\s*>"
)
UNSAFE_HTML_SINGLE_RE = re.compile(r"(?is)<(script|style|object|iframe|embed|link|meta)[^>]*>")
HTML_ATTRIBUTE_RE = re.compile(r"""(?is)\s(?:style|class|id|on\w+|href|src|data-[\w-]+)=("[^"]*"|'[^']*'|[^\s>]+)""")
MARKDOWN_SIGNAL_RE = re.compile(
    r"(?m)^(#{1,3}\s+|[-*]\s+|\d+\.\s+|>\s+|```)|(\*\*[^*\n]+\*\*)|(`[^`\n]+`)"
)


class NotesEditor(QTextEdit):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.document().setDefaultStyleSheet(
            """
            body { color: #2C2926; font-size: 15px; line-height: 180%; }
            p { margin: 0 0 12px 0; }
            h1 { font-size: 22pt; font-weight: 700; margin: 12px 0 8px 0; }
            h2 { font-size: 18pt; font-weight: 700; margin: 10px 0 6px 0; }
            h3 { font-size: 16pt; font-weight: 700; margin: 8px 0 6px 0; }
            blockquote { margin-left: 18px; color: #8A8078; font-style: italic; }
            pre { font-family: 'SF Mono', Menlo, Monaco, monospace; font-size: 12pt; color: #5D554D; background: #EDE6DB; padding: 8px; }
            code { font-family: 'SF Mono', Menlo, Monaco, monospace; color: #5D554D; background: #EDE6DB; }
            """
        )

    def insertFromMimeData(self, source: QMimeData) -> None:
        try:
            if source.hasHtml():
                safe_html = self.sanitize_html(source.html())
                if safe_html.strip():
                    self.insertHtml(safe_html)
                    return

            text = self.sanitize_pasted_text(source.text() if source.hasText() else "")
            if text and self.looks_like_markdown(text):
                self.insertHtml(self.markdown_to_html(text))
            elif text:
                self.insertPlainText(text)
        except Exception:
            if source.hasText():
                self.insertPlainText(self.sanitize_pasted_text(source.text()))

    def paste_plain_text(self) -> None:
        clipboard = self.clipboard_text()
        self.insertPlainText(self.sanitize_pasted_text(clipboard))

    def clipboard_text(self) -> str:
        from PySide6.QtWidgets import QApplication

        return QApplication.clipboard().text()

    def set_note_content(self, content: str) -> None:
        if HTML_MARKER_RE.search(content or ""):
            self.setHtml(content)
        else:
            self.setPlainText(content or "")

    def note_content_html(self) -> str:
        return self.toHtml()

    def apply_bold(self) -> None:
        self.setFontWeight(QFont.Normal if self.fontWeight() > QFont.Normal else QFont.Bold)

    def apply_italic(self) -> None:
        self.setFontItalic(not self.fontItalic())

    def apply_strike(self) -> None:
        char_format = QTextCharFormat()
        char_format.setFontStrikeOut(not self.textCursor().charFormat().fontStrikeOut())
        self.textCursor().mergeCharFormat(char_format)
        self.mergeCurrentCharFormat(char_format)

    def clear_formatting(self) -> None:
        cursor = self.textCursor()
        if cursor.hasSelection():
            text = self.sanitize_pasted_text(cursor.selection().toPlainText())
            start = cursor.selectionStart()
            cursor.beginEditBlock()
            cursor.insertText(text, self.clean_char_format())
            end = cursor.position()
            self.normalize_format_range(start, end)
            cursor.endEditBlock()
            return

        text = self.toPlainText()
        self.setPlainText(self.sanitize_pasted_text(text))

    def apply_heading(self, level: int) -> None:
        cursor = self.textCursor()
        block_format = QTextBlockFormat()
        char_format = QTextCharFormat()
        if level == 1:
            block_format.setHeadingLevel(1)
            char_format.setFontPointSize(22)
            char_format.setFontWeight(QFont.Bold)
        elif level == 2:
            block_format.setHeadingLevel(2)
            char_format.setFontPointSize(18)
            char_format.setFontWeight(QFont.Bold)
        else:
            block_format.setHeadingLevel(0)
            char_format.setFontPointSize(14)
            char_format.setFontWeight(QFont.Normal)
        cursor.mergeBlockFormat(block_format)
        cursor.mergeCharFormat(char_format)
        self.mergeCurrentCharFormat(char_format)

    def apply_font_size(self, point_size: int) -> None:
        char_format = QTextCharFormat()
        char_format.setFontPointSize(point_size)
        self.textCursor().mergeCharFormat(char_format)
        self.mergeCurrentCharFormat(char_format)

    def apply_list(self, ordered: bool) -> None:
        cursor = self.textCursor()
        current_list = cursor.currentList()
        target_style = QTextListFormat.ListDecimal if ordered else QTextListFormat.ListDisc
        if current_list is not None and current_list.format().style() == target_style:
            self.reset_current_block()
            return
        list_format = QTextListFormat()
        list_format.setStyle(target_style)
        cursor.createList(list_format)

    def apply_quote(self) -> None:
        cursor = self.textCursor()
        current_format = cursor.blockFormat()
        if current_format.leftMargin() >= 20:
            self.reset_current_block()
            return
        block_format = QTextBlockFormat()
        block_format.setLeftMargin(22)
        block_format.setRightMargin(8)
        char_format = QTextCharFormat()
        char_format.setFontItalic(True)
        cursor.mergeBlockFormat(block_format)
        cursor.mergeCharFormat(char_format)

    def apply_code_block(self) -> None:
        cursor = self.textCursor()
        block_format = cursor.blockFormat()
        if block_format.leftMargin() >= 16 and block_format.topMargin() >= 4:
            self.reset_current_block()
            return
        block_format = QTextBlockFormat()
        block_format.setLeftMargin(18)
        block_format.setTopMargin(6)
        block_format.setBottomMargin(6)
        char_format = QTextCharFormat()
        char_format.setFontFixedPitch(True)
        char_format.setFontStyleHint(QFont.Monospace)
        char_format.setFontPointSize(13)
        cursor.mergeBlockFormat(block_format)
        cursor.mergeCharFormat(char_format)

    def reset_current_block(self) -> None:
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.setBlockFormat(self.clean_block_format())
        cursor.mergeCharFormat(self.clean_char_format())
        self.mergeCurrentCharFormat(self.clean_char_format())
        cursor.endEditBlock()

    def normalize_format_range(self, start: int, end: int) -> None:
        if end <= start:
            return
        clean_chars = self.clean_char_format()
        clean_block = self.clean_block_format()
        cursor = QTextCursor(self.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        cursor.mergeCharFormat(clean_chars)

        block = self.document().findBlock(start)
        while block.isValid() and block.position() <= end:
            block_cursor = QTextCursor(block)
            block_cursor.setBlockFormat(clean_block)
            block = block.next()

    @staticmethod
    def clean_char_format() -> QTextCharFormat:
        char_format = QTextCharFormat()
        char_format.clearProperty(QTextFormat.BackgroundBrush)
        char_format.clearProperty(QTextFormat.ForegroundBrush)
        char_format.clearProperty(QTextFormat.FontFamily)
        char_format.clearProperty(QTextFormat.FontPointSize)
        char_format.clearProperty(QTextFormat.FontWeight)
        char_format.clearProperty(QTextFormat.FontItalic)
        char_format.clearProperty(QTextFormat.FontUnderline)
        char_format.clearProperty(QTextFormat.FontStrikeOut)
        char_format.setForeground(QColor("#2C2926"))
        char_format.setFontPointSize(15)
        char_format.setFontWeight(QFont.Normal)
        char_format.setFontItalic(False)
        char_format.setFontStrikeOut(False)
        char_format.setFontFixedPitch(False)
        return char_format

    @staticmethod
    def clean_block_format() -> QTextBlockFormat:
        block_format = QTextBlockFormat()
        block_format.clearProperty(QTextFormat.BackgroundBrush)
        block_format.setHeadingLevel(0)
        block_format.setObjectIndex(-1)
        block_format.setLeftMargin(0)
        block_format.setRightMargin(0)
        block_format.setTopMargin(0)
        block_format.setBottomMargin(0)
        block_format.setIndent(0)
        return block_format

    def find_next(self, query: str) -> bool:
        if not query:
            return False
        if self.find(query):
            return True
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.Start)
        self.setTextCursor(cursor)
        return self.find(query)

    def replace_current(self, query: str, replacement: str) -> bool:
        cursor = self.textCursor()
        if not cursor.hasSelection() or cursor.selectedText() != query:
            if not self.find_next(query):
                return False
            cursor = self.textCursor()
        cursor.insertText(replacement)
        return True

    def replace_all(self, query: str, replacement: str) -> int:
        if not query:
            return 0
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.movePosition(QTextCursor.Start)
        self.setTextCursor(cursor)
        count = 0
        while self.find(query):
            self.textCursor().insertText(replacement)
            count += 1
        cursor.endEditBlock()
        return count

    @staticmethod
    def sanitize_pasted_text(text: str) -> str:
        clean_text = CONTROL_CHAR_RE.sub("", text or "")
        clean_text = clean_text.replace("\r\n", "\n").replace("\r", "\n")
        return clean_text

    @staticmethod
    def looks_like_markdown(text: str) -> bool:
        return bool(MARKDOWN_SIGNAL_RE.search(text or ""))

    @classmethod
    def sanitize_html(cls, markup: str) -> str:
        clean = cls.sanitize_pasted_text(markup or "")
        clean = UNSAFE_HTML_BLOCK_RE.sub("", clean)
        clean = UNSAFE_HTML_SINGLE_RE.sub("", clean)
        clean = re.sub(r"(?is)<!--.*?-->", "", clean)
        clean = re.sub(r"(?is)<\s*div[^>]*>", "<p>", clean)
        clean = re.sub(r"(?is)</\s*div\s*>", "</p>", clean)
        clean = re.sub(r"(?is)<\s*span[^>]*>", "", clean)
        clean = re.sub(r"(?is)</\s*span\s*>", "", clean)
        clean = HTML_ATTRIBUTE_RE.sub("", clean)

        allowed = "|".join(ALLOWED_HTML_TAGS)

        def normalize_tag(match: re.Match[str]) -> str:
            slash = "/" if match.group(1) else ""
            tag = match.group(2).lower()
            if tag == "br":
                return "<br>"
            return f"<{slash}{tag}>"

        clean = re.sub(
            rf"(?is)<\s*(/?)\s*({allowed})\b[^>]*>",
            normalize_tag,
            clean,
        )
        clean = re.sub(rf"(?is)</?(?!{allowed}\b)[a-z][^>]*>", "", clean)
        return clean

    @classmethod
    def markdown_to_html(cls, text: str) -> str:
        lines = cls.sanitize_pasted_text(text).split("\n")
        output: list[str] = []
        paragraph: list[str] = []
        list_type: str | None = None
        in_code = False
        code_lines: list[str] = []

        def flush_paragraph() -> None:
            nonlocal paragraph
            if paragraph:
                output.append(
                    "<p>"
                    + "<br>".join(cls.format_inline_markdown(line) for line in paragraph)
                    + "</p>"
                )
                paragraph = []

        def close_list() -> None:
            nonlocal list_type
            if list_type:
                output.append(f"</{list_type}>")
                list_type = None

        for raw_line in lines:
            line = raw_line.rstrip()
            if line.strip().startswith("```"):
                flush_paragraph()
                close_list()
                if in_code:
                    output.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
                    code_lines = []
                    in_code = False
                else:
                    in_code = True
                continue
            if in_code:
                code_lines.append(line)
                continue

            if not line.strip():
                flush_paragraph()
                close_list()
                continue

            heading = re.match(r"^(#{1,3})\s+(.+)$", line)
            if heading:
                flush_paragraph()
                close_list()
                level = len(heading.group(1))
                output.append(
                    f"<h{level}>{cls.format_inline_markdown(heading.group(2))}</h{level}>"
                )
                continue

            unordered = re.match(r"^\s*[-*]\s+(.+)$", line)
            ordered = re.match(r"^\s*\d+\.\s+(.+)$", line)
            if unordered or ordered:
                flush_paragraph()
                target_type = "ul" if unordered else "ol"
                if list_type != target_type:
                    close_list()
                    output.append(f"<{target_type}>")
                    list_type = target_type
                content = (unordered or ordered).group(1)
                output.append(f"<li>{cls.format_inline_markdown(content)}</li>")
                continue

            quote = re.match(r"^\s*>\s+(.+)$", line)
            if quote:
                flush_paragraph()
                close_list()
                output.append(
                    f"<blockquote>{cls.format_inline_markdown(quote.group(1))}</blockquote>"
                )
                continue

            close_list()
            paragraph.append(line)

        if in_code:
            output.append("<pre><code>" + html.escape("\n".join(code_lines)) + "</code></pre>")
        flush_paragraph()
        close_list()
        return "".join(output)

    @staticmethod
    def format_inline_markdown(text: str) -> str:
        escaped = html.escape(text)
        escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
        escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
        return escaped

    @staticmethod
    def _html_to_text(markup: str) -> str:
        text = re.sub(r"(?i)<br\s*/?>", "\n", markup or "")
        text = re.sub(r"(?i)</p\s*>", "\n", text)
        text = re.sub(r"<[^>]+>", "", text)
        return html.unescape(text)
