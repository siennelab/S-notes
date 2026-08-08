"""Package 2 visual composition with no persistence decisions."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from src.snotes.ui.editor import NotesEditor
from src.snotes.ui.editor_toolbar import EditorToolbar
from src.snotes.ui.sidebar import NoteSidebar


class SnotesShell(QWidget):
    """Own the Package 2 widget tree; MainWindow owns coordination."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("shellRoot")
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = NoteSidebar()
        self.editor_pane = QWidget()
        self.editor_pane.setObjectName("editorPane")
        self.editor_pane.setAttribute(Qt.WA_StyledBackground, True)
        editor_layout = QVBoxLayout(self.editor_pane)
        editor_layout.setContentsMargins(24, 16, 24, 12)
        editor_layout.setSpacing(8)

        self.title_input = QLineEdit()
        self.title_input.setObjectName("titleInput")
        self.title_input.setPlaceholderText("标题")
        editor_layout.addWidget(self.title_input)

        self.body_editor = NotesEditor()
        self.body_editor.setPlaceholderText("写正文")
        self.editor_toolbar = EditorToolbar(self.body_editor)
        editor_layout.addWidget(self.editor_toolbar)
        editor_layout.addWidget(self.body_editor, stretch=1)

        self.empty_label = QLabel("新建或选择一篇笔记开始编辑")
        self.empty_label.setAlignment(Qt.AlignCenter)
        editor_layout.addWidget(self.empty_label, stretch=1)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(8)
        self.status_label = QLabel("未选择笔记")
        self.status_label.setObjectName("statusLabel")
        status_row.addWidget(self.status_label, stretch=1)
        self.reload_button = QPushButton("重新加载")
        self.reload_button.setToolTip("放弃当前未保存内容并加载数据库中的最新版本")
        self.reload_button.setVisible(False)
        status_row.addWidget(self.reload_button)
        editor_layout.addLayout(status_row)

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.setHandleWidth(1)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(self.editor_pane)
        self.splitter.setSizes([280, 1080])
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        layout.addWidget(self.splitter)

    @staticmethod
    def _make_surface_child_transparent(widget: QWidget) -> None:
        palette = QPalette(widget.palette())
        palette.setColor(QPalette.Window, Qt.transparent)
        palette.setColor(QPalette.Base, Qt.transparent)
        widget.setPalette(palette)
        widget.setAutoFillBackground(False)

    def enforce_single_surface_backgrounds(self) -> None:
        """Prevent native child palettes from repainting layout-sized regions."""

        transparent_children = (
            self.sidebar.note_list,
            self.sidebar.note_list.viewport(),
            self.title_input,
            self.editor_toolbar,
            *self.editor_toolbar.toolbar_rows,
            *self.editor_toolbar.toolbar_groups,
            self.editor_toolbar.find_panel,
            self.body_editor,
            self.body_editor.viewport(),
            self.empty_label,
            self.status_label,
        )
        for widget in transparent_children:
            self._make_surface_child_transparent(widget)

    def set_editor_available(self, available: bool) -> None:
        self.title_input.setVisible(available)
        self.editor_toolbar.setVisible(available)
        self.body_editor.setVisible(available)
        self.empty_label.setVisible(not available)
