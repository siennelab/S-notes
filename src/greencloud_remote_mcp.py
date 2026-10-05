"""Production HTTP entrypoint for Gale's unified GreenCloud MCP tools."""

from __future__ import annotations

import json
import logging
import os
import sys

import uvicorn

from src.greencloud_internal_mcp import compose_greencloud_internal_runtime
from src.snotes.transport.mcp.greencloud_remote_transport import (
    build_greencloud_remote_app,
)


HOST = "127.0.0.1"
PORT = 8768


def _configure_safe_logging() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    for logger_name in (
        "fastmcp",
        "mcp",
        "authlib",
        "httpx",
        "httpcore",
        "uvicorn.error",
    ):
        logging.getLogger(logger_name).setLevel(logging.CRITICAL)


def main() -> int:
    runtime = None
    app = None
    os.umask(0o077)
    _configure_safe_logging()
    try:
        if len(sys.argv) != 1:
            raise RuntimeError("arguments_forbidden")

        # Imported only by the production entrypoint so transport tests can inject
        # a fictional provider without loading deployment configuration.
        from src.snotes.transport.mcp.greencloud_remote_auth import (
            build_auth_provider,
        )

        runtime = compose_greencloud_internal_runtime(os.geteuid())
        provider = build_auth_provider()
        app = build_greencloud_remote_app(runtime, provider)
        uvicorn.run(
            app,
            host=HOST,
            port=PORT,
            access_log=False,
            server_header=False,
            proxy_headers=False,
            log_level="warning",
        )
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        # Never echo auth/provider exceptions: they may contain token material.
        sys.stderr.write(
            json.dumps(
                {"status": "failed", "reason": "startup_failed"},
                separators=(",", ":"),
            )
            + "\n"
        )
        sys.stderr.flush()
        return 2
    finally:
        if app is not None:
            app.state.greencloud_runtime_owner.close()
        elif runtime is not None:
            try:
                runtime.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
