"""GreenCloud service entrypoint for the generalized Notes proposal outbox."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import stat
import sys

from src.services.gale_submission_outbox import initialize_outbox
from src.snotes.storage.cloud_read import SQLiteCloudMirrorReader
from src.snotes.transport.cloud_outbox_http import (
    CloudOutboxHttpApplication,
    serve_cloud_outbox,
)


DATABASE_PATH = Path("/srv/snotes/data/outbox.db")
API_CREDENTIAL_NAME = "reminder-api.json"
ACTORS = frozenset({"claude-code", "clawbot"})


def production_reader(actor: str) -> SQLiteCloudMirrorReader:
    if actor not in ACTORS:
        raise RuntimeError("unsupported_actor")
    return SQLiteCloudMirrorReader(Path(f"/srv/snotes/mirror/{actor}/current.db"))


def load_actor_credentials() -> dict[str, str]:
    """Return token -> actor for only the two Cloud Notes identities."""
    try:
        payload = json.loads(
            _credential_bytes(API_CREDENTIAL_NAME, limit=65_536).decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("cloud_outbox_credential_invalid") from error
    if not isinstance(payload, dict) or not ACTORS.issubset(payload):
        raise RuntimeError("cloud_outbox_credential_invalid")
    credentials: dict[str, str] = {}
    for actor in ACTORS:
        token = payload.get(actor)
        if (
            not isinstance(token, str)
            or not token
            or any(character.isspace() for character in token)
        ):
            raise RuntimeError("cloud_outbox_credential_invalid")
        if token in credentials:
            raise RuntimeError("cloud_outbox_credential_invalid")
        credentials[token] = actor
    return credentials


def initialize_service_database(database_path: Path = DATABASE_PATH) -> None:
    previous_umask = os.umask(0o077)
    try:
        initialize_outbox(database_path, generalized=True)
    finally:
        os.umask(previous_umask)
    os.chmod(database_path, 0o600)
    connection = sqlite3.connect(database_path, timeout=5.0)
    try:
        mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0])
        if mode.casefold() != "wal":
            raise RuntimeError("cloud_outbox_wal_not_enabled")
    finally:
        connection.close()
    os.chmod(database_path, 0o600)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{database_path}{suffix}")
        if sidecar.exists():
            os.chmod(sidecar, 0o600)


def main() -> int:
    try:
        if len(sys.argv) != 1:
            raise RuntimeError("arguments_forbidden")
        initialize_service_database()
        application = CloudOutboxHttpApplication(
            DATABASE_PATH,
            load_actor_credentials(),
            reader_factory=production_reader,
        )
        serve_cloud_outbox(application, host="127.0.0.1", port=8767)
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
        return 2


def _credential_bytes(name: str, *, limit: int) -> bytes:
    directory_value = os.environ.get("CREDENTIALS_DIRECTORY", "")
    if not directory_value:
        raise RuntimeError("systemd_credentials_directory_required")
    directory = Path(directory_value)
    if not directory.is_absolute():
        raise RuntimeError("systemd_credentials_directory_must_be_absolute")
    path = directory / name
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RuntimeError("cloud_outbox_credential_unavailable") from error
    try:
        metadata = os.fstat(descriptor)
        directory_metadata = directory.lstat()
        systemd_managed = (
            directory.parent == Path("/run/credentials")
            and not stat.S_ISLNK(directory_metadata.st_mode)
            and directory_metadata.st_uid == 0
            and stat.S_IMODE(directory_metadata.st_mode) == 0o550
            and metadata.st_uid == 0
            and metadata.st_gid == 0
            and stat.S_IMODE(metadata.st_mode) == 0o440
        )
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > limit
            or metadata.st_uid not in {0, os.geteuid()}
            or (
                stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}
                and not systemd_managed
            )
        ):
            raise RuntimeError("cloud_outbox_credential_invalid")
        payload = os.read(descriptor, limit + 1)
    finally:
        os.close(descriptor)
    if not payload or len(payload) > limit:
        raise RuntimeError("cloud_outbox_credential_invalid")
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
