from __future__ import annotations

from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtGui import QKeyEvent, QKeySequence, QMouseEvent
from PySide6.QtWidgets import QAbstractItemView, QApplication, QTreeWidget, QTreeWidgetItem


MULTI_SELECT_MODIFIERS = Qt.ControlModifier | Qt.MetaModifier | Qt.ShiftModifier


def item_data(item: QTreeWidgetItem | None) -> dict:
    if item is None:
        return {}
    return item.data(0, Qt.UserRole) or {}


def is_multi_select_click() -> bool:
    return bool(QApplication.keyboardModifiers() & MULTI_SELECT_MODIFIERS)


def configure_trash_item(item: QTreeWidgetItem) -> None:
    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)


def selected_items_without_selected_ancestors(
    items: list[QTreeWidgetItem],
) -> list[QTreeWidgetItem]:
    selected_identity = {id(item) for item in items}
    roots: list[QTreeWidgetItem] = []
    for item in items:
        ancestor = item.parent()
        while ancestor is not None and id(ancestor) not in selected_identity:
            ancestor = ancestor.parent()
        if ancestor is None:
            roots.append(item)
    return roots


class TrashTreeWidget(QTreeWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._filtering_selection = False
        self.setHeaderHidden(True)
        self.setIndentation(14)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self.viewport().setAcceptDrops(False)
        self.setDropIndicatorShown(False)
        self.setDragDropMode(QAbstractItemView.NoDragDrop)

    def selectable_item_types(self) -> set[str]:
        return {"deleted_note", "deleted_directory"}

    def selected_payloads(self) -> list[dict]:
        return [item_data(item) for item in self.selectedItems() if item_data(item)]

    def all_items(self) -> list[QTreeWidgetItem]:
        result: list[QTreeWidgetItem] = []

        def visit(item: QTreeWidgetItem) -> None:
            result.append(item)
            for index in range(item.childCount()):
                visit(item.child(index))

        for index in range(self.topLevelItemCount()):
            visit(self.topLevelItem(index))
        return result

    def select_all_selectable_items(self) -> None:
        allowed = self.selectable_item_types()
        self.clearSelection()
        for item in self.all_items():
            if item_data(item).get("type") in allowed:
                item.setSelected(True)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.matches(QKeySequence.SelectAll):
            self.select_all_selectable_items()
            event.accept()
            return
        super().keyPressEvent(event)
        self._remove_unselectable_items()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        item = self.itemAt(event.position().toPoint())
        if event.button() == Qt.RightButton and item is not None:
            if item.isSelected():
                self.setCurrentItem(item, 0, QItemSelectionModel.NoUpdate)
            else:
                self.clearSelection()
                item.setSelected(True)
                self.setCurrentItem(item, 0, QItemSelectionModel.NoUpdate)
            event.accept()
            self._remove_unselectable_items()
            return
        super().mousePressEvent(event)
        self._remove_unselectable_items()

    def selectionChanged(self, selected, deselected) -> None:
        super().selectionChanged(selected, deselected)
        self._remove_unselectable_items()

    def _remove_unselectable_items(self) -> None:
        if self._filtering_selection:
            return
        self._filtering_selection = True
        try:
            allowed = self.selectable_item_types()
            for item in self.selectedItems():
                if item_data(item).get("type") not in allowed:
                    item.setSelected(False)
        finally:
            self._filtering_selection = False
