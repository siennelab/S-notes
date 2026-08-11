from __future__ import annotations

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

from src.snotes.ui.html_sanitizer import (
    contains_html_markup,
    safe_link_target,
    sanitize_html,
    sanitize_pasted_text,
)
from src.snotes.ui.markdown_cleanup import strip_markdown_markup
from src.snotes.ui.paste_content import (
    looks_like_markdown,
    markdown_to_html,
    select_paste_representation,
)
from src.snotes.ui.theme import (
    ThemeTokens,
    apply_popup_menu_theme,
    current_theme,
    editor_document_style,
)


QUOTE_BLOCK_PROPERTY = QTextFormat.UserProperty + 1
CODE_BLOCK_PROPERTY = QTextFormat.UserProperty + 2
ZERO_WIDTH_BLANK_CHARACTERS = frozenset("\u200b\u200c\u200d\u2060\ufeff")


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
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        scroll_bar = self.verticalScrollBar()
        scroll_bar.setProperty("scrollable", False)
        scroll_bar.rangeChanged.connect(self._sync_vertical_scrollbar_visual_state)
        self._document_reset_in_progress = False
        self._find_highlight_cursor: QTextCursor | None = None
        self.document().setDocumentMargin(2.0)
        self.document().setDefaultFont(editor_document_font())
        self.document().setDefaultStyleSheet(editor_document_style())
        apply_editor_document_theme(self)
        self.textChanged.connect(self._refresh_document_theme_overlays)

    def _sync_vertical_scrollbar_visual_state(
        self,
        minimum: int,
        maximum: int,
    ) -> None:
        scroll_bar = self.verticalScrollBar()
        scrollable = maximum > minimum
        if scroll_bar.property("scrollable") == scrollable:
            return
        scroll_bar.setProperty("scrollable", scrollable)
        scroll_bar.style().unpolish(scroll_bar)
        scroll_bar.style().polish(scroll_bar)
        scroll_bar.update()

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

    def _normalize_empty_ordinary_block_before_return(
        self,
        cursor: QTextCursor,
    ) -> tuple[QTextCursor, QTextCharFormat | None]:
        """Let Return leave an empty rich paragraph without consuming the key.

        QTextEdit normally uses Return on an empty formatted paragraph to drop
        its inherited input format.  In that state the key changes formatting
        but creates no block, so consecutive Returns appear to lose every
        second newline.  Amend the previous Return's formatting compensation
        before the next native Return.  Structured blocks retain their own Qt
        exit behavior.
        """

        block = cursor.block()
        block_format = block.blockFormat()
        if (
            block.text()
            or block.textList() is not None
            or block_format.headingLevel()
            or bool(block_format.property(QUOTE_BLOCK_PROPERTY))
            or self._is_code_block_format(block_format)
        ):
            return cursor, None

        input_format = cursor.charFormat()
        block_char_format = block.charFormat()
        default_pixel_size = editor_document_font().pixelSize()
        inline_intent = QTextCharFormat()
        inherited_pixel_size = None
        for char_format in (input_format, block_char_format):
            if char_format.hasProperty(QTextFormat.FontPixelSize):
                try:
                    candidate = int(
                        char_format.property(QTextFormat.FontPixelSize)
                    )
                except (TypeError, ValueError):
                    continue
                if candidate != default_pixel_size:
                    inherited_pixel_size = candidate
                    break
        if inherited_pixel_size is not None:
            self._set_char_format_pixel_size(
                inline_intent,
                inherited_pixel_size,
            )
        if input_format.fontWeight() > QFont.Normal:
            inline_intent.setFontWeight(input_format.fontWeight())
        if input_format.fontItalic():
            inline_intent.setFontItalic(True)
        if input_format.fontUnderline():
            inline_intent.setFontUnderline(True)
        if input_format.fontStrikeOut():
            inline_intent.setFontStrikeOut(True)

        non_size_char_properties = any(
            int(property_key) != int(QTextFormat.FontPixelSize)
            for char_format in (input_format, block_char_format)
            for property_key in char_format.properties()
        )
        redundant_default_size = any(
            char_format.hasProperty(QTextFormat.FontPixelSize)
            and int(char_format.property(QTextFormat.FontPixelSize))
            == default_pixel_size
            for char_format in (input_format, block_char_format)
        )
        needs_normalization = bool(
            non_size_char_properties
            or redundant_default_size
            or block_format.properties()
        )
        if not needs_normalization:
            return cursor, None

        normalized_input = QTextCharFormat()
        normalized_block_chars = QTextCharFormat()
        if inherited_pixel_size is not None:
            self._set_char_format_pixel_size(
                normalized_input,
                inherited_pixel_size,
            )
            self._set_char_format_pixel_size(
                normalized_block_chars,
                inherited_pixel_size,
            )

        # Formatting belongs to the preceding Return, while the Return about
        # to run must remain outside this block so Qt owns cursor visibility.
        edit_cursor = QTextCursor(cursor)
        edit_cursor.joinPreviousEditBlock()
        try:
            block_cursor = QTextCursor(block)
            block_cursor.setBlockFormat(QTextBlockFormat())
            block_cursor.setBlockCharFormat(normalized_block_chars)
            self.setTextCursor(cursor)
            self.setCurrentCharFormat(normalized_input)
        finally:
            edit_cursor.endEditBlock()
        preserved_inline_intent = (
            inline_intent if inline_intent.properties() else None
        )
        return self.textCursor(), preserved_inline_intent

    def keyPressEvent(self, event) -> None:
        if self.isReadOnly() or not self.isEnabled():
            super().keyPressEvent(event)
            return
        modifiers = event.modifiers()
        command_modifiers = Qt.ControlModifier | Qt.MetaModifier | Qt.AltModifier
        cursor = self.textCursor()
        plain_return = bool(
            event.key() in {Qt.Key_Return, Qt.Key_Enter}
            and not cursor.hasSelection()
            and not modifiers & (command_modifiers | Qt.ShiftModifier)
        )
        inline_intent = None
        if plain_return:
            cursor, inline_intent = (
                self._normalize_empty_ordinary_block_before_return(cursor)
            )
        preserve_pixel_size = (
            plain_return
            and cursor.charFormat().hasProperty(QTextFormat.FontPixelSize)
        )
        if preserve_pixel_size or inline_intent is not None:
            inherited_format = QTextCharFormat(inline_intent or QTextCharFormat())
            if preserve_pixel_size and not inherited_format.hasProperty(
                QTextFormat.FontPixelSize
            ):
                self._set_char_format_pixel_size(
                    inherited_format,
                    int(cursor.charFormat().property(QTextFormat.FontPixelSize)),
                )
            # Let QTextEdit finish Return layout and native cursor scrolling
            # before opening a format edit block. Running Return inside an
            # already-open block resets long wrapped documents to the top.
            super().keyPressEvent(event)
            cursor = self.textCursor()
            edit_cursor = QTextCursor(cursor)
            # Keep Return plus its inherited-size compensation as one undo.
            edit_cursor.joinPreviousEditBlock()
            try:
                block_char_format = QTextCharFormat(
                    cursor.block().charFormat()
                )
                block_char_format.merge(inherited_format)
                QTextCursor(cursor.block()).setBlockCharFormat(
                    block_char_format
                )
                cursor.mergeCharFormat(inherited_format)
                self.setTextCursor(cursor)
                self.mergeCurrentCharFormat(inherited_format)
            finally:
                edit_cursor.endEditBlock()
            return
        if (
            event.text()
            and event.text() not in {"\r", "\n", "\t"}
            and not modifiers & command_modifiers
        ):
            self.insertPlainText(event.text())
            event.accept()
            return
        super().keyPressEvent(event)

    def createStandardContextMenu(self, position=None):
        if position is None:
            menu = super().createStandardContextMenu()
        else:
            menu = super().createStandardContextMenu(position)
        apply_popup_menu_theme(menu)
        return menu

    def contextMenuEvent(self, event) -> None:
        menu = self.createStandardContextMenu(event.pos())
        menu.exec(event.globalPos())
        menu.deleteLater()

    def inputMethodEvent(self, event) -> None:
        if self.isReadOnly() or not self.isEnabled():
            super().inputMethodEvent(event)
            return
        if event.commitString() and not self.textCursor().hasSelection():
            self._normalize_current_input_format()
        super().inputMethodEvent(event)
        self._normalize_current_input_format()

    def insertFromMimeData(self, source: QMimeData) -> None:
        if self.isReadOnly() or not self.isEnabled():
            return
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
        if self.isReadOnly() or not self.isEnabled():
            return
        clipboard = self.clipboard_text()
        self.insertPlainText(self.sanitize_pasted_text(clipboard))

    def clipboard_text(self) -> str:
        from PySide6.QtWidgets import QApplication

        return QApplication.clipboard().text()

    def set_note_content(self, content: str) -> None:
        self._find_highlight_cursor = None
        self._document_reset_in_progress = True
        try:
            if contains_html_markup(content):
                self.document().setDefaultStyleSheet("")
                try:
                    self.setHtml(content)
                    self._normalize_legacy_document_colors()
                    self._restore_quote_properties()
                    self._restore_code_block_properties()
                finally:
                    self.document().setDefaultStyleSheet(editor_document_style())
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
        return self.toHtml()

    def apply_link(self, href: str) -> bool:
        target = self._safe_link_target(href)
        cursor = self.textCursor()
        if not target or not cursor.hasSelection():
            return False
        char_format = QTextCharFormat()
        char_format.setAnchor(True)
        char_format.setAnchorHref(target)
        char_format.setFontUnderline(True)
        cursor.mergeCharFormat(char_format)
        self.setTextCursor(cursor)
        self._refresh_document_theme_overlays()
        return True

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
        if not cursor.hasSelection():
            cursor.select(QTextCursor.Document)
        text = self.sanitize_pasted_text(cursor.selection().toPlainText())
        start = cursor.selectionStart()
        cursor.beginEditBlock()
        try:
            cursor.insertText(text, self.clean_char_format())
            end = cursor.position()
            self.normalize_format_range(
                start,
                end,
                normalize_block_chars=True,
            )
            self.setTextCursor(cursor)
            self.setCurrentCharFormat(self.clean_char_format())
        finally:
            cursor.endEditBlock()

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
        char_format.clearProperty(QTextFormat.FontPointSize)
        char_format.clearProperty(QTextFormat.FontPixelSize)
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
        if cursor.hasSelection():
            self._apply_selected_font_size(cursor, pixel_size)
            return
        char_format = QTextCharFormat()
        self._set_char_format_pixel_size(char_format, pixel_size)
        cursor.beginEditBlock()
        try:
            cursor.mergeCharFormat(char_format)
            self.mergeCurrentCharFormat(char_format)
        finally:
            cursor.endEditBlock()

    def _apply_selected_font_size(
        self,
        selection: QTextCursor,
        pixel_size: int,
    ) -> None:
        start = selection.selectionStart()
        end = selection.selectionEnd()
        document = self.document()
        document_end = document.characterCount() - 1
        selects_entire_document = start == 0 and end == document_end
        edit_cursor = QTextCursor(document)
        edit_cursor.beginEditBlock()
        try:
            block = document.findBlock(start)
            while block.isValid() and (
                block.position() < end or selects_entire_document
            ):
                block_start = block.position()
                block_text_end = block_start + block.length() - 1
                is_terminal_block = not block.next().isValid()
                paragraph_boundary_selected = (
                    end > block_text_end
                    or (is_terminal_block and end == document_end)
                )
                fully_selected = selects_entire_document or (
                    start <= block_start
                    and end >= block_text_end
                    and paragraph_boundary_selected
                )
                iterator = block.begin()
                while not iterator.atEnd():
                    fragment = iterator.fragment()
                    fragment_start = max(start, fragment.position())
                    fragment_end = min(end, fragment.position() + fragment.length())
                    if fragment.isValid() and fragment_end > fragment_start:
                        char_format = QTextCharFormat(fragment.charFormat())
                        self._set_char_format_pixel_size(char_format, pixel_size)
                        range_cursor = QTextCursor(document)
                        range_cursor.setPosition(fragment_start)
                        range_cursor.setPosition(
                            fragment_end,
                            QTextCursor.KeepAnchor,
                        )
                        range_cursor.setCharFormat(char_format)
                    iterator += 1
                if fully_selected:
                    block_char_format = QTextCharFormat(block.charFormat())
                    self._set_char_format_pixel_size(
                        block_char_format,
                        pixel_size,
                    )
                    QTextCursor(block).setBlockCharFormat(block_char_format)
                block = block.next()
        finally:
            edit_cursor.endEditBlock()
        self.setTextCursor(selection)

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
        if (
            self._find_highlight_cursor is not None
            and self._find_highlight_cursor.hasSelection()
        ):
            find_selection = QTextEdit.ExtraSelection()
            find_selection.cursor = QTextCursor(self._find_highlight_cursor)
            find_selection.format.setBackground(QColor(tokens.accent))
            find_selection.format.setForeground(QColor(tokens.accent_text))
            selections.append(find_selection)
        self.setExtraSelections(selections)

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

    def normalize_format_range(
        self,
        start: int,
        end: int,
        *,
        normalize_block_chars: bool = False,
    ) -> None:
        if end <= start:
            return
        clean_chars = self.clean_char_format()
        clean_block = self.clean_block_format()
        cursor = QTextCursor(self.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.KeepAnchor)
        cursor.mergeCharFormat(clean_chars)

        document = self.document()
        document_end = document.characterCount() - 1
        selects_entire_document = start == 0 and end == document_end
        block = document.findBlock(start)
        while block.isValid() and block.position() <= end:
            block_cursor = QTextCursor(block)
            block_cursor.setBlockFormat(clean_block)
            if normalize_block_chars:
                block_text_end = block.position() + block.length() - 1
                is_terminal_block = not block.next().isValid()
                paragraph_boundary_selected = (
                    end > block_text_end
                    or (is_terminal_block and end == document_end)
                )
                fully_selected = selects_entire_document or (
                    start <= block.position()
                    and end >= block_text_end
                    and paragraph_boundary_selected
                )
                if fully_selected:
                    block_cursor.setBlockCharFormat(clean_chars)
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
            self.clear_find_highlight()
            return False
        if self.find(query):
            self._show_find_highlight()
            return True
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.Start)
        self.setTextCursor(cursor)
        if self.find(query):
            self._show_find_highlight()
            return True
        self.clear_find_highlight()
        return False

    def find_first(self, query: str) -> bool:
        self.clear_find_highlight()
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.Start)
        self.setTextCursor(cursor)
        return self.find_next(query)

    def _show_find_highlight(self) -> None:
        match_cursor = self.textCursor()
        self._find_highlight_cursor = (
            QTextCursor(match_cursor) if match_cursor.hasSelection() else None
        )
        if self._find_highlight_cursor is not None:
            edit_cursor = QTextCursor(self._find_highlight_cursor)
            edit_cursor.clearSelection()
            self.setTextCursor(edit_cursor)
        self._refresh_document_theme_overlays()
        self.ensureCursorVisible()

    def clear_find_highlight(self) -> None:
        if self._find_highlight_cursor is None:
            return
        self._find_highlight_cursor = None
        self._refresh_document_theme_overlays()

    def replace_current(self, query: str, replacement: str) -> bool:
        cursor = (
            QTextCursor(self._find_highlight_cursor)
            if self._find_highlight_cursor is not None
            else QTextCursor()
        )
        if not cursor.hasSelection() or cursor.selectedText() != query:
            if not self.find_next(query):
                return False
            if self._find_highlight_cursor is None:
                return False
            cursor = QTextCursor(self._find_highlight_cursor)
        cursor.insertText(replacement)
        self.setTextCursor(cursor)
        self._find_highlight_cursor = None
        self.find_next(query)
        return True

    def replace_all(self, query: str, replacement: str) -> int:
        self.clear_find_highlight()
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
        self.clear_find_highlight()
        return count

    @staticmethod
    def sanitize_pasted_text(text: str) -> str:
        return sanitize_pasted_text(text)

    @staticmethod
    def looks_like_markdown(text: str) -> bool:
        return looks_like_markdown(text)

    @staticmethod
    def sanitize_html(markup: str) -> str:
        return sanitize_html(markup)

    @staticmethod
    def _safe_link_target(raw_value: str) -> str:
        return safe_link_target(raw_value)

    @staticmethod
    def _normalized_html_and_text(markup: str) -> tuple[str, str]:
        document = QTextDocument()
        document.setDefaultFont(editor_document_font())
        document.setHtml(markup or "")
        return (
            document.toHtml(),
            NotesEditor.sanitize_pasted_text(document.toPlainText()),
        )

    @staticmethod
    def _select_paste_representation(
        normalized_html: str,
        html_text: str,
        plain_text: str,
    ) -> tuple[str, str]:
        return select_paste_representation(
            normalized_html,
            html_text,
            plain_text,
        )

    @staticmethod
    def markdown_to_html(text: str) -> str:
        return markdown_to_html(text)
