"""Composition root for the R2 S-notes application."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtWidgets import QApplication

from src.snotes.application.note_service import NoteService
from src.snotes.storage.repositories.directories import SQLiteDirectoriesRepository
from src.snotes.storage.repositories.notes import SQLiteNotesRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.ui.main_window import MainWindow


DATA_ROOT_ENV = "S_NOTES_DATA_ROOT"
FORMAL_DATA_ROOT = Path("/Users/s/Library/Application Support/S-notes")


def resolve_rebuild_data_root(data_root: Path | None = None) -> Path:
    supplied = data_root if data_root is not None else os.environ.get(DATA_ROOT_ENV)
    if not supplied:
        raise RuntimeError(
            "S-lumen rebuild requires an explicit isolated S_NOTES_DATA_ROOT"
        )
    resolved = Path(supplied).expanduser().resolve()
    if resolved == FORMAL_DATA_ROOT.resolve():
        raise RuntimeError("S-lumen rebuild refuses the formal S-notes data root")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def build_application(
    argv: Sequence[str] | None = None,
    *,
    data_root: Path | None = None,
) -> tuple[QApplication, MainWindow]:
    """Compose the Package 2 runtime against an explicit isolated data root."""
    application = QApplication.instance()
    if application is None:
        application = QApplication(list(argv) if argv is not None else sys.argv)

    root = resolve_rebuild_data_root(data_root)
    connections = initialize_database(root / "s_notes.db")
    directories = SQLiteDirectoriesRepository(connections)
    notes = SQLiteNotesRepository(connections, directories)
    note_service = NoteService(notes)
    window = MainWindow(
        note_service,
        theme_settings_path=root / "config" / "ui_settings.ini",
    )
    return application, window


def main(argv: Sequence[str] | None = None, *, data_root: Path | None = None) -> int:
    application, window = build_application(argv, data_root=data_root)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
