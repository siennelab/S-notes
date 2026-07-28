from __future__ import annotations

import sys


def main() -> int:
    try:
        from src.database import initialize_database
        from src.main_window import run_app
    except RuntimeError as error:
        if "S_NOTES_DATA_ROOT" in str(error):
            print(f"S-notes startup failed: {error}", file=sys.stderr)
            return 2
        raise
    initialize_database()
    return run_app()


if __name__ == "__main__":
    raise SystemExit(main())
