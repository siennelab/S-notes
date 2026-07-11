from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class CategoryManagerDialog(QDialog):
    """Three-level UI over MainWindow's existing directory operations."""

    def __init__(self, owner) -> None:
        super().__init__(owner)
        self.owner = owner
        self.setObjectName("categoryManagerDialog")
        self.setWindowTitle("分类管理")
        self.resize(520, 400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(12)

        columns = QHBoxLayout()
        columns.setSpacing(10)
        self.main_list = self._column("主分类", 0)
        self.sub_list = self._column("子分类", 1)
        self.sub_sub_list = self._column("子子分类", 2)
        for column in (self.main_column, self.sub_column, self.sub_sub_column):
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
        self.sub_list.currentItemChanged.connect(self._sub_changed)
        self.main_list.itemDoubleClicked.connect(lambda item: self._rename_item(item))
        self.sub_list.itemDoubleClicked.connect(lambda item: self._rename_item(item))
        self.sub_sub_list.itemDoubleClicked.connect(lambda item: self._rename_item(item))
        self.refresh()

    def _column(self, title: str, level: int) -> QListWidget:
        column = QWidget()
        column.setObjectName("categoryColumn")
        column_layout = QVBoxLayout(column)
        column_layout.setContentsMargins(10, 10, 10, 8)
        column_layout.setSpacing(7)
        label = QLabel(title)
        label.setObjectName("categoryManagerTitle")
        column_layout.addWidget(label)
        category_list = QListWidget()
        category_list.setObjectName("categoryManagerList")
        category_list.setTextElideMode(Qt.ElideRight)
        category_list.setContextMenuPolicy(Qt.CustomContextMenu)
        category_list.customContextMenuRequested.connect(
            lambda position, target=category_list: self._open_context_menu(target, position)
        )
        column_layout.addWidget(category_list, stretch=1)
        new_button = QPushButton("+ 新建分类")
        new_button.setObjectName("categoryNewButton")
        new_button.clicked.connect(lambda: self._create(level))
        column_layout.addWidget(new_button)
        if level == 0:
            self.main_column = column
            self.main_new_button = new_button
        elif level == 1:
            self.sub_column = column
            self.sub_new_button = new_button
        else:
            self.sub_sub_column = column
            self.sub_sub_new_button = new_button
        return category_list

    @staticmethod
    def _item_id(item: QListWidgetItem | None) -> int | None:
        if item is None or item.data(Qt.UserRole) is None:
            return None
        return int(item.data(Qt.UserRole))

    def _selected_id(self, level: int) -> int | None:
        return self._item_id((self.main_list, self.sub_list, self.sub_sub_list)[level].currentItem())

    def _rows(self, parent_id: int | None) -> list[dict]:
        return self.owner.display_children_for_directory(parent_id)

    def _fill(
        self,
        target: QListWidget,
        rows: list[dict],
        wanted_id: int | None,
        empty_text: str,
    ) -> None:
        target.blockSignals(True)
        target.clear()
        selected_row = -1
        for index, row in enumerate(rows):
            item = QListWidgetItem(row["name"])
            item.setData(Qt.UserRole, row["id"])
            item.setToolTip(row["name"])
            target.addItem(item)
            if row["id"] == wanted_id:
                selected_row = index
        if not rows:
            empty = QListWidgetItem(empty_text)
            empty.setFlags(Qt.NoItemFlags)
            empty.setData(Qt.UserRole, None)
            target.addItem(empty)
        elif selected_row >= 0:
            target.setCurrentRow(selected_row)
        target.blockSignals(False)

    def refresh(self) -> None:
        main_id = self._selected_id(0)
        sub_id = self._selected_id(1)
        sub_sub_id = self._selected_id(2)
        self.owner.reload_directory_cache()
        self._fill(self.main_list, self._rows(None), main_id, "暂无主分类")
        main_id = self._selected_id(0)
        self._fill(
            self.sub_list,
            self._rows(main_id) if main_id else [],
            sub_id,
            "选择主分类",
        )
        sub_id = self._selected_id(1)
        self._fill(
            self.sub_sub_list,
            self._rows(sub_id) if sub_id else [],
            sub_sub_id,
            "选择子分类",
        )
        self._update_new_actions()

    def _main_changed(self, *_args) -> None:
        main_id = self._selected_id(0)
        self._fill(
            self.sub_list,
            self._rows(main_id) if main_id else [],
            None,
            "选择主分类",
        )
        self._fill(self.sub_sub_list, [], None, "选择子分类")
        self._update_new_actions()

    def _sub_changed(self, *_args) -> None:
        sub_id = self._selected_id(1)
        self._fill(
            self.sub_sub_list,
            self._rows(sub_id) if sub_id else [],
            None,
            "选择子分类",
        )
        self._update_new_actions()

    def _update_new_actions(self) -> None:
        self.main_new_button.setEnabled(True)
        self.sub_new_button.setEnabled(self._selected_id(0) is not None)
        self.sub_sub_new_button.setEnabled(self._selected_id(1) is not None)

    def _create(self, level: int) -> None:
        parent_id = None if level == 0 else self._selected_id(level - 1)
        if level > 0 and parent_id is None:
            return
        self.owner.prompt_create_directory(parent_id)
        self.refresh()

    def _rename_item(self, item: QListWidgetItem) -> None:
        directory_id = self._item_id(item)
        if directory_id is None:
            return
        self.owner.prompt_rename_directory(directory_id)
        self.refresh()

    def _open_context_menu(self, target: QListWidget, position) -> None:
        item = target.itemAt(position)
        directory_id = self._item_id(item)
        if directory_id is None:
            return
        target.setCurrentItem(item)
        menu = QMenu(self)
        rename_action = menu.addAction("重命名")
        delete_action = menu.addAction("删除")
        chosen = menu.exec(target.viewport().mapToGlobal(position))
        if chosen == rename_action:
            self.owner.prompt_rename_directory(directory_id)
            self.refresh()
        elif chosen == delete_action:
            self.owner.confirm_delete_directory(directory_id)
            self.refresh()
