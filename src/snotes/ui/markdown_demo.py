"""Fixture-only entry point. Never imports bootstrap, NoteService, or a database."""
from __future__ import annotations

import sys
from uuid import uuid4

from PySide6.QtCore import QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget

from src.snotes.ui.markdown_editor import MarkdownEditorWidget
from src.snotes.ui.theme import THEMES, THEME_LABELS, apply_theme

FIXTURES = {
    "列表与内容样式": '''1. # 一级标题列表
2. ## 二级标题列表
3. ### 三级标题列表

- :size[整项 15px，圆点跟随]{size="15"}
- 默认 17px，圆点保持 17px
- :size[整项 20px，圆点跟随]{size="20"}
- 只有 :size[这个词]{size="20"} 变大，圆点仍为 17px

在列表项内点 H1/H2/H3 或 15/17/20；再次点击当前列表按钮可取消列表。
''',
    "格式样例": '''# Markdown 编辑器验收

中文输入与 English mixed text，可在中间插入、删除和换行。

## 二级标题

### 三级标题

普通正文、**加粗**、*斜体*、~~删除线~~、`inline code`。

:size[小号正文]{size="15"}、默认 17、:size[大号正文]{size="20"}。

- 第一项
- 第二项

1. 有序列表
2. 第二项

> 引用正文

```python
print("Hello，世界")
```

[外部链接](https://example.com)
''',
    "空白": "",
    "相同正文 A": "两篇正文完全相同。\n",
    "相同正文 B": "两篇正文完全相同。\n",
    "未知扩展": ''':::note
保留未知容器的文字
:::

:tada:

:xxx[test]{foo="bar"}

:size[非法字号]{size="99"}

:size[额外属性]{size="20" other="x"}

:size[没有属性]

:size[未闭合
''',
}


class MarkdownDemo(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("S-notes · Package 1 Markdown fixture 验收")
        self.resize(1280, 920)
        self.confirmed_close = False
        self.snapshots = {}
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addWidget(QLabel("独立组件验收 · 固定样例 · 保存仅在本次窗口内存中，退出后清除"))
        bar = QHBoxLayout()
        self.fixtures = QComboBox()
        self.fixtures.addItems(FIXTURES)
        bar.addWidget(self.fixtures)
        reload_button = QPushButton("重新加载样例")
        bar.addWidget(reload_button)
        self.save_button = QPushButton("保存到演示内存")
        bar.addWidget(self.save_button)
        self.themes = QComboBox()
        for name, label in THEME_LABELS:
            self.themes.addItem(label, name)
        self.themes.setCurrentIndex(1)
        bar.addWidget(self.themes)
        readonly = QCheckBox("只读")
        bar.addWidget(readonly)
        layout.addLayout(bar)
        self.editor = MarkdownEditorWidget()
        layout.addWidget(self.editor, 1)
        self.saved_status = QLabel("")
        layout.addWidget(self.saved_status)
        self.setCentralWidget(central)
        self.fixtures.currentTextChanged.connect(self.load_fixture)
        reload_button.clicked.connect(lambda: self.load_fixture(self.fixtures.currentText()))
        self.save_button.clicked.connect(self.save_snapshot)
        self.editor.stateChanged.connect(lambda: self.save_button.setEnabled(self.editor.can_save))
        self.editor.closeReady.connect(self.finish_close)
        self.themes.currentIndexChanged.connect(self.theme_changed)
        readonly.toggled.connect(self.editor.setReadOnly)
        for key in ('Ctrl+W', 'Ctrl+Q'):
            QShortcut(QKeySequence(key), self).activated.connect(self.close)
        self.load_fixture(self.fixtures.currentText())

    def load_fixture(self, name):
        self.editor.setMarkdown(FIXTURES[name], uuid4().hex)
        self.saved_status.setText("切换/重载使用固定样例；可先保存到演示内存。")

    def save_snapshot(self):
        if self.editor.can_save:
            self.snapshots[self.editor.current_load_id] = self.editor.latest_markdown
            self.saved_status.setText(f"已保存到演示内存 · {len(self.editor.latest_markdown)} 字符 · 不写磁盘")

    def theme_changed(self):
        name = self.themes.currentData()
        apply_theme(QApplication.instance(), name)
        self.editor.setTheme(THEMES[name])

    def closeEvent(self, event):
        if self.confirmed_close:
            event.accept()
            return
        event.ignore()
        if not self.editor.closing:
            self.editor.prepareClose()
            QTimer.singleShot(5000, self.close_timeout)

    def close_timeout(self):
        if not self.confirmed_close:
            self.saved_status.setText("关闭尚未收到回执，窗口保留；当前 Markdown 可从 Raw 视图复制。")

    def finish_close(self):
        self.confirmed_close = True
        QTimer.singleShot(0, self.close)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("S-notes Markdown Fixture")
    apply_theme(app, 'light-slate')
    window = MarkdownDemo()
    app.aboutToQuit.connect(window.editor.page.deleteLater)
    window.show()
    if '--markdown-smoke' in sys.argv:
        from src.snotes.ui.markdown_smoke import run_smoke
        run_smoke(app, window)
    return app.exec()
