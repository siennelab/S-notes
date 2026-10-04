"""SQLite-safe outbox snapshots, attached to the existing Cloud backup timer."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile

DATABASE = Path("/srv/snotes/data/outbox.db")
DIRECTORY = Path("/srv/snotes/backups/notes-outbox")
GENERATED = re.compile(r"^outbox-\d{8}T\d{12}Z\.db$")


def create_backup(database: Path = DATABASE, directory: Path = DIRECTORY, *, retain: int = 14) -> Path:
    if retain < 1 or not stat.S_ISREG(database.lstat().st_mode):
        raise ValueError("outbox_backup_source_invalid")
    if directory.is_symlink():
        raise ValueError("outbox_backup_directory_invalid")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    final = directory / ("outbox-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".db")
    descriptor, name = tempfile.mkstemp(prefix=".outbox-backup-", dir=directory)
    os.close(descriptor)
    temporary = Path(name)
    latest_temporary = directory / (".outbox-latest-" + str(os.getpid()))
    try:
        temporary.chmod(0o600)
        with closing(sqlite3.connect(database.absolute().as_uri() + "?mode=ro", uri=True, timeout=5)) as source:
            with closing(sqlite3.connect(temporary)) as destination:
                source.backup(destination)
                if destination.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise RuntimeError("outbox_backup_integrity_failed")
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, final)
        os.link(final, latest_temporary)
        os.replace(latest_temporary, directory / "outbox-latest.db")
        generated = sorted(path for path in directory.iterdir()
                           if GENERATED.fullmatch(path.name) and stat.S_ISREG(path.lstat().st_mode))
        for path in generated[:-retain]:
            path.unlink()
        directory_descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        return final
    finally:
        temporary.unlink(missing_ok=True)
        latest_temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    os.umask(0o077)
    try:
        create_backup()
        print("NOTES_OUTBOX_BACKUP=PASS")
    except Exception:
        print("NOTES_OUTBOX_BACKUP=FAIL")
        raise SystemExit(2)
