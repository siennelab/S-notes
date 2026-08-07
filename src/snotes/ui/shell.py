"""Minimal package-0B desktop shell."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow


class SnotesShell(QMainWindow):
    """A construction smoke target with no product or persistence behavior."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("S-notes")
        self.resize(640, 400)

        label = QLabel("S-notes", self)
        label.setAlignment(Qt.AlignCenter)
        self.setCentralWidget(label)
