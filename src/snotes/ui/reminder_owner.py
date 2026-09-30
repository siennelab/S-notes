"""GUI-only Keychain access and compatible snapshot cache; no MCP imports."""
from __future__ import annotations
import json
import os
import subprocess
import tempfile
from pathlib import Path
from PySide6.QtCore import QCoreApplication, QThread
from src.snotes.storage.schema import REMINDER_SCHEMA_VERSION

OWNER_KEYCHAIN_SERVICE = 'de.ke-den.snotes.step07.reminder-owner'
OWNER_KEYCHAIN_ACCOUNT = 'owner'


def require_gui_worker() -> None:
    app = QCoreApplication.instance()
    if app is None or QThread.currentThread() == app.thread():
        raise RuntimeError('owner_credential_requires_gui_worker')


def owner_credential() -> str:
    require_gui_worker()
    try:
        result = subprocess.run(
            ['/usr/bin/security', 'find-generic-password', '-s', OWNER_KEYCHAIN_SERVICE,
             '-a', OWNER_KEYCHAIN_ACCOUNT, '-w'] + (
                [str(Path(os.environ['S_NOTES_STEP07_HOST_HOME']) / 'Library/Keychains/login.keychain-db')]
                if 'S_NOTES_STEP07_HOST_HOME' in os.environ else []),
            capture_output=True, timeout=3, check=False,
        )
        value = result.stdout.decode('utf-8').strip() if result.returncode == 0 else ''
        if not value:
            raise RuntimeError('owner_credential_unavailable')
        return value
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        raise RuntimeError('owner_credential_unavailable') from None


class OwnerSnapshotCache:
    """Isolated GUI cache, never a repository or write queue."""
    def __init__(self, path: Path):
        self.path = Path(path)

    @staticmethod
    def compatible(snapshot: object) -> bool:
        return (
            isinstance(snapshot, dict)
            and type(snapshot.get('schema_version')) is int
            and snapshot['schema_version'] == int(REMINDER_SCHEMA_VERSION)
            and isinstance(snapshot.get('snapshot_time'), str)
            and all(isinstance(snapshot.get(key), list) for key in ('labels', 'tables', 'items'))
            and isinstance(snapshot.get('settings'), dict)
            and isinstance(snapshot.get('badge_count'), int)
        )

    def load(self) -> dict | None:
        try:
            if self.path.is_symlink():
                return None
            stat = self.path.stat()
            if stat.st_uid != os.getuid() or stat.st_mode & 0o077 or stat.st_size > 16 * 1024 * 1024:
                return None
            snapshot = json.loads(self.path.read_text(encoding='utf-8'))
            return snapshot if self.compatible(snapshot) else None
        except (OSError, ValueError):
            return None

    def save(self, snapshot: dict) -> None:
        if not self.compatible(snapshot):
            return
        saved = {key: snapshot[key] for key in (
            'schema_version', 'server_time', 'snapshot_time', 'labels', 'tables',
            'items', 'settings', 'badge_count', 'trash') if key in snapshot}
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.parent.is_symlink() or self.path.is_symlink():
            raise OSError('owner_cache_symlink_rejected')
        descriptor, name = tempfile.mkstemp(prefix='.snapshot-', dir=self.path.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump(saved, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)
