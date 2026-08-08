#!/usr/bin/env python3
"""Run a bounded real-event-loop Package 3 smoke against isolated data."""

from __future__ import annotations

import argparse
from pathlib import Path

from PySide6.QtCore import QTimer

from src.snotes.bootstrap import build_application


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("data_root", type=Path)
    args = parser.parse_args()
    application, window = build_application([], data_root=args.data_root)
    result = {"ok": False, "error": "event loop did not finish"}

    def verify_and_close() -> None:
        try:
            window.show_search("中文 alpha")
            if len(window.visible_notes) != 1:
                raise AssertionError("AND search did not return the one fictional match")
            window.show_tag("todo")
            if not window.visible_notes:
                raise AssertionError("tag view is empty")
            window.show_smart_view("pinned")
            if len(window.visible_notes) != 1:
                raise AssertionError("pinned smart view did not return one note")
            window.show_directory()
            for view_name in ("recent", "todo", "ai"):
                button = window.shell.sidebar.smart_buttons[view_name]
                button.click()
                if window.current_mode != "smart_view":
                    raise AssertionError(f"{view_name} smart view did not activate")
                button.click()
                if window.current_mode != "directory" or window.current_directory_path:
                    raise AssertionError(f"{view_name} smart view did not toggle to all notes")
            window.show_trash()
            trash = window.directory_service.list_trash()
            if len(trash.notes) != 2 or len(trash.directories) != 2:
                raise AssertionError("trash candidate fixture changed unexpectedly")
            window.show_directory()
            first = window.note_service.list_active_notes()[0]
            window.load_note(first.id)
            window.toggle_links()
            result.update(ok=True, error="")
        except Exception as error:
            result.update(ok=False, error=str(error))
        finally:
            window.close()
            application.quit()

    window.show()
    QTimer.singleShot(120, verify_and_close)
    exit_code = application.exec()
    if exit_code != 0 or not result["ok"]:
        print(f"PACKAGE3_GUI_SMOKE_ERROR={result['error']}")
        return 1
    print("PACKAGE3_GUI_SMOKE=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
