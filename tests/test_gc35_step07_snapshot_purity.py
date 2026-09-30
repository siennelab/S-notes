from __future__ import annotations

import sqlite3
from contextlib import closing
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from src.snotes.application.reminder_service import ReminderService
from src.snotes.domain.reminders import REMINDER_ACTIVE, ReminderDraft, ReminderSettings
from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.transport.reminder_http import (
    OWNER_ROLE,
    ReminderCredential,
    ReminderHttpApplication,
)


SHANGHAI = ZoneInfo("Asia/Shanghai")


class SnapshotReadPurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-step07-purity-")
        self.database_path = Path(self.temporary.name) / "fictional.db"
        self.connections = initialize_database(self.database_path)
        self.now = datetime(2026, 9, 30, 10, 0, tzinfo=SHANGHAI)
        self.repository = SQLiteReminderRepository(
            self.connections,
            clock=lambda: self.now,
        )
        self.service = ReminderService(
            self.repository,
            clock=lambda: self.now.timestamp(),
        )
        self.application = ReminderHttpApplication(
            self.service,
            {"owner-token": ReminderCredential("owner", OWNER_ROLE)},
            clock=lambda: datetime(2026, 9, 30, 2, 0, tzinfo=timezone.utc),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def snapshot(self) -> dict:
        response = self.application.handle(
            "GET",
            "/v1/reminder/snapshot",
            headers={"Authorization": "Bearer owner-token"},
        )
        self.assertEqual(response.status, 200)
        return response.body

    def database_state(self) -> dict[str, object]:
        with closing(sqlite3.connect(self.database_path)) as connection:
            return {
                "reminders": connection.execute(
                    "SELECT COUNT(*) FROM reminders"
                ).fetchone()[0],
                "occurrences": connection.execute(
                    "SELECT COUNT(*) FROM reminder_occurrences"
                ).fetchone()[0],
                "labels": connection.execute(
                    "SELECT COUNT(*) FROM reminder_labels"
                ).fetchone()[0],
                "tables": connection.execute(
                    "SELECT COUNT(*) FROM reminder_tables"
                ).fetchone()[0],
                "settings": connection.execute(
                    "SELECT setting_key, setting_value, updated_at "
                    "FROM app_settings ORDER BY setting_key"
                ).fetchall(),
            }

    def test_repeated_snapshot_with_missing_occurrence_and_settings_is_read_only(self) -> None:
        table = self.repository.default_table()
        reminder = self.repository.create(
            ReminderDraft(
                "FICTIONAL PURE READ",
                REMINDER_ACTIVE,
                {"type": "one_time", "date": "2026-10-10"},
                table_id=table.id,
            )
        )
        self.assertEqual(self.service.history(reminder.id), [])
        before = self.database_state()

        # Every connection used by the GET is opened in SQLite read-only mode.
        # A hidden INSERT, UPDATE, DELETE, or repair therefore fails this request.
        self.connections.read_only = True
        first = self.snapshot()
        second = self.snapshot()

        self.assertEqual(first["items"][0]["occurrence"], None)
        self.assertEqual(second["items"][0]["occurrence"], None)
        self.assertEqual(first["settings"]["daily_time"], "10:00")
        self.assertEqual(first["settings"]["ntfy_topic"], "")
        self.assertEqual(self.database_state(), before)

    def test_snapshot_returns_existing_occurrence_without_writing(self) -> None:
        reminder = self.service.create_reminder(
            ReminderDraft(
                "FICTIONAL EXISTING OCCURRENCE",
                REMINDER_ACTIVE,
                {"type": "one_time", "date": "2026-10-10"},
            )
        )
        occurrence = self.service.history(reminder.id)[0]
        before = self.database_state()
        self.connections.read_only = True

        item = self.snapshot()["items"][0]

        self.assertEqual(item["occurrence"]["occurrence_id"], occurrence.id)
        self.assertEqual(item["next_action_date"], "2026-10-10")
        self.assertEqual(self.database_state(), before)

    def test_completion_materializes_next_cycle_before_snapshot(self) -> None:
        reminder = self.service.create_reminder(
            ReminderDraft(
                "FICTIONAL COMPLETION LIFECYCLE",
                REMINDER_ACTIVE,
                {
                    "type": "interval_days",
                    "anchor_date": "2026-10-01",
                    "days": 90,
                },
                recurrence_mode="completion_date",
            )
        )
        self.service.complete_occurrence(reminder.id)
        history = self.service.history(reminder.id)
        self.assertEqual([item.status for item in history], ["completed", "pending"])
        self.assertEqual(history[1].action_date, "2026-12-29")
        before = self.database_state()
        self.connections.read_only = True

        item = self.snapshot()["items"][0]

        self.assertEqual(item["next_action_date"], "2026-12-29")
        self.assertEqual(self.database_state(), before)

    def test_sql_trace_and_full_rows_unchanged_over_repeated_gets(self):
        from unittest.mock import patch
        self.repository.save_settings(ReminderSettings(True, "", "https://ntfy.sh/", ""))
        table = self.repository.default_table()
        self.repository.create(ReminderDraft("FICTIONAL missing", REMINDER_ACTIVE,
            {"type": "monthly_day", "day": 15}, table_id=table.id))
        item = self.service.create_reminder(ReminderDraft("FICTIONAL recurring", REMINDER_ACTIVE,
            {"type": "interval_days", "anchor_date": "2026-09-30", "days": 10}, table_id=table.id))
        self.service.complete_occurrence(item.id)
        self.repository.delete_pending_occurrences(item.id)
        def rows():
            with closing(sqlite3.connect(self.database_path)) as connection:
                return {name: connection.execute("SELECT * FROM " + name + " ORDER BY rowid").fetchall()
                        for name in ("reminders", "reminder_occurrences", "reminder_labels", "reminder_tables", "app_settings")}
        before = rows()
        writes = []
        original = self.connections._open
        def traced():
            connection = original()
            connection.set_trace_callback(lambda sql: writes.append(sql) if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "REPLACE")) else None)
            return connection
        with patch.object(self.connections, "_open", traced):
            for _ in range(4):
                self.snapshot()
        self.assertEqual(writes, [])
        self.assertEqual(rows(), before)

    def test_settings_read_is_pure_and_explicit_initialization_persists(self) -> None:
        before = self.database_state()
        effective = self.service.settings()
        self.assertEqual(effective.daily_time, "10:00")
        self.assertEqual(effective.ntfy_topic, "")
        self.assertEqual(self.database_state(), before)

        initialized = self.service.initialize_settings()
        self.assertTrue(initialized.ntfy_topic.startswith("snotes-reminder-"))
        persisted = self.database_state()
        self.assertEqual(len(persisted["settings"]), 4)

        self.service.save_settings(
            ReminderSettings(True, "09:15", "https://ntfy.sh", "")
        )
        saved = self.service.settings()
        self.assertTrue(saved.notifications_enabled)
        self.assertEqual(saved.daily_time, "09:15")
        self.assertEqual(saved.ntfy_endpoint, "")
        self.assertTrue(saved.ntfy_topic.startswith("snotes-reminder-"))


if __name__ == "__main__":
    unittest.main()
