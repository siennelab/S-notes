from __future__ import annotations

import argparse
from pathlib import Path

from src.snotes.background.gale import (
    HELPER_FILENAME,
    HELPER_VERSION,
    run_one_shot,
)
from src.snotes.gale_bootstrap import (
    build_gale_control_service,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=HELPER_FILENAME)
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--scheduled", action="store_true")
    parser.add_argument("--data-root", type=Path)
    parser.add_argument(
        "--mode", choices=("production", "staging"), default="production"
    )
    args = parser.parse_args(argv)
    if args.version:
        print(f"{HELPER_FILENAME} {HELPER_VERSION}")
        return 0
    try:
        control = build_gale_control_service(
            data_root=args.data_root, outbox_mode=args.mode
        )
        return run_one_shot(control, scheduled=args.scheduled)
    except Exception:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
