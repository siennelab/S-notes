"""Narrow Step 8C checks; no notification sends and no private values in output."""
import argparse
from contextlib import closing
import hashlib
from http.client import HTTPConnection, HTTPSConnection
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
from unittest.mock import patch

FORMAL = Path('/srv/snotes/data/reminder-production.db')
STAGING = Path('/srv/snotes/data/reminder.db')
TABLES = ('reminder_labels', 'reminder_tables', 'reminders', 'reminder_occurrences', 'ai_reminder_operations')


def readonly(path):
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    conn.execute('PRAGMA query_only=ON')
    return conn


def counts(path):
    with closing(readonly(path)) as conn:
        assert conn.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
        return {table: conn.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0] for table in TABLES}


def no_notification():
    from src import reminder_cloud as runtime
    class NoSend:
        calls = 0
        def send(self, **kwargs):
            self.calls += 1
    transport = NoSend()
    with tempfile.TemporaryDirectory(prefix='step08c-no-send-') as name:
        clone = Path(name) / 'formal-clone.db'
        with closing(readonly(FORMAL)) as source, closing(sqlite3.connect(clone)) as target:
            source.backup(target)
        clone.chmod(0o600)
        with patch.object(runtime, 'FORMAL_DATABASE_PATH', clone), patch.object(
            runtime, '_authenticated_ntfy_transport', return_value=transport
        ):
            result = runtime.run_schedule_once(clone)
        # No mutations were made to the actual formal database. Count records
        # that the real scheduler would send before allowing timer activation.
        print(json.dumps({'scheduler_clone_no_crash': True, 'would_send': result,
                          'transport_calls_blocked': transport.calls,
                          'production_counts': counts(FORMAL)}))
        assert result == 0 and transport.calls == 0, 'pending_notifications_require_review'


def api():
    from src.snotes.application.reminder_service import ReminderService
    from src.snotes.storage.connection import SQLiteConnectionFactory
    from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
    # Owner snapshot omits trashed/deleted rows; compare its active view with
    # the same read-only formal business view, not the raw migration counts.
    source = ReminderService(SQLiteReminderRepository(SQLiteConnectionFactory(FORMAL, read_only=True)))
    expected_visible = {'labels': len(source.list_labels()), 'tables': len(source.list_tables()),
                        'items': len(source.list_reminders(materialize_occurrences=False))}
    tokens = json.loads(Path('/root/greencloud-secrets/reminder-api.json').read_text())
    pid = subprocess.check_output(['systemctl', 'show', 'snotes-reminder-api.service', '-p', 'MainPID', '--value'], text=True).strip()
    args = Path('/proc/' + pid + '/cmdline').read_bytes().split(b'\0')
    assert str(FORMAL).encode() in args and str(STAGING).encode() not in args
    seen = set()
    stop = threading.Event()
    def observe():
        directory = Path('/proc/' + pid + '/fd')
        while not stop.is_set():
            for fd in directory.iterdir():
                try:
                    path = os.readlink(fd)
                    if path.startswith('/srv/snotes/data/'):
                        seen.add(path)
                except FileNotFoundError:
                    pass
            time.sleep(0.001)
    observer = threading.Thread(target=observe, daemon=True)
    observer.start()
    checks = []
    try:
        for public in (False, True):
            for auth, expected in ((None, 401), ('invalid-test-token', 403), (tokens['owner'], 200)):
                conn = HTTPSConnection('reminder.ke-den.de', timeout=15) if public else HTTPConnection('127.0.0.1', 8766, timeout=15)
                if public:
                    conn._create_connection = lambda address, timeout, source_address=None: socket.create_connection(('109.94.170.215', address[1]), timeout, source_address)
                try:
                    conn.request('GET', '/v1/reminder/snapshot', headers={} if auth is None else {'Authorization': 'Bearer ' + auth})
                    response = conn.getresponse()
                    body = response.read()
                    assert response.status == expected
                    if expected == 200:
                        snapshot = json.loads(body)
                        assert {key: len(snapshot[key]) for key in expected_visible} == expected_visible
                        assert snapshot['settings']['ntfy_topic'] == 's-reminder-prod'
                        assert all(value.encode() not in body for value in tokens.values())
                    checks.append({'public': public, 'status': response.status})
                finally:
                    conn.close()
    finally:
        stop.set()
        observer.join()
    assert str(FORMAL) in seen, 'formal_open_not_observed'
    assert not any(path == str(STAGING) or path.startswith(str(STAGING) + '-') for path in seen)
    print(json.dumps({'api_checks': checks, 'actual_api_opened_formal': True,
                      'actual_api_opened_staging': False, 'counts': counts(FORMAL),
                      'visible_snapshot_counts': expected_visible}))


def backup():
    directory = Path('/srv/snotes/backups/reminder-production')
    latest = directory / 'reminder-latest.db'
    source_counts, backup_counts = counts(FORMAL), counts(latest)
    assert source_counts == backup_counts
    print(json.dumps({'backup_path': str(latest), 'quick_check': 'ok', 'foreign_key_check': 0,
                      'counts': backup_counts, 'sha256': hashlib.sha256(latest.read_bytes()).hexdigest()}))


def acl():
    token = Path('/root/greencloud-secrets/reminder-ntfy-production-token').read_text().strip()
    statuses = []
    for topic, authenticated in (('s-reminder-prod', False), ('s-reminder-prod', True), ('s-step06', True)):
        conn = HTTPSConnection('ntfy.ke-den.de', timeout=15)
        conn._create_connection = lambda address, timeout, source_address=None: socket.create_connection(('109.94.170.215', address[1]), timeout, source_address)
        try:
            conn.request('GET', '/' + topic + '/json?poll=1', headers={'Authorization': 'Bearer ' + token} if authenticated else {})
            response = conn.getresponse()
            response.read()
            assert response.status in (401, 403)
            statuses.append(response.status)
        finally:
            conn.close()
    print(json.dumps({'anonymous_and_sender_reads_denied': True, 'statuses': statuses, 'notifications_sent': 0}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('no-notification', 'api', 'backup', 'acl'))
    args = parser.parse_args()
    try:
        {'no-notification': no_notification, 'api': api, 'backup': backup, 'acl': acl}[args.mode]()
    except Exception as error:
        print('STEP08C_SMOKE_FAILED=' + type(error).__name__)
        raise SystemExit(2)
