"""Ordinary related-notes drawer."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.snotes.domain.relations import RelatedNote


class LinkDrawer(QWidget):
    noteSelected = Signal(int)
    closeRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("linkDrawer")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumWidth(280)
        self.setMaximumWidth(360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        self.close_button = QPushButton("关闭关联")
        self.close_button.clicked.connect(self.closeRequested.emit)
        layout.addWidget(self.close_button)
        self.summary = QLabel("共 0 篇关联笔记")
        layout.addWidget(self.summary)
        self.results = QListWidget()
        self.results.setObjectName("linkResults")
        self.results.itemActivated.connect(self._activate)
        self.results.itemClicked.connect(self._activate)
        layout.addWidget(self.results, stretch=1)

    def set_results(self, results: list[RelatedNote]) -> None:
        self.results.clear()
        self.summary.setText(f"共 {len(results)} 篇关联笔记")
        for result in results:
            reason = "、".join(result.reasons)
            subtitle = " · ".join(part for part in (reason, result.path) if part)
            item = QListWidgetItem(f"{result.title}\n{subtitle}")
            item.setData(Qt.UserRole, result.note_id)
            item.setToolTip(f"{result.title}\n{subtitle}")
            self.results.addItem(item)

    def _activate(self, item: QListWidgetItem) -> None:
        self.noteSelected.emit(int(item.data(Qt.UserRole)))
