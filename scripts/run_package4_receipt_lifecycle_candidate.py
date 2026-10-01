#!/usr/bin/env python3
"""Launch the isolated Package 4 receipt lifecycle candidate."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.snotes.bootstrap import build_application
from src.snotes.reminder_authority import resolve_reminder_authority


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True, type=Path)
    arguments = parser.parse_args()
    application, window = build_application(
        [],
        data_root=arguments.data_root,
        reminder_authority=resolve_reminder_authority(),
    )
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
