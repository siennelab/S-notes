"""Production entrypoint for the GreenCloud actor-fixed read-only MCP."""

from __future__ import annotations

import asyncio
import os
import pwd
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


@dataclass(slots=True)
class ActorCloudRuntime:
    """Actor-fixed Cloud Notes services with one owned proposal client."""

    binding: ActorBinding
    read_service: CloudReadService
    proposal_service: CloudProposalService
    _client: CloudProposalClient

    def close(self) -> None:
        self._client.close()


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

GALE_OS_ACCOUNT = "snotes-gale"
GALE_MIRROR_PATH = Path("/srv/snotes/mirror/gale/current.db")
GALE_NOTES_CREDENTIAL_PATH = Path(
    "/srv/snotes/actor-credentials/gale/notes-proposal-token"
)


def gale_production_binding() -> ActorBinding:
    """Resolve Gale's installation-assigned UID without guessing it in code."""
    try:
        account = pwd.getpwnam(GALE_OS_ACCOUNT)
        uid = int(account.pw_uid)
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("gale_os_identity_unavailable") from error
    if (
        getattr(account, "pw_name", GALE_OS_ACCOUNT) != GALE_OS_ACCOUNT
        or uid <= 0
        or uid in PRODUCTION_UID_BINDINGS
        or account.pw_gid != uid
        or account.pw_shell not in {"/usr/sbin/nologin", "/sbin/nologin"}
    ):
        raise RuntimeError("gale_os_identity_invalid")
    return ActorBinding(
        uid=uid,
        actor="gale",
        mirror_path=GALE_MIRROR_PATH,
        credential_path=GALE_NOTES_CREDENTIAL_PATH,
    )


def binding_for_effective_uid(effective_uid: int) -> ActorBinding:
    try:
        uid = int(effective_uid)
    except (TypeError, ValueError) as error:
        raise RuntimeError("unsupported_os_identity") from error
    try:
        return PRODUCTION_UID_BINDINGS[uid]
    except KeyError:
        pass
    try:
        gale = gale_production_binding()
    except RuntimeError as error:
        raise RuntimeError("unsupported_os_identity") from error
    if uid != gale.uid:
        raise RuntimeError("unsupported_os_identity")
    return gale


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


def compose_actor_cloud_runtime(
    effective_uid: int | None = None,
) -> ActorCloudRuntime:
    """Build the existing Notes services for the caller's fixed OS identity."""
    binding = binding_for_effective_uid(
        os.geteuid() if effective_uid is None else effective_uid
    )
    reader = SQLiteCloudMirrorReader(binding.mirror_path)
    read_service = CloudReadService(reader)
    client = CloudProposalClient(lambda: read_actor_credential(binding))
    return ActorCloudRuntime(
        binding=binding,
        read_service=read_service,
        proposal_service=CloudProposalService(reader, client),
        _client=client,
    )


def main() -> int:
    runtime: ActorCloudRuntime | None = None
    try:
        runtime = compose_actor_cloud_runtime()
        asyncio.run(
            serve_cloud_read(runtime.read_service, runtime.proposal_service)
        )
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        startup_error(str(error) or type(error).__name__)
        return 2
    finally:
        if runtime is not None:
            runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
