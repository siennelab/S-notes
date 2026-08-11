"""R1-shaped ordinary navigation for the Package 3 desktop shell."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.snotes.domain.models import Directory, Note, TrashSnapshot
from src.snotes.ui.floating_combo import FloatingComboBox
from src.snotes.ui.sidebar_widgets import (
    DirectoryFilterCombo,
    DirectoryDropButton,
    NOTE_DATE_ROLE,
    NOTE_PINNED_ROLE,
    NOTE_TITLE_ROLE,
    PAYLOAD_ROLE,
    NoteListWidget,
    format_sidebar_clock,
    note_date_width,
    sidebar_clock_time_width,
)
from src.snotes.ui.theme import (
    THEME_LABELS,
    apply_popup_menu_theme,
    current_theme,
    repolish,
    themed_icon,
)

class NoteSidebar(QWidget):
    noteSelected = Signal(int)
    newNoteRequested = Signal()
    themeSelected = Signal(str)
    searchRequested = Signal(str)
    directoryFilterRequested = Signal(object)
    smartViewRequested = Signal(str)
    pinNoteRequested = Signal(int, bool)
    renameNoteRequested = Signal(int)
    deleteNotesRequested = Signal(object)
    moveNotesRequested = Signal(object, int)
    manageDirectoriesRequested = Signal()
    trashRequested = Signal()
    exitTrashRequested = Signal()
    trashNoteSelected = Signal(int)
    restoreTrashRequested = Signal(object, object)
    permanentDeleteTrashRequested = Signal(object, object)
    emptyTrashRequested = Signal()
    aiCenterRequested = Signal()
    aiActorFilterRequested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumWidth(280)
        self.setMaximumWidth(480)
        self.directories: list[Directory] = []
        self._directories_by_id: dict[int, Directory] = {}
        self._children_by_parent: dict[int | None, list[Directory]] = {}
        self._changing_filters = False
        self.mode = "directory"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        self._build_header(layout)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("sidebarSearch")
        self.search_input.setPlaceholderText("搜索笔记或 #标签")
        self.search_input.setFixedHeight(32)
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._search_text_changed)
        layout.addWidget(self.search_input)

        smart_row = QHBoxLayout()
        smart_row.setSpacing(4)
        self.smart_buttons: dict[str, QPushButton] = {}
        for label, name in (("最近", "recent"), ("待办", "todo"), ("AI资料", "ai")):
            button = QPushButton(label)
            button.setObjectName("smartTabButton")
            button.setProperty("active", False)
            button.setCheckable(True)
            button.setFixedHeight(28)
            button.clicked.connect(
                lambda checked=False, value=name: self.smartViewRequested.emit(value)
            )
            smart_row.addWidget(button, stretch=1)
            self.smart_buttons[name] = button
        self.ai_center_button = QPushButton("AI中心")
        self.ai_center_button.setObjectName("smartTabButton")
        self.ai_center_button.setFixedHeight(28)
        self.ai_center_button.setEnabled(False)
        self.ai_center_button.setToolTip("打开 AI 中心")
        self.ai_center_button.clicked.connect(lambda: self.aiCenterRequested.emit())
        smart_row.addWidget(self.ai_center_button, stretch=1)
        layout.addLayout(smart_row)

        self.ai_actor_filter_widget = QWidget()
        self.ai_actor_filter_widget.setObjectName("aiActorFilter")
        actor_layout = QHBoxLayout(self.ai_actor_filter_widget)
        actor_layout.setContentsMargins(0, 0, 0, 0)
        actor_layout.setSpacing(6)
        self.ai_actor_filter_label = QLabel("AI 对象")
        self.ai_actor_filter_label.setObjectName("aiActorFilterLabel")
        self.ai_actor_filter_label.setFixedHeight(28)
        self.ai_actor_filter_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        actor_layout.addWidget(self.ai_actor_filter_label)
        self.ai_actor_filter_combo = FloatingComboBox(surface_role="sidebar")
        self.ai_actor_filter_combo.setObjectName("aiActorFilterCombo")
        self.ai_actor_filter_combo.setFixedHeight(28)
        self.ai_actor_filter_combo.setMinimumWidth(0)
        self.ai_actor_filter_combo.configure_popup(170)
        self.ai_actor_filter_combo.currentIndexChanged.connect(
            self._ai_actor_filter_changed
        )
        actor_layout.addWidget(self.ai_actor_filter_combo, stretch=1)
        layout.addWidget(self.ai_actor_filter_widget)
        self.ai_actor_filter_widget.setVisible(False)

        self._build_directory_filters(layout)
        self._build_content_stack(layout)
        self._build_footer(layout)
        self._update_clock()
        self._update_clock_metrics()
        self.clock_timer = QTimer(self)
        self.clock_timer.setInterval(1000)
        self.clock_timer.timeout.connect(self._update_clock)
        self.clock_timer.start()

    def set_ai_center_available(self, available: bool) -> None:
        self.ai_center_button.setEnabled(available)
        self.ai_center_button.setToolTip(
            "打开 AI 中心" if available else "AI 中心当前不可用"
        )

    def set_ai_center_pending_count(self, pending_count: int) -> None:
        count = int(pending_count)
        self.ai_center_button.setText(
            f"AI中心 ({count})" if count else "AI中心"
        )

    def configure_ai_actor_filters(
        self,
        targets: Sequence[tuple[str, str]],
    ) -> None:
        self.ai_actor_filter_combo.blockSignals(True)
        self.ai_actor_filter_combo.clear()
        self.ai_actor_filter_combo.addItem("全部", None)
        for target_id, display_name in targets:
            self.ai_actor_filter_combo.addItem(display_name, target_id)
        self.ai_actor_filter_combo.setCurrentIndex(0)
        self.ai_actor_filter_combo.blockSignals(False)

    def selected_ai_actor_id(self) -> str | None:
        value = self.ai_actor_filter_combo.currentData()
        return str(value) if value is not None else None
    def _build_header(self, layout: QVBoxLayout) -> None:
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        self.clock_widget = QWidget()
        self.clock_widget.setObjectName("sidebarClock")
        self.clock_widget.setFixedHeight(32)
        clock_layout = QHBoxLayout(self.clock_widget)
        clock_layout.setContentsMargins(0, 0, 0, 0)
        clock_layout.setSpacing(4)
        self.clock_time_label = QLabel()
        self.clock_time_label.setObjectName("sidebarClockTime")
        self.clock_time_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.clock_time_label.setFixedHeight(32)
        self.clock_date_label = QLabel()
        self.clock_date_label.setObjectName("sidebarClockDate")
        self.clock_date_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.clock_date_label.setFixedHeight(32)
        clock_layout.addWidget(self.clock_time_label)
        clock_layout.addWidget(self.clock_date_label)
        header.addWidget(self.clock_widget)
        header.addStretch(1)
        self.new_note_button = QPushButton()
        self.new_note_button.setObjectName("newNoteButton")
        self.new_note_button.setToolTip("新建笔记")
        self.new_note_button.setFixedSize(28, 28)
        self.new_note_button.setIconSize(QSize(14, 14))
        self.new_note_button.clicked.connect(self.newNoteRequested.emit)
        header.addWidget(self.new_note_button)
        layout.addLayout(header)
    def _build_directory_filters(self, layout: QVBoxLayout) -> None:
        filters = QHBoxLayout()
        filters.setSpacing(4)
        self.main_filter_combo = DirectoryFilterCombo()
        self.sub_filter_combo = DirectoryFilterCombo()
        for combo in self._filter_combos:
            combo.setFixedHeight(32)
            combo.setMinimumWidth(0)
            combo.configure_popup(160)
            combo.notesDropped.connect(self.moveNotesRequested)
        self.category_manager_button = DirectoryDropButton("管理")
        self.category_manager_button.setObjectName("categoryManagerButton")
        self.category_manager_button.setToolTip("分类管理")
        self.category_manager_button.setFixedSize(48, 32)
        self.category_manager_button.clicked.connect(self.manageDirectoriesRequested.emit)
        self.category_manager_button.notesDropped.connect(self.moveNotesRequested)
        filters.addWidget(self.main_filter_combo, stretch=1)
        filters.addWidget(self.sub_filter_combo, stretch=1)
        filters.addWidget(self.category_manager_button)
        self.main_filter_combo.currentIndexChanged.connect(self._main_filter_changed)
        self.sub_filter_combo.currentIndexChanged.connect(self._sub_filter_changed)
        layout.addLayout(filters)
    def _build_content_stack(self, layout: QVBoxLayout) -> None:
        self.note_list = NoteListWidget()
        self.note_list.currentItemChanged.connect(self._emit_selected_note)
        self.note_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.note_list.customContextMenuRequested.connect(self._open_note_context_menu)
        self.trash_tree = QTreeWidget()
        self.trash_tree.setObjectName("trashTree")
        self.trash_tree.setHeaderHidden(True)
        self.trash_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.trash_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.trash_tree.itemClicked.connect(self._trash_item_clicked)
        self.trash_tree.customContextMenuRequested.connect(self._open_trash_context_menu)
        self.sidebar_stack = QStackedWidget()
        self.sidebar_stack.addWidget(self.note_list)
        self.sidebar_stack.addWidget(self.trash_tree)
        layout.addWidget(self.sidebar_stack, stretch=1)
    def _build_footer(self, layout: QVBoxLayout) -> None:
        footer = QHBoxLayout()
        footer.setSpacing(4)
        self.trash_button = QPushButton()
        self.trash_button.setObjectName("trashButton")
        self.trash_button.setFixedSize(28, 28)
        self.trash_button.setIconSize(QSize(14, 14))
        self.trash_button.setToolTip("回收站")
        self.trash_button.clicked.connect(self._toggle_trash)
        self.trash_button.setContextMenuPolicy(Qt.CustomContextMenu)
        self.trash_button.customContextMenuRequested.connect(self._open_trash_root_menu)
        footer.addWidget(self.trash_button)
        footer.addStretch(1)
        self.theme_buttons: dict[str, QPushButton] = {}
        for name, label in THEME_LABELS:
            button = QPushButton()
            button.setObjectName("themeSwatch")
            button.setProperty("themeName", name)
            button.setProperty("active", False)
            button.setFixedSize(14, 14)
            button.setToolTip(label)
            button.setAccessibleName(label)
            button.clicked.connect(
                lambda checked=False, selected=name: self.themeSelected.emit(selected)
            )
            footer.addWidget(button, alignment=Qt.AlignVCenter)
            self.theme_buttons[name] = button
        layout.addLayout(footer)
    @property
    def _filter_combos(self) -> tuple[DirectoryFilterCombo, ...]:
        return self.main_filter_combo, self.sub_filter_combo

    def set_directories(
        self,
        directories: list[Directory],
        selected_path: tuple[str, ...] = (),
    ) -> None:
        self.directories = [
            row for row in directories if len(self._path_in_rows(row, directories)) <= 2
        ]
        self._directories_by_id = {row.id: row for row in self.directories}
        self._children_by_parent = {}
        for row in self.directories:
            self._children_by_parent.setdefault(row.parent_id, []).append(row)
        for rows in self._children_by_parent.values():
            rows.sort(key=lambda row: (row.sort_order, row.name.casefold(), row.id))
        self.category_manager_button.set_targets(
            [(" / ".join(self._directory_path(row.id)), row.id) for row in self.directories]
        )
        self._changing_filters = True
        try:
            self._fill_combo(
                self.main_filter_combo,
                self._children_by_parent.get(None, []),
                "全部",
            )
            self._select_combo_name(self.main_filter_combo, selected_path[0] if selected_path else "")
            self._populate_sub_filters(selected_path)
        finally:
            self._changing_filters = False

    def set_notes(self, notes: list[Note], selected_note_id: int | None) -> None:
        self.mode = "directory" if self.mode == "trash" else self.mode
        self.sidebar_stack.setCurrentWidget(self.note_list)
        self.note_list.blockSignals(True)
        self.note_list.clear()
        selected_item: QListWidgetItem | None = None
        for note in notes:
            title = note.title.strip() or "未命名笔记"
            date = note.updated_at[5:10] if len(note.updated_at) >= 10 else ""
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, note.id)
            item.setData(PAYLOAD_ROLE, {"type": "note", "id": note.id, "pinned": note.is_pinned})
            item.setData(NOTE_TITLE_ROLE, title)
            item.setData(NOTE_DATE_ROLE, date)
            item.setData(NOTE_PINNED_ROLE, note.is_pinned)
            item.setToolTip(title)
            item.setFlags(item.flags() | Qt.ItemIsDragEnabled)
            self.note_list.addItem(item)
            if note.id == selected_note_id:
                selected_item = item
        if selected_item is not None:
            self.note_list.setCurrentItem(selected_item)
            self.note_list.scrollToItem(selected_item)
        self.note_list.blockSignals(False)
        self._update_mode_states()

    def show_flat(self, notes: list[Note], *, mode: str, selected_note_id: int | None) -> None:
        self.mode = mode
        self.set_notes(notes, selected_note_id)
        self.mode = mode
        self._update_mode_states()

    def show_trash(self, snapshot: TrashSnapshot, paths: dict[int, tuple[str, ...]]) -> None:
        self.mode = "trash"
        self.sidebar_stack.setCurrentWidget(self.trash_tree)
        self.trash_tree.clear()
        directory_items: dict[int, QTreeWidgetItem] = {}
        for directory in sorted(snapshot.directories, key=lambda row: (len(paths[row.id]), row.name)):
            item = QTreeWidgetItem([directory.name])
            item.setData(0, PAYLOAD_ROLE, {"type": "deleted_directory", "id": directory.id})
            directory_items[directory.id] = item
            parent = directory_items.get(directory.parent_id)
            (parent.addChild if parent is not None else self.trash_tree.addTopLevelItem)(item)
        for note in snapshot.notes:
            item = QTreeWidgetItem([note.title or "未命名笔记"])
            item.setData(0, PAYLOAD_ROLE, {"type": "deleted_note", "id": note.id})
            note_path = note.location.values()
            parent = next(
                (
                    directory_items[row.id]
                    for candidate in (note_path[:3], note_path[:2], note_path[:1])
                    for row in snapshot.directories
                    if row.trash_group_id == note.trash_group_id and paths[row.id] == candidate
                ),
                None,
            )
            (parent.addChild if parent is not None else self.trash_tree.addTopLevelItem)(item)
        if not snapshot.notes and not snapshot.directories:
            empty = QTreeWidgetItem(["回收站为空"])
            empty.setFlags(Qt.ItemIsEnabled)
            self.trash_tree.addTopLevelItem(empty)
        self.trash_tree.expandAll()
        self._update_mode_states()

    def selected_note_ids(self) -> list[int]:
        return self.note_list.selected_note_ids() if self.mode != "trash" else []

    def selected_directory_ids(self) -> list[int]:
        directory_id = self.current_directory_id()
        return [directory_id] if directory_id is not None else []

    def current_directory_id(self) -> int | None:
        for combo in reversed(self._filter_combos):
            if combo.currentData() is not None:
                return int(combo.currentData())
        return None

    def current_directory_path(self) -> tuple[str, ...]:
        directory_id = self.current_directory_id()
        return self._directory_path(directory_id) if directory_id is not None else ()

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
            size = 16 if theme_name == name else 14
            button.setFixedSize(size, size)
            repolish(button)
        self.new_note_button.setIcon(themed_icon("plus.svg", "accent_text"))
        self.trash_button.setIcon(themed_icon("archive-box.svg", "sidebar_text_muted"))
        self._update_clock_metrics()
        self._apply_sidebar_palette()

    def _apply_sidebar_palette(self) -> None:
        tokens = current_theme()
        palette = QPalette(self.palette())
        palette.setColor(QPalette.Window, tokens.sidebar)
        palette.setColor(QPalette.Base, Qt.transparent)
        palette.setColor(QPalette.Text, tokens.sidebar_text_muted)
        palette.setColor(QPalette.WindowText, tokens.sidebar_text)
        palette.setColor(QPalette.ButtonText, tokens.sidebar_text)
        palette.setColor(QPalette.Highlight, tokens.sidebar_selected)
        palette.setColor(QPalette.HighlightedText, tokens.sidebar_text)
        self.setPalette(palette)
        for widget in (self.note_list, self.note_list.viewport(), self.trash_tree, self.trash_tree.viewport()):
            widget.setPalette(palette)
            widget.viewport().update() if hasattr(widget, "viewport") else widget.update()

    def _fill_combo(self, combo: QComboBox, rows: list[Directory], all_label: str) -> None:
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(all_label, None)
        for row in rows:
            combo.addItem(row.name, row.id)
        combo.blockSignals(False)

    @staticmethod
    def _select_combo_name(combo: QComboBox, name: str) -> None:
        index = combo.findText(name) if name else 0
        combo.setCurrentIndex(max(0, index))

    def _populate_sub_filters(self, selected_path: tuple[str, ...] = ()) -> None:
        main_id = self.main_filter_combo.currentData()
        self._fill_combo(
            self.sub_filter_combo,
            self._children_by_parent.get(main_id, []) if main_id is not None else [],
            "-",
        )
        self._select_combo_name(self.sub_filter_combo, selected_path[1] if len(selected_path) > 1 else "")

    def _main_filter_changed(self) -> None:
        if self._changing_filters:
            return
        self._changing_filters = True
        try:
            self._populate_sub_filters()
        finally:
            self._changing_filters = False
        self._emit_directory_filter()

    def _sub_filter_changed(self) -> None:
        if self._changing_filters:
            return
        self._emit_directory_filter()

    def _emit_directory_filter(self) -> None:
        if not self._changing_filters:
            self.directoryFilterRequested.emit(self.current_directory_path())

    def _directory_path(self, directory_id: int) -> tuple[str, ...]:
        names: list[str] = []
        current = self._directories_by_id.get(directory_id)
        while current is not None:
            names.append(current.name)
            current = self._directories_by_id.get(current.parent_id)
        return tuple(reversed(names))

    @staticmethod
    def _path_in_rows(directory: Directory, rows: list[Directory]) -> tuple[str, ...]:
        by_id = {row.id: row for row in rows}
        names = [directory.name]
        parent_id = directory.parent_id
        while parent_id in by_id:
            parent = by_id[parent_id]
            names.append(parent.name)
            parent_id = parent.parent_id
        return tuple(reversed(names))

    def _update_clock(self) -> None:
        time_text, date_text = format_sidebar_clock()
        self.clock_time_label.setText(time_text)
        self.clock_date_label.setText(date_text)

    def _update_clock_metrics(self) -> None:
        self.clock_time_label.ensurePolished()
        self.clock_time_label.setFixedWidth(
            sidebar_clock_time_width(self.clock_time_label.fontMetrics())
        )

    def _search_text_changed(self, value: str) -> None:
        self.searchRequested.emit(value)

    def _ai_actor_filter_changed(self, _index: int = -1) -> None:
        self.aiActorFilterRequested.emit(self.selected_ai_actor_id())

    def clear_search(self) -> None:
        self.search_input.blockSignals(True)
        self.search_input.clear()
        self.search_input.blockSignals(False)

    def _update_mode_states(self) -> None:
        for name, button in self.smart_buttons.items():
            button.setProperty("active", self.mode == "smart_view" and name == getattr(self, "active_smart_view", ""))
            button.setChecked(bool(button.property("active")))
            repolish(button)
        self.ai_actor_filter_widget.setVisible(
            self.mode == "smart_view"
            and getattr(self, "active_smart_view", "") == "ai"
        )

    def set_active_smart_view(self, name: str) -> None:
        self.active_smart_view = name
        self._update_mode_states()

    def _emit_selected_note(self, current: QListWidgetItem | None, _previous) -> None:
        if current is not None:
            self.noteSelected.emit(int(current.data(Qt.UserRole)))

    def _toggle_trash(self) -> None:
        (self.exitTrashRequested if self.mode == "trash" else self.trashRequested).emit()

    def _open_note_context_menu(self, position) -> None:
        item = self.note_list.itemAt(position)
        if item is None:
            return
        if not item.isSelected():
            self.note_list.clearSelection()
            item.setSelected(True)
        note_ids = self.selected_note_ids()
        payload = item.data(PAYLOAD_ROLE) or {}
        menu = QMenu(self)
        apply_popup_menu_theme(menu)
        if len(note_ids) == 1:
            menu.addAction(
                "取消固定" if payload.get("pinned") else "固定笔记",
                lambda: self.pinNoteRequested.emit(int(payload["id"]), not bool(payload.get("pinned"))),
            )
            menu.addAction("重命名", lambda: self.renameNoteRequested.emit(int(payload["id"])))
        move_menu = menu.addMenu("移动到")
        apply_popup_menu_theme(move_menu)
        for row in self.directories:
            move_menu.addAction(
                " / ".join(self._directory_path(row.id)),
                lambda checked=False, target=row.id, ids=note_ids: self.moveNotesRequested.emit(ids, target),
            )
        menu.addAction("移入回收站", lambda: self.deleteNotesRequested.emit(note_ids))
        menu.exec(self.note_list.viewport().mapToGlobal(position))

    def _selected_trash_ids(self) -> tuple[list[int], list[int]]:
        notes: list[int] = []
        directories: list[int] = []
        for item in self.trash_tree.selectedItems():
            payload = item.data(0, PAYLOAD_ROLE) or {}
            if payload.get("type") == "deleted_note":
                notes.append(int(payload["id"]))
            elif payload.get("type") == "deleted_directory":
                directories.append(int(payload["id"]))
        return notes, directories

    def _trash_item_clicked(self, item: QTreeWidgetItem, _column: int = 0) -> None:
        payload = item.data(0, PAYLOAD_ROLE) or {}
        if payload.get("type") == "deleted_note":
            self.trashNoteSelected.emit(int(payload["id"]))
        elif payload.get("type") == "deleted_directory":
            item.setExpanded(not item.isExpanded())

    def _open_trash_context_menu(self, position) -> None:
        item = self.trash_tree.itemAt(position)
        if item is None:
            return
        if not item.isSelected():
            self.trash_tree.clearSelection()
            item.setSelected(True)
        note_ids, directory_ids = self._selected_trash_ids()
        menu = QMenu(self)
        apply_popup_menu_theme(menu)
        menu.addAction("恢复所选", lambda: self.restoreTrashRequested.emit(note_ids, directory_ids))
        menu.addAction("永久删除所选", lambda: self.permanentDeleteTrashRequested.emit(note_ids, directory_ids))
        menu.addSeparator()
        menu.addAction("清空回收站", self.emptyTrashRequested.emit)
        menu.exec(self.trash_tree.viewport().mapToGlobal(position))

    def _open_trash_root_menu(self, position) -> None:
        if self.mode != "trash":
            return
        menu = QMenu(self)
        apply_popup_menu_theme(menu)
        menu.addAction("清空回收站", self.emptyTrashRequested.emit)
        menu.exec(self.trash_button.mapToGlobal(position))
