from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from scripts import gc35_step08c_live_acceptance as live
from src import reminder_cloud
from src.snotes.application.reminder_service import ReminderService
from src.snotes.domain.reminder_schedule import SHANGHAI
from src.snotes.domain.reminders import REMINDER_ACTIVE, ReminderDraft
from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
from src.snotes.storage.schema import initialize_database


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


class Step08CLiveAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-step08c-live-")
        self.root = Path(self.temporary.name)
        self.database = self.root / "reminder-production.db"
        self.credentials = self.root / "credentials"
        self.credentials.mkdir(mode=0o700)
        (self.credentials / reminder_cloud.PRODUCTION_NTFY_CREDENTIAL_NAME).write_text(
            "fictional-production-token",
            encoding="utf-8",
        )
        (self.credentials / reminder_cloud.PRODUCTION_NTFY_CREDENTIAL_NAME).chmod(0o600)
        self.action_date = date(2026, 10, 17)
        self.deadline_date = date(2026, 10, 24)
        creation_instant = datetime(2026, 10, 1, 10, 0, tzinfo=SHANGHAI)
        connections = initialize_database(self.database)
        self.database.chmod(0o600)
        repository = SQLiteReminderRepository(
            connections,
            clock=lambda: creation_instant,
        )
        repository.set_text_setting("reminder/notifications_enabled", "1")
        repository.set_text_setting("reminder/daily_time", "10:00")
        service = ReminderService(repository, clock=lambda: creation_instant.timestamp())
        table = service.create_table("FICTIONAL Step 8C live acceptance")
        fixture = service.create_reminder(
            ReminderDraft(
                title=live.DEADLINE_TITLE,
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "one_time", "date": self.action_date.isoformat()},
                deadline_spec={"type": "one_time", "date": self.deadline_date.isoformat()},
                note=live.FIXTURE_NOTE,
                table_id=table.id,
                multi_notify=True,
            )
        )
        start_fixture = service.create_reminder(
            ReminderDraft(
                title=live.START_TITLE,
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "one_time", "date": self.deadline_date.isoformat()},
                note=live.FIXTURE_NOTE,
                table_id=table.id,
                multi_notify=True,
            )
        )
        private = service.create_reminder(
            ReminderDraft(
                title="PRIVATE reminder must not send",
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "one_time", "date": self.action_date.isoformat()},
                deadline_spec={"type": "one_time", "date": self.deadline_date.isoformat()},
                note="private",
                table_id=table.id,
                multi_notify=True,
            )
        )
        self.fixture_id = fixture.id
        self.start_fixture_id = start_fixture.id
        self.private_id = private.id
        occurrence = repository.list_occurrences(fixture.id)[0]
        start_occurrence = repository.list_occurrences(start_fixture.id)[0]
        self.occurrence_id = occurrence.id
        self.start_occurrence_id = start_occurrence.id
        self.state = self.root / "fixture.json"
        self._write_state()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_state(
        self,
        *,
        deadline_reminder_id: int | None = None,
        deadline_occurrence_id: int | None = None,
    ) -> None:
        self.state.write_text(
            json.dumps(
                {
                    "schema": live.STATE_SCHEMA,
                    "purpose": live.STATE_PURPOSE,
                    "database": str(self.database),
                    "topic": reminder_cloud.PRODUCTION_NTFY_TOPIC,
                    "fixtures": [
                        {
                            "reminder_id": deadline_reminder_id or self.fixture_id,
                            "occurrence_id": deadline_occurrence_id or self.occurrence_id,
                            "title": live.DEADLINE_TITLE,
                            "action_date": self.action_date.isoformat(),
                            "deadline_date": self.deadline_date.isoformat(),
                        },
                        {
                            "reminder_id": self.start_fixture_id,
                            "occurrence_id": self.start_occurrence_id,
                            "title": live.START_TITLE,
                            "action_date": self.deadline_date.isoformat(),
                            "deadline_date": None,
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.state.chmod(0o600)

    def _environment(self):
        return patch.dict(
            os.environ,
            {
                "CREDENTIALS_DIRECTORY": str(self.credentials),
                reminder_cloud.PRODUCTION_NTFY_TOPIC_ENV:
                    reminder_cloud.PRODUCTION_NTFY_TOPIC,
            },
            clear=True,
        )

    def _private_occurrence(self) -> tuple:
        with closing(sqlite3.connect(self.database)) as connection:
            return connection.execute(
                """
                SELECT action_notified_at, deadline_notified_at,
                       advance_7_notified_at, advance_3_notified_at,
                       advance_1_notified_at
                FROM reminder_occurrences WHERE reminder_id = ?
                """,
                (self.private_id,),
            ).fetchone()

    def test_real_schedule_is_scoped_and_four_nodes_persist_once(self) -> None:
        requests: list = []
        with (
            patch.object(reminder_cloud, "FORMAL_DATABASE_PATH", self.database),
            self._environment(),
            patch(
                "src.snotes.transport.reminder_ntfy.build_opener",
                side_effect=lambda *handlers: _Opener(requests),
            ),
        ):
            for fixture_id, normal_kind in (
                (self.fixture_id, "deadline"),
                (self.start_fixture_id, "action"),
            ):
                for business_date, expected_kind in (
                    (date(2026, 10, 17), "advance_7"),
                    (date(2026, 10, 21), "advance_3"),
                    (date(2026, 10, 23), "advance_1"),
                    (date(2026, 10, 24), normal_kind),
                ):
                    result = live.run_multi_node(
                        self.state,
                        database=self.database,
                        fixture_id=fixture_id,
                        business_date=business_date,
                        expect_topic=reminder_cloud.PRODUCTION_NTFY_TOPIC,
                    )
                    self.assertEqual(result["node"], expected_kind)
                    self.assertEqual(result["first_pass_sent"], 1)
                    self.assertEqual(result["duplicate_pass_sent"], 0)

        self.assertEqual(len(requests), 8)
        self.assertTrue(all(
            request.full_url == "https://ntfy.ke-den.de/s-reminder-prod"
            and request.get_header("Authorization") == "Bearer fictional-production-token"
            for request, _ in requests
        ))
        self.assertEqual(self._private_occurrence(), (None, None, None, None, None))

    def test_state_cannot_select_an_unmarked_private_reminder(self) -> None:
        with closing(sqlite3.connect(self.database)) as connection:
            occurrence_id = connection.execute(
                "SELECT id FROM reminder_occurrences WHERE reminder_id = ?",
                (self.private_id,),
            ).fetchone()[0]
        self._write_state(
            deadline_reminder_id=self.private_id,
            deadline_occurrence_id=occurrence_id,
        )
        requests: list = []
        with (
            patch.object(reminder_cloud, "FORMAL_DATABASE_PATH", self.database),
            self._environment(),
            patch(
                "src.snotes.transport.reminder_ntfy.build_opener",
                side_effect=lambda *handlers: _Opener(requests),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "fixture_invalid"):
                live.run_multi_node(
                    self.state,
                    database=self.database,
                    fixture_id=self.private_id,
                    business_date=date(2026, 10, 17),
                    expect_topic=reminder_cloud.PRODUCTION_NTFY_TOPIC,
                )
        self.assertEqual(requests, [])
        self.assertEqual(self._private_occurrence(), (None, None, None, None, None))


if __name__ == "__main__":
    unittest.main()
