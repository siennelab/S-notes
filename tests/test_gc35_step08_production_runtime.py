from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from src import reminder_cloud
from src.snotes.application.reminder_service import ReminderService
from src.snotes.domain.reminder_schedule import SHANGHAI
from src.snotes.domain.reminders import REMINDER_ACTIVE, ReminderDraft
from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.transport.reminder_http import (
    OWNER_ROLE,
    ReminderCredential,
    ReminderHttpApplication,
)


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


class ProductionReminderRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-step08-runtime-")
        self.root = Path(self.temporary.name)
        self.database = self.root / "reminder-production.db"
        self.credentials = self.root / "credentials"
        self.credentials.mkdir(mode=0o700)
        self.instant = datetime(2026, 10, 3, 10, 0, tzinfo=SHANGHAI)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _credential(self, name: str, value: str) -> None:
        path = self.credentials / name
        path.write_text(value, encoding="utf-8")
        path.chmod(0o600)

    def _seed(self, *, due: bool) -> None:
        connections = initialize_database(self.database)
        self.database.chmod(0o600)
        repository = SQLiteReminderRepository(
            connections,
            clock=lambda: self.instant,
        )
        repository.set_text_setting("reminder/notifications_enabled", "1")
        repository.set_text_setting("reminder/daily_time", "10:00")
        service = ReminderService(
            repository,
            clock=lambda: self.instant.timestamp(),
        )
        table = service.create_table("FICTIONAL production runtime")
        action_date = self.instant.date()
        if not due:
            action_date += timedelta(days=1)
        service.create_reminder(
            ReminderDraft(
                title="FICTIONAL production notification",
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "one_time", "date": action_date.isoformat()},
                table_id=table.id,
            )
        )

    def _formal_runtime(self):
        return (
            patch.object(reminder_cloud, "FORMAL_DATABASE_PATH", self.database),
            patch.dict(
                os.environ,
                {
                    "CREDENTIALS_DIRECTORY": str(self.credentials),
                    reminder_cloud.PRODUCTION_NTFY_TOPIC_ENV:
                        reminder_cloud.PRODUCTION_NTFY_TOPIC,
                },
                clear=True,
            ),
        )

    def test_actual_schedule_uses_only_production_config_and_credential(self) -> None:
        self._seed(due=True)
        self._credential(reminder_cloud.NTFY_CREDENTIAL_NAME, "fictional-staging-token")
        self._credential(
            reminder_cloud.PRODUCTION_NTFY_CREDENTIAL_NAME,
            "fictional-production-token",
        )
        requests: list = []
        formal_path, environment = self._formal_runtime()
        with (
            formal_path,
            environment,
            patch(
                "src.snotes.transport.reminder_ntfy.build_opener",
                side_effect=lambda *handlers: _Opener(requests),
            ),
        ):
            self.assertEqual(
                reminder_cloud.run_schedule_once(
                    self.database,
                    clock=lambda: self.instant.timestamp(),
                ),
                1,
            )

        self.assertEqual(len(requests), 1)
        request, timeout = requests[0]
        self.assertEqual(
            request.full_url,
            "https://ntfy.ke-den.de/s-reminder-prod",
        )
        self.assertEqual(
            request.get_header("Authorization"),
            "Bearer fictional-production-token",
        )
        self.assertEqual(timeout, 10.0)
        with closing(sqlite3.connect(self.database)) as connection:
            transport_settings = connection.execute(
                """
                SELECT setting_key FROM app_settings
                WHERE setting_key IN ('reminder/ntfy_endpoint', 'reminder/ntfy_topic')
                """
            ).fetchall()
        self.assertEqual(transport_settings, [])

    def test_missing_or_wrong_production_topic_fails_before_credentials_or_network(self) -> None:
        self._seed(due=True)
        for environment in (
            {"CREDENTIALS_DIRECTORY": str(self.credentials)},
            {
                "CREDENTIALS_DIRECTORY": str(self.credentials),
                reminder_cloud.PRODUCTION_NTFY_TOPIC_ENV: "s-step06",
            },
            {
                "CREDENTIALS_DIRECTORY": str(self.credentials),
                reminder_cloud.PRODUCTION_NTFY_TOPIC_ENV: " s-reminder-prod ",
            },
        ):
            with (
                self.subTest(environment=environment),
                patch.object(reminder_cloud, "FORMAL_DATABASE_PATH", self.database),
                patch.dict(os.environ, environment, clear=True),
                patch("src.snotes.transport.reminder_ntfy.build_opener") as opener,
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "production_ntfy_configuration_invalid",
                ):
                    reminder_cloud.run_schedule_once(self.database)
            opener.assert_not_called()

    def test_production_credential_does_not_fall_back_to_staging(self) -> None:
        self._seed(due=True)
        self._credential(reminder_cloud.NTFY_CREDENTIAL_NAME, "fictional-staging-token")
        formal_path, environment = self._formal_runtime()
        with (
            formal_path,
            environment,
            patch("src.snotes.transport.reminder_ntfy.build_opener") as opener,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "credential_unavailable:reminder-ntfy-production-token",
            ):
                reminder_cloud.run_schedule_once(self.database)
        opener.assert_not_called()

    def test_no_due_notification_does_not_send(self) -> None:
        self._seed(due=False)
        self._credential(
            reminder_cloud.PRODUCTION_NTFY_CREDENTIAL_NAME,
            "fictional-production-token",
        )
        requests: list = []
        formal_path, environment = self._formal_runtime()
        with (
            formal_path,
            environment,
            patch(
                "src.snotes.transport.reminder_ntfy.build_opener",
                side_effect=lambda *handlers: _Opener(requests),
            ),
        ):
            self.assertEqual(
                reminder_cloud.run_schedule_once(
                    self.database,
                    clock=lambda: self.instant.timestamp(),
                ),
                0,
            )
        self.assertEqual(requests, [])

    def test_owner_can_write_business_settings_but_cannot_redirect_topic(self) -> None:
        self._seed(due=False)
        formal_path, environment = self._formal_runtime()
        with formal_path, environment:
            service = reminder_cloud.compose_cloud_reminder_service(
                self.database,
                clock=lambda: self.instant.timestamp(),
            )
            application = ReminderHttpApplication(
                service,
                {"owner-token": ReminderCredential("owner", OWNER_ROLE)},
            )
            rejected = application.handle(
                "POST",
                "/v1/reminder/commands/save_settings",
                headers={"Authorization": "Bearer owner-token"},
                body=json.dumps(
                    {
                        "notifications_enabled": False,
                        "daily_time": "11:30",
                        "ntfy_topic": "s-other",
                    }
                ).encode("utf-8"),
            )
            self.assertEqual(rejected.status, 400)
            saved = application.handle(
                "POST",
                "/v1/reminder/commands/save_settings",
                headers={"Authorization": "Bearer owner-token"},
                body=json.dumps(
                    {
                        "notifications_enabled": False,
                        "daily_time": "11:30",
                        "ntfy_topic": reminder_cloud.PRODUCTION_NTFY_TOPIC,
                    }
                ).encode("utf-8"),
            )
            self.assertEqual(saved.status, 200)
            effective = service.settings()

        self.assertFalse(effective.notifications_enabled)
        self.assertEqual(effective.daily_time, "11:30")
        self.assertEqual(effective.ntfy_endpoint, reminder_cloud.NTFY_ENDPOINT)
        self.assertEqual(effective.ntfy_topic, reminder_cloud.PRODUCTION_NTFY_TOPIC)
        with closing(sqlite3.connect(self.database)) as connection:
            settings = dict(
                connection.execute(
                    "SELECT setting_key, setting_value FROM app_settings"
                )
            )
        self.assertEqual(settings["reminder/notifications_enabled"], "0")
        self.assertEqual(settings["reminder/daily_time"], "11:30")
        self.assertNotIn("reminder/ntfy_endpoint", settings)
        self.assertNotIn("reminder/ntfy_topic", settings)

    def test_backup_directory_cli_is_scoped_and_formal_default_is_separate(self) -> None:
        selected = self.root / "production-backups"
        with patch.object(reminder_cloud, "create_backup", return_value=selected) as backup:
            self.assertEqual(
                reminder_cloud.main(
                    [
                        "backup",
                        "--database",
                        str(self.database),
                        "--backup-directory",
                        str(selected),
                    ]
                ),
                0,
            )
        backup.assert_called_once_with(self.database, selected)

        with patch.object(reminder_cloud, "serve_reminder_api") as serve:
            self.assertEqual(
                reminder_cloud.main(
                    ["serve", "--backup-directory", str(selected)]
                ),
                2,
            )
        serve.assert_not_called()

        with (
            patch.object(reminder_cloud, "FORMAL_DATABASE_PATH", self.database),
            patch.object(reminder_cloud, "create_backup", return_value=selected) as backup,
        ):
            self.assertEqual(
                reminder_cloud.main(["backup", "--database", str(self.database)]),
                0,
            )
        backup.assert_called_once_with(
            self.database,
            reminder_cloud.PRODUCTION_BACKUP_DIRECTORY,
        )

        with patch.object(reminder_cloud, "export_latest_backup") as export:
            self.assertEqual(
                reminder_cloud.main(
                    ["export-backup", "--backup-directory", str(selected)]
                ),
                0,
            )
        export.assert_called_once_with(selected)


if __name__ == "__main__":
    unittest.main()
