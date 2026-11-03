"""Fixed-path receiver for actor mirror publishes on GreenCloud."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import tempfile
from typing import BinaryIO

from src.snotes.domain.actor_mirror import ActorMirrorError
from src.snotes.domain.greencloud import GREEN_CLOUD_MIRROR_ACTOR_SEQUENCE
from src.snotes.storage.actor_mirror import verify_actor_mirror


MIRROR_ROOT = Path("/srv/snotes/mirror")
ALLOWED_ACTORS = frozenset(GREEN_CLOUD_MIRROR_ACTOR_SEQUENCE)
MAX_HEADER_BYTES = 4096
MAX_PAYLOAD_BYTES = 512 * 1024 * 1024
INPUT_TIMEOUT_SECONDS = 120
COPY_CHUNK_BYTES = 1024 * 1024
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class MirrorPublishError(RuntimeError):
    """A receiver failure with a bounded, safe protocol reason."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def receive_publish(
    source: BinaryIO,
    *,
    mirror_root: Path = MIRROR_ROOT,
) -> dict[str, object]:
    """Receive, validate, and atomically publish one actor mirror."""
    header = _read_header(source)
    actor = header["actor"]
    size = header["size"]
    expected_digest = header["sha256"]
    actor_directory = Path(mirror_root) / actor
    _require_private_actor_directory(actor_directory)

    candidate: Path | None = None
    try:
        file_descriptor, candidate_name = tempfile.mkstemp(
            prefix=".incoming.", suffix=".candidate", dir=actor_directory
        )
        candidate = Path(candidate_name)
        os.fchmod(file_descriptor, 0o600)
        digest = hashlib.sha256()
        try:
            with os.fdopen(file_descriptor, "wb", closefd=True) as destination:
                remaining = size
                while remaining:
                    chunk = source.read(min(COPY_CHUNK_BYTES, remaining))
                    if not chunk:
                        raise MirrorPublishError("payload_truncated")
                    destination.write(chunk)
                    digest.update(chunk)
                    remaining -= len(chunk)
                destination.flush()
                os.fsync(destination.fileno())
        except Exception:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
            raise

        if source.read(1) != b"":
            raise MirrorPublishError("trailing_data")
        if digest.hexdigest() != expected_digest:
            raise MirrorPublishError("checksum_mismatch")

        lock_path = actor_directory / ".publish.lock"
        lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(lock_descriptor, 0o600)
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
            try:
                verification = verify_actor_mirror(candidate, expected_actor=actor)
            except ActorMirrorError as error:
                raise MirrorPublishError("candidate_rejected") from error

            os.chmod(candidate, 0o640)
            _fsync_file(candidate)
            os.replace(candidate, actor_directory / "current.db")
            candidate = None
            _fsync_directory(actor_directory)
        finally:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)

        return {
            "status": "published",
            "actor": actor,
            "hash": verification["hash"],
            "note_count": verification["note_count"],
        }
    except MirrorPublishError:
        raise
    except OSError as error:
        raise MirrorPublishError("publish_failed") from error
    finally:
        if candidate is not None:
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass


def _read_header(source: BinaryIO) -> dict[str, object]:
    line = source.readline(MAX_HEADER_BYTES + 1)
    if not line.endswith(b"\n") or len(line) > MAX_HEADER_BYTES:
        raise MirrorPublishError("invalid_header")
    try:
        header = json.loads(line[:-1].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise MirrorPublishError("invalid_header") from None
    if not isinstance(header, dict) or set(header) != {"actor", "size", "sha256"}:
        raise MirrorPublishError("invalid_header")

    actor = header["actor"]
    size = header["size"]
    sha256 = header["sha256"]
    if not isinstance(actor, str) or actor not in ALLOWED_ACTORS:
        raise MirrorPublishError("unsupported_actor")
    if type(size) is not int or size < 1 or size > MAX_PAYLOAD_BYTES:
        raise MirrorPublishError("invalid_size")
    if not isinstance(sha256, str) or SHA256_PATTERN.fullmatch(sha256) is None:
        raise MirrorPublishError("invalid_sha256")
    return {"actor": actor, "size": size, "sha256": sha256}


def _require_private_actor_directory(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise MirrorPublishError("mirror_unavailable") from error
    if not stat.S_ISDIR(metadata.st_mode) or path.is_symlink():
        raise MirrorPublishError("mirror_unavailable")


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _timeout_handler(_signum: int, _frame: object) -> None:
    raise MirrorPublishError("input_timeout")


def _write_response(payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    sys.stdout.write(f"{encoded}\n")
    sys.stdout.flush()


def main() -> int:
    if len(sys.argv) != 1:
        _write_response({"status": "error", "reason": "invalid_invocation"})
        return 64

    previous_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(INPUT_TIMEOUT_SECONDS)
    try:
        result = receive_publish(sys.stdin.buffer)
    except MirrorPublishError as error:
        _write_response({"status": "error", "reason": error.reason})
        return 65
    except Exception:
        _write_response({"status": "error", "reason": "internal_error"})
        return 70
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)
    _write_response(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
