from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.gale_production_background import (
    HELPER_FILENAME,
    HELPER_VERSION,
    BackgroundPullError,
    ensure_background_installation,
    run_production_pull_once,
    start_installed_helper,
    uninstall_background,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=HELPER_FILENAME)
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    args = parser.parse_args(argv)
    if args.version:
        print(f"{HELPER_FILENAME} {HELPER_VERSION}")
        return 0
    if args.install and args.uninstall:
        return 2
    try:
        if args.install:
            ensure_background_installation(source=Path(sys.executable))
            start_installed_helper()
            return 0
        if args.uninstall:
            uninstall_background()
            return 0
        outcome = run_production_pull_once()
        return 0 if outcome.status in {"success", "disabled", "locked"} else 2
    except BackgroundPullError:
        return 2
    except Exception:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
