from __future__ import annotations

import sys

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.database import create_note, get_note, list_notes, update_note
from src.theme import APP_STYLE
from src.time_utils import format_display_datetime


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.current_note: dict | None = None
        self.is_loading_note = False
        self.has_unsaved_changes = False
        self.save_timer = QTimer(self)
        self.save_timer.setInterval(1000)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.save_current_note)

        self.setWindowTitle("S-notes")
        self.resize(980, 680)
        self.setCentralWidget(self._build_content())
        self.refresh_note_list()

    def _build_content(self) -> QWidget:
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = self._build_sidebar()
        sidebar.setFixedWidth(260)
        layout.addWidget(sidebar)
        layout.addWidget(self._build_editor(), stretch=1)

        return root

    def _build_sidebar(self) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("S-notes")
        title.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        header.addWidget(title, stretch=1)
        header.addWidget(QLabel("⬇️"))
        header.addWidget(QLabel("🔗"))
        layout.addLayout(header)

        search = QLineEdit()
        search.setPlaceholderText("Search")
        layout.addWidget(search)

        add_button = QPushButton("+")
        add_button.setToolTip("新建笔记")
        add_button.clicked.connect(self.handle_create_note)
        layout.addWidget(add_button)

        self.note_list = QListWidget()
        self.note_list.currentItemChanged.connect(self.handle_note_selection_changed)
        layout.addWidget(self.note_list, stretch=1)

        return sidebar

    def _build_editor(self) -> QWidget:
        editor = QWidget()
        editor.setObjectName("editor")
        layout = QVBoxLayout(editor)
        layout.setContentsMargins(22, 18, 22, 14)
        layout.setSpacing(10)

        self.title_input = QLineEdit()
        self.title_input.setObjectName("titleInput")
        self.title_input.setPlaceholderText("标题")
        self.title_input.textChanged.connect(self.mark_note_dirty)
        layout.addWidget(self.title_input)

        self.metadata_label = QLabel("主分类：- / 子分类：- / 子子分类：- / AI 可见性：-")
        self.metadata_label.setObjectName("metadataLabel")
        layout.addWidget(self.metadata_label)

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

    def refresh_note_list(self, selected_note_id: int | None = None) -> None:
        self.note_list.blockSignals(True)
        self.note_list.clear()
        selected_item = None
        for note in list_notes():
            item = QListWidgetItem(note["title"] or "未命名笔记")
            item.setData(Qt.UserRole, note["id"])
            updated_at = format_display_datetime(note["updated_at"])
            item.setToolTip(f"{note['main_category']} / 更新于 {updated_at}")
            self.note_list.addItem(item)
            if selected_note_id == note["id"]:
                selected_item = item
        if selected_item is not None:
            self.note_list.setCurrentItem(selected_item)
        self.note_list.blockSignals(False)

    def handle_create_note(self) -> None:
        if not self.force_save_current_note(refresh_list=False):
            return
        note = create_note()
        self.refresh_note_list(note["id"])
        self.load_note(note["id"])

    def handle_note_selection_changed(
        self, current: QListWidgetItem | None, previous: QListWidgetItem | None
    ) -> None:
        if current is None:
            return
        note_id = current.data(Qt.UserRole)
        if previous is not None and not self.force_save_current_note(refresh_list=False):
            self.note_list.blockSignals(True)
            self.note_list.setCurrentItem(previous)
            self.note_list.blockSignals(False)
            return
        self.load_note(note_id)
        self.refresh_note_list(note_id)

    def load_note(self, note_id: int) -> None:
        note = get_note(note_id)
        if note is None:
            self.status_label.setText("保存失败：笔记不存在")
            self.refresh_note_list()
            return

        self.is_loading_note = True
        self.current_note = note
        self.title_input.setText(note["title"])
        self.body_edit.setPlainText(note["content_html"])
        self.metadata_label.setText(
            "主分类：{main_category} / 子分类：{sub_category} / "
            "子子分类：{sub_sub_category} / AI 可见性：{ai_visibility}".format(**note)
        )
        self.status_label.setText(
            f"已保存 · 更新于 {format_display_datetime(note['updated_at'])}"
        )
        self.has_unsaved_changes = False
        self.save_timer.stop()
        self.set_editor_enabled(True)
        self.is_loading_note = False

    def set_editor_enabled(self, enabled: bool) -> None:
        self.title_input.setEnabled(enabled)
        self.body_edit.setEnabled(enabled)

    def mark_note_dirty(self) -> None:
        if self.is_loading_note or self.current_note is None:
            return
        self.has_unsaved_changes = True
        self.status_label.setText("未保存")
        self.save_timer.start()

    def force_save_current_note(self, *, refresh_list: bool = True) -> bool:
        self.save_timer.stop()
        return self.save_current_note(refresh_list=refresh_list)

    def save_current_note(self, *, refresh_list: bool = True) -> bool:
        if self.current_note is None or not self.has_unsaved_changes:
            return True

        self.status_label.setText("保存中...")
        QApplication.processEvents()
        try:
            note = update_note(
                self.current_note["id"],
                title=self.title_input.text().strip() or "未命名笔记",
                content_html=self.body_edit.toPlainText(),
            )
        except Exception as error:
            self.status_label.setText(f"保存失败：{error}")
            self.has_unsaved_changes = True
            return False

        self.current_note = note
        self.has_unsaved_changes = False
        self.status_label.setText(
            f"已保存 · 更新于 {format_display_datetime(note['updated_at'])}"
        )
        if refresh_list:
            self.refresh_note_list(note["id"])
        return True

    def closeEvent(self, event) -> None:
        if self.force_save_current_note(refresh_list=False):
            event.accept()
        else:
            event.ignore()


def run_app() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_STYLE)
    window = MainWindow()
    window.show()
    return app.exec()
