from __future__ import annotations

from PySide6.QtCore import QSize, Qt

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.editor import NotesEditor
from src.theme import themed_icon


FONT_SIZE_OPTIONS = (
    ("小字", 11),
    ("正文大小", 15),
    ("中大字", 20),
    ("大字", 26),
)


class EditorToolbar(QWidget):
    def __init__(self, editor: NotesEditor, parent=None) -> None:
        super().__init__(parent)
        self.editor = editor
        self.icon_buttons: list[QPushButton | QToolButton] = []
        self.setObjectName("editorToolbar")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.toolbar_rows: list[QWidget] = []
        self.toolbar_row_layouts: list[QHBoxLayout] = []
        for _index in range(5):
            row = QWidget()
            row.setObjectName("toolbarRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(4)
            row.setVisible(False)
            layout.addWidget(row)
            self.toolbar_rows.append(row)
            self.toolbar_row_layouts.append(row_layout)

        history_group, history_layout = self._group("history")
        self.undo_button = self._button(
            history_layout, "", "撤销", editor.undo, "undo.svg"
        )
        self.redo_button = self._button(
            history_layout, "", "重做", editor.redo, "redo.svg"
        )

        appearance_group, appearance_layout = self._group("appearance")
        self.font_size_button = QToolButton()
        self.font_size_button.setText("字号")
        self.font_size_button.setObjectName("toolbarButton")
        self.font_size_button.setToolTip("文字大小")
        self.font_size_button.setFixedSize(37, 29)
        self.font_size_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.font_size_button.setMenu(self._build_font_size_menu())
        self.font_size_button.setPopupMode(QToolButton.InstantPopup)
        appearance_layout.addWidget(self.font_size_button)
        self.bold_button = self._button(
            appearance_layout, "", "加粗", editor.apply_bold, "bold.svg"
        )
        self.italic_button = self._button(
            appearance_layout, "", "斜体", editor.apply_italic, "italic.svg"
        )
        self.strike_button = self._button(
            appearance_layout,
            "",
            "删除线",
            editor.apply_strike,
            "strikethrough.svg",
        )

        cleanup_group, cleanup_layout = self._group("cleanup")
        self.clear_format_button = self._button(
            cleanup_layout,
            "",
            "清除选区或全文格式",
            editor.clear_formatting,
            "eraser.svg",
        )
        self.clear_markdown_button = self._button(
            cleanup_layout,
            "",
            "清除 Markdown 标记",
            editor.clear_markdown_markup,
            "remove-formatting.svg",
        )
        self.compress_blank_lines_button = QToolButton()
        self.compress_blank_lines_button.setObjectName("toolbarButton")
        self.compress_blank_lines_button.setToolTip("压缩空行")
        self.compress_blank_lines_button.setFixedSize(28, 28)
        self.compress_blank_lines_button.setIconSize(QSize(14, 14))
        self.compress_blank_lines_button.setProperty("iconFile", "compress-blank-lines.svg")
        self.icon_buttons.append(self.compress_blank_lines_button)
        self.compress_blank_lines_button.setMenu(self._build_compress_blank_lines_menu())
        self.compress_blank_lines_button.setPopupMode(QToolButton.InstantPopup)
        cleanup_layout.addWidget(self.compress_blank_lines_button)

        structure_group, structure_layout = self._group("structure")
        self.heading_button = QToolButton()
        self.heading_button.setText("标题")
        self.heading_button.setObjectName("toolbarButton")
        self.heading_button.setToolTip("标题层级")
        self.heading_button.setFixedSize(37, 29)
        self.heading_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.heading_button.setMenu(self._build_heading_menu())
        self.heading_button.setPopupMode(QToolButton.InstantPopup)
        structure_layout.addWidget(self.heading_button)
        self.format_button = self.heading_button

        for text, tooltip, callback, icon in (
            ("", "无序列表", lambda: editor.apply_list(False), "bullets.svg"),
            ("", "有序列表", lambda: editor.apply_list(True), "numbered.svg"),
            ("", "引用", editor.apply_quote, "quote.svg"),
            ("", "代码块", editor.apply_code_block, "code.svg"),
        ):
            self._button(structure_layout, text, tooltip, callback, icon)

        tools_group, tools_layout = self._group("tools")
        self.find_toggle_button = self._button(
            tools_layout,
            "",
            "展开或收起查找替换",
            self.toggle_find_panel,
            "search.svg",
        )
        self.toolbar_groups = (
            history_group,
            appearance_group,
            cleanup_group,
            structure_group,
            tools_group,
        )
        self.toolbar_group_names = (
            "history",
            "appearance",
            "cleanup",
            "structure",
            "tools",
        )
        self.toolbar_separators: list[QFrame] = []
        for _index in range(len(self.toolbar_groups) - 1):
            separator = QFrame()
            separator.setObjectName("toolbarSeparator")
            separator.setFrameShape(QFrame.VLine)
            separator.setFixedWidth(1)
            separator.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
            separator.setVisible(False)
            self.toolbar_separators.append(separator)
        self._toolbar_layout_signature: tuple[int, ...] | None = None
        self._toolbar_relayout_in_progress = False
        self._relayout_toolbar_groups(10_000)

        self.find_panel = QWidget()
        self.find_panel.setObjectName("findPanel")
        find_row = QHBoxLayout(self.find_panel)
        find_row.setContentsMargins(0, 0, 0, 0)
        find_row.setSpacing(4)
        find_row.addWidget(QLabel("查找"))
        self.find_input = QLineEdit()
        self.find_input.setObjectName("toolbarInput")
        self.find_input.setPlaceholderText("关键词")
        find_row.addWidget(self.find_input)
        self._button(find_row, "下一个", "查找下一个", self.find_next)

        self.replace_input = QLineEdit()
        self.replace_input.setObjectName("toolbarInput")
        self.replace_input.setPlaceholderText("替换为")
        find_row.addWidget(self.replace_input)
        self._button(find_row, "替换", "替换当前匹配", self.replace_current)
        self._button(find_row, "全部", "全部替换", self.replace_all)

        self.find_status = QLabel("")
        self.find_status.setObjectName("metadataLabel")
        find_row.addWidget(self.find_status)
        find_row.addStretch(1)
        layout.addWidget(self.find_panel)
        self.find_panel.setVisible(False)
        self.refresh_theme()

    def _build_heading_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction("正文", lambda: self.editor.apply_heading(0))
        menu.addAction("标题 1", lambda: self.editor.apply_heading(1))
        menu.addAction("标题 2", lambda: self.editor.apply_heading(2))
        return menu

    def _build_font_size_menu(self) -> QMenu:
        menu = QMenu(self)
        for label, pixel_size in FONT_SIZE_OPTIONS:
            menu.addAction(
                label,
                lambda checked=False, size=pixel_size: self.editor.apply_font_size(size),
            )
        return menu

    @staticmethod
    def _group(name: str) -> tuple[QWidget, QHBoxLayout]:
        group = QWidget()
        group.setObjectName("toolbarGroup")
        group.setProperty("groupName", name)
        group_layout = QHBoxLayout(group)
        group_layout.setContentsMargins(0, 0, 0, 0)
        group_layout.setSpacing(4)
        return group, group_layout

    def _relayout_toolbar_groups(self, available_width: int) -> None:
        available_width = max(1, available_width)
        row_index = 0
        used_width = 0
        separator_width = 9
        row_assignments: list[int] = []
        for group in self.toolbar_groups:
            group_width = group.sizeHint().width()
            required = group_width + (separator_width if used_width else 0)
            if used_width and used_width + required > available_width:
                row_index += 1
                used_width = 0
            row_index = min(row_index, len(self.toolbar_row_layouts) - 1)
            row_assignments.append(row_index)
            if used_width:
                used_width += separator_width
            used_width += group_width

        signature = tuple(row_assignments)
        if (
            self._toolbar_relayout_in_progress
            or signature == self._toolbar_layout_signature
        ):
            return

        self._toolbar_relayout_in_progress = True
        try:
            for row_layout in self.toolbar_row_layouts:
                while row_layout.count():
                    row_layout.takeAt(0)
            for separator in self.toolbar_separators:
                if not separator.isHidden():
                    separator.hide()

            used_rows = set(row_assignments)
            prior_row_index: int | None = None
            for group_index, (group, target_row_index) in enumerate(
                zip(self.toolbar_groups, row_assignments)
            ):
                row_layout = self.toolbar_row_layouts[target_row_index]
                if prior_row_index == target_row_index:
                    separator = self.toolbar_separators[group_index - 1]
                    if separator.isHidden():
                        separator.show()
                    row_layout.addWidget(separator)
                row_layout.addWidget(group)
                if group.isHidden():
                    group.show()
                prior_row_index = target_row_index

            for target_row_index in used_rows:
                self.toolbar_row_layouts[target_row_index].addStretch(1)
            for index, row in enumerate(self.toolbar_rows):
                should_show = index in used_rows
                if should_show and row.isHidden():
                    row.show()
                elif not should_show and not row.isHidden():
                    row.hide()
            self._toolbar_layout_signature = signature
        finally:
            self._toolbar_relayout_in_progress = False

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout_toolbar_groups(max(1, event.size().width() - 16))

    def _build_compress_blank_lines_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction(
            "保留一个空行", lambda: self.editor.compress_blank_paragraphs(True)
        )
        menu.addAction(
            "移除所有空行", lambda: self.editor.compress_blank_paragraphs(False)
        )
        return menu

    def _button(self, layout: QHBoxLayout, text: str, tooltip: str, callback, icon: str | None = None) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("toolbarButton")
        button.setToolTip(tooltip)
        button.setFixedHeight(28)
        if icon:
            button.setFixedWidth(28)
            button.setIconSize(QSize(14, 14))
            button.setProperty("iconFile", icon)
            self.icon_buttons.append(button)
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def refresh_theme(self) -> None:
        for button in self.icon_buttons:
            button.setIcon(themed_icon(str(button.property("iconFile"))))
        self.editor.apply_theme()

    def toggle_find_panel(self) -> None:
        self.find_panel.setVisible(self.find_panel.isHidden())
        if not self.find_panel.isHidden():
            self.find_input.setFocus()

    def find_next(self) -> None:
        found = self.editor.find_next(self.find_input.text())
        self.find_status.setText("" if found else "未找到")

    def replace_current(self) -> None:
        replaced = self.editor.replace_current(
            self.find_input.text(), self.replace_input.text()
        )
        self.find_status.setText("" if replaced else "未找到")

    def replace_all(self) -> None:
        count = self.editor.replace_all(self.find_input.text(), self.replace_input.text())
        self.find_status.setText(f"已替换 {count} 处" if count else "未找到")
