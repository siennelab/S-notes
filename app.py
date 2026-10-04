from __future__ import annotations

import sys



def run() -> int:
    if "--mcp-stdio" in sys.argv:
        from src.snotes.transport.mcp.server import main

        return main(["serve"])
    if "--markdown-demo" in sys.argv or "--markdown-smoke" in sys.argv:
        from src.snotes.ui.markdown_demo import main

        return main()
    from src.snotes.bootstrap import DATA_ROOT_ENV, main
    from src.snotes.reminder_authority import ReminderAuthorityError
    try:
        return main()
    except ReminderAuthorityError as error:
        print(f"S-notes startup failed: {error}", file=sys.stderr)
        return 2
    except RuntimeError as error:
        if DATA_ROOT_ENV not in str(error):
            raise
        print(f"S-notes startup failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run())
