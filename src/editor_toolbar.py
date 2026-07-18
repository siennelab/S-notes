from __future__ import annotations

from PySide6.QtCore import QSize

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.editor import NotesEditor
from src.theme import themed_icon


class EditorToolbar(QWidget):
    def __init__(self, editor: NotesEditor, parent=None) -> None:
        super().__init__(parent)
        self.editor = editor
        self.icon_buttons: list[QPushButton | QToolButton] = []
        self.setObjectName("editorToolbar")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        main_row = QHBoxLayout()
        main_row.setContentsMargins(0, 0, 0, 0)
        main_row.setSpacing(4)
        layout.addLayout(main_row)

        self.undo_button = self._button(main_row, "", "撤销", editor.undo, "undo.svg")
        self.redo_button = self._button(main_row, "", "重做", editor.redo, "redo.svg")
        main_row.addSpacing(8)
        self.bold_button = self._button(main_row, "", "加粗", editor.apply_bold, "bold.svg")
        self.italic_button = self._button(main_row, "", "斜体", editor.apply_italic, "italic.svg")
        self.strike_button = self._button(main_row, "", "删除线", editor.apply_strike, "strikethrough.svg")
        self.clear_format_button = self._button(
            main_row, "", "清除选区或全文格式", editor.clear_formatting, "eraser.svg"
        )
        self.clear_markdown_button = self._button(
            main_row,
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
        main_row.addWidget(self.compress_blank_lines_button)
        main_row.addSpacing(8)
        self.format_button = QPushButton("标题")
        self.format_button.setObjectName("toolbarButton")
        self.format_button.setToolTip("标题和字号")
        self.format_button.setMenu(self._build_format_menu())
        main_row.addWidget(self.format_button)

        for text, tooltip, callback, icon in (
            ("", "无序列表", lambda: editor.apply_list(False), "bullets.svg"),
            ("", "有序列表", lambda: editor.apply_list(True), "numbered.svg"),
            ("", "引用", editor.apply_quote, "quote.svg"),
            ("", "代码块", editor.apply_code_block, "code.svg"),
        ):
            self._button(main_row, text, tooltip, callback, icon)
        main_row.addSpacing(8)

        self.find_toggle_button = self._button(
            main_row, "", "展开或收起查找替换", self.toggle_find_panel, "search.svg"
        )
        main_row.addStretch(1)

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

    def _build_format_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction("正文", lambda: self.editor.apply_heading(0))
        menu.addAction("标题 1", lambda: self.editor.apply_heading(1))
        menu.addAction("标题 2", lambda: self.editor.apply_heading(2))
        menu.addSeparator()
        menu.addAction("小字", lambda: self.editor.apply_font_size(11))
        menu.addAction("正文大小", lambda: self.editor.apply_font_size(15))
        menu.addAction("大字", lambda: self.editor.apply_font_size(26))
        return menu

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
