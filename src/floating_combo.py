from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QIcon, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QGraphicsDropShadowEffect,
    QListView,
    QStyle,
    QStyledItemDelegate,
)

from src.theme import (
    EDITOR_BACKGROUND,
    EDITOR_TEXT,
    SELECTED_BACKGROUND,
    SELECTED_TEXT,
    SIDEBAR_SURFACE,
    SIDEBAR_TEXT,
    TOOLBAR_ICON,
)


ICON_DIR = Path(__file__).with_name("icons")


class FloatingComboItemDelegate(QStyledItemDelegate):
    ITEM_HEIGHT = 40

    def __init__(self, combo: "FloatingComboBox") -> None:
        super().__init__(combo.view())
        self.combo = combo

    def paint(self, painter: QPainter, option, index) -> None:
        dark = self.combo.popup_theme == "dark"
        background = QColor(SIDEBAR_SURFACE if dark else EDITOR_BACKGROUND)
        text_color = QColor(SIDEBAR_TEXT if dark else EDITOR_TEXT)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(option.rect, background)

        if option.state & QStyle.State_MouseOver:
            hover_rect = option.rect.adjusted(6, 3, -6, -3)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(SELECTED_BACKGROUND))
            painter.drawRoundedRect(hover_rect, 6, 6)
            text_color = QColor(SELECTED_TEXT)

        text_rect = option.rect.adjusted(14, 0, -38, 0)
        painter.setPen(text_color)
        painter.drawText(
            text_rect,
            Qt.AlignLeft | Qt.AlignVCenter,
            str(index.data(Qt.DisplayRole) or ""),
        )

        if index.row() == self.combo.currentIndex():
            icon_rect = QRect(
                option.rect.right() - 28,
                option.rect.center().y() - 6,
                12,
                12,
            )
            self.combo.check_icon.paint(painter, icon_rect)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        return QSize(super().sizeHint(option, index).width(), self.ITEM_HEIGHT)


class FloatingComboBox(QComboBox):
    GAP = 6
    SHADOW_LEFT = 12
    SHADOW_TOP = 12
    SHADOW_RIGHT = 12
    SHADOW_BOTTOM = 18

    def __init__(self, parent=None, *, popup_theme: str = "light") -> None:
        super().__init__(parent)
        self.popup_theme = popup_theme
        self.minimum_popup_width = 0
        self._shadow_effect: QGraphicsDropShadowEffect | None = None
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
        filename = (
            "combo-check-sidebar.svg"
            if self.popup_theme == "dark"
            else "combo-check.svg"
        )
        return QIcon(str(ICON_DIR / filename))

    def configure_popup(self, minimum_width: int, *, theme: str | None = None) -> None:
        self.minimum_popup_width = minimum_width
        if theme is not None:
            self.set_popup_theme(theme)

    def set_popup_theme(self, theme: str) -> None:
        if theme not in {"light", "dark"}:
            raise ValueError(f"未知下拉主题: {theme}")
        self.popup_theme = theme
        self.view().setObjectName(
            "sidebarComboPopupView" if theme == "dark" else "comboPopupView"
        )

    def showPopup(self) -> None:
        super().showPopup()
        self._position_popup()

    def _position_popup(self) -> None:
        view = self.view()
        popup = view.window()
        popup.setObjectName("floatingComboPopup")
        popup.setAttribute(Qt.WA_TranslucentBackground, True)
        if popup.layout() is not None:
            popup.layout().setContentsMargins(
                self.SHADOW_LEFT,
                self.SHADOW_TOP,
                self.SHADOW_RIGHT,
                self.SHADOW_BOTTOM,
            )
            popup.layout().setSpacing(0)

        if self._shadow_effect is None:
            effect = QGraphicsDropShadowEffect(view)
            effect.setBlurRadius(18)
            effect.setOffset(0, 5)
            effect.setColor(QColor(0, 0, 0, 72))
            view.setGraphicsEffect(effect)
            self._shadow_effect = effect

        trigger_top_left = self.mapToGlobal(QPoint(0, 0))
        trigger_rect = QRect(trigger_top_left, self.size())
        screen = QGuiApplication.screenAt(trigger_rect.center()) or self.screen()
        available = screen.availableGeometry()

        metrics = view.fontMetrics()
        content_width = max(
            self.width(),
            self.minimum_popup_width,
            max(
                (metrics.horizontalAdvance(self.itemText(row)) + 56 for row in range(self.count())),
                default=0,
            ),
        )
        content_width = min(content_width, max(120, available.width() - 24))
        visible_rows = min(max(self.count(), 1), self.maxVisibleItems())
        content_height = visible_rows * FloatingComboItemDelegate.ITEM_HEIGHT + 2
        content_height = min(content_height, max(42, available.height() - 48))

        total_width = content_width + self.SHADOW_LEFT + self.SHADOW_RIGHT
        total_height = content_height + self.SHADOW_TOP + self.SHADOW_BOTTOM
        visual_below_y = trigger_rect.bottom() + 1 + self.GAP
        enough_below = visual_below_y + content_height + self.SHADOW_BOTTOM <= available.bottom() + 1

        visual_x = min(
            max(trigger_rect.left(), available.left() + self.SHADOW_LEFT),
            available.right() - content_width - self.SHADOW_RIGHT + 1,
        )
        outer_x = visual_x - self.SHADOW_LEFT
        if enough_below:
            visual_y = visual_below_y
            outer_y = visual_y - self.SHADOW_TOP
            self.last_popup_direction = "below"
        else:
            visual_y = max(
                available.top() + self.SHADOW_TOP,
                trigger_rect.top() - self.GAP - content_height,
            )
            outer_y = visual_y - self.SHADOW_TOP
            self.last_popup_direction = "above"

        popup.setGeometry(outer_x, outer_y, total_width, total_height)
        self.last_popup_surface_geometry = QRect(
            visual_x, visual_y, content_width, content_height
        )
        view.viewport().update()
