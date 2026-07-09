from __future__ import annotations

import sys

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.database import (
    DEFAULT_MAIN_CATEGORY,
    DEFAULT_SUB_CATEGORY,
    DEFAULT_SUB_SUB_CATEGORY,
    create_directory,
    create_note_in_directory,
    delete_directory,
    get_note,
    get_note_tags,
    list_directories,
    list_deleted_notes,
    list_notes,
    list_notes_by_tag,
    list_recent_or_common_tags,
    list_trash_tree,
    permanently_delete_note,
    permanently_delete_trash_directory,
    rename_note,
    rename_directory,
    restore_note,
    restore_trash_directory,
    search_notes,
    set_note_pinned,
    set_note_tags,
    soft_delete_note,
    update_note,
)
from src.smart_views import SMART_VIEWS, get_smart_view_notes
from src.tag_manager import format_tags, parse_tags
from src.theme import APP_STYLE
from src.time_utils import format_display_datetime


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.current_note: dict | None = None
        self.is_loading_note = False
        self.has_unsaved_changes = False
        self.category_controls_loading = False
        self.directory_rows: list[dict] = []
        self.children_by_parent: dict[int | None, list[dict]] = {}
        self.current_sidebar_mode = "directory"
        self.current_filter_value = ""
        self.save_timer = QTimer(self)
        self.save_timer.setInterval(1000)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.save_current_note)
        self.search_timer = QTimer(self)
        self.search_timer.setInterval(300)
        self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self.apply_search_filter)

        self.setWindowTitle("S-notes")
        self.resize(980, 680)
        self.setCentralWidget(self._build_content())
        self.refresh_tags()
        self.refresh_directory_tree()

    def _build_content(self) -> QWidget:
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = self._build_sidebar()
        sidebar.setFixedWidth(280)
        layout.addWidget(sidebar)
        layout.addWidget(self._build_editor(), stretch=1)

        return root

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("S-notes")
        title.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        header.addWidget(title, stretch=1)
        header.addWidget(QLabel("⬇️"))
        header.addWidget(QLabel("🔗"))
        layout.addLayout(header)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索标题、正文、标签、目录")
        self.search_input.textChanged.connect(self.handle_search_text_changed)
        layout.addWidget(self.search_input)

        self.tag_section_label = QLabel("标签")
        self.tag_section_label.setObjectName("sectionLabel")
        layout.addWidget(self.tag_section_label)

        self.tag_chip_row = QHBoxLayout()
        self.tag_chip_row.setSpacing(6)
        layout.addLayout(self.tag_chip_row)

        add_button = QPushButton("+")
        add_button.setToolTip("新建笔记")
        add_button.clicked.connect(self.handle_create_note)
        layout.addWidget(add_button)

        self.smart_section_label = QLabel("智能视图")
        self.smart_section_label.setObjectName("sectionLabel")
        layout.addWidget(self.smart_section_label)

        smart_row = QHBoxLayout()
        smart_row.setSpacing(6)
        for label, view_name in SMART_VIEWS:
            button = QPushButton(label)
            button.setObjectName("chipButton")
            button.clicked.connect(lambda checked=False, name=view_name: self.show_smart_view(name))
            smart_row.addWidget(button)
        layout.addLayout(smart_row)

        view_status_row = QHBoxLayout()
        view_status_row.setSpacing(6)
        self.filter_label = QLabel("目录")
        self.filter_label.setObjectName("filterLabel")
        view_status_row.addWidget(self.filter_label, stretch=1)
        self.clear_filter_button = QPushButton("×")
        self.clear_filter_button.setObjectName("clearFilterButton")
        self.clear_filter_button.setToolTip("返回目录")
        self.clear_filter_button.clicked.connect(self.return_to_directory)
        view_status_row.addWidget(self.clear_filter_button)
        layout.addLayout(view_status_row)

        self.directory_tree = QTreeWidget()
        self.directory_tree.setHeaderHidden(True)
        self.directory_tree.itemClicked.connect(self.handle_tree_item_clicked)
        self.directory_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.directory_tree.customContextMenuRequested.connect(
            self.open_directory_context_menu
        )
        layout.addWidget(self.directory_tree, stretch=1)

        self.trash_button = QPushButton("回收站")
        self.trash_button.setObjectName("trashButton")
        self.trash_button.clicked.connect(self.show_trash)
        layout.addWidget(self.trash_button)

        return sidebar

    def _build_editor(self) -> QWidget:
        editor = QWidget()
        editor.setObjectName("editor")
        layout = QVBoxLayout(editor)
        layout.setContentsMargins(28, 24, 28, 18)
        layout.setSpacing(14)

        self.title_input = QLineEdit()
        self.title_input.setObjectName("titleInput")
        self.title_input.setPlaceholderText("标题")
        self.title_input.textChanged.connect(self.mark_note_dirty)
        layout.addWidget(self.title_input)

        metadata_layout = QHBoxLayout()
        metadata_layout.setSpacing(10)
        metadata_layout.addWidget(QLabel("主分类"))
        self.main_category_combo = QComboBox()
        self.main_category_combo.currentTextChanged.connect(
            self.handle_main_category_changed
        )
        metadata_layout.addWidget(self.main_category_combo)
        metadata_layout.addWidget(QLabel("子分类"))
        self.sub_category_combo = QComboBox()
        self.sub_category_combo.currentTextChanged.connect(
            self.handle_sub_category_changed
        )
        metadata_layout.addWidget(self.sub_category_combo)
        metadata_layout.addWidget(QLabel("子子分类"))
        self.sub_sub_category_combo = QComboBox()
        self.sub_sub_category_combo.currentTextChanged.connect(
            self.handle_sub_sub_category_changed
        )
        metadata_layout.addWidget(self.sub_sub_category_combo)
        self.ai_visibility_label = QLabel("AI 可见性：-")
        self.ai_visibility_label.setObjectName("metadataLabel")
        metadata_layout.addWidget(self.ai_visibility_label)
        metadata_layout.addStretch(1)
        layout.addLayout(metadata_layout)

        tags_layout = QHBoxLayout()
        tags_layout.setSpacing(10)
        tags_label = QLabel("标签")
        tags_label.setObjectName("metadataLabel")
        tags_layout.addWidget(tags_label)
        self.tags_input = QLineEdit()
        self.tags_input.setPlaceholderText("todo, vps, ai-ok")
        self.tags_input.textChanged.connect(self.mark_note_dirty)
        tags_layout.addWidget(self.tags_input, stretch=1)
        layout.addLayout(tags_layout)

        self.body_edit = QTextEdit()
        self.body_edit.setPlaceholderText("写正文")
        self.body_edit.textChanged.connect(self.mark_note_dirty)
        layout.addWidget(self.body_edit, stretch=1)

        self.status_label = QLabel("未选择笔记")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self.status_label)

        self.set_editor_enabled(False)
        return editor

    def refresh_tags(self) -> None:
        while self.tag_chip_row.count():
            item = self.tag_chip_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        tags = list_recent_or_common_tags()
        if not tags:
            empty_label = QLabel("暂无")
            empty_label.setObjectName("metadataLabel")
            self.tag_chip_row.addWidget(empty_label)
            self.tag_chip_row.addStretch(1)
            return
        for tag in tags[:8]:
            button = QPushButton(f"#{tag['name']}")
            button.setObjectName("chipButton")
            button.clicked.connect(
                lambda checked=False, tag_name=tag["name"]: self.show_tag_results(tag_name)
            )
            self.tag_chip_row.addWidget(button)
        self.tag_chip_row.addStretch(1)

    def reload_directory_cache(self) -> None:
        self.directory_rows = list_directories()
        self.children_by_parent = {}
        for row in self.directory_rows:
            self.children_by_parent.setdefault(row["parent_id"], []).append(row)
        for rows in self.children_by_parent.values():
            rows.sort(key=lambda item: (item["name"].casefold(), item["id"]))

    def display_children_for_directory(self, directory_id: int | None) -> list[dict]:
        return [
            row
            for row in self.children_by_parent.get(directory_id, [])
            if row["name"] != DEFAULT_SUB_CATEGORY
        ]

    def refresh_directory_tree(self, selected_note_id: int | None = None) -> None:
        self.set_sidebar_mode("directory")
        self.reload_directory_cache()
        notes = list_notes()
        selected_note = None
        if selected_note_id is not None:
            selected_note = next(
                (note for note in notes if note["id"] == selected_note_id), None
            )

        self.directory_tree.blockSignals(True)
        self.directory_tree.clear()
        selected_item = None
        expanded_directory_ids = self.expanded_directory_ids_for_note(selected_note)

        for main_dir in self.display_children_for_directory(None):
            main_item = self.create_directory_item(main_dir)
            self.directory_tree.addTopLevelItem(main_item)
            self.add_directory_children(main_item, main_dir, notes, selected_note_id)
            self.apply_directory_expansion(main_item, expanded_directory_ids)
            if selected_note_id is not None:
                selected_item = selected_item or self.find_note_item(
                    main_item, selected_note_id
                )

        if selected_item is not None:
            self.directory_tree.setCurrentItem(selected_item)
            self.directory_tree.scrollToItem(selected_item)
        self.directory_tree.blockSignals(False)

    def display_flat_notes(
        self,
        title: str,
        notes: list[dict],
        *,
        mode: str,
        value: str = "",
        deleted: bool = False,
        selected_note_id: int | None = None,
    ) -> None:
        self.set_sidebar_mode(mode, value, title)
        self.directory_tree.blockSignals(True)
        self.directory_tree.clear()
        selected_item = None
        for note in notes:
            item = QTreeWidgetItem([note["title"] or "未命名笔记"])
            item.setData(
                0,
                Qt.UserRole,
                {"type": "deleted_note" if deleted else "note", "id": note["id"]},
            )
            updated_at = format_display_datetime(note["updated_at"])
            item.setToolTip(0, f"{note['main_category']} / 更新于 {updated_at}")
            self.directory_tree.addTopLevelItem(item)
            if selected_note_id == note["id"]:
                selected_item = item
        if selected_item is not None:
            self.directory_tree.setCurrentItem(selected_item)
            self.directory_tree.scrollToItem(selected_item)
        self.directory_tree.blockSignals(False)

    def handle_search_text_changed(self) -> None:
        self.search_timer.start()

    def apply_search_filter(self) -> None:
        query = self.search_input.text().strip()
        if not query:
            self.refresh_directory_tree(self.current_note["id"] if self.current_note else None)
            return
        self.display_flat_notes(
            f"搜索：{query}",
            search_notes(query),
            mode="search",
            value=query,
            selected_note_id=self.current_note["id"] if self.current_note else None,
        )

    def show_tag_results(self, tag: str) -> None:
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        self.display_flat_notes(
            f"标签：#{tag}",
            list_notes_by_tag(tag),
            mode="tag",
            value=tag,
            selected_note_id=self.current_note["id"] if self.current_note else None,
        )

    def show_smart_view(self, view_name: str) -> None:
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        title, notes = get_smart_view_notes(view_name)
        self.display_flat_notes(
            f"智能视图：{title}",
            notes,
            mode="smart_view",
            value=view_name,
            selected_note_id=self.current_note["id"] if self.current_note else None,
        )

    def show_trash(self) -> None:
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        self.set_sidebar_mode("trash", label="回收站")
        trash_tree = list_trash_tree()
        self.directory_tree.blockSignals(True)
        self.directory_tree.clear()
        self.populate_trash_tree(trash_tree)
        self.directory_tree.expandAll()
        self.directory_tree.blockSignals(False)

    def populate_trash_tree(self, trash_tree: dict) -> None:
        directories = trash_tree["directories"]
        notes = trash_tree["notes"]
        directory_items: dict[int, QTreeWidgetItem] = {}
        directory_paths = {tuple(directory["path"]): directory for directory in directories}

        for directory in sorted(directories, key=lambda item: (len(item["path"]), item["path"])):
            source = " / ".join(directory["source_path"])
            label = f"📁 {directory['name']}"
            if source:
                label = f"{label} · 来自 {source}"
            item = QTreeWidgetItem([label])
            item.setData(
                0,
                Qt.UserRole,
                {"type": "deleted_directory", "id": directory["id"]},
            )
            directory_items[directory["id"]] = item
            parent_item = directory_items.get(directory["parent_id"])
            if parent_item is not None:
                parent_item.addChild(item)
            else:
                self.directory_tree.addTopLevelItem(item)

        for note in notes:
            note_item = QTreeWidgetItem([f"📝 {note['title'] or '未命名笔记'}"])
            note_item.setData(0, Qt.UserRole, {"type": "deleted_note", "id": note["id"]})
            parent_item = self.trash_parent_item_for_note(
                note, directory_paths, directory_items
            )
            if parent_item is None:
                self.directory_tree.addTopLevelItem(note_item)
            else:
                parent_item.addChild(note_item)

    def trash_parent_item_for_note(
        self,
        note: dict,
        directory_paths: dict[tuple[str, ...], dict],
        directory_items: dict[int, QTreeWidgetItem],
    ) -> QTreeWidgetItem | None:
        raw_path = note.get("path") or []
        candidate_paths = [
            tuple(raw_path[:3]),
            tuple(raw_path[:2]),
            tuple(raw_path[:1]),
        ]
        for path in candidate_paths:
            directory = directory_paths.get(path)
            if directory is not None:
                return directory_items.get(directory["id"])
        return None

    def set_sidebar_mode(
        self, mode: str, value: str = "", label: str | None = None
    ) -> None:
        self.current_sidebar_mode = mode
        self.current_filter_value = value
        self.filter_label.setText(label or "目录")
        self.clear_filter_button.setVisible(mode != "directory")

    def return_to_directory(self) -> None:
        self.search_timer.stop()
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)
        selected_note_id = self.current_note["id"] if self.current_note else None
        if selected_note_id is not None and get_note(selected_note_id) is None:
            selected_note_id = None
        self.refresh_directory_tree(selected_note_id)

    def add_directory_children(
        self,
        parent_item: QTreeWidgetItem,
        parent_dir: dict,
        notes: list[dict],
        selected_note_id: int | None,
    ) -> None:
        path = self.item_path(parent_item)
        for note in self.notes_for_path(notes, path):
            note_item = QTreeWidgetItem([note["title"] or "未命名笔记"])
            note_item.setData(0, Qt.UserRole, {"type": "note", "id": note["id"]})
            updated_at = format_display_datetime(note["updated_at"])
            note_item.setToolTip(0, f"更新于 {updated_at}")
            parent_item.addChild(note_item)

        for child_dir in self.display_children_for_directory(parent_dir["id"]):
            child_item = self.create_directory_item(child_dir)
            parent_item.addChild(child_item)
            self.add_directory_children(child_item, child_dir, notes, selected_note_id)

    def create_directory_item(self, directory: dict) -> QTreeWidgetItem:
        item = QTreeWidgetItem([directory["name"]])
        item.setData(
            0,
            Qt.UserRole,
            {"type": "directory", "id": directory["id"], "parent_id": directory["parent_id"]},
        )
        return item

    def apply_directory_expansion(
        self, item: QTreeWidgetItem, expanded_directory_ids: set[int]
    ) -> None:
        data = item.data(0, Qt.UserRole)
        if data and data.get("type") == "directory":
            item.setExpanded(data["id"] in expanded_directory_ids)
        for index in range(item.childCount()):
            self.apply_directory_expansion(item.child(index), expanded_directory_ids)

    def expanded_directory_ids_for_note(self, note: dict | None) -> set[int]:
        if note is None:
            return set()
        path = [note["main_category"], note["sub_category"], note["sub_sub_category"]]
        expanded_ids: set[int] = set()
        parent_id = None
        for name in path:
            directory = self.find_directory_by_name(name, parent_id)
            if directory is None:
                break
            expanded_ids.add(directory["id"])
            parent_id = directory["id"]
        return expanded_ids

    def find_note_item(
        self, item: QTreeWidgetItem, note_id: int
    ) -> QTreeWidgetItem | None:
        data = item.data(0, Qt.UserRole)
        if data and data.get("type") == "note" and data["id"] == note_id:
            return item
        for index in range(item.childCount()):
            found = self.find_note_item(item.child(index), note_id)
            if found is not None:
                return found
        return None

    def notes_for_path(self, notes: list[dict], path: tuple[str, ...]) -> list[dict]:
        if len(path) == 1:
            return [
                note
                for note in notes
                if note["main_category"] == path[0]
                and note["sub_category"] == DEFAULT_SUB_CATEGORY
                and note["sub_sub_category"] == DEFAULT_SUB_SUB_CATEGORY
            ]
        if len(path) == 2:
            return [
                note
                for note in notes
                if note["main_category"] == path[0]
                and note["sub_category"] == path[1]
                and note["sub_sub_category"] == DEFAULT_SUB_SUB_CATEGORY
            ]
        if len(path) != 3:
            return []
        return [
            note
            for note in notes
            if (
                note["main_category"],
                note["sub_category"],
                note["sub_sub_category"],
            )
            == path
        ]

    def item_path(self, item: QTreeWidgetItem) -> tuple[str, ...]:
        names = []
        current = item
        while current is not None:
            names.append(current.text(0))
            current = current.parent()
        return tuple(reversed(names))

    def handle_tree_item_clicked(
        self, item: QTreeWidgetItem, column: int = 0
    ) -> None:
        data = item.data(0, Qt.UserRole)
        if not data:
            return
        if data["type"] == "directory":
            item.setExpanded(not item.isExpanded())
            return
        if data["type"] == "note":
            if not self.force_save_current_note(refresh_tree=False):
                return
            self.load_note(data["id"])
            self.refresh_current_sidebar_view(data["id"])
        if data["type"] == "deleted_note":
            if not self.force_save_current_note(refresh_tree=False):
                return
            self.load_deleted_note(data["id"])
        if data["type"] == "deleted_directory":
            item.setExpanded(not item.isExpanded())

    def handle_create_note(self) -> None:
        if not self.force_save_current_note(refresh_tree=False):
            return
        note = create_note_in_directory(*self.selected_note_target_path())
        self.load_note(note["id"])
        self.refresh_directory_tree(note["id"])

    def selected_note_target_path(self) -> tuple[str, str, str]:
        item = self.directory_tree.currentItem()
        if item is None:
            return (
                DEFAULT_MAIN_CATEGORY,
                DEFAULT_SUB_CATEGORY,
                DEFAULT_SUB_SUB_CATEGORY,
            )
        data = item.data(0, Qt.UserRole)
        if data and data.get("type") == "note":
            note = get_note(data["id"])
            if note is not None:
                return (
                    note["main_category"],
                    note["sub_category"],
                    note["sub_sub_category"],
                )
        path = self.item_path(item)
        return self.category_tuple_for_path(path)

    def category_tuple_for_path(self, path: tuple[str, ...]) -> tuple[str, str, str]:
        if len(path) == 1:
            return (path[0], DEFAULT_SUB_CATEGORY, DEFAULT_SUB_SUB_CATEGORY)
        if len(path) == 2:
            return (path[0], path[1], DEFAULT_SUB_SUB_CATEGORY)
        if len(path) >= 3:
            return (path[0], path[1], path[2])
        return (
            DEFAULT_MAIN_CATEGORY,
            DEFAULT_SUB_CATEGORY,
            DEFAULT_SUB_SUB_CATEGORY,
        )

    def load_note(self, note_id: int) -> None:
        note = get_note(note_id)
        if note is None:
            self.status_label.setText("保存失败：笔记不存在")
            self.refresh_directory_tree()
            return

        self.is_loading_note = True
        self.current_note = note
        self.title_input.setText(note["title"])
        self.body_edit.setPlainText(note["content_html"])
        self.populate_category_controls(note)
        self.tags_input.setText(format_tags(get_note_tags(note_id)))
        self.ai_visibility_label.setText(f"AI 可见性：{note['ai_visibility']}")
        self.status_label.setText(
            f"已保存 · 更新于 {format_display_datetime(note['updated_at'])}"
        )
        self.has_unsaved_changes = False
        self.save_timer.stop()
        self.set_editor_enabled(True)
        self.is_loading_note = False

    def load_deleted_note(self, note_id: int) -> None:
        note = get_note(note_id, include_deleted=True)
        if note is None:
            self.show_trash()
            return
        self.is_loading_note = True
        self.current_note = None
        self.title_input.setText(note["title"])
        self.body_edit.setPlainText(note["content_html"])
        self.populate_category_controls(note)
        self.tags_input.setText(format_tags(get_note_tags(note_id)))
        self.ai_visibility_label.setText(f"AI 可见性：{note['ai_visibility']}")
        self.status_label.setText("回收站中 · 只读")
        self.has_unsaved_changes = False
        self.save_timer.stop()
        self.set_editor_enabled(False)
        self.is_loading_note = False

    def populate_category_controls(self, note: dict) -> None:
        self.reload_directory_cache()
        self.category_controls_loading = True
        self.set_combo_items(
            self.main_category_combo,
            self.directory_names(None),
            note["main_category"],
        )
        main_id = self.directory_id_for_path((self.main_category_combo.currentText(),))
        self.set_combo_items(
            self.sub_category_combo,
            [DEFAULT_SUB_CATEGORY, *self.directory_names(main_id)],
            note["sub_category"],
        )
        sub_id = None
        if self.sub_category_combo.currentText() != DEFAULT_SUB_CATEGORY:
            sub_id = self.directory_id_for_path(
                (
                    self.main_category_combo.currentText(),
                    self.sub_category_combo.currentText(),
                )
            )
        sub_sub_names = [DEFAULT_SUB_SUB_CATEGORY]
        if sub_id is not None:
            sub_sub_names.extend(self.directory_names(sub_id))
        self.set_combo_items(
            self.sub_sub_category_combo,
            sub_sub_names,
            note["sub_sub_category"],
        )
        self.category_controls_loading = False

    def set_combo_items(
        self, combo: QComboBox, values: list[str], current_value: str
    ) -> None:
        combo.blockSignals(True)
        combo.clear()
        options = list(values)
        if current_value and current_value not in options:
            options.append(current_value)
            options.sort(key=str.casefold)
        combo.addItems(options)
        index = combo.findText(current_value)
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def directory_names(self, parent_id: int | None) -> list[str]:
        return [
            row["name"]
            for row in self.display_children_for_directory(parent_id)
            if row["name"] != DEFAULT_SUB_CATEGORY
        ]

    def directory_id_for_path(self, path: tuple[str, ...]) -> int | None:
        parent_id = None
        current_id = None
        for name in path:
            directory = self.find_directory_by_name(name, parent_id)
            if directory is None:
                return None
            current_id = directory["id"]
            parent_id = current_id
        return current_id

    def find_directory_by_name(
        self, name: str, parent_id: int | None
    ) -> dict | None:
        for row in self.children_by_parent.get(parent_id, []):
            if row["name"] == name:
                return row
        return None

    def handle_main_category_changed(self) -> None:
        if self.category_controls_loading or self.current_note is None:
            return
        self.category_controls_loading = True
        main_id = self.directory_id_for_path((self.main_category_combo.currentText(),))
        sub_names = [DEFAULT_SUB_CATEGORY, *self.directory_names(main_id)]
        self.set_combo_items(self.sub_category_combo, sub_names, DEFAULT_SUB_CATEGORY)
        sub_sub_names = [DEFAULT_SUB_SUB_CATEGORY]
        self.set_combo_items(
            self.sub_sub_category_combo, sub_sub_names, DEFAULT_SUB_SUB_CATEGORY
        )
        self.category_controls_loading = False
        self.mark_note_dirty()

    def handle_sub_category_changed(self) -> None:
        if self.category_controls_loading or self.current_note is None:
            return
        self.category_controls_loading = True
        sub_id = None
        if self.sub_category_combo.currentText() != DEFAULT_SUB_CATEGORY:
            sub_id = self.directory_id_for_path(
                (
                    self.main_category_combo.currentText(),
                    self.sub_category_combo.currentText(),
                )
            )
        sub_sub_names = [DEFAULT_SUB_SUB_CATEGORY, *self.directory_names(sub_id)]
        self.set_combo_items(
            self.sub_sub_category_combo, sub_sub_names, DEFAULT_SUB_SUB_CATEGORY
        )
        self.category_controls_loading = False
        self.mark_note_dirty()

    def handle_sub_sub_category_changed(self) -> None:
        if self.category_controls_loading or self.current_note is None:
            return
        self.mark_note_dirty()

    def set_editor_enabled(self, enabled: bool) -> None:
        self.title_input.setEnabled(enabled)
        self.main_category_combo.setEnabled(enabled)
        self.sub_category_combo.setEnabled(enabled)
        self.sub_sub_category_combo.setEnabled(enabled)
        self.tags_input.setEnabled(enabled)
        self.body_edit.setEnabled(enabled)

    def mark_note_dirty(self) -> None:
        if self.is_loading_note or self.current_note is None:
            return
        self.has_unsaved_changes = True
        self.status_label.setText("未保存")
        self.save_timer.start()

    def force_save_current_note(self, *, refresh_tree: bool = True) -> bool:
        self.save_timer.stop()
        return self.save_current_note(refresh_tree=refresh_tree)

    def save_current_note(self, *, refresh_tree: bool = True) -> bool:
        if self.current_note is None or not self.has_unsaved_changes:
            return True

        self.status_label.setText("保存中...")
        QApplication.processEvents()
        try:
            note = update_note(
                self.current_note["id"],
                title=self.title_input.text().strip() or "未命名笔记",
                content_html=self.body_edit.toPlainText(),
                main_category=self.main_category_combo.currentText()
                or DEFAULT_MAIN_CATEGORY,
                sub_category=self.sub_category_combo.currentText() or DEFAULT_SUB_CATEGORY,
                sub_sub_category=self.sub_sub_category_combo.currentText()
                or DEFAULT_SUB_SUB_CATEGORY,
            )
            set_note_tags(note["id"], parse_tags(self.tags_input.text()))
            note = get_note(note["id"])
            if note is None:
                raise RuntimeError("保存后读取失败")
        except Exception as error:
            self.status_label.setText(f"保存失败：{error}")
            self.has_unsaved_changes = True
            return False

        self.current_note = note
        self.has_unsaved_changes = False
        self.status_label.setText(
            f"已保存 · 更新于 {format_display_datetime(note['updated_at'])}"
        )
        if refresh_tree:
            self.refresh_current_sidebar_view(note["id"])
        self.refresh_tags()
        return True

    def refresh_current_sidebar_view(self, selected_note_id: int | None = None) -> None:
        if self.current_sidebar_mode == "search":
            self.apply_search_filter()
        elif self.current_sidebar_mode == "tag":
            self.show_tag_results(self.current_filter_value)
        elif self.current_sidebar_mode == "smart_view":
            self.show_smart_view(self.current_filter_value)
        elif self.current_sidebar_mode == "trash":
            self.show_trash()
        else:
            self.refresh_directory_tree(selected_note_id)

    def open_directory_context_menu(self, position) -> None:
        item = self.directory_tree.itemAt(position)
        data = item.data(0, Qt.UserRole) if item is not None else None
        menu = QMenu(self)

        if not data:
            menu.addAction("新增主分类", lambda: self.prompt_create_directory(None))
        elif data.get("type") == "note":
            note_id = data["id"]
            note = get_note(note_id)
            if note and note["is_pinned"]:
                menu.addAction("取消固定", lambda: self.toggle_note_pinned(note_id, False))
            else:
                menu.addAction("固定笔记", lambda: self.toggle_note_pinned(note_id, True))
            menu.addAction("重命名笔记", lambda: self.prompt_rename_note(note_id))
            menu.addAction("删除笔记", lambda: self.confirm_delete_note(note_id))
        elif data.get("type") == "deleted_note":
            note_id = data["id"]
            menu.addAction("恢复笔记", lambda: self.restore_deleted_note(note_id))
            menu.addAction("永久删除笔记", lambda: self.confirm_permanent_delete_note(note_id))
        elif data.get("type") == "deleted_directory":
            directory_id = data["id"]
            menu.addAction("恢复目录", lambda: self.restore_deleted_directory(directory_id))
            menu.addAction(
                "永久删除目录",
                lambda: self.confirm_permanent_delete_directory(directory_id),
            )
        elif data.get("type") == "directory":
            directory_id = data["id"]
            depth = len(self.item_path(item))
            menu.addAction(
                "新增笔记",
                lambda: self.create_note_at_path(self.item_path(item)),
            )
            if depth == 1:
                menu.addAction(
                    "新增子分类", lambda: self.prompt_create_directory(directory_id)
                )
            elif depth == 2:
                menu.addAction(
                    "新增子子分类", lambda: self.prompt_create_directory(directory_id)
                )
            menu.addAction("重命名", lambda: self.prompt_rename_directory(directory_id))
            menu.addAction("删除", lambda: self.confirm_delete_directory(directory_id))

        menu.exec(self.directory_tree.viewport().mapToGlobal(position))

    def create_note_at_path(self, path: tuple[str, ...]) -> None:
        if not self.force_save_current_note(refresh_tree=False):
            return
        note = create_note_in_directory(*self.category_tuple_for_path(path))
        self.load_note(note["id"])
        self.refresh_directory_tree(note["id"])

    def prompt_create_directory(self, parent_id: int | None) -> None:
        name, accepted = QInputDialog.getText(self, "新增目录", "目录名称")
        if not accepted:
            return
        try:
            create_directory(name, parent_id)
        except Exception as error:
            QMessageBox.warning(self, "新增目录失败", str(error))
            return
        self.refresh_after_directory_change()

    def prompt_rename_directory(self, directory_id: int) -> None:
        current_name = self.directory_name_by_id(directory_id)
        name, accepted = QInputDialog.getText(
            self, "重命名目录", "目录名称", text=current_name
        )
        if not accepted:
            return
        try:
            rename_directory(directory_id, name)
        except Exception as error:
            QMessageBox.warning(self, "重命名目录失败", str(error))
            return
        self.reload_current_note()
        self.refresh_after_directory_change()

    def prompt_rename_note(self, note_id: int) -> None:
        note = get_note(note_id)
        if note is None:
            self.refresh_directory_tree()
            return
        title, accepted = QInputDialog.getText(
            self, "重命名笔记", "笔记标题", text=note["title"]
        )
        if not accepted:
            return
        try:
            renamed = rename_note(note_id, title)
        except Exception as error:
            QMessageBox.warning(self, "重命名笔记失败", str(error))
            return
        if self.current_note and self.current_note["id"] == note_id:
            self.load_note(note_id)
        else:
            self.current_note = renamed if self.current_note is None else self.current_note
        self.refresh_directory_tree(note_id)

    def toggle_note_pinned(self, note_id: int, pinned: bool) -> None:
        try:
            note = set_note_pinned(note_id, pinned)
        except Exception as error:
            QMessageBox.warning(self, "固定笔记失败", str(error))
            return
        if self.current_note and self.current_note["id"] == note_id:
            self.current_note = note
        self.refresh_current_sidebar_view(note_id)

    def confirm_delete_note(self, note_id: int) -> None:
        note = get_note(note_id)
        if note is None:
            self.refresh_directory_tree()
            return
        if not self.confirm_continue(
            "删除笔记",
            f"删除“{note['title']}”后，该笔记会移入回收站。是否继续？",
        ):
            return
        try:
            soft_delete_note(note_id)
        except Exception as error:
            QMessageBox.warning(self, "删除笔记失败", str(error))
            return
        if self.current_note and self.current_note["id"] == note_id:
            self.clear_editor()
        self.refresh_current_sidebar_view()

    def restore_deleted_note(self, note_id: int) -> None:
        try:
            note = restore_note(note_id)
        except Exception as error:
            QMessageBox.warning(self, "恢复笔记失败", str(error))
            return
        self.load_note(note["id"])
        self.refresh_tags()
        self.refresh_directory_tree(note["id"])

    def restore_deleted_directory(self, directory_id: int) -> None:
        try:
            restore_trash_directory(directory_id)
        except Exception as error:
            QMessageBox.warning(self, "恢复目录失败", str(error))
            return
        self.refresh_tags()
        self.show_trash()

    def confirm_permanent_delete_note(self, note_id: int) -> None:
        note = get_note(note_id, include_deleted=True)
        if note is None:
            self.show_trash()
            return
        if not self.confirm_continue(
            "永久删除笔记",
            f"将永久删除该笔记。此操作不可恢复。是否继续？",
        ):
            return
        try:
            permanently_delete_note(note_id)
        except Exception as error:
            QMessageBox.warning(self, "永久删除失败", str(error))
            return
        self.clear_editor()
        self.show_trash()

    def confirm_permanent_delete_directory(self, directory_id: int) -> None:
        if not self.confirm_continue(
            "永久删除目录",
            "将永久删除该目录及其所有子目录和笔记。此操作不可恢复。是否继续？",
        ):
            return
        try:
            permanently_delete_trash_directory(directory_id)
        except Exception as error:
            QMessageBox.warning(self, "永久删除目录失败", str(error))
            return
        self.clear_editor()
        self.show_trash()

    def confirm_delete_directory(self, directory_id: int) -> None:
        current_name = self.directory_name_by_id(directory_id)
        if not self.confirm_continue(
            "删除目录",
            f"删除“{current_name}”后，该目录及其所有子目录、笔记都会移入回收站。是否继续？",
        ):
            return
        try:
            delete_directory(directory_id)
        except Exception as error:
            QMessageBox.warning(self, "删除目录失败", str(error))
            return
        self.reload_current_note()
        self.refresh_after_directory_change()

    def confirm_continue(self, title: str, text: str) -> bool:
        message_box = QMessageBox(self)
        message_box.setWindowTitle(title)
        message_box.setText(text)
        continue_button = message_box.addButton("继续", QMessageBox.AcceptRole)
        message_box.addButton("取消", QMessageBox.RejectRole)
        message_box.setDefaultButton(continue_button)
        message_box.exec()
        return message_box.clickedButton() == continue_button

    def clear_editor(self) -> None:
        self.is_loading_note = True
        self.current_note = None
        self.title_input.clear()
        self.body_edit.clear()
        self.main_category_combo.clear()
        self.sub_category_combo.clear()
        self.sub_sub_category_combo.clear()
        self.ai_visibility_label.setText("AI 可见性：-")
        self.status_label.setText("未选择笔记")
        self.has_unsaved_changes = False
        self.save_timer.stop()
        self.set_editor_enabled(False)
        self.is_loading_note = False

    def refresh_after_directory_change(self) -> None:
        selected_note_id = self.current_note["id"] if self.current_note else None
        if selected_note_id is not None:
            if get_note(selected_note_id) is None:
                self.clear_editor()
                selected_note_id = None
            else:
                self.load_note(selected_note_id)
        self.refresh_directory_tree(selected_note_id)

    def reload_current_note(self) -> None:
        if self.current_note is None:
            return
        note = get_note(self.current_note["id"])
        if note is not None:
            self.current_note = note

    def directory_name_by_id(self, directory_id: int) -> str:
        self.reload_directory_cache()
        for row in self.directory_rows:
            if row["id"] == directory_id:
                return row["name"]
        return ""

    def closeEvent(self, event) -> None:
        if self.force_save_current_note(refresh_tree=False):
            event.accept()
        else:
            event.ignore()


def run_app() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_STYLE)
    window = MainWindow()
    window.show()
    return app.exec()
