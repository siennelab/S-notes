"""Shared values and validation primitives for the frozen Gale publish contract.

No local orchestration or server transaction behavior belongs here.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REMOTE_MIRROR_ROOT = Path("/var/lib/snotes-gale-mcp/mirror")
REMOTE_INCOMING_DIR = REMOTE_MIRROR_ROOT / "incoming"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SAFE_REASON_PATTERN = re.compile(r"^[a-z0-9_]{1,64}$")


class GalePublishError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        previous_preserved: bool = True,
        check: str = "",
    ) -> None:
        self.code = code
        self.previous_preserved = previous_preserved
        self.check = safe_reason_text(check) if check else ""
        super().__init__(code)


@dataclass(frozen=True)
class RemoteActivationResult:
    status: str
    reason: str
    sha256: str
    note_count: int
    rolled_back: bool = False
    previous_preserved: bool = True


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_sha256(value: str) -> None:
    if not SHA256_PATTERN.fullmatch(str(value)):
        raise GalePublishError("invalid_sha256")


def safe_hash_text(value: object) -> str:
    text = str(value or "")
    return text if not text or SHA256_PATTERN.fullmatch(text) else ""


def safe_state_text(value: object) -> str:
    text = str(value or "")
    return text[:64] if len(text) <= 64 else ""


def safe_reason_text(value: object) -> str:
    text = str(value or "")
    return text if SAFE_REASON_PATTERN.fullmatch(text) else "invalid_reason"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
