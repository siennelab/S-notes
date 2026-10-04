from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.snotes.application.reminder_service import ReminderService
from src.snotes.domain.reminders import (
    REMINDER_ACTIVE,
    REMINDER_COMPLETED,
    ReminderDraft,
    ReminderQuery,
)
from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
from src.snotes.storage.schema import initialize_database


SHANGHAI = ZoneInfo("Asia/Shanghai")


class ReminderNoOccurrenceDisplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="snotes-no-occurrence-display-"
        )
        self.connections = initialize_database(
            Path(self.temporary.name) / "fictional.db"
        )
        self.now = datetime(2026, 9, 10, 10, 0, tzinfo=SHANGHAI)
        self.repository = SQLiteReminderRepository(
            self.connections,
            clock=lambda: self.now,
        )
        self.service = ReminderService(
            self.repository,
            clock=lambda: self.now.timestamp(),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create(
        self,
        title: str,
        action_spec: dict,
        deadline_spec: dict | None = None,
    ):
        return self.service.create_reminder(
            ReminderDraft(
                title,
                REMINDER_ACTIVE,
                action_spec,
                deadline_spec=deadline_spec,
            )
        )

    def test_active_reminders_with_current_occurrences_keep_existing_statuses(self) -> None:
        self.now = datetime(2026, 8, 1, 10, 0, tzinfo=SHANGHAI)
        self.create(
            "FICTIONAL UPCOMING",
            {"type": "one_time", "date": "2026-09-20"},
        )
        self.create(
            "FICTIONAL DUE",
            {"type": "one_time", "date": "2026-09-10"},
        )
        self.create(
            "FICTIONAL OVERDUE",
            {"type": "one_time", "date": "2026-09-01"},
            {"type": "one_time", "date": "2026-09-05"},
        )
        self.now = datetime(2026, 9, 10, 10, 0, tzinfo=SHANGHAI)

        items = {
            item.reminder.title: item for item in self.service.list_reminders()
        }

        self.assertEqual(items["FICTIONAL UPCOMING"].status, "upcoming")
        self.assertEqual(items["FICTIONAL DUE"].status, "due")
        self.assertEqual(items["FICTIONAL OVERDUE"].status, "overdue")
        self.assertTrue(all(item.occurrence is not None for item in items.values()))

    def test_ai_current_completion_keeps_active_reminder_display_active(self) -> None:
        reminder = self.create(
            "FICTIONAL AI CURRENT COMPLETION",
            {"type": "one_time", "date": "2026-09-10"},
        )

        result = self.service.ai_complete_item(
            actor_target_id="fictional-actor",
            request_id="fictional-current-completion",
            item_id=reminder.id,
        )
        item = self.service.get_reminder(reminder.id)

        self.assertEqual(result["status"], "updated")
        self.assertIsNotNone(item)
        self.assertEqual(item.reminder.lifecycle, REMINDER_ACTIVE)
        self.assertIsNone(item.reminder.completed_at)
        self.assertIsNone(item.occurrence)
        self.assertEqual(item.status, REMINDER_ACTIVE)
        self.assertEqual(item.status_label, "启用")
        self.assertEqual(
            [occurrence.status for occurrence in self.service.history(reminder.id)],
            ["completed"],
        )
        self.assertNotIn(
            reminder.id,
            {
                row.reminder.id
                for row in self.service.list_reminders(
                    ReminderQuery(lifecycle=REMINDER_COMPLETED)
                )
            },
        )

    def test_missing_occurrence_read_is_active_and_does_not_materialize_history(self) -> None:
        reminder = self.repository.create(
            ReminderDraft(
                "FICTIONAL MISSING OCCURRENCE",
                REMINDER_ACTIVE,
                {"type": "one_time", "date": "2026-09-20"},
            )
        )
        self.assertEqual(self.service.history(reminder.id), [])
        self.connections.read_only = True

        first = self.service.get_reminder(reminder.id)
        second = self.service.list_reminders()[0]

        self.assertIsNotNone(first)
        self.assertEqual(first.status, REMINDER_ACTIVE)
        self.assertIsNone(first.occurrence)
        self.assertEqual(second.status, REMINDER_ACTIVE)
        self.assertIsNone(second.occurrence)
        self.assertEqual(self.service.history(reminder.id), [])

    def test_whole_reminder_completion_still_displays_completed(self) -> None:
        reminder = self.create(
            "FICTIONAL WHOLE COMPLETION",
            {"type": "one_time", "date": "2026-09-10"},
        )

        self.service.complete_reminder(reminder.id)
        item = self.service.get_reminder(reminder.id)

        self.assertIsNotNone(item)
        self.assertEqual(item.reminder.lifecycle, REMINDER_COMPLETED)
        self.assertIsNotNone(item.reminder.completed_at)
        self.assertEqual(item.status, REMINDER_COMPLETED)
        self.assertEqual(item.status_label, "已完成")


if __name__ == "__main__":
    unittest.main()
