from __future__ import annotations

import io
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src import reminder_cloud
from src.snotes.transport.reminder_ntfy import NtfyReminderTransport


SHANGHAI = ZoneInfo("Asia/Shanghai")


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class _Opener:
    def __init__(self, requests: list) -> None:
        self.requests = requests

    def open(self, request, *, timeout):
        self.requests.append((request, timeout))
        return _Response()


class CloudReminderRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-step06-runtime-")
        self.root = Path(self.temporary.name)
        self.database = self.root / "data" / "reminder.db"
        self.backups = self.root / "backups"
        self.credentials = self.root / "credentials"
        self.credentials.mkdir(mode=0o700)
        self.business_date = date(2026, 9, 29)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _credential(self, name: str, value: str) -> Path:
        path = self.credentials / name
        path.write_text(value, encoding="utf-8")
        path.chmod(0o600)
        return path

    def _initialize(self) -> None:
        # Freeze both business and repository clocks while seeding fictional
        # rows now that create materializes occurrences immediately.
        instant = datetime(2026, 9, 29, 10, 0, tzinfo=SHANGHAI)
        from src.snotes.application.reminder_service import ReminderService
        from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
        with patch.object(reminder_cloud, "ReminderService", side_effect=lambda repo: ReminderService(repo, clock=lambda: instant.timestamp())), patch.object(reminder_cloud, "SQLiteReminderRepository", side_effect=lambda connections: SQLiteReminderRepository(connections, clock=lambda: instant)):
            reminder_cloud.initialize_staging(self.database, business_date=self.business_date)

    def test_init_is_absent_only_private_wal_and_fictional(self) -> None:
        self._initialize()
        self.assertEqual(stat.S_IMODE(self.database.stat().st_mode), 0o600)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
            reminders = connection.execute(
                "SELECT title, note FROM reminders ORDER BY id"
            ).fetchall()
            visibility = {
                bool(row[0])
                for row in connection.execute("SELECT ai_visible FROM reminder_tables")
            }
            settings = dict(
                connection.execute(
                    "SELECT setting_key, setting_value FROM app_settings "
                    "WHERE setting_key LIKE 'reminder/%'"
                )
            )
        self.assertEqual(len(reminders), 4)
        self.assertTrue(all("FICTIONAL" in title for title, _ in reminders))
        self.assertEqual(visibility, {False, True})
        self.assertEqual(settings["reminder/ntfy_endpoint"], reminder_cloud.NTFY_ENDPOINT)
        self.assertEqual(settings["reminder/ntfy_topic"], reminder_cloud.NTFY_TOPIC)
        before = self.database.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "already_exists"):
            self._initialize()
        self.assertEqual(self.database.read_bytes(), before)

    def test_service_startup_refuses_missing_database_without_creating_it(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "not_initialized"):
            reminder_cloud.compose_cloud_reminder_service(self.database)
        self.assertFalse(self.database.exists())

    def test_systemd_credentials_are_strict_and_fix_actor_identity(self) -> None:
        tokens = {
            "owner": "owner-token",
            "yan": "yan-token",
            "claude-code": "claude-token",
            "clawbot": "clawbot-token",
        }
        credential = self._credential(
            reminder_cloud.API_CREDENTIAL_NAME,
            json.dumps(tokens),
        )
        with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(self.credentials)}):
            loaded = reminder_cloud.load_api_credentials()
        self.assertEqual(loaded["owner-token"].identity, "owner")
        self.assertEqual(loaded["owner-token"].role, "owner")
        self.assertEqual(loaded["claude-token"].identity, "claude-code")
        self.assertEqual(loaded["claude-token"].role, "actor")

        credential.chmod(0o644)
        with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(self.credentials)}):
            with self.assertRaisesRegex(RuntimeError, "credential_invalid"):
                reminder_cloud.load_api_credentials()

    def test_authenticated_schedule_uses_fixed_origin_and_dedupes_nodes(self) -> None:
        self._initialize()
        self._credential(reminder_cloud.NTFY_CREDENTIAL_NAME, "fictional-staging-token\n")
        requests: list = []
        clock = lambda: datetime(2026, 9, 29, 10, 0, tzinfo=SHANGHAI).timestamp()
        with (
            patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(self.credentials)}),
            patch(
                "src.snotes.transport.reminder_ntfy.build_opener",
                side_effect=lambda *handlers: _Opener(requests),
            ),
        ):
            self.assertEqual(
                reminder_cloud.run_schedule_once(self.database, clock=clock),
                2,
            )
            self.assertEqual(
                reminder_cloud.run_schedule_once(self.database, clock=clock),
                0,
            )
        self.assertEqual(len(requests), 2)
        for request, timeout in requests:
            self.assertEqual(request.full_url, "https://ntfy.ke-den.de/s-step06")
            self.assertEqual(request.get_header("Authorization"), "Bearer fictional-staging-token")
            self.assertEqual(timeout, 10.0)
        with closing(sqlite3.connect(self.database)) as connection:
            marks = connection.execute(
                """
                SELECT action_notified_at, advance_7_notified_at
                FROM reminder_occurrences
                WHERE action_notified_at IS NOT NULL OR advance_7_notified_at IS NOT NULL
                """
            ).fetchall()
        self.assertEqual(len(marks), 2)
        self.assertEqual(sum(mark[0] is not None for mark in marks), 1)
        self.assertEqual(sum(mark[1] is not None for mark in marks), 1)

    def test_missing_ntfy_credential_fails_before_network(self) -> None:
        self._initialize()
        with (
            patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(self.credentials)}),
            patch("src.snotes.transport.reminder_ntfy.build_opener") as opener,
        ):
            with self.assertRaisesRegex(RuntimeError, "credential_unavailable"):
                reminder_cloud.run_schedule_once(self.database)
        opener.assert_not_called()

    def test_api_serve_requires_both_credentials_and_authenticated_transport(self) -> None:
        self._initialize()
        self._credential(
            reminder_cloud.API_CREDENTIAL_NAME,
            json.dumps(
                {
                    "owner": "owner-token",
                    "yan": "yan-token",
                    "claude-code": "claude-token",
                    "clawbot": "clawbot-token",
                }
            ),
        )
        self._credential(reminder_cloud.NTFY_CREDENTIAL_NAME, "fictional-ntfy-token")
        with (
            patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": str(self.credentials)}),
            patch("src.snotes.transport.reminder_http.serve") as serve,
        ):
            reminder_cloud.serve_reminder_api(self.database)
        application = serve.call_args.args[0]
        self.assertEqual(serve.call_args.kwargs, {"host": "127.0.0.1", "port": 8766})
        self.assertEqual(application._service._transport.bearer_token, "fictional-ntfy-token")
        self.assertEqual(application._service._transport.endpoint, "https://ntfy.ke-den.de/")

    def test_authenticated_adapter_rejects_public_ntfy_and_redirects(self) -> None:
        with self.assertRaisesRegex(ValueError, "https_endpoint_required"):
            NtfyReminderTransport("https://ntfy.sh", bearer_token="fictional-token")
        transport = NtfyReminderTransport(
            reminder_cloud.NTFY_ENDPOINT,
            bearer_token="fictional-token",
        )
        requests: list = []
        handlers: list = []

        def opener(*items):
            handlers.extend(items)
            return _Opener(requests)

        with patch("src.snotes.transport.reminder_ntfy.build_opener", side_effect=opener):
            transport.send(topic="s-step06", title="FICTIONAL", message="FICTIONAL")
        self.assertEqual(len(handlers), 1)
        self.assertIsNone(
            handlers[0].redirect_request(None, None, 302, "redirect", {}, "https://ntfy.sh/x")
        )
        self.assertEqual(len(requests), 1)

    def test_backup_is_recoverable_private_rolling_and_fixed_export(self) -> None:
        self._initialize()
        generated = []
        for second in range(3):
            generated.append(
                reminder_cloud.create_backup(
                    self.database,
                    self.backups,
                    retain=2,
                    now=datetime(2026, 9, 29, 8, 0, second, tzinfo=timezone.utc),
                )
            )
        remaining = sorted(self.backups.glob("reminder-2*.db"))
        self.assertEqual(remaining, generated[1:])
        latest = self.backups / reminder_cloud.LATEST_BACKUP_NAME
        self.assertEqual(latest.stat().st_ino, generated[-1].stat().st_ino)
        self.assertEqual(stat.S_IMODE(latest.stat().st_mode), 0o600)
        with closing(sqlite3.connect(f"{latest.resolve().as_uri()}?mode=ro", uri=True)) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM reminders").fetchone()[0], 4)

        exported = io.BytesIO()
        size = reminder_cloud.export_latest_backup(self.backups, output=exported)
        self.assertEqual(size, latest.stat().st_size)
        self.assertEqual(exported.getvalue(), latest.read_bytes())
        with self.assertRaises(SystemExit):
            reminder_cloud._parser().parse_args(["export-backup", "/etc/passwd"])

    def test_export_rejects_latest_symlink_even_to_generated_backup(self) -> None:
        self._initialize()
        generated = reminder_cloud.create_backup(self.database, self.backups)
        latest = self.backups / reminder_cloud.LATEST_BACKUP_NAME
        latest.unlink()
        latest.symlink_to(generated.name)
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            reminder_cloud.export_latest_backup(self.backups, output=io.BytesIO())


if __name__ == "__main__":
    unittest.main()
