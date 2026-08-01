from __future__ import annotations

from PySide6.QtGui import QTextCharFormat, QTextCursor, QTextFormat

from src.editor import NotesEditor


_ORIGINAL_APPLY_SELECTED_FONT_SIZE = NotesEditor._apply_selected_font_size
_INSTALLED = False


def _font_size_format(source: QTextCharFormat, pixel_size: int) -> QTextCharFormat:
    char_format = QTextCharFormat(source)
    char_format.clearProperty(QTextFormat.FontPointSize)
    char_format.clearProperty(QTextFormat.FontPixelSize)
    char_format.setProperty(QTextFormat.FontPixelSize, pixel_size)
    return char_format


def _apply_selected_font_size_with_block_inheritance(
    self: NotesEditor,
    selection: QTextCursor,
    pixel_size: int,
) -> None:
    """Apply visible text size and preserve it at fully selected paragraph boundaries."""

    start = selection.selectionStart()
    end = selection.selectionEnd()
    _ORIGINAL_APPLY_SELECTED_FONT_SIZE(self, selection, pixel_size)

    edit_cursor = QTextCursor(self.document())
    edit_cursor.beginEditBlock()
    try:
        block = self.document().findBlock(start)
        while block.isValid() and block.position() < end:
            block_start = block.position()
            block_text_end = block_start + len(block.text())
            fully_selected = start <= block_start and end >= block_text_end
            selected_empty_block = not block.text() and start <= block_start < end
            if fully_selected or selected_empty_block:
                block_cursor = QTextCursor(block)
                block_cursor.setBlockCharFormat(
                    _font_size_format(block.charFormat(), pixel_size)
                )
            block = block.next()
    finally:
        edit_cursor.endEditBlock()

    self.setTextCursor(selection)


def install_font_size_cursor_fix() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    NotesEditor._apply_selected_font_size = _apply_selected_font_size_with_block_inheritance
    _INSTALLED = True
