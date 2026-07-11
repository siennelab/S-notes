from __future__ import annotations

import html
import re

from PySide6.QtCore import QMimeData
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPen,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextFormat,
    QTextListFormat,
)
from PySide6.QtWidgets import QTextEdit

from src.theme import EDITOR_TEXT, SIDEBAR_TEXT, SUBTLE_BACKGROUND, TOOLBAR_ICON


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
QUOTE_BLOCK_PROPERTY = QTextFormat.UserProperty + 1
CODE_BLOCK_PROPERTY = QTextFormat.UserProperty + 2
ZERO_WIDTH_BLANK_CHARACTERS = frozenset("\u200b\u200c\u200d\u2060\ufeff")


class NotesEditor(QTextEdit):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.document().setDefaultStyleSheet(
            f"""
            body {{ color: {EDITOR_TEXT}; font-size: 15px; line-height: 180%; }}
            p {{ margin: 0 0 12px 0; }}
            h1 {{ font-size: 22pt; font-weight: 700; margin: 12px 0 8px 0; }}
            h2 {{ font-size: 18pt; font-weight: 700; margin: 10px 0 6px 0; }}
            h3 {{ font-size: 16pt; font-weight: 700; margin: 8px 0 6px 0; }}
            blockquote {{ margin-left: 18px; color: {SIDEBAR_TEXT}; font-style: italic; }}
            pre {{ font-family: 'SF Mono', Menlo, Monaco, monospace; font-size: 12pt; color: {TOOLBAR_ICON}; background: {SUBTLE_BACKGROUND}; padding: 8px; }}
            code {{ font-family: 'SF Mono', Menlo, Monaco, monospace; color: {TOOLBAR_ICON}; background: {SUBTLE_BACKGROUND}; }}
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
            self._restore_quote_properties()
            self._restore_code_block_properties()
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

    def compress_blank_paragraphs(self, keep_one: bool) -> int:
        if self.isReadOnly() or not self.isEnabled():
            return 0

        first_block, last_block = self._selected_block_range()
        blank_runs = self._blank_block_runs(first_block, last_block)
        positions_to_remove = [
            block.position()
            for run in blank_runs
            for block in (run[1:] if keep_one else run)
        ]
        if (
            not keep_one
            and self.document().blockCount() == 1
            and not self.document().begin().text()
        ):
            positions_to_remove = []
        if not positions_to_remove:
            self.setFocus()
            return 0

        preserved_cursor = QTextCursor(self.textCursor())
        vertical_scroll = self.verticalScrollBar().value()
        edit_cursor = QTextCursor(self.document())
        edit_cursor.beginEditBlock()
        try:
            for position in reversed(positions_to_remove):
                block = self.document().findBlock(position)
                if block.isValid():
                    self._remove_blank_block(block)
        finally:
            edit_cursor.endEditBlock()

        self.setTextCursor(preserved_cursor)
        self.verticalScrollBar().setValue(vertical_scroll)
        self.setFocus()
        return len(positions_to_remove)

    @staticmethod
    def _is_blank_block(block) -> bool:
        if not block.isValid():
            return False
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if (
                fragment.isValid()
                and fragment.charFormat().objectType() != QTextFormat.NoObject
            ):
                return False
            iterator += 1
        return all(
            character.isspace() or character in ZERO_WIDTH_BLANK_CHARACTERS
            for character in block.text()
        )

    def _selected_block_range(self):
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return self.document().begin(), self.document().lastBlock()
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        return (
            self.document().findBlock(start),
            self.document().findBlock(max(start, end - 1)),
        )

    def _blank_block_runs(self, first_block, last_block) -> list[list]:
        runs: list[list] = []
        current_run: list = []
        block = first_block
        last_number = last_block.blockNumber()
        while block.isValid() and block.blockNumber() <= last_number:
            if self._is_blank_block(block):
                current_run.append(block)
            elif current_run:
                runs.append(current_run)
                current_run = []
            block = block.next()
        if current_run:
            runs.append(current_run)
        return runs

    def _remove_blank_block(self, block) -> None:
        cursor = QTextCursor(self.document())
        next_block = block.next()
        if next_block.isValid():
            cursor.setPosition(block.position())
            cursor.setPosition(next_block.position(), QTextCursor.KeepAnchor)
        else:
            previous_block = block.previous()
            if previous_block.isValid():
                cursor.setPosition(previous_block.position() + previous_block.length() - 1)
                cursor.setPosition(self.document().characterCount() - 1, QTextCursor.KeepAnchor)
            else:
                cursor.setPosition(block.position())
                cursor.setPosition(block.position() + block.length() - 1, QTextCursor.KeepAnchor)
        cursor.removeSelectedText()

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
        target_style = QTextListFormat.ListDecimal if ordered else QTextListFormat.ListDisc
        cursor.beginEditBlock()
        try:
            target_cursor = self._isolate_selected_blocks(cursor)
            blocks = self._blocks_for_cursor(target_cursor)
            remove_target_list = bool(blocks) and all(
                self._block_list_style(block) == target_style for block in blocks
            )
            if remove_target_list:
                for block in blocks:
                    block_format = block.blockFormat()
                    block_format.setObjectIndex(-1)
                    QTextCursor(block).setBlockFormat(block_format)
            else:
                list_format = QTextListFormat()
                list_format.setStyle(target_style)
                target_cursor.createList(list_format)
        finally:
            cursor.endEditBlock()
        self.setTextCursor(target_cursor)

    @staticmethod
    def _block_list_style(block):
        block_list = block.textList()
        try:
            return block_list.format().style() if block_list is not None else None
        finally:
            del block_list

    def _isolate_selected_blocks(self, cursor: QTextCursor) -> QTextCursor:
        if not cursor.hasSelection():
            return QTextCursor(cursor)

        document = cursor.document()
        start = cursor.selectionStart()
        end = cursor.selectionEnd()
        end_block = document.findBlock(end)
        split_end = (
            end_block.isValid()
            and end > end_block.position()
            and end < end_block.position() + len(end_block.text())
        )
        if split_end:
            self._split_block_at(end)

        start_block = document.findBlock(start)
        split_start = (
            start_block.isValid()
            and start > start_block.position()
            and start < start_block.position() + len(start_block.text())
        )
        if split_start:
            self._split_block_at(start)

        isolated = QTextCursor(document)
        position_shift = 1 if split_start else 0
        isolated.setPosition(start + position_shift)
        isolated.setPosition(end + position_shift, QTextCursor.KeepAnchor)
        first_block, last_block = self._normalized_selected_block_range(isolated)
        isolated.setPosition(first_block.position())
        isolated.setPosition(
            last_block.position() + len(last_block.text()),
            QTextCursor.KeepAnchor,
        )
        return isolated

    def _split_block_at(self, position: int) -> None:
        split_cursor = QTextCursor(self.document())
        split_cursor.setPosition(position)
        split_cursor.insertBlock(
            split_cursor.blockFormat(),
            split_cursor.charFormat(),
        )

    @staticmethod
    def _normalized_selected_block_range(cursor: QTextCursor):
        document = cursor.document()
        first_block = document.findBlock(cursor.selectionStart())
        if not cursor.hasSelection():
            return first_block, first_block
        last_block = document.findBlock(cursor.selectionEnd() - 1)
        return first_block, last_block

    @classmethod
    def _blocks_for_cursor(cls, cursor: QTextCursor) -> list:
        first_block, last_block = cls._normalized_selected_block_range(cursor)
        block = first_block
        last_block_number = last_block.blockNumber()
        blocks = []
        while block.isValid() and block.blockNumber() <= last_block_number:
            blocks.append(block)
            block = block.next()
        return blocks

    def apply_quote(self) -> None:
        cursor = self.textCursor()
        cursor.beginEditBlock()
        try:
            target_cursor = self._isolate_selected_blocks(cursor)
            blocks = self._blocks_for_cursor(target_cursor)
            remove_quote = bool(blocks) and all(
                bool(block.blockFormat().property(QUOTE_BLOCK_PROPERTY))
                for block in blocks
            )
            for block in blocks:
                block_format = block.blockFormat()
                quote_enabled = not remove_quote
                block_format.setProperty(QUOTE_BLOCK_PROPERTY, quote_enabled)
                self._apply_quote_block_visuals(block_format, quote_enabled)
                QTextCursor(block).setBlockFormat(block_format)
        finally:
            cursor.endEditBlock()
        self.setTextCursor(target_cursor)
        self.viewport().update()

    def _apply_quote_block_visuals(
        self, block_format: QTextBlockFormat, enabled: bool
    ) -> None:
        if self._is_code_block_format(block_format):
            block_format.setLeftMargin(22 if enabled else 18)
            return
        if enabled:
            block_format.setLeftMargin(22)
            block_format.setRightMargin(8)
            block_format.setTopMargin(3)
            block_format.setBottomMargin(3)
            return
        block_format.setLeftMargin(0)
        block_format.setRightMargin(0)
        block_format.setTopMargin(0)
        block_format.setBottomMargin(0)

    def _restore_quote_properties(self) -> None:
        block = self.document().begin()
        while block.isValid():
            block_format = block.blockFormat()
            if block_format.leftMargin() >= 20:
                block_format.setProperty(QUOTE_BLOCK_PROPERTY, True)
                QTextCursor(block).setBlockFormat(block_format)
            block = block.next()
        self.document().clearUndoRedoStacks()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self.viewport())
        painter.setPen(QPen(QColor(SIDEBAR_TEXT), 3))
        document_layout = self.document().documentLayout()
        horizontal_offset = self.horizontalScrollBar().value()
        vertical_offset = self.verticalScrollBar().value()
        block = self.document().begin()
        while block.isValid():
            block_format = block.blockFormat()
            if bool(block_format.property(QUOTE_BLOCK_PROPERTY)):
                rect = document_layout.blockBoundingRect(block).translated(
                    -horizontal_offset, -vertical_offset
                )
                if rect.bottom() >= 0 and rect.top() <= self.viewport().height():
                    line_x = rect.left() + 7
                    painter.drawLine(
                        int(line_x),
                        int(rect.top() + 3),
                        int(line_x),
                        int(rect.bottom() - 3),
                    )
            block = block.next()

    def apply_code_block(self) -> None:
        cursor = self.textCursor()
        cursor.beginEditBlock()
        try:
            target_cursor = self._isolate_selected_blocks(cursor)
            blocks = self._blocks_for_cursor(target_cursor)
            remove_code_block = bool(blocks) and all(
                self._is_code_block_format(block.blockFormat()) for block in blocks
            )
            for block in blocks:
                block_format = block.blockFormat()
                code_enabled = not remove_code_block
                if code_enabled:
                    block_format.setProperty(CODE_BLOCK_PROPERTY, True)
                else:
                    block_format.clearProperty(CODE_BLOCK_PROPERTY)
                self._apply_code_block_visuals(block_format, code_enabled)
                QTextCursor(block).setBlockFormat(block_format)
        finally:
            cursor.endEditBlock()
        self.setTextCursor(target_cursor)

    @staticmethod
    def _apply_code_block_visuals(
        block_format: QTextBlockFormat, enabled: bool
    ) -> None:
        quote_enabled = bool(block_format.property(QUOTE_BLOCK_PROPERTY))
        if enabled:
            block_format.setBackground(QColor(SUBTLE_BACKGROUND))
            block_format.setLeftMargin(22 if quote_enabled else 18)
            block_format.setRightMargin(8)
            block_format.setTopMargin(6)
            block_format.setBottomMargin(6)
            return
        block_format.clearProperty(QTextFormat.BackgroundBrush)
        if quote_enabled:
            block_format.setLeftMargin(22)
            block_format.setRightMargin(8)
            block_format.setTopMargin(3)
            block_format.setBottomMargin(3)
            return
        block_format.setLeftMargin(0)
        block_format.setRightMargin(0)
        block_format.setTopMargin(0)
        block_format.setBottomMargin(0)

    @staticmethod
    def _is_code_block_format(block_format: QTextBlockFormat) -> bool:
        if bool(block_format.property(CODE_BLOCK_PROPERTY)):
            return True
        has_code_background = (
            block_format.hasProperty(QTextFormat.BackgroundBrush)
            and block_format.background().color() == QColor(SUBTLE_BACKGROUND)
        )
        has_code_geometry = (
            block_format.leftMargin() in {18, 22}
            and block_format.topMargin() == 6
            and block_format.bottomMargin() == 6
        )
        is_legacy_code_block = (
            block_format.leftMargin() == 18
            and has_code_geometry
            and not bool(block_format.property(QUOTE_BLOCK_PROPERTY))
        )
        return (has_code_background and has_code_geometry) or is_legacy_code_block

    def _restore_code_block_properties(self) -> None:
        block = self.document().begin()
        while block.isValid():
            block_format = block.blockFormat()
            if self._is_code_block_format(block_format):
                block_format.setProperty(CODE_BLOCK_PROPERTY, True)
                QTextCursor(block).setBlockFormat(block_format)
            block = block.next()
        self.document().clearUndoRedoStacks()

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
        char_format.setForeground(QColor(EDITOR_TEXT))
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
