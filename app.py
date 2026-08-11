from __future__ import annotations

import sys

from src.snotes.bootstrap import DATA_ROOT_ENV, main


def run() -> int:
    try:
        return main()
    except RuntimeError as error:
        if DATA_ROOT_ENV not in str(error):
            raise
        print(f"S-notes startup failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run())
