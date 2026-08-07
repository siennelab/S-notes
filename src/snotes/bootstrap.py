"""Composition root for the R2 S-notes application."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from PySide6.QtWidgets import QApplication

from src.snotes.ui.shell import SnotesShell


def build_application(
    argv: Sequence[str] | None = None,
) -> tuple[QApplication, SnotesShell]:
    """Construct the package-0B shell without touching storage or services."""
    application = QApplication.instance()
    if application is None:
        application = QApplication(list(argv) if argv is not None else sys.argv)

    shell = SnotesShell()
    return application, shell


def main(argv: Sequence[str] | None = None) -> int:
    application, shell = build_application(argv)
    shell.show()
    return application.exec()
