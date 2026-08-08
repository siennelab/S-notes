"""Reusable painted list and drag targets for the ordinary sidebar."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from PySide6.QtCore import QEvent, QMimeData, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QDropEvent, QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

from src.snotes.ui.theme import current_theme, themed_icon
from src.snotes.ui.floating_combo import FloatingComboBox


PAYLOAD_ROLE = Qt.UserRole + 10
NOTE_TITLE_ROLE = Qt.UserRole + 1
NOTE_DATE_ROLE = Qt.UserRole + 2
NOTE_PINNED_ROLE = Qt.UserRole + 3
NOTE_IDS_MIME = "application/x-snotes-note-ids"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
CHINESE_WEEKDAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def note_date_width(font_metrics) -> int:
    return font_metrics.horizontalAdvance("00-00")


def sidebar_clock_time_width(font_metrics) -> int:
    return font_metrics.horizontalAdvance("88:88:88")


def note_ids_from_mime(mime) -> list[int]:
    if not mime.hasFormat(NOTE_IDS_MIME):
        return []
    try:
        return [
            int(value)
            for value in json.loads(bytes(mime.data(NOTE_IDS_MIME)).decode("utf-8"))
        ]
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return []


def format_sidebar_clock(value: datetime | None = None) -> tuple[str, str]:
    timestamp = value or datetime.now(SHANGHAI_TZ)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=SHANGHAI_TZ)
    timestamp = timestamp.astimezone(SHANGHAI_TZ)
    return timestamp.strftime("%H:%M:%S"), (
        f"{timestamp:%Y/%m/%d} {CHINESE_WEEKDAYS[timestamp.weekday()]}"
    )


class NoteListDelegate(QStyledItemDelegate):
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
        pinned = bool(index.data(NOTE_PINNED_ROLE))
        styled.text = ""
        style = styled.widget.style() if styled.widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, styled, painter, styled.widget)
        title_rect, date_rect = self.column_rects(styled)
        selected = bool(styled.state & QStyle.State_Selected)
        tokens = current_theme()
        painter.save()
        if selected:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(tokens.accent))
            marker = styled.rect.adjusted(0, 8, -(styled.rect.width() - 3), -8)
            painter.drawRect(marker)
        if pinned:
            icon_rect = QRect(title_rect.left(), title_rect.center().y() - 7, 14, 14)
            themed_icon("pin.svg", "accent").paint(painter, icon_rect)
            title_rect.adjust(20, 0, 0, 0)
        title_font = QFont(styled.font)
        if selected:
            title_font.setWeight(QFont.DemiBold)
        painter.setFont(title_font)
        painter.setPen(tokens.sidebar_text if selected else tokens.sidebar_text_muted)
        painter.drawText(
            title_rect,
            Qt.AlignLeft | Qt.AlignVCenter,
            styled.fontMetrics.elidedText(title, Qt.ElideRight, title_rect.width()),
        )
        painter.setFont(styled.font)
        painter.setPen(tokens.sidebar_text if selected else tokens.sidebar_text_subtle)
        painter.drawText(date_rect, Qt.AlignRight | Qt.AlignVCenter, date)
        painter.restore()


class NoteListWidget(QListWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("noteList")
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setItemDelegate(NoteListDelegate(self))

    def selected_note_ids(self) -> list[int]:
        return [int(item.data(Qt.UserRole)) for item in self.selectedItems()]

    def startDrag(self, supported_actions) -> None:
        note_ids = self.selected_note_ids()
        if not note_ids:
            return
        mime = QMimeData()
        mime.setData(NOTE_IDS_MIME, json.dumps(note_ids).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.MoveAction)


class DirectoryFilterCombo(FloatingComboBox):
    notesDropped = Signal(object, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent, surface_role="sidebar")
        self.setObjectName("categoryFilter")
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if self.currentData() is not None and event.mimeData().hasFormat(NOTE_IDS_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        directory_id = self.currentData()
        if directory_id is None or not event.mimeData().hasFormat(NOTE_IDS_MIME):
            event.ignore()
            return
        note_ids = note_ids_from_mime(event.mimeData())
        if not note_ids:
            event.ignore()
            return
        self.notesDropped.emit(note_ids, int(directory_id))
        event.acceptProposedAction()


class DirectoryDropButton(QPushButton):
    """R1 manager button that reveals all move targets only during a note drag."""

    notesDropped = Signal(object, int)

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(text, parent)
        self.setAcceptDrops(True)
        self.popup = QListWidget(self)
        self.popup.setObjectName("directoryDropPopup")
        self.popup.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.popup.setAcceptDrops(True)
        self.popup.viewport().setAcceptDrops(True)
        self.popup.viewport().installEventFilter(self)

    def set_targets(self, targets: list[tuple[str, int]]) -> None:
        self.popup.clear()
        for label, directory_id in targets:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, directory_id)
            self.popup.addItem(item)

    def dragEnterEvent(self, event) -> None:
        if note_ids_from_mime(event.mimeData()) and self.popup.count():
            width = max(260, self.width())
            height = min(320, max(40, self.popup.count() * 30 + 8))
            self.popup.resize(width, height)
            self.popup.move(self.mapToGlobal(QPoint(0, self.height())))
            self.popup.show()
            event.acceptProposedAction()
        else:
            event.ignore()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.popup.viewport() and event.type() in {
            QEvent.DragEnter,
            QEvent.DragMove,
        }:
            if note_ids_from_mime(event.mimeData()):
                event.acceptProposedAction()
                return True
        if watched is self.popup.viewport() and event.type() == QEvent.Drop:
            note_ids = note_ids_from_mime(event.mimeData())
            item = self.popup.itemAt(event.position().toPoint())
            if note_ids and item is not None:
                self.notesDropped.emit(note_ids, int(item.data(Qt.UserRole)))
                event.acceptProposedAction()
                self.popup.hide()
                return True
            self.popup.hide()
        return super().eventFilter(watched, event)
