import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.theme import APP_STYLE


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("S-notes")
        self.resize(980, 680)
        self.setCentralWidget(self._build_content())

    def _build_content(self) -> QWidget:
        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = self._build_sidebar()
        sidebar.setFixedWidth(260)
        layout.addWidget(sidebar)

        editor = QTextEdit()
        editor.setReadOnly(True)
        editor.setPlainText("S-notes R1 skeleton")
        layout.addWidget(editor, stretch=1)

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
        layout.addWidget(add_button)
        layout.addStretch(1)

        return sidebar


def run_app() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_STYLE)
    window = MainWindow()
    window.show()
    return app.exec()
