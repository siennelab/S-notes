from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QListView,
    QStyle,
    QStyledItemDelegate,
)

from src.theme import current_theme, themed_icon


class FloatingComboItemDelegate(QStyledItemDelegate):
    ITEM_HEIGHT = 32

    def __init__(self, combo: "FloatingComboBox") -> None:
        super().__init__(combo.view())
        self.combo = combo

    def item_colors(self, state: QStyle.StateFlag) -> tuple[QColor, QColor]:
        tokens = current_theme()
        sidebar = self.combo.surface_role == "sidebar"
        active = bool(state & (QStyle.State_MouseOver | QStyle.State_Selected))
        enabled = bool(state & QStyle.State_Enabled)
        if sidebar:
            background = tokens.sidebar_selected if active else tokens.sidebar
            text = tokens.sidebar_text if enabled else tokens.sidebar_text_subtle
        else:
            background = tokens.surface_hover if active else tokens.surface
            text = tokens.text if enabled else tokens.disabled
        return QColor(background), QColor(text)

    def paint(self, painter: QPainter, option, index) -> None:
        background, text_color = self.item_colors(option.state)
        tokens = current_theme()
        base_background = QColor(
            tokens.sidebar
            if self.combo.surface_role == "sidebar"
            else tokens.surface
        )
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(option.rect, base_background)

        if option.state & (QStyle.State_MouseOver | QStyle.State_Selected):
            hover_rect = option.rect.adjusted(4, 0, -4, 0)
            painter.setPen(Qt.NoPen)
            painter.setBrush(background)
            painter.drawRoundedRect(hover_rect, 6, 6)

        text_rect = option.rect.adjusted(12, 0, -32, 0)
        painter.setPen(text_color)
        painter.drawText(
            text_rect,
            Qt.AlignLeft | Qt.AlignVCenter,
            str(index.data(Qt.DisplayRole) or ""),
        )

        if index.row() == self.combo.currentIndex():
            icon_rect = QRect(
                option.rect.right() - 24,
                option.rect.center().y() - 7,
                14,
                14,
            )
            self.combo.check_icon.paint(painter, icon_rect)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        return QSize(super().sizeHint(option, index).width(), self.ITEM_HEIGHT)


class FloatingComboBox(QComboBox):
    GAP = 4
    POPUP_PADDING = 4

    def __init__(self, parent=None, *, popup_theme: str = "light") -> None:
        super().__init__(parent)
        self.surface_role = "editor"
        self.minimum_popup_width = 0
        self.last_popup_direction = "below"
        self.last_popup_surface_geometry = QRect()

        view = QListView(self)
        view.setMouseTracking(True)
        view.setUniformItemSizes(True)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setView(view)
        self.setMaxVisibleItems(8)
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.set_popup_theme(popup_theme)
        view.setItemDelegate(FloatingComboItemDelegate(self))

    @property
    def check_icon(self) -> QIcon:
        filename = "combo-check-sidebar.svg" if self.surface_role == "sidebar" else "combo-check.svg"
        return themed_icon(filename, self.check_icon_role)

    @property
    def check_icon_role(self) -> str:
        if self.surface_role == "sidebar" and current_theme().name == "light-slate":
            return "sidebar_text_muted"
        return "accent"

    def configure_popup(self, minimum_width: int, *, theme: str | None = None) -> None:
        self.minimum_popup_width = minimum_width
        if theme is not None:
            self.set_popup_theme(theme)

    def set_popup_theme(self, theme: str) -> None:
        role = {"dark": "sidebar", "light": "editor"}.get(theme, theme)
        if role not in {"sidebar", "editor"}:
            raise ValueError(f"未知下拉主题: {theme}")
        self.surface_role = role
        self.view().setObjectName(
            "sidebarComboPopupView" if role == "sidebar" else "comboPopupView"
        )
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        icon = themed_icon("combo-chevron.svg", "text_muted")
        painter = QPainter(self)
        icon.paint(painter, self.width() - 24, (self.height() - 14) // 2, 14, 14)

    def showPopup(self) -> None:
        super().showPopup()
        self._position_popup()

    def _position_popup(self) -> None:
        view = self.view()
        popup = view.window()
        popup.setObjectName("floatingComboPopup")
        popup.setAttribute(Qt.WA_TranslucentBackground, True)
        if popup.layout() is not None:
            popup.layout().setContentsMargins(0, 0, 0, 0)
            popup.layout().setSpacing(0)

        trigger_top_left = self.mapToGlobal(QPoint(0, 0))
        trigger_rect = QRect(trigger_top_left, self.size())
        screen = QGuiApplication.screenAt(trigger_rect.center()) or self.screen()
        available = screen.availableGeometry()

        metrics = view.fontMetrics()
        content_width = max(
            self.width(),
            self.minimum_popup_width,
            max(
                (metrics.horizontalAdvance(self.itemText(row)) + 48 for row in range(self.count())),
                default=0,
            ),
        )
        content_width = min(content_width, max(120, available.width() - 24))
        visible_rows = min(max(self.count(), 1), self.maxVisibleItems())
        content_height = visible_rows * FloatingComboItemDelegate.ITEM_HEIGHT + 8
        content_height = min(content_height, max(40, available.height() - 48))

        total_width = content_width
        total_height = content_height
        visual_below_y = trigger_rect.bottom() + 1 + self.GAP
        enough_below = visual_below_y + content_height <= available.bottom() + 1

        visual_x = min(
            max(trigger_rect.left(), available.left()),
            available.right() - content_width + 1,
        )
        outer_x = visual_x
        if enough_below:
            visual_y = visual_below_y
            outer_y = visual_y
            self.last_popup_direction = "below"
        else:
            visual_y = max(
                available.top(),
                trigger_rect.top() - self.GAP - content_height,
            )
            outer_y = visual_y
            self.last_popup_direction = "above"

        popup.setGeometry(outer_x, outer_y, total_width, total_height)
        self.last_popup_surface_geometry = QRect(
            visual_x, visual_y, content_width, content_height
        )
        view.viewport().update()
