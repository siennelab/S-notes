"""Production entrypoint for the GreenCloud actor-fixed read-only MCP."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

from src.snotes.application.cloud_read_service import CloudReadService
from src.snotes.storage.cloud_read import SQLiteCloudMirrorReader
from src.snotes.transport.mcp.cloud_read_server import serve_cloud_read, startup_error


@dataclass(frozen=True, slots=True)
class ActorBinding:
    actor: str
    mirror_path: Path


PRODUCTION_UID_BINDINGS = {
    2001: ActorBinding(
        actor="claude-code",
        mirror_path=Path("/srv/snotes/mirror/claude-code/current.db"),
    ),
    2002: ActorBinding(
        actor="clawbot",
        mirror_path=Path("/srv/snotes/mirror/clawbot/current.db"),
    ),
}


def binding_for_effective_uid(effective_uid: int) -> ActorBinding:
    try:
        return PRODUCTION_UID_BINDINGS[int(effective_uid)]
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("unsupported_os_identity") from error


def main() -> int:
    try:
        binding = binding_for_effective_uid(os.geteuid())
        service = CloudReadService(SQLiteCloudMirrorReader(binding.mirror_path))
        asyncio.run(serve_cloud_read(service))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        startup_error(str(error) or type(error).__name__)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
