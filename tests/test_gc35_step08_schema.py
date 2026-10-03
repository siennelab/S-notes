from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.snotes.application.reminder_service import ReminderService
from src.snotes.domain.reminder_remote_contract import REMINDER_WIRE_SCHEMA_VERSION
from src.snotes.domain.reminders import REMINDER_ACTIVE, ReminderDraft
from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
from src.snotes.storage.schema import (
    REMINDER_SCHEMA_VERSION,
    REMINDER_SCHEMA_VERSION_KEY,
    initialize_database,
)
from src.snotes.transport.reminder_http import _item_payload


NOW = datetime(2026, 10, 2, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
HISTORY_DELETED_AT = "2026-08-15T09:10:11+08:00"
HISTORY_TRASH_GROUP_ID = "fictional-legacy-item-trash-group"


class Step08ReminderSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-step08-schema-")
        self.database = Path(self.temporary.name) / "candidate.db"
        self.connections = initialize_database(self.database)
        self.repository = SQLiteReminderRepository(
            self.connections,
            clock=lambda: NOW,
        )
        self.service = ReminderService(
            self.repository,
            clock=lambda: NOW.timestamp(),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _create_reminder(self):
        return self.service.create_reminder(
            ReminderDraft(
                title="FICTIONAL_STEP08_SCHEMA",
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "one_time", "date": "2026-10-20"},
                note="FICTIONAL_HISTORY_PRESERVATION",
            )
        )

    def _reminder_columns(self) -> dict[str, sqlite3.Row]:
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            return {
                str(row["name"]): row
                for row in connection.execute('PRAGMA table_info("reminders")')
            }

    def _schema_version(self) -> str:
        with closing(sqlite3.connect(self.database)) as connection:
            row = connection.execute(
                "SELECT meta_value FROM schema_meta WHERE meta_key = ?",
                (REMINDER_SCHEMA_VERSION_KEY,),
            ).fetchone()
        self.assertIsNotNone(row)
        return str(row[0])

    def test_fresh_bootstrap_has_nullable_history_columns_at_v5(self) -> None:
        columns = self._reminder_columns()

        self.assertEqual(REMINDER_SCHEMA_VERSION, "5")
        self.assertEqual(self._schema_version(), "5")
        for name in ("deleted_at", "trash_group_id"):
            self.assertIn(name, columns)
            self.assertEqual(str(columns[name]["type"]).upper(), "TEXT")
            self.assertEqual(int(columns[name]["notnull"]), 0)
            self.assertIsNone(columns[name]["dflt_value"])
        self.assertNotIn("label_id", columns)
        self.assertNotIn("ai_visible", columns)

    def test_v4_without_history_columns_upgrades_additively(self) -> None:
        reminder = self._create_reminder()
        before = self.repository.get(reminder.id)
        with self.connections.write() as connection:
            connection.execute("ALTER TABLE reminders DROP COLUMN deleted_at")
            connection.execute("ALTER TABLE reminders DROP COLUMN trash_group_id")
            connection.execute(
                "UPDATE schema_meta SET meta_value = '4' WHERE meta_key = ?",
                (REMINDER_SCHEMA_VERSION_KEY,),
            )

        initialize_database(self.database)

        columns = self._reminder_columns()
        self.assertIn("deleted_at", columns)
        self.assertIn("trash_group_id", columns)
        self.assertEqual(self._schema_version(), "5")
        with self.connections.read() as connection:
            row = connection.execute(
                "SELECT deleted_at, trash_group_id FROM reminders WHERE id = ?",
                (reminder.id,),
            ).fetchone()
        self.assertEqual(tuple(row), (None, None))
        self.assertEqual(self.repository.get(reminder.id), before)

    def test_v4_existing_history_values_survive_upgrade_and_reinitialization(self) -> None:
        reminder = self._create_reminder()
        domain_before = self.repository.get(reminder.id)
        with self.connections.write() as connection:
            connection.execute(
                """
                UPDATE reminders
                SET deleted_at = ?, trash_group_id = ?
                WHERE id = ?
                """,
                (HISTORY_DELETED_AT, HISTORY_TRASH_GROUP_ID, reminder.id),
            )
            connection.execute(
                "UPDATE schema_meta SET meta_value = '4' WHERE meta_key = ?",
                (REMINDER_SCHEMA_VERSION_KEY,),
            )

        initialize_database(self.database)
        initialize_database(self.database)

        with self.connections.read() as connection:
            row = connection.execute(
                "SELECT deleted_at, trash_group_id FROM reminders WHERE id = ?",
                (reminder.id,),
            ).fetchone()
        self.assertEqual(
            tuple(row),
            (HISTORY_DELETED_AT, HISTORY_TRASH_GROUP_ID),
        )
        self.assertEqual(self.repository.get(reminder.id), domain_before)

    def test_history_columns_do_not_change_domain_or_wire_contract(self) -> None:
        reminder = self._create_reminder()
        with self.connections.write() as connection:
            connection.execute(
                """
                UPDATE reminders
                SET deleted_at = ?, trash_group_id = ?
                WHERE id = ?
                """,
                (HISTORY_DELETED_AT, HISTORY_TRASH_GROUP_ID, reminder.id),
            )

        # Repository reads use SELECT * in active paths; named mapping must stay safe.
        stored = self.repository.get(reminder.id)
        self.assertEqual(stored, reminder)
        self.assertFalse(hasattr(stored, "deleted_at"))
        self.assertFalse(hasattr(stored, "trash_group_id"))

        item = self.service.get_reminder(reminder.id)
        self.assertIsNotNone(item)
        payload = _item_payload(item)
        self.assertEqual(REMINDER_WIRE_SCHEMA_VERSION, 4)
        self.assertNotIn("deleted_at", payload)
        self.assertNotIn("trash_group_id", payload)

        with self.connections.read() as connection:
            row = connection.execute(
                "SELECT deleted_at, trash_group_id FROM reminders WHERE id = ?",
                (reminder.id,),
            ).fetchone()
        self.assertEqual(
            tuple(row),
            (HISTORY_DELETED_AT, HISTORY_TRASH_GROUP_ID),
        )


if __name__ == "__main__":
    unittest.main()
