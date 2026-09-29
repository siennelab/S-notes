"""Manually retrieve the fixed Step 6 backup through its read-only SSH key."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile


def main():
    destination = Path.home() / 'Library/Application Support/S-notes/reminder-cloud-backups'
    identity = destination / 'backup-transport'
    if not identity.is_file() or identity.is_symlink() or identity.stat().st_mode & 0o077:
        raise SystemExit('private_backup_transport_key_required')
    descriptor, temporary = tempfile.mkstemp(prefix='.incoming-', dir=destination)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, 'wb') as output:
            subprocess.run([
                'ssh', '-T', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
                '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=yes',
                '-i', str(identity), 'snotes-publish@optimal',
            ], stdout=output, check=True, timeout=120)
        with closing(sqlite3.connect(temporary_path.as_uri() + '?mode=ro', uri=True)) as database:
            if database.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise RuntimeError('backup_integrity_failed')
            counts = {
                table: database.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                for table in ('reminders', 'reminder_tables', 'reminder_occurrences')
            }
            if database.execute('SELECT COUNT(*) FROM notes').fetchone()[0] != 0:
                raise RuntimeError('staging_backup_contains_notes')
        digest = hashlib.sha256(temporary_path.read_bytes()).hexdigest()
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        final = destination / ('reminder-staging-' + stamp + '.db')
        temporary_path.replace(final)
        print(json.dumps({'path': str(final), 'integrity_check': 'ok', 'counts': counts, 'sha256': digest}))
    finally:
        temporary_path.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
