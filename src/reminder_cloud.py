"""GreenCloud Reminder staging runtime.

This entry point owns only the Step 6 staging database and runtime operations.
Its command line deliberately exposes no path overrides.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import stat
import sys
import tempfile
import time
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import BinaryIO, Callable, Sequence

from src.snotes.application.reminder_service import ReminderService
from src.snotes.domain.reminder_schedule import SHANGHAI
from src.snotes.domain.reminders import (
    REMINDER_ACTIVE,
    ReminderDraft,
    ReminderSettings,
)
from src.snotes.storage.connection import SQLiteConnectionFactory
from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.transport.reminder_ntfy import NtfyReminderTransport


DATABASE_PATH = Path("/srv/snotes/data/reminder.db")
BACKUP_DIRECTORY = Path("/srv/snotes/backups/reminder")
LATEST_BACKUP_NAME = "reminder-latest.db"
BACKUP_RETENTION = 14
API_HOST = "127.0.0.1"
API_PORT = 8766
NTFY_ENDPOINT = "https://ntfy.ke-den.de"
NTFY_TOPIC = "s-step06"
API_CREDENTIAL_NAME = "reminder-api.json"
NTFY_CREDENTIAL_NAME = "reminder-ntfy-token"
API_IDENTITIES = frozenset({"owner", "yan", "claude-code", "clawbot"})
_BACKUP_NAME = re.compile(r"^reminder-\d{8}T\d{12}Z\.db$")


def _private_mode(path: Path, *, expected: int = 0o600) -> None:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RuntimeError(f"unsafe_regular_file_required:{path.name}")
    if stat.S_IMODE(metadata.st_mode) != expected:
        raise RuntimeError(f"unsafe_file_mode:{path.name}")


def _existing_database(database_path: Path = DATABASE_PATH) -> SQLiteConnectionFactory:
    path = Path(database_path)
    if not path.exists():
        raise RuntimeError("reminder_staging_database_not_initialized")
    _private_mode(path)
    connections = SQLiteConnectionFactory(path)
    # Opening through the existing factory validates the schema marker without
    # running initialization or migrations during service startup.
    with connections.read() as connection:
        connection.execute("SELECT 1").fetchone()
    return connections


def _service_clock(clock: Callable[[], float]) -> Callable[[], datetime]:
    return lambda: datetime.fromtimestamp(clock(), SHANGHAI)


def compose_cloud_reminder_service(
    database_path: Path = DATABASE_PATH,
    *,
    transport: NtfyReminderTransport | None = None,
    clock: Callable[[], float] = time.time,
) -> ReminderService:
    connections = _existing_database(database_path)
    repository = SQLiteReminderRepository(
        connections,
        clock=_service_clock(clock),
    )
    return ReminderService(repository, transport=transport, clock=clock)


def initialize_staging(
    database_path: Path = DATABASE_PATH,
    *,
    business_date: date | None = None,
) -> Path:
    """Create a new, fictional-only staging DB; never open an existing one."""
    path = Path(database_path)
    if path.exists() or path.is_symlink():
        raise RuntimeError("reminder_staging_database_already_exists")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    old_umask = os.umask(0o077)
    try:
        connections = initialize_database(path)
    finally:
        os.umask(old_umask)
    os.chmod(path, 0o600)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("PRAGMA busy_timeout = 5000")
        mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0])
        if mode.casefold() != "wal":
            raise RuntimeError("reminder_staging_wal_not_enabled")

    today = business_date or datetime.now(SHANGHAI).date()
    repository = SQLiteReminderRepository(connections)
    service = ReminderService(repository)
    service.save_settings(
        ReminderSettings(
            notifications_enabled=True,
            daily_time="10:00",
            ntfy_endpoint=NTFY_ENDPOINT,
            ntfy_topic=NTFY_TOPIC,
        )
    )
    table = service.create_table("FICTIONAL Step 6 staging", ai_visible=True)
    service.create_reminder(
        ReminderDraft(
            title="FICTIONAL Step 6 due multi-notify reminder",
            lifecycle=REMINDER_ACTIVE,
            action_spec={"type": "one_time", "date": today.isoformat()},
            note="FICTIONAL staging data only",
            table_id=table.id,
            multi_notify=True,
        )
    )
    service.create_reminder(
        ReminderDraft(
            title="FICTIONAL Step 6 multi-notify reminder",
            lifecycle=REMINDER_ACTIVE,
            action_spec={
                "type": "one_time",
                "date": (today + timedelta(days=7)).isoformat(),
            },
            note="FICTIONAL staging data only",
            table_id=table.id,
            multi_notify=True,
        )
    )
    service.create_reminder(
        ReminderDraft(
            title="FICTIONAL Step 6 next-day observation reminder",
            lifecycle=REMINDER_ACTIVE,
            action_spec={
                "type": "one_time",
                "date": (today + timedelta(days=1)).isoformat(),
            },
            note="FICTIONAL 24-hour staging observation data",
            table_id=table.id,
        )
    )
    hidden_table = service.create_table(
        "FICTIONAL Step 6 hidden staging",
        ai_visible=False,
    )
    service.create_reminder(
        ReminderDraft(
            title="FICTIONAL Step 6 actor-hidden reminder",
            lifecycle=REMINDER_ACTIVE,
            action_spec={
                "type": "one_time",
                "date": (today + timedelta(days=30)).isoformat(),
            },
            note="FICTIONAL actor authorization fixture",
            table_id=hidden_table.id,
        )
    )
    os.chmod(path, 0o600)
    return path


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
        raise RuntimeError(f"credential_unavailable:{name}") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}
            or metadata.st_uid not in {0, os.geteuid()}
            or metadata.st_size > limit
        ):
            raise RuntimeError(f"credential_invalid:{name}")
        payload = os.read(descriptor, limit + 1)
    finally:
        os.close(descriptor)
    if not payload or len(payload) > limit:
        raise RuntimeError(f"credential_invalid:{name}")
    return payload


def load_ntfy_token() -> str:
    try:
        token = _credential_bytes(NTFY_CREDENTIAL_NAME, limit=4096).decode("utf-8").strip()
    except UnicodeDecodeError as error:
        raise RuntimeError("reminder_ntfy_credential_invalid") from error
    if not token or any(character.isspace() for character in token):
        raise RuntimeError("reminder_ntfy_credential_invalid")
    return token


def load_api_credentials() -> dict[str, object]:
    """Return token -> fixed ReminderCredential mappings for the HTTP app."""
    try:
        payload = json.loads(
            _credential_bytes(API_CREDENTIAL_NAME, limit=65536).decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("reminder_api_credential_invalid") from error
    if not isinstance(payload, dict) or set(payload) != API_IDENTITIES:
        raise RuntimeError("reminder_api_credential_invalid")
    try:
        from src.snotes.transport.reminder_http import ReminderCredential
    except ImportError as error:
        raise RuntimeError("reminder_http_runtime_unavailable") from error

    credentials: dict[str, object] = {}
    for identity, token in payload.items():
        if (
            not isinstance(identity, str)
            or not identity.strip()
            or identity != identity.strip()
            or not isinstance(token, str)
            or not token
            or any(character.isspace() for character in token)
            or token in credentials
        ):
            raise RuntimeError("reminder_api_credential_invalid")
        role = "owner" if identity == "owner" else "actor"
        credentials[token] = ReminderCredential(identity=identity, role=role)
    if not any(getattr(value, "role", None) == "owner" for value in credentials.values()):
        raise RuntimeError("reminder_api_owner_credential_required")
    return credentials


def run_schedule_once(
    database_path: Path = DATABASE_PATH,
    *,
    clock: Callable[[], float] = time.time,
) -> int:
    transport = _authenticated_ntfy_transport()
    service = compose_cloud_reminder_service(
        database_path,
        transport=transport,
        clock=clock,
    )
    settings = service.settings()
    if settings.ntfy_endpoint.rstrip("/") != NTFY_ENDPOINT or settings.ntfy_topic != NTFY_TOPIC:
        raise RuntimeError("reminder_staging_ntfy_configuration_invalid")
    return service.run_scheduled_once()


def _authenticated_ntfy_transport() -> NtfyReminderTransport:
    return NtfyReminderTransport(
        NTFY_ENDPOINT,
        bearer_token=load_ntfy_token(),
    )


def serve_reminder_api(database_path: Path = DATABASE_PATH) -> None:
    from src.snotes.transport.reminder_http import ReminderHttpApplication, serve

    credentials = load_api_credentials()
    service = compose_cloud_reminder_service(
        database_path,
        transport=_authenticated_ntfy_transport(),
    )
    settings = service.settings()
    if settings.ntfy_endpoint.rstrip("/") != NTFY_ENDPOINT or settings.ntfy_topic != NTFY_TOPIC:
        raise RuntimeError("reminder_staging_ntfy_configuration_invalid")
    application = ReminderHttpApplication(service, credentials)
    serve(application, host=API_HOST, port=API_PORT)


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def create_backup(
    database_path: Path = DATABASE_PATH,
    backup_directory: Path = BACKUP_DIRECTORY,
    *,
    retain: int = BACKUP_RETENTION,
    now: datetime | None = None,
) -> Path:
    if retain < 1:
        raise ValueError("reminder_backup_retention_invalid")
    source_path = Path(database_path)
    _existing_database(source_path)
    directory = Path(backup_directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    timestamp = (now or datetime.now(SHANGHAI)).astimezone(timezone.utc)
    backup_name = f"reminder-{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}.db"
    final_path = directory / backup_name
    descriptor, temporary_name = tempfile.mkstemp(prefix=".reminder-backup-", dir=directory)
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        os.chmod(temporary_path, 0o600)
        source_uri = f"{source_path.resolve().as_uri()}?mode=ro"
        with closing(sqlite3.connect(source_uri, uri=True, timeout=5.0)) as source:
            source.execute("PRAGMA busy_timeout = 5000")
            with closing(sqlite3.connect(temporary_path)) as destination:
                source.backup(destination)
                result = destination.execute("PRAGMA integrity_check").fetchone()
                if result is None or result[0] != "ok":
                    raise RuntimeError("reminder_backup_integrity_failed")
        os.chmod(temporary_path, 0o600)
        with temporary_path.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary_path, final_path)
        _fsync_directory(directory)

        latest_path = directory / LATEST_BACKUP_NAME
        latest_temporary = directory / f".{LATEST_BACKUP_NAME}.{os.getpid()}"
        try:
            os.link(final_path, latest_temporary)
            os.replace(latest_temporary, latest_path)
        finally:
            try:
                latest_temporary.unlink()
            except FileNotFoundError:
                pass

        generated = sorted(
            path for path in directory.iterdir()
            if _BACKUP_NAME.fullmatch(path.name)
            and stat.S_ISREG(path.lstat().st_mode)
        )
        for expired in generated[:-retain]:
            expired.unlink()
        _fsync_directory(directory)
        return final_path
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def export_latest_backup(
    backup_directory: Path = BACKUP_DIRECTORY,
    *,
    output: BinaryIO | None = None,
) -> int:
    directory = Path(backup_directory)
    latest = directory / LATEST_BACKUP_NAME
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(latest, flags)
    except OSError as error:
        raise RuntimeError("reminder_latest_backup_unavailable") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise RuntimeError("reminder_latest_backup_invalid")
        generated = []
        for path in directory.iterdir():
            if not _BACKUP_NAME.fullmatch(path.name):
                continue
            generated_metadata = path.lstat()
            if stat.S_ISREG(generated_metadata.st_mode):
                generated.append((generated_metadata.st_dev, generated_metadata.st_ino))
        if not any(
            device == metadata.st_dev and inode == metadata.st_ino
            for device, inode in generated
        ):
            raise RuntimeError("reminder_latest_backup_not_generated")
        header = os.read(descriptor, 16)
        if header != b"SQLite format 3\x00":
            raise RuntimeError("reminder_latest_backup_invalid")
        os.lseek(descriptor, 0, os.SEEK_SET)
        destination = output or sys.stdout.buffer
        written = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            destination.write(chunk)
            written += len(chunk)
        destination.flush()
        return written
    finally:
        os.close(descriptor)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="snotes-reminder-cloud")
    parser.add_argument(
        "command",
        choices=("init-staging", "serve", "schedule", "backup", "export-backup"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    command = _parser().parse_args(argv).command
    try:
        if command == "init-staging":
            print(initialize_staging())
        elif command == "serve":
            serve_reminder_api()
        elif command == "schedule":
            print(run_schedule_once())
        elif command == "backup":
            print(create_backup())
        elif command == "export-backup":
            export_latest_backup()
        return 0
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
        print(f"snotes-reminder-cloud: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
