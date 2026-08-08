"""R1-shaped two-column category management dialog."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QInputDialog,
    QVBoxLayout,
    QWidget,
)

from src.snotes.domain.models import Directory
from src.snotes.application.directory_service import DirectoryService
from src.snotes.ui.theme import apply_popup_menu_theme


class DirectoryManagerDialog(QDialog):
    """Approved two-level UI over public MainWindow directory actions."""

    def __init__(
        self,
        list_directories: Callable[[], list[Directory]],
        create_directory: Callable[[int | None], None],
        rename_directory: Callable[[int], None],
        delete_directory: Callable[[int], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._list_directories = list_directories
        self._create_directory = create_directory
        self._rename_directory = rename_directory
        self._delete_directory = delete_directory
        self._rows: list[Directory] = []
        self.setObjectName("categoryManagerDialog")
        self.setWindowTitle("分类管理")
        self.resize(520, 400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        columns = QHBoxLayout()
        columns.setSpacing(8)
        self.main_list = self._build_column("主分类", 0)
        self.sub_list = self._build_column("子分类", 1)
        for column in (self.main_column, self.sub_column):
            columns.addWidget(column, stretch=1)
        layout.addLayout(columns, stretch=1)
        footer = QHBoxLayout()
        footer.addStretch(1)
        close_button = QPushButton("关闭")
        close_button.setObjectName("categoryManagerClose")
        close_button.clicked.connect(self.accept)
        footer.addWidget(close_button)
        layout.addLayout(footer)

        self.main_list.currentItemChanged.connect(self._main_changed)
        for target in (self.main_list, self.sub_list):
            target.itemDoubleClicked.connect(self._rename_item)
        self.refresh()

    def _build_column(self, title: str, level: int) -> QListWidget:
        column = QWidget()
        column.setObjectName("categoryColumn")
        column_layout = QVBoxLayout(column)
        column_layout.setContentsMargins(12, 12, 12, 8)
        column_layout.setSpacing(8)
        label = QLabel(title)
        label.setObjectName("categoryManagerTitle")
        column_layout.addWidget(label)
        target = QListWidget()
        target.setObjectName("categoryManagerList")
        target.setTextElideMode(Qt.ElideRight)
        target.setContextMenuPolicy(Qt.CustomContextMenu)
        target.customContextMenuRequested.connect(
            lambda position, widget=target: self._open_context_menu(widget, position)
        )
        column_layout.addWidget(target, stretch=1)
        new_button = QPushButton("新建分类")
        new_button.setObjectName("categoryNewButton")
        new_button.clicked.connect(lambda: self._create(level))
        column_layout.addWidget(new_button)
        if level == 0:
            self.main_column, self.main_new_button = column, new_button
        elif level == 1:
            self.sub_column, self.sub_new_button = column, new_button
        return target

    @staticmethod
    def _item_id(item: QListWidgetItem | None) -> int | None:
        value = item.data(Qt.UserRole) if item is not None else None
        return int(value) if value is not None else None

    def _selected_id(self, level: int) -> int | None:
        target = (self.main_list, self.sub_list)[level]
        return self._item_id(target.currentItem())

    def _children(self, parent_id: int | None) -> list[Directory]:
        return sorted(
            (row for row in self._rows if row.parent_id == parent_id),
            key=lambda row: (row.sort_order, row.name.casefold(), row.id),
        )

    @staticmethod
    def _fill(target: QListWidget, rows: list[Directory], empty_text: str) -> None:
        target.blockSignals(True)
        target.clear()
        for row in rows:
            item = QListWidgetItem(row.name)
            item.setData(Qt.UserRole, row.id)
            item.setToolTip(row.name)
            target.addItem(item)
        if not rows:
            empty = QListWidgetItem(empty_text)
            empty.setFlags(Qt.NoItemFlags)
            target.addItem(empty)
        target.blockSignals(False)

    def refresh(self) -> None:
        all_rows = self._list_directories()
        by_id = {row.id: row for row in all_rows}
        self._rows = [row for row in all_rows if self._depth(row, by_id) <= 2]
        self._fill(self.main_list, self._children(None), "暂无主分类")
        self._fill(self.sub_list, [], "选择主分类")
        self._update_new_actions()

    def _main_changed(self, *_args) -> None:
        main_id = self._selected_id(0)
        self._fill(self.sub_list, self._children(main_id) if main_id else [], "选择主分类")
        self._update_new_actions()

    def _update_new_actions(self) -> None:
        self.sub_new_button.setEnabled(self._selected_id(0) is not None)

    @staticmethod
    def _depth(row: Directory, by_id: dict[int, Directory]) -> int:
        depth = 1
        parent_id = row.parent_id
        while parent_id in by_id:
            depth += 1
            parent_id = by_id[parent_id].parent_id
        return depth

    def _create(self, level: int) -> None:
        parent_id = None if level == 0 else self._selected_id(level - 1)
        if level > 0 and parent_id is None:
            return
        self._create_directory(parent_id)
        self.refresh()

    def _rename_item(self, item: QListWidgetItem) -> None:
        directory_id = self._item_id(item)
        if directory_id is not None:
            self._rename_directory(directory_id)
            self.refresh()

    def _open_context_menu(self, target: QListWidget, position) -> None:
        item = target.itemAt(position)
        directory_id = self._item_id(item)
        if directory_id is None:
            return
        target.setCurrentItem(item)
        menu = QMenu(self)
        apply_popup_menu_theme(menu)
        rename_action = menu.addAction("重命名")
        delete_action = menu.addAction("删除")
        chosen = menu.exec(target.viewport().mapToGlobal(position))
        if chosen == rename_action:
            self._rename_directory(directory_id)
            self.refresh()
        elif chosen == delete_action:
            self._delete_directory(directory_id)
            self.refresh()


class DirectoryUiWorkflow:
    """UI prompts and dialog coordination; DirectoryService owns all mutations."""

    def __init__(self, service: DirectoryService | None, owner) -> None:
        self.service = service
        self.owner = owner

    def list_directories(self) -> list[Directory]:
        return self.service.list_directories() if self.service is not None else []

    def open(self) -> None:
        if self.service is None or not self.owner.force_save_current_note():
            return
        DirectoryManagerDialog(
            self.list_directories,
            self.create,
            self.rename,
            self.delete,
            self.owner,
        ).exec()

    def create(self, parent_id: int | None) -> None:
        if self.service is None:
            return
        name, accepted = QInputDialog.getText(self.owner, "新增分类", "分类名称")
        if accepted:
            try:
                self.service.create_directory(name, parent_id)
            except Exception as error:
                QMessageBox.warning(self.owner, "新增分类失败", str(error))
            self.owner.refresh_note_list()

    def rename(self, directory_id: int) -> None:
        if self.service is None:
            return
        current = next(
            (row for row in self.service.list_directories() if row.id == directory_id),
            None,
        )
        if current is None:
            return
        name, accepted = QInputDialog.getText(
            self.owner, "重命名分类", "分类名称", text=current.name
        )
        if accepted:
            try:
                self.service.rename_directory(directory_id, name)
            except Exception as error:
                QMessageBox.warning(self.owner, "重命名分类失败", str(error))
            if self.owner.current_note is not None:
                self.owner.load_note(self.owner.current_note.id)
            self.owner.refresh_note_list()

    def delete(self, directory_id: int) -> None:
        if self.service is None or not self.owner.confirm(
            "删除分类", "该分类、子分类和笔记都会移入回收站。是否继续？"
        ):
            return
        self.service.soft_delete_directory(directory_id)
        if (
            self.owner.current_note is not None
            and self.owner.note_service.get_note(self.owner.current_note.id) is None
        ):
            self.owner.clear_editor()
        self.owner.refresh_note_list()
