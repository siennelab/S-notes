"""Minimal Package 2 sidebar: identity, note list, create, and themes."""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from src.snotes.domain.models import Note
from src.snotes.ui.theme import THEME_LABELS, repolish


NOTE_TITLE_ROLE = Qt.UserRole + 1
NOTE_DATE_ROLE = Qt.UserRole + 2
NOTE_PINNED_ROLE = Qt.UserRole + 3


def note_date_width(font_metrics) -> int:
    """Stable width for the frozen ``MM-DD`` sidebar date format."""
    return font_metrics.horizontalAdvance("00-00")


class NoteListDelegate(QStyledItemDelegate):
    """Paint title and date as two columns so long titles cannot cover dates."""

    def sizeHint(self, option, index) -> QSize:
        return QSize(super().sizeHint(option, index).width(), 32)

    def column_rects(self, option: QStyleOptionViewItem) -> tuple[QRect, QRect]:
        content = option.rect.adjusted(8, 0, -8, 0)
        date_width = note_date_width(option.fontMetrics)
        date_rect = QRect(
            content.right() - date_width + 1,
            content.top(),
            date_width,
            content.height(),
        )
        title_rect = QRect(
            content.left(),
            content.top(),
            max(0, date_rect.left() - content.left() - 8),
            content.height(),
        )
        return title_rect, date_rect

    def paint(self, painter: QPainter, option, index) -> None:
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        title = str(index.data(NOTE_TITLE_ROLE) or "未命名笔记")
        date = str(index.data(NOTE_DATE_ROLE) or "")
        if bool(index.data(NOTE_PINNED_ROLE)):
            title = f"◆ {title}"
        styled.text = ""
        style = styled.widget.style() if styled.widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, styled, painter, styled.widget)

        title_rect, date_rect = self.column_rects(styled)
        color_role = (
            QPalette.HighlightedText
            if styled.state & QStyle.State_Selected
            else QPalette.Text
        )
        painter.save()
        painter.setPen(styled.palette.color(color_role))
        painter.drawText(
            title_rect,
            Qt.AlignLeft | Qt.AlignVCenter,
            styled.fontMetrics.elidedText(title, Qt.ElideRight, title_rect.width()),
        )
        painter.drawText(date_rect, Qt.AlignRight | Qt.AlignVCenter, date)
        painter.restore()


class NoteSidebar(QWidget):
    noteSelected = Signal(int)
    newNoteRequested = Signal()
    themeSelected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumWidth(280)
        self.setMaximumWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        brand = QLabel("S-notes")
        brand.setObjectName("brandLabel")
        header.addWidget(brand)
        header.addStretch(1)
        self.new_note_button = QPushButton("+")
        self.new_note_button.setObjectName("newNoteButton")
        self.new_note_button.setToolTip("新建笔记")
        self.new_note_button.setFixedSize(28, 28)
        self.new_note_button.clicked.connect(self.newNoteRequested.emit)
        header.addWidget(self.new_note_button)
        layout.addLayout(header)

        self.note_list = QListWidget()
        self.note_list.setObjectName("noteList")
        self.note_list.setAutoFillBackground(False)
        self.note_list.viewport().setAutoFillBackground(False)
        self.note_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.note_list.setItemDelegate(NoteListDelegate(self.note_list))
        self.note_list.currentItemChanged.connect(self._emit_selected_note)
        layout.addWidget(self.note_list, stretch=1)

        theme_row = QHBoxLayout()
        theme_row.setSpacing(4)
        self.theme_buttons: dict[str, QPushButton] = {}
        for name, label in THEME_LABELS:
            button = QPushButton(label)
            button.setObjectName("themeButton")
            button.setProperty("active", False)
            button.clicked.connect(
                lambda checked=False, selected=name: self.themeSelected.emit(selected)
            )
            theme_row.addWidget(button, stretch=1)
            self.theme_buttons[name] = button
        layout.addLayout(theme_row)

    def set_notes(self, notes: list[Note], selected_note_id: int | None) -> None:
        self.note_list.blockSignals(True)
        self.note_list.clear()
        selected_item: QListWidgetItem | None = None
        for note in notes:
            title = note.title.strip() or "未命名笔记"
            date = note.updated_at[5:10] if len(note.updated_at) >= 10 else ""
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, note.id)
            item.setData(NOTE_TITLE_ROLE, title)
            item.setData(NOTE_DATE_ROLE, date)
            item.setData(NOTE_PINNED_ROLE, note.is_pinned)
            item.setToolTip(title)
            self.note_list.addItem(item)
            if note.id == selected_note_id:
                selected_item = item
        if selected_item is not None:
            self.note_list.setCurrentItem(selected_item)
            self.note_list.scrollToItem(selected_item)
        self.note_list.blockSignals(False)

    def select_note(self, note_id: int) -> None:
        for row in range(self.note_list.count()):
            item = self.note_list.item(row)
            if int(item.data(Qt.UserRole)) == note_id:
                self.note_list.blockSignals(True)
                self.note_list.setCurrentItem(item)
                self.note_list.blockSignals(False)
                return

    def set_current_theme(self, name: str) -> None:
        for theme_name, button in self.theme_buttons.items():
            button.setProperty("active", theme_name == name)
            repolish(button)

    def _emit_selected_note(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if current is not None:
            self.noteSelected.emit(int(current.data(Qt.UserRole)))
