from __future__ import annotations

import html
import re
import unicodedata
from difflib import SequenceMatcher

from PySide6.QtCore import QMimeData, QSignalBlocker, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPen,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextFormat,
    QTextListFormat,
)
from PySide6.QtWidgets import QTextEdit

from src.theme import ThemeTokens, current_theme, editor_document_style


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
    "div",
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
MARKDOWN_FENCE_RE = re.compile(
    r"^[ \t]{0,3}(`{3,}|~{3,})(?:[ \t]*[A-Za-z0-9_+.-]+)?[ \t]*$"
)
MARKDOWN_HEADING_RE = re.compile(
    r"^[ \t]{0,3}#{1,6}(?:[ \t]+|(?=\d+[、.)．。]))"
)
PASTE_LIST_PREFIX_RE = re.compile(
    r"^[ \t]*(?:[-+*•◦▪‣][ \t]+|\d+[.)、．。][ \t]*)"
)
MARKDOWN_QUOTE_RE = re.compile(r"^([ \t]{0,3})>[ \t]?")
MARKDOWN_TASK_RE = re.compile(r"^([ ]{0,3})[-+*][ \t]+\[[ xX]\][ \t]+")
MARKDOWN_UNORDERED_RE = re.compile(r"^([ ]{0,3})[-+*][ \t]+")
MARKDOWN_ORDERED_RE = re.compile(r"^([ ]{0,3})\d+[.)][ \t]+")
MARKDOWN_HORIZONTAL_RULE_RE = re.compile(
    r"^[ \t]{0,3}(?:-{3,}|\*{3,}|_{3,})[ \t]*$"
)
MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]\n]*)\]\(([^)\n]+)\)")
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[([^\]\n]+)\]\(([^)\n]+)\)")
MARKDOWN_AUTOLINK_RE = re.compile(r"<(https?://[^>\s]+)>")
MARKDOWN_INLINE_CODE_RE = re.compile(r"(?<![\\`])`([^`\n]+)`(?!`)")
MARKDOWN_STRONG_STAR_RE = re.compile(
    r"(?<![\\*])\*\*(?=\S)(.+?)(?<=\S)\*\*(?!\*)"
)
MARKDOWN_STRONG_UNDERSCORE_RE = re.compile(
    r"(?<![\\\w])__(?=\S)(.+?)(?<=\S)__(?!\w)"
)
MARKDOWN_STRIKE_RE = re.compile(r"(?<![\\~])~~(?=\S)(.+?)(?<=\S)~~(?!~)")
MARKDOWN_ITALIC_STAR_RE = re.compile(
    r"(?<![\\\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])"
)
MARKDOWN_ITALIC_UNDERSCORE_RE = re.compile(
    r"(?<![\\\w])_(?=\S)([^_\n]+?)(?<=\S)_(?!\w)"
)
QUOTE_BLOCK_PROPERTY = QTextFormat.UserProperty + 1
CODE_BLOCK_PROPERTY = QTextFormat.UserProperty + 2
ZERO_WIDTH_BLANK_CHARACTERS = frozenset("\u200b\u200c\u200d\u2060\ufeff")


def _line_body_and_ending(line: str) -> tuple[str, str]:
    for ending in ("\r\n", "\n", "\r"):
        if line.endswith(ending):
            return line[: -len(ending)], ending
    return line, ""


def _is_matching_fence(line: str, marker: str) -> bool:
    body, _ = _line_body_and_ending(line)
    match = re.fullmatch(r"[ \t]{0,3}(`{3,}|~{3,})[ \t]*", body)
    return bool(
        match
        and match.group(1)[0] == marker[0]
        and len(match.group(1)) >= len(marker)
    )


def _strip_inline_markdown(text: str) -> str:
    text = MARKDOWN_IMAGE_RE.sub(r"\1 (\2)", text)
    text = MARKDOWN_LINK_RE.sub(r"\1 (\2)", text)
    text = MARKDOWN_AUTOLINK_RE.sub(r"\1", text)
    text = MARKDOWN_INLINE_CODE_RE.sub(r"\1", text)
    text = MARKDOWN_STRONG_STAR_RE.sub(r"\1", text)
    text = MARKDOWN_STRONG_UNDERSCORE_RE.sub(r"\1", text)
    text = MARKDOWN_STRIKE_RE.sub(r"\1", text)
    text = MARKDOWN_ITALIC_STAR_RE.sub(r"\1", text)
    return MARKDOWN_ITALIC_UNDERSCORE_RE.sub(r"\1", text)


def _strip_markdown_line(line: str) -> str:
    body, ending = _line_body_and_ending(line)
    if MARKDOWN_HORIZONTAL_RULE_RE.fullmatch(body):
        return ""
    body = MARKDOWN_HEADING_RE.sub("", body, count=1)
    body = MARKDOWN_QUOTE_RE.sub(r"\1", body, count=1)
    body = MARKDOWN_TASK_RE.sub(r"\1", body, count=1)
    body = MARKDOWN_UNORDERED_RE.sub(r"\1", body, count=1)
    body = MARKDOWN_ORDERED_RE.sub(r"\1", body, count=1)
    return _strip_inline_markdown(body) + ending


def strip_markdown_markup(text: str) -> str:
    """Remove conservative Markdown source markers while preserving content."""

    lines = text.splitlines(keepends=True)
    converted: list[str] = []
    index = 0
    while index < len(lines):
        body, _ = _line_body_and_ending(lines[index])
        fence_match = MARKDOWN_FENCE_RE.fullmatch(body)
        if fence_match:
            marker = fence_match.group(1)
            closing_index = index + 1
            while closing_index < len(lines) and not _is_matching_fence(
                lines[closing_index], marker
            ):
                closing_index += 1
            if closing_index < len(lines):
                converted.extend(lines[index + 1 : closing_index])
                index = closing_index + 1
                continue
            converted.extend(lines[index:])
            break
        converted.append(_strip_markdown_line(lines[index]))
        index += 1
    return "".join(converted)


def _qt_text_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _python_index_from_qt_position(text: str, position: int) -> int:
    qt_offset = 0
    for index, character in enumerate(text):
        next_offset = qt_offset + (2 if ord(character) > 0xFFFF else 1)
        if next_offset > position:
            return index
        qt_offset = next_offset
    return len(text)


def _mapped_text_offset(source: str, converted: str, source_offset: int) -> int:
    for tag, old_start, old_end, new_start, new_end in SequenceMatcher(
        None, source, converted, autojunk=False
    ).get_opcodes():
        if source_offset < old_end or (source_offset == old_end and old_end == len(source)):
            if tag == "equal":
                return new_start + min(source_offset - old_start, new_end - new_start)
            if tag == "insert":
                continue
            return new_start + min(max(source_offset - old_start, 0), new_end - new_start)
    return len(converted)


def editor_document_font() -> QFont:
    font = QFont()
    font.setFamilies(
        [
            "PingFang SC",
            "SF Pro Text",
            "-apple-system",
            "Helvetica Neue",
            "sans-serif",
        ]
    )
    font.setPixelSize(15)
    return font


def apply_editor_document_theme(
    editor: "NotesEditor", tokens: ThemeTokens | None = None
) -> None:
    """Refresh display-only document colors while preserving editor state."""

    tokens = tokens or current_theme()
    cursor = QTextCursor(editor.textCursor())
    vertical_scroll = editor.verticalScrollBar().value()
    horizontal_scroll = editor.horizontalScrollBar().value()
    document = editor.document()
    was_modified = document.isModified()
    editor_blocker = QSignalBlocker(editor)
    document_blocker = QSignalBlocker(document)
    try:
        editor._document_theme_tokens = tokens
        document.setDefaultFont(editor_document_font())
        editor.setTextCursor(cursor)
        editor._normalize_current_input_format()
        editor.verticalScrollBar().setValue(vertical_scroll)
        editor.horizontalScrollBar().setValue(horizontal_scroll)
        document.setModified(was_modified)
    finally:
        del document_blocker
        del editor_blocker

    editor._refresh_document_theme_overlays()
    editor.viewport().update()


class NotesEditor(QTextEdit):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("bodyEdit")
        self._document_reset_in_progress = False
        self.document().setDefaultFont(editor_document_font())
        self.document().setDefaultStyleSheet(editor_document_style())
        apply_editor_document_theme(self)
        self.textChanged.connect(self._refresh_document_theme_overlays)

    def apply_theme(self) -> None:
        apply_editor_document_theme(self)

    @staticmethod
    def _theme_neutral_char_format(char_format: QTextCharFormat) -> QTextCharFormat:
        neutral_format = QTextCharFormat(char_format)
        neutral_format.clearProperty(QTextFormat.ForegroundBrush)
        return neutral_format

    def _normalize_current_input_format(self) -> None:
        if self._document_reset_in_progress or self.textCursor().hasSelection():
            return
        current_format = self.currentCharFormat()
        if not current_format.hasProperty(QTextFormat.ForegroundBrush):
            return
        self.setCurrentCharFormat(self._theme_neutral_char_format(current_format))

    def insertPlainText(self, text: str) -> None:
        cursor = self.textCursor()
        cursor.insertText(text, self._theme_neutral_char_format(cursor.charFormat()))
        self.setTextCursor(cursor)

    def insertHtml(self, text: str) -> None:
        cursor = self.textCursor()
        cursor.setCharFormat(self._theme_neutral_char_format(cursor.charFormat()))
        cursor.insertHtml(text)
        self.setTextCursor(cursor)

    def keyPressEvent(self, event) -> None:
        modifiers = event.modifiers()
        command_modifiers = Qt.ControlModifier | Qt.MetaModifier | Qt.AltModifier
        if (
            event.text()
            and event.text() not in {"\r", "\n", "\t"}
            and not modifiers & command_modifiers
        ):
            self.insertPlainText(event.text())
            event.accept()
            return
        super().keyPressEvent(event)

    def inputMethodEvent(self, event) -> None:
        if event.commitString() and not self.textCursor().hasSelection():
            self._normalize_current_input_format()
        super().inputMethodEvent(event)
        self._normalize_current_input_format()

    def insertFromMimeData(self, source: QMimeData) -> None:
        try:
            if source.hasHtml():
                safe_html = self.sanitize_html(source.html())
                if safe_html.strip():
                    normalized_html, html_text = self._normalized_html_and_text(
                        safe_html
                    )
                    if source.hasText():
                        source_text = self.sanitize_pasted_text(source.text())
                        representation, content = self._select_paste_representation(
                            normalized_html, html_text, source_text
                        )
                        if representation == "plain":
                            if content:
                                self.insertPlainText(content)
                            return
                    self.insertHtml(normalized_html)
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
        self._document_reset_in_progress = True
        try:
            if HTML_MARKER_RE.search(content or ""):
                self.setHtml(content)
                self._normalize_legacy_document_colors()
                self._restore_quote_properties()
                self._restore_code_block_properties()
            else:
                self.setPlainText(content or "")
        finally:
            self._document_reset_in_progress = False
        self.document().setModified(False)
        apply_editor_document_theme(self)

    def _normalize_legacy_document_colors(self) -> None:
        document = self.document()
        was_modified = document.isModified()
        block_char_replacements: list[tuple[int, QTextCharFormat]] = []
        replacements: list[tuple[int, int, QTextCharFormat]] = []
        block = document.begin()
        while block.isValid():
            block_char_format = block.charFormat()
            if block_char_format.hasProperty(
                QTextFormat.ForegroundBrush
            ) or block_char_format.hasProperty(QTextFormat.BackgroundBrush):
                neutral_block_char_format = QTextCharFormat(block_char_format)
                neutral_block_char_format.clearProperty(QTextFormat.ForegroundBrush)
                neutral_block_char_format.clearProperty(QTextFormat.BackgroundBrush)
                block_char_replacements.append(
                    (block.position(), neutral_block_char_format)
                )
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid():
                    char_format = fragment.charFormat()
                    if char_format.hasProperty(
                        QTextFormat.ForegroundBrush
                    ) or char_format.hasProperty(QTextFormat.BackgroundBrush):
                        neutral_format = QTextCharFormat(char_format)
                        neutral_format.clearProperty(QTextFormat.ForegroundBrush)
                        neutral_format.clearProperty(QTextFormat.BackgroundBrush)
                        replacements.append(
                            (fragment.position(), fragment.length(), neutral_format)
                        )
                iterator += 1
            block = block.next()

        if not block_char_replacements and not replacements:
            return

        editor_blocker = QSignalBlocker(self)
        document_blocker = QSignalBlocker(document)
        cursor = QTextCursor(document)
        cursor.beginEditBlock()
        try:
            for position, char_format in block_char_replacements:
                block = document.findBlock(position)
                if block.isValid():
                    QTextCursor(block).setBlockCharFormat(char_format)
            for start, length, char_format in replacements:
                cursor.setPosition(start)
                cursor.setPosition(start + length, QTextCursor.KeepAnchor)
                cursor.setCharFormat(char_format)
        finally:
            cursor.endEditBlock()
            document.setModified(was_modified)
            document.clearUndoRedoStacks()
            del document_blocker
            del editor_blocker

    def note_content_html(self) -> str:
        document = QTextDocument()
        document.setDefaultFont(editor_document_font())
        document.setDefaultStyleSheet(editor_document_style())
        document.setHtml(self.toHtml())
        return document.toHtml()

    def apply_bold(self) -> None:
        self.setFontWeight(QFont.Normal if self.fontWeight() > QFont.Normal else QFont.Bold)

    def apply_italic(self) -> None:
        self.setFontItalic(not self.fontItalic())

    def apply_strike(self) -> None:
        char_format = QTextCharFormat()
        char_format.setFontStrikeOut(not self.textCursor().charFormat().fontStrikeOut())
        self.textCursor().mergeCharFormat(char_format)
        self.mergeCurrentCharFormat(char_format)

    def clear_formatting(self) -> bool:
        if self.isReadOnly() or not self.isEnabled():
            return False

        before_html = self.toHtml()
        cursor = self.textCursor()
        if cursor.hasSelection():
            text = self.sanitize_pasted_text(cursor.selection().toPlainText())
            start = cursor.selectionStart()
            cursor.beginEditBlock()
            cursor.insertText(text, self.clean_char_format())
            end = cursor.position()
            self.normalize_format_range(start, end)
            cursor.endEditBlock()
        else:
            text = self.toPlainText()
            self.setPlainText(self.sanitize_pasted_text(text))

        changed = self.toHtml() != before_html
        if changed:
            self.document().setModified(True)
        return changed

    def clear_markdown_markup(self) -> bool:
        if self.isReadOnly() or not self.isEnabled():
            return False

        original_cursor = QTextCursor(self.textCursor())
        has_selection = original_cursor.hasSelection()
        source = (
            original_cursor.selection().toPlainText()
            if has_selection
            else self.toPlainText()
        )
        converted = strip_markdown_markup(source)
        if converted == source:
            return False

        start = original_cursor.selectionStart() if has_selection else 0
        original_python_offset = _python_index_from_qt_position(
            source,
            original_cursor.position() - start,
        )
        vertical_scroll = self.verticalScrollBar().value()
        horizontal_scroll = self.horizontalScrollBar().value()
        edit_cursor = QTextCursor(original_cursor)
        if not has_selection:
            edit_cursor.select(QTextCursor.Document)

        edit_cursor.beginEditBlock()
        try:
            edit_cursor.insertText(converted, self.clean_char_format())
            if not has_selection:
                self.normalize_format_range(0, _qt_text_length(converted))
        finally:
            edit_cursor.endEditBlock()

        restored_cursor = QTextCursor(self.document())
        if has_selection:
            restored_cursor.setPosition(start)
            restored_cursor.setPosition(
                start + _qt_text_length(converted),
                QTextCursor.KeepAnchor,
            )
        else:
            converted_python_offset = _mapped_text_offset(
                source,
                converted,
                original_python_offset,
            )
            restored_cursor.setPosition(
                _qt_text_length(converted[:converted_python_offset])
            )
        self.setTextCursor(restored_cursor)
        self.verticalScrollBar().setValue(vertical_scroll)
        self.horizontalScrollBar().setValue(horizontal_scroll)
        self._refresh_document_theme_overlays()
        return True

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

    @staticmethod
    def _set_char_format_pixel_size(
        char_format: QTextCharFormat,
        pixel_size: int,
    ) -> None:
        char_format.setProperty(QTextFormat.FontPixelSize, pixel_size)

    def apply_heading(self, level: int) -> None:
        cursor = self.textCursor()
        block_format = QTextBlockFormat()
        char_format = QTextCharFormat()
        if level == 1:
            block_format.setHeadingLevel(1)
            self._set_char_format_pixel_size(char_format, 26)
            char_format.setFontWeight(QFont.Bold)
        elif level == 2:
            block_format.setHeadingLevel(2)
            self._set_char_format_pixel_size(char_format, 15)
            char_format.setFontWeight(QFont.Bold)
        else:
            block_format.setHeadingLevel(0)
            self._set_char_format_pixel_size(char_format, 15)
            char_format.setFontWeight(QFont.Normal)
        cursor.beginEditBlock()
        try:
            cursor.mergeBlockFormat(block_format)
            cursor.mergeCharFormat(char_format)
            self.mergeCurrentCharFormat(char_format)
        finally:
            cursor.endEditBlock()

    def apply_font_size(self, pixel_size: int) -> None:
        cursor = self.textCursor()
        char_format = QTextCharFormat()
        self._set_char_format_pixel_size(char_format, pixel_size)
        cursor.beginEditBlock()
        try:
            cursor.mergeCharFormat(char_format)
            self.mergeCurrentCharFormat(char_format)
        finally:
            cursor.endEditBlock()

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
        self._refresh_document_theme_overlays()

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
            block_format.setLeftMargin(24 if enabled else 16)
            return
        if enabled:
            block_format.setLeftMargin(24)
            block_format.setRightMargin(8)
            block_format.setTopMargin(4)
            block_format.setBottomMargin(4)
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
        painter.setPen(QPen(QColor(current_theme().accent), 2))
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
                    line_x = rect.left() + 8
                    painter.drawLine(
                        int(line_x),
                        int(rect.top() + 4),
                        int(line_x),
                        int(rect.bottom() - 4),
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

    def _apply_code_block_visuals(self, block_format: QTextBlockFormat, enabled: bool) -> None:
        quote_enabled = bool(block_format.property(QUOTE_BLOCK_PROPERTY))
        if enabled:
            block_format.clearBackground()
            block_format.setLeftMargin(24 if quote_enabled else 16)
            block_format.setRightMargin(8)
            block_format.setTopMargin(8)
            block_format.setBottomMargin(8)
            return
        block_format.clearBackground()
        if quote_enabled:
            block_format.setLeftMargin(24)
            block_format.setRightMargin(8)
            block_format.setTopMargin(4)
            block_format.setBottomMargin(4)
            return
        block_format.setLeftMargin(0)
        block_format.setRightMargin(0)
        block_format.setTopMargin(0)
        block_format.setBottomMargin(0)

    @staticmethod
    def _is_code_block_format(block_format: QTextBlockFormat) -> bool:
        if bool(block_format.property(CODE_BLOCK_PROPERTY)):
            return True
        active_geometry = (
            block_format.leftMargin() in {16, 24}
            and block_format.topMargin() == 8
            and block_format.bottomMargin() == 8
        )
        legacy_code_background = block_format.hasProperty(
            QTextFormat.BackgroundBrush
        ) and block_format.leftMargin() > 0
        return active_geometry or (
            legacy_code_background
        )

    @staticmethod
    def _is_inline_code_format(char_format: QTextCharFormat) -> bool:
        if char_format.fontFixedPitch():
            return True
        return any(
            marker in family.casefold()
            for family in (char_format.fontFamilies() or [])
            for marker in ("mono", "menlo", "monaco")
        )

    @staticmethod
    def _foreground_selection(
        cursor: QTextCursor, color: str
    ) -> QTextEdit.ExtraSelection:
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format.setForeground(QColor(color))
        return selection

    def _refresh_document_theme_overlays(self) -> None:
        tokens = getattr(self, "_document_theme_tokens", current_theme())
        selections: list[QTextEdit.ExtraSelection] = []
        if self.document().characterCount() > 1:
            document_cursor = QTextCursor(self.document())
            document_cursor.setPosition(0)
            document_cursor.setPosition(
                self.document().characterCount() - 1,
                QTextCursor.KeepAnchor,
            )
            selections.append(
                self._foreground_selection(document_cursor, tokens.document_text)
            )

        block = self.document().begin()
        while block.isValid():
            block_cursor = QTextCursor(block)
            block_cursor.setPosition(block.position())
            block_cursor.setPosition(
                block.position() + block.length() - 1,
                QTextCursor.KeepAnchor,
            )
            block_format = block.blockFormat()
            if self._is_code_block_format(block_format):
                code_selection = self._foreground_selection(
                    block_cursor, tokens.document_code
                )
                code_selection.format.setBackground(QColor(tokens.surface))
                code_selection.format.setProperty(QTextFormat.FullWidthSelection, True)
                selections.append(code_selection)
            elif bool(block_format.property(QUOTE_BLOCK_PROPERTY)):
                selections.append(
                    self._foreground_selection(
                        block_cursor, tokens.document_secondary
                    )
                )

            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid():
                    char_format = fragment.charFormat()
                    if char_format.isAnchor():
                        color = tokens.document_link
                    elif self._is_inline_code_format(char_format):
                        color = tokens.document_code
                    else:
                        iterator += 1
                        continue
                    fragment_cursor = QTextCursor(self.document())
                    fragment_cursor.setPosition(fragment.position())
                    fragment_cursor.setPosition(
                        fragment.position() + fragment.length(),
                        QTextCursor.KeepAnchor,
                    )
                    selections.append(
                        self._foreground_selection(fragment_cursor, color)
                    )
                iterator += 1
            block = block.next()
        self.setExtraSelections(selections)

    def _refresh_code_highlights(self) -> None:
        self._refresh_document_theme_overlays()

    def _restore_code_block_properties(self) -> None:
        block = self.document().begin()
        while block.isValid():
            block_format = block.blockFormat()
            if self._is_code_block_format(block_format):
                block_format.setProperty(CODE_BLOCK_PROPERTY, True)
                self._apply_code_block_visuals(block_format, True)
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
        char_format.clearProperty(QTextFormat.FontPixelSize)
        char_format.clearProperty(QTextFormat.FontWeight)
        char_format.clearProperty(QTextFormat.FontItalic)
        char_format.clearProperty(QTextFormat.FontUnderline)
        char_format.clearProperty(QTextFormat.FontStrikeOut)
        char_format.setProperty(QTextFormat.FontPixelSize, 15)
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

    @staticmethod
    def _normalized_html_and_text(markup: str) -> tuple[str, str]:
        document = QTextDocument()
        document.setHtml(markup or "")
        return (
            document.toHtml(),
            NotesEditor.sanitize_pasted_text(document.toPlainText()),
        )

    @staticmethod
    def _comparison_text(text: str) -> str:
        normalized = NotesEditor.sanitize_pasted_text(text)
        normalized = normalized.replace("\u2028", "\n").replace("\u2029", "\n")
        normalized = normalized.replace("\u00a0", " ")
        normalized = normalized.replace("\u2007", " ").replace("\u202f", " ")
        normalized = unicodedata.normalize("NFC", normalized)
        lines = [line.rstrip() for line in normalized.split("\n")]
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)

    @classmethod
    def _semantic_lines(
        cls, text: str, *, strip_list_prefixes: bool = False
    ) -> list[str]:
        lines: list[str] = []
        for line in cls._comparison_text(text).split("\n"):
            clean_line = line.strip()
            if not clean_line:
                continue
            lines.append(
                PASTE_LIST_PREFIX_RE.sub("", clean_line)
                if strip_list_prefixes
                else clean_line
            )
        return lines

    @classmethod
    def _semantic_characters(
        cls, text: str, *, strip_list_prefixes: bool = False
    ) -> str:
        return "".join(
            re.sub(r"\s+", "", line)
            for line in cls._semantic_lines(
                text, strip_list_prefixes=strip_list_prefixes
            )
        )

    @staticmethod
    def _is_line_subsequence(needle: list[str], haystack: list[str]) -> bool:
        if not needle:
            return True
        position = 0
        for line in haystack:
            if line == needle[position]:
                position += 1
                if position == len(needle):
                    return True
        return False

    @classmethod
    def _merge_visible_texts(cls, html_text: str, plain_text: str) -> str:
        html_lines = cls.sanitize_pasted_text(html_text).rstrip("\n").split("\n")
        plain_lines = cls.sanitize_pasted_text(plain_text).rstrip("\n").split("\n")
        html_keys = [cls._comparison_text(line) for line in html_lines]
        plain_keys = [cls._comparison_text(line) for line in plain_lines]
        merged: list[str] = []
        for tag, html_start, html_end, plain_start, plain_end in SequenceMatcher(
            None, html_keys, plain_keys, autojunk=False
        ).get_opcodes():
            if tag == "equal":
                merged.extend(plain_lines[plain_start:plain_end])
            elif tag == "delete":
                merged.extend(html_lines[html_start:html_end])
            elif tag == "insert":
                merged.extend(plain_lines[plain_start:plain_end])
            else:
                merged.extend(html_lines[html_start:html_end])
                merged.extend(plain_lines[plain_start:plain_end])
        return "\n".join(merged)

    @classmethod
    def _select_paste_representation(
        cls,
        normalized_html: str,
        html_text: str,
        plain_text: str,
    ) -> tuple[str, str]:
        if not html_text and plain_text:
            return "plain", plain_text
        if html_text and not plain_text:
            return "html", normalized_html
        if not html_text and not plain_text:
            return "plain", ""

        html_comparison = cls._comparison_text(html_text)
        plain_comparison = cls._comparison_text(plain_text)
        if html_comparison == plain_comparison:
            return "html", normalized_html

        html_has_list = bool(re.search(r"<(?:ol|ul)\b", normalized_html, re.I))
        html_semantic_lines = cls._semantic_lines(html_text)
        plain_semantic_lines = cls._semantic_lines(
            plain_text, strip_list_prefixes=html_has_list
        )
        if html_semantic_lines == plain_semantic_lines:
            html_nonblank = [
                line
                for line in html_comparison.split("\n")
                if line.strip()
            ]
            plain_nonblank = [
                line
                for line in plain_comparison.split("\n")
                if line.strip()
            ]
            if html_nonblank == plain_nonblank:
                return "plain", plain_text
            return "html", normalized_html
        if cls._is_line_subsequence(plain_semantic_lines, html_semantic_lines):
            return "html", normalized_html
        if cls._is_line_subsequence(html_semantic_lines, plain_semantic_lines):
            return "plain", plain_text
        html_characters = cls._semantic_characters(html_text)
        plain_characters = cls._semantic_characters(
            plain_text, strip_list_prefixes=html_has_list
        )
        if html_characters == plain_characters:
            return "plain", plain_text
        if plain_characters and plain_characters in html_characters:
            return "html", normalized_html
        if html_characters and html_characters in plain_characters:
            return "plain", plain_text
        if plain_comparison and plain_comparison in html_comparison:
            return "html", normalized_html
        if html_comparison and html_comparison in plain_comparison:
            return "plain", plain_text
        return "plain", cls._merge_visible_texts(html_text, plain_text)

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
