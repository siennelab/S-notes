from __future__ import annotations

import sys



def run() -> int:
    from src.snotes.step07_candidate import configure_candidate

    candidate = configure_candidate()
    if "--step07-smoke" in sys.argv:
        if not candidate:
            return 2
        from src.snotes.ui.reminder_remote_smoke import main as smoke
        return smoke()
    if "--mcp-stdio" in sys.argv:
        from src.snotes.transport.mcp.server import main

        return main(["serve"])
    if "--markdown-demo" in sys.argv or "--markdown-smoke" in sys.argv:
        from src.snotes.ui.markdown_demo import main

        return main()
    from src.snotes.bootstrap import DATA_ROOT_ENV, main
    try:
        return main()
    except RuntimeError as error:
        if DATA_ROOT_ENV not in str(error):
            raise
        print(f"S-notes startup failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run())
