"""Production stdio entrypoint for Gale's unified GreenCloud S-notes tools."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import sys
from typing import Callable, Protocol

from mcp.server.stdio import stdio_server

from src.snotes.application.remote_mcp_reminder import RemoteMcpReminderAdapter
from src.snotes.transport.mcp.greencloud_internal_server import (
    build_greencloud_internal_server,
)
from src.snotes.transport.remote_reminder_client import RemoteReminderClient


GALE_ACTOR = "gale"
REMINDER_ENDPOINT = "http://127.0.0.1:8766"
GALE_REMINDER_CREDENTIAL = Path(
    "/srv/snotes/actor-credentials/gale/reminder-token"
)
MAX_CREDENTIAL_BYTES = 4096


class _Closable(Protocol):
    def close(self) -> None: ...


class FixedActorCredentialProvider:
    """Read one private credential only for the process's fixed OS identity."""

    def __init__(self, uid: int, path: Path) -> None:
        self.uid = int(uid)
        self.path = Path(path)

    def __call__(self) -> str:
        if os.geteuid() != self.uid:
            raise RuntimeError("credential_identity_mismatch")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            descriptor = os.open(self.path, flags)
        except OSError as error:
            raise RuntimeError("credential_unavailable") from error
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != self.uid
                or stat.S_IMODE(metadata.st_mode) != 0o400
                or metadata.st_size > MAX_CREDENTIAL_BYTES
            ):
                raise RuntimeError("credential_permissions_invalid")
            raw = os.read(descriptor, MAX_CREDENTIAL_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(raw) > MAX_CREDENTIAL_BYTES:
            raise RuntimeError("credential_invalid")
        try:
            token = raw.decode("utf-8").strip()
        except UnicodeDecodeError as error:
            raise RuntimeError("credential_invalid") from error
        if not token or any(character.isspace() for character in token):
            raise RuntimeError("credential_invalid")
        return token


@dataclass(slots=True)
class GreenCloudInternalRuntime:
    notes: _Closable
    reminder_client: _Closable
    server: object

    def close(self) -> None:
        try:
            self.reminder_client.close()
        finally:
            self.notes.close()


def compose_greencloud_internal_runtime(
    effective_uid: int | None = None,
    *,
    reminder_client_factory: Callable[..., _Closable] = RemoteReminderClient,
) -> GreenCloudInternalRuntime:
    # Imported here so the identity binding remains owned by the formal Notes
    # composition root and startup can close a partly composed runtime safely.
    from src.cloud_read_mcp import compose_actor_cloud_runtime

    notes = compose_actor_cloud_runtime(effective_uid)
    reminder_client = None
    try:
        if notes.binding.actor != GALE_ACTOR:
            raise RuntimeError("gale_os_identity_required")
        provider = FixedActorCredentialProvider(
            notes.binding.uid,
            GALE_REMINDER_CREDENTIAL,
        )
        reminder_client = reminder_client_factory(
            REMINDER_ENDPOINT,
            provider,
            timeout_seconds=4.0,
        )
        reminder_adapter = RemoteMcpReminderAdapter(reminder_client)
        server = build_greencloud_internal_server(
            notes.read_service,
            notes.proposal_service,
            reminder_adapter,
        )
        return GreenCloudInternalRuntime(notes, reminder_client, server)
    except Exception:
        if reminder_client is not None:
            reminder_client.close()
        notes.close()
        raise


async def serve_greencloud_internal(runtime: GreenCloudInternalRuntime) -> None:
    async with stdio_server() as (read_stream, write_stream):
        await runtime.server.run(
            read_stream,
            write_stream,
            runtime.server.create_initialization_options(),
        )


def main() -> int:
    runtime: GreenCloudInternalRuntime | None = None
    try:
        if len(sys.argv) != 1:
            raise RuntimeError("arguments_forbidden")
        runtime = compose_greencloud_internal_runtime()
        asyncio.run(serve_greencloud_internal(runtime))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        sys.stderr.write(
            json.dumps(
                {"status": "failed", "reason": str(error) or type(error).__name__},
                ensure_ascii=False,
            )
            + "\n"
        )
        sys.stderr.flush()
        return 2
    finally:
        if runtime is not None:
            runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
