"""One stdin request to the existing Reminder API, fixed by effective UID."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import sys

# This root-owned entrypoint runs with Python -I. No caller-controlled imports.
sys.path.insert(0, "/srv/snotes/reminder-runtime")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.snotes.transport.reminder_http import ACTOR_COMMANDS
from remote_reminder_client import RemoteReminderClient


ENDPOINT = "http://127.0.0.1:8766"
MAX_REQUEST_BYTES = 64 * 1024


@dataclass(frozen=True)
class ActorAccess:
    uid: int
    actor: str
    credential_path: Path


UID_BINDINGS = {
    2001: ActorAccess(2001, "claude-code", Path("/srv/snotes/actor-credentials/claude-code/reminder-token")),
    2002: ActorAccess(2002, "clawbot", Path("/srv/snotes/actor-credentials/clawbot/reminder-token")),
}


def binding_for_effective_uid(uid: int) -> ActorAccess:
    try:
        return UID_BINDINGS[uid]
    except KeyError:
        raise ValueError("unsupported_os_identity") from None


def read_credential(binding: ActorAccess) -> str:
    """Keep tokens out of argv/environment/output; validate the opened inode."""
    if os.geteuid() != binding.uid:
        raise ValueError("credential_identity_mismatch")
    descriptor = os.open(binding.credential_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != binding.uid
                or stat.S_IMODE(metadata.st_mode) != 0o400 or metadata.st_size > 4096):
            raise ValueError("credential_permissions_invalid")
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError("credential_invalid")
    token = raw.decode("utf-8").strip()
    if not token or any(character.isspace() for character in token):
        raise ValueError("credential_invalid")
    return token


def dispatch(request: dict) -> dict:
    binding = binding_for_effective_uid(os.geteuid())
    if not isinstance(request, dict) or set(request) != {"command", "payload"}:
        raise ValueError("request_invalid")
    command, payload = request["command"], request["payload"]
    if not isinstance(command, str) or command not in ACTOR_COMMANDS:
        raise ValueError("command_forbidden")
    if not isinstance(payload, dict):
        raise ValueError("payload_invalid")
    client = RemoteReminderClient(ENDPOINT, lambda: read_credential(binding))
    try:
        return client.command(command, payload)
    finally:
        client.close()


def main() -> int:
    try:
        binding_for_effective_uid(os.geteuid())
        if len(sys.argv) != 1:
            raise ValueError("arguments_forbidden")
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("request_too_large")
        result = dispatch(json.loads(raw))
    except Exception:
        # Library diagnostics may contain request content. Emit only a safe code.
        print(json.dumps({"status": "failed", "reason": "actor_request_rejected"}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
