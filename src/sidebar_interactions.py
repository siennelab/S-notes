from __future__ import annotations

import json
from collections.abc import Callable

from PySide6.QtCore import QItemSelectionModel, QMimeData, Qt, Signal
from PySide6.QtGui import QDrag, QKeyEvent, QKeySequence, QMouseEvent
from PySide6.QtWidgets import QAbstractItemView, QApplication, QTreeWidget, QTreeWidgetItem


NOTE_MIME_TYPE = "application/x-s-notes-note-ids"
MULTI_SELECT_MODIFIERS = Qt.ControlModifier | Qt.MetaModifier | Qt.ShiftModifier


def item_data(item: QTreeWidgetItem | None) -> dict:
    if item is None:
        return {}
    return item.data(0, Qt.UserRole) or {}


def is_multi_select_click() -> bool:
    return bool(QApplication.keyboardModifiers() & MULTI_SELECT_MODIFIERS)


def configure_note_item(item: QTreeWidgetItem, *, draggable: bool) -> None:
    flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
    if draggable:
        flags |= Qt.ItemIsDragEnabled
    item.setFlags(flags)


def configure_directory_item(item: QTreeWidgetItem, *, droppable: bool) -> None:
    flags = Qt.ItemIsEnabled
    if droppable:
        flags |= Qt.ItemIsDropEnabled
    item.setFlags(flags)


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


class SidebarTreeWidget(QTreeWidget):
    notesDropped = Signal(list, int)

    def __init__(
        self,
        drop_handler: Callable[[list[int], int], bool] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.interaction_mode = "directory"
        self._drop_handler = drop_handler
        self._filtering_selection = False
        self.setHeaderHidden(True)
        self.setIndentation(14)
        self.set_interaction_mode("directory")

    def set_interaction_mode(self, mode: str) -> None:
        self.interaction_mode = mode
        self.clearSelection()
        if mode == "directory":
            self.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self.setDragEnabled(True)
            self.setAcceptDrops(True)
            self.viewport().setAcceptDrops(True)
            self.setDropIndicatorShown(True)
            self.setDragDropMode(QAbstractItemView.DragDrop)
            self.setDefaultDropAction(Qt.MoveAction)
        elif mode == "trash":
            self.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self.setDragEnabled(False)
            self.setAcceptDrops(False)
            self.viewport().setAcceptDrops(False)
            self.setDropIndicatorShown(False)
            self.setDragDropMode(QAbstractItemView.NoDragDrop)
        else:
            self.setSelectionMode(QAbstractItemView.SingleSelection)
            self.setDragEnabled(False)
            self.setAcceptDrops(False)
            self.viewport().setAcceptDrops(False)
            self.setDropIndicatorShown(False)
            self.setDragDropMode(QAbstractItemView.NoDragDrop)

    def selectable_item_types(self) -> set[str]:
        if self.interaction_mode == "directory":
            return {"note"}
        if self.interaction_mode == "trash":
            return {"deleted_note", "deleted_directory"}
        return {"note"}

    def selected_payloads(self) -> list[dict]:
        return [item_data(item) for item in self.selectedItems() if item_data(item)]

    def selected_note_ids(self) -> list[int]:
        return list(
            dict.fromkeys(
                data["id"]
                for data in self.selected_payloads()
                if data.get("type") == "note"
            )
        )

    def select_note_ids(self, note_ids: list[int]) -> None:
        wanted = set(note_ids)
        self.clearSelection()
        for item in self.all_items():
            data = item_data(item)
            if data.get("type") == "note" and data.get("id") in wanted:
                item.setSelected(True)

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
        if (
            self.interaction_mode in {"directory", "trash"}
            and event.matches(QKeySequence.SelectAll)
        ):
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

    def startDrag(self, supported_actions) -> None:
        if self.interaction_mode != "directory":
            return
        note_ids = self.selected_note_ids()
        if not note_ids:
            return
        mime_data = QMimeData()
        mime_data.setData(NOTE_MIME_TYPE, json.dumps(note_ids).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        drag.exec(Qt.MoveAction)

    def dragEnterEvent(self, event) -> None:
        if self._drag_event_is_valid(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if self._drop_target(event) is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        target = self._drop_target(event)
        note_ids = self._note_ids_from_mime(event.mimeData())
        if target is None or not note_ids:
            event.ignore()
            return
        directory_id = item_data(target)["id"]
        if self._drop_handler is not None:
            succeeded = self._drop_handler(note_ids, directory_id)
        else:
            self.notesDropped.emit(note_ids, directory_id)
            succeeded = True
        if succeeded:
            event.setDropAction(Qt.MoveAction)
            event.accept()
        else:
            event.ignore()

    def _drag_event_is_valid(self, event) -> bool:
        return (
            self.interaction_mode == "directory"
            and event.source() is self
            and bool(self._note_ids_from_mime(event.mimeData()))
        )

    def _drop_target(self, event) -> QTreeWidgetItem | None:
        if not self._drag_event_is_valid(event):
            return None
        target = self.itemAt(event.position().toPoint())
        if item_data(target).get("type") != "directory":
            return None
        return target

    @staticmethod
    def _note_ids_from_mime(mime_data: QMimeData) -> list[int]:
        if not mime_data.hasFormat(NOTE_MIME_TYPE):
            return []
        try:
            values = json.loads(bytes(mime_data.data(NOTE_MIME_TYPE)).decode("utf-8"))
            return list(dict.fromkeys(int(value) for value in values))
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
