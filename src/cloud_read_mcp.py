"""Production entrypoint for the GreenCloud actor-fixed read-only MCP."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
import stat

from src.snotes.application.cloud_proposal_service import CloudProposalService
from src.snotes.application.cloud_read_service import CloudReadService
from src.snotes.storage.cloud_read import SQLiteCloudMirrorReader
from src.snotes.transport.cloud_proposal_client import CloudProposalClient
from src.snotes.transport.mcp.cloud_read_server import serve_cloud_read, startup_error


@dataclass(frozen=True, slots=True)
class ActorBinding:
    uid: int
    actor: str
    mirror_path: Path
    credential_path: Path


PRODUCTION_UID_BINDINGS = {
    2001: ActorBinding(
        uid=2001,
        actor="claude-code",
        mirror_path=Path("/srv/snotes/mirror/claude-code/current.db"),
        credential_path=Path(
            "/srv/snotes/actor-credentials/claude-code/reminder-token"
        ),
    ),
    2002: ActorBinding(
        uid=2002,
        actor="clawbot",
        mirror_path=Path("/srv/snotes/mirror/clawbot/current.db"),
        credential_path=Path(
            "/srv/snotes/actor-credentials/clawbot/reminder-token"
        ),
    ),
}


def binding_for_effective_uid(effective_uid: int) -> ActorBinding:
    try:
        return PRODUCTION_UID_BINDINGS[int(effective_uid)]
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("unsupported_os_identity") from error


def read_actor_credential(binding: ActorBinding) -> str:
    """Read the actor's Step 9 credential after validating the opened inode."""
    if os.geteuid() != binding.uid:
        raise RuntimeError("credential_identity_mismatch")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(binding.credential_path, flags)
    except OSError as error:
        raise RuntimeError("credential_unavailable") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != binding.uid
            or stat.S_IMODE(metadata.st_mode) != 0o400
            or metadata.st_size > 4096
        ):
            raise RuntimeError("credential_permissions_invalid")
        raw = os.read(descriptor, 4097)
    finally:
        os.close(descriptor)
    if len(raw) > 4096:
        raise RuntimeError("credential_invalid")
    try:
        token = raw.decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise RuntimeError("credential_invalid") from error
    if not token or any(character.isspace() for character in token):
        raise RuntimeError("credential_invalid")
    return token


def main() -> int:
    client: CloudProposalClient | None = None
    try:
        binding = binding_for_effective_uid(os.geteuid())
        reader = SQLiteCloudMirrorReader(binding.mirror_path)
        service = CloudReadService(reader)
        client = CloudProposalClient(lambda: read_actor_credential(binding))
        proposal_service = CloudProposalService(reader, client)
        asyncio.run(serve_cloud_read(service, proposal_service))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        startup_error(str(error) or type(error).__name__)
        return 2
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
