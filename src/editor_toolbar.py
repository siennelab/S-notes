from __future__ import annotations

from pathlib import Path
from PySide6.QtGui import QIcon

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.editor import NotesEditor


class EditorToolbar(QWidget):
    def __init__(self, editor: NotesEditor, parent=None) -> None:
        super().__init__(parent)
        self.editor = editor
        self.setObjectName("editorToolbar")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        main_row = QHBoxLayout()
        main_row.setContentsMargins(0, 0, 0, 0)
        main_row.setSpacing(5)
        layout.addLayout(main_row)

        self.undo_button = self._button(main_row, "", "撤销", editor.undo, "undo.svg")
        self.redo_button = self._button(main_row, "", "重做", editor.redo, "redo.svg")
        main_row.addSpacing(8)
        self.bold_button = self._button(main_row, "B", "加粗", editor.apply_bold)
        self.italic_button = self._button(main_row, "I", "斜体", editor.apply_italic)
        self.strike_button = self._button(main_row, "S", "删除线", editor.apply_strike)
        self.clear_format_button = self._button(
            main_row, "清格式", "清除选区或全文格式", editor.clear_formatting
        )
        main_row.addSpacing(8)
        self.format_button = QPushButton("标题▾")
        self.format_button.setObjectName("toolbarButton")
        self.format_button.setToolTip("标题和字号")
        self.format_button.setMenu(self._build_format_menu())
        main_row.addWidget(self.format_button)

        for text, tooltip, callback, icon in (
            ("", "无序列表", lambda: editor.apply_list(False), "bullets.svg"),
            ("", "有序列表", lambda: editor.apply_list(True), "numbered.svg"),
            ("", "引用", editor.apply_quote, "quote.svg"),
            ("代码", "代码块", editor.apply_code_block, None),
        ):
            self._button(main_row, text, tooltip, callback, icon)
        main_row.addSpacing(8)

        self.find_toggle_button = self._button(
            main_row, "查找", "展开或收起查找替换", self.toggle_find_panel
        )
        main_row.addStretch(1)

        self.find_panel = QWidget()
        self.find_panel.setObjectName("findPanel")
        find_row = QHBoxLayout(self.find_panel)
        find_row.setContentsMargins(0, 0, 0, 0)
        find_row.setSpacing(5)
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

    def _build_format_menu(self) -> QMenu:
        menu = QMenu(self)
        menu.addAction("正文", lambda: self.editor.apply_heading(0))
        menu.addAction("标题 1", lambda: self.editor.apply_heading(1))
        menu.addAction("标题 2", lambda: self.editor.apply_heading(2))
        menu.addSeparator()
        menu.addAction("小字", lambda: self.editor.apply_font_size(12))
        menu.addAction("正文大小", lambda: self.editor.apply_font_size(16))
        menu.addAction("大字", lambda: self.editor.apply_font_size(20))
        return menu

    def _button(self, layout: QHBoxLayout, text: str, tooltip: str, callback, icon: str | None = None) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("toolbarButton")
        button.setToolTip(tooltip)
        button.setFixedHeight(28)
        if icon:
            button.setIcon(QIcon(str(Path(__file__).with_name("icons") / icon)))
        button.clicked.connect(callback)
        layout.addWidget(button)
        return button

    def _separator(self, layout: QHBoxLayout) -> None:
        line = QFrame()
        line.setObjectName("toolbarSeparator")
        line.setFrameShape(QFrame.VLine)
        layout.addWidget(line)

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
