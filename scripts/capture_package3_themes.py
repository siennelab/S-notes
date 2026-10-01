#!/usr/bin/env python3
"""Capture the isolated Package 3 candidate in all accepted R2 themes."""

from __future__ import annotations

import argparse
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QApplication

from src.snotes.bootstrap import build_application
from src.snotes.reminder_authority import resolve_reminder_authority


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("data_root", type=Path)
    parser.add_argument("output_root", type=Path)
    args = parser.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    application, window = build_application(
        [],
        data_root=args.data_root,
        reminder_authority=resolve_reminder_authority(),
    )
    first = window.note_service.list_active_notes()[0]
    window.load_note(first.id)
    window.shell.set_metadata_expanded(True)
    window.resize(1420, 880)
    window.show()
    application.processEvents()
    for theme_name in ("light-slate", "light-clay", "dark"):
        window.set_theme(theme_name)
        cursor = window.body_edit.textCursor()
        cursor.movePosition(QTextCursor.Start)
        cursor.movePosition(QTextCursor.NextWord, QTextCursor.KeepAnchor)
        window.body_edit.setTextCursor(cursor)
        window.body_edit.setFocus(Qt.OtherFocusReason)
        application.processEvents()
        target = args.output_root / f"package3-{theme_name}.png"
        if not window.grab().save(str(target)):
            raise RuntimeError(f"failed to write {target}")
        print(f"THEME_SCREENSHOT={target}")
    window.close()
    application.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
