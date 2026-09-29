from __future__ import annotations

import sqlite3
import tempfile
import unittest
from email.header import decode_header, make_header
from http.client import HTTPConnection
from unittest.mock import MagicMock, patch
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from src.snotes.application.reminder_service import ReminderService
from src.snotes.background.reminder import (
    LAUNCH_AGENT_INTERVAL_SECONDS,
    desired_launch_agent,
    write_launch_agent_template,
)
from src.snotes.bootstrap import compose_reminder_service
from src.snotes.domain.reminder_schedule import (
    is_cn_a_share_trading_day,
    lunar_to_solar,
)
from src.snotes.domain.reminders import (
    REMINDER_ACTIVE,
    REMINDER_COMPLETED,
    REMINDER_PAUSED,
    ReminderDraft,
    ReminderQuery,
    ReminderSettings,
)
from src.snotes.storage.repositories.reminders import SQLiteReminderRepository
from src.snotes.storage.schema import initialize_database
from src.snotes.transport.reminder_ntfy import NtfyReminderTransport


SHANGHAI = ZoneInfo("Asia/Shanghai")


class FakeClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value.timestamp()

    def set(self, value: datetime) -> None:
        self.value = value


class FakeTransport:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, str]] = []

    def send(self, *, topic: str, title: str, message: str) -> None:
        if self.fail:
            raise RuntimeError("FICTIONAL_NTFY_FAILURE")
        self.calls.append({"topic": topic, "title": title, "message": message})


class ReminderR1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="snotes-reminder-r1-")
        self.database_path = Path(self.temporary.name) / "fictional.db"
        self.connections = initialize_database(self.database_path)
        self.clock = FakeClock(datetime(2026, 9, 10, 10, 0, tzinfo=SHANGHAI))
        self.transport = FakeTransport()
        self.repository = SQLiteReminderRepository(
            self.connections,
            clock=lambda: datetime.fromtimestamp(self.clock(), SHANGHAI),
        )
        self.service = ReminderService(
            self.repository,
            transport=self.transport,
            clock=self.clock,
        )
        self.service.settings()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create(self, title: str, action_spec: dict, deadline_spec: dict | None = None):
        return self.service.create_reminder(
            ReminderDraft(
                title=title,
                lifecycle=REMINDER_ACTIVE,
                action_spec=action_spec,
                deadline_spec=deadline_spec,
                note="FICTIONAL_NOTE",
            )
        )

    def enable_notifications(self, daily_time: str = "10:00") -> None:
        settings = self.service.settings()
        self.service.save_settings(
            ReminderSettings(
                notifications_enabled=True,
                daily_time=daily_time,
                ntfy_endpoint=settings.ntfy_endpoint,
                ntfy_topic=settings.ntfy_topic,
            )
        )

    def test_one_time_due_and_complete(self) -> None:
        reminder = self.create(
            "FICTIONAL_ONE_TIME",
            {"type": "one_time", "date": "2026-09-10"},
        )
        item = self.service.list_reminders()[0]
        self.assertEqual(item.status, "due")
        self.assertEqual(self.service.badge_count(), 1)
        self.service.complete_reminder(reminder.id)
        self.assertEqual(self.service.list_reminders()[0].status, "completed")
        self.assertEqual(self.service.badge_count(), 0)

    def test_monthly_action_deadline_and_badge_semantics(self) -> None:
        self.create(
            "FICTIONAL_MONTHLY_BILL",
            {"type": "monthly_day", "day": 10},
            {"type": "monthly_day", "day": 15},
        )
        self.create("FICTIONAL_FUTURE", {"type": "one_time", "date": "2026-09-20"})
        paused = self.create("FICTIONAL_PAUSED", {"type": "one_time", "date": "2026-09-01"})
        self.service.pause_reminder(paused.id)
        self.assertEqual(self.service.badge_count(), 1)
        self.clock.set(datetime(2026, 9, 16, 10, 0, tzinfo=SHANGHAI))
        items = {item.reminder.title: item for item in self.service.list_reminders()}
        self.assertEqual(items["FICTIONAL_MONTHLY_BILL"].status, "overdue")
        self.assertEqual(self.service.badge_count(), 1)

    def test_action_equal_deadline_sends_only_one_notification(self) -> None:
        self.create(
            "FICTIONAL_SAME_DAY",
            {"type": "one_time", "date": "2026-09-10"},
            {"type": "same_day"},
        )
        self.assertEqual(self.service.dispatch_due_notifications(), 1)
        self.assertEqual(len(self.transport.calls), 1)
        self.assertIn("最晚处理日", self.transport.calls[0]["message"])
        self.assertEqual(self.service.dispatch_due_notifications(), 0)

    def test_recurring_complete_occurrence_leaves_next_cycle_active(self) -> None:
        reminder = self.create(
            "FICTIONAL_RECURRING",
            {"type": "monthly_day", "day": 10},
            {"type": "monthly_day", "day": 15},
        )
        self.service.complete_occurrence(reminder.id)
        item = self.service.list_reminders()[0]
        self.assertEqual(item.next_action_date, "2026-10-10")
        self.assertEqual(item.next_deadline_date, "2026-10-15")
        self.assertNotEqual(item.next_deadline_date, "2026-08-15")
        self.assertEqual(item.status, "occurrence_completed")
        self.clock.set(datetime(2026, 10, 10, 10, 0, tzinfo=SHANGHAI))
        item = self.service.list_reminders()[0]
        self.assertEqual(item.next_action_date, "2026-10-10")
        self.assertEqual(item.next_deadline_date, "2026-10-15")
        self.assertEqual(item.status, "due")

    def test_editing_schedule_refreshes_uncompleted_occurrence(self) -> None:
        self.clock.set(datetime(2026, 8, 21, 10, 0, tzinfo=SHANGHAI))
        reminder = self.create(
            "FICTIONAL_CELLFIE",
            {"type": "monthly_day", "day": 4},
        )
        item = self.service.list_reminders()[0]
        self.assertEqual(item.next_action_date, "2026-09-04")
        self.assertIsNone(item.next_deadline_date)

        self.service.update_reminder(
            reminder.id,
            ReminderDraft(
                title="FICTIONAL_CELLFIE",
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "monthly_day", "day": 1},
                deadline_spec={"type": "monthly_day", "day": 3},
                note="FICTIONAL_NOTE",
            ),
        )

        item = self.service.list_reminders()[0]
        self.assertEqual(item.next_action_date, "2026-09-01")
        self.assertEqual(item.next_deadline_date, "2026-09-03")
        occurrences = self.service.history(reminder.id)
        self.assertEqual([occurrence.action_date for occurrence in occurrences], ["2026-09-01"])

    def test_completion_suppresses_same_occurrence_deadline(self) -> None:
        reminder = self.create(
            "FICTIONAL_DEADLINE_SUPPRESSED",
            {"type": "monthly_day", "day": 10},
            {"type": "monthly_day", "day": 15},
        )
        self.service.complete_occurrence(reminder.id)
        self.clock.set(datetime(2026, 9, 16, 10, 0, tzinfo=SHANGHAI))
        self.assertEqual(self.service.dispatch_due_notifications(), 0)

    def test_pause_resume_and_delete_history(self) -> None:
        reminder = self.create("FICTIONAL_MUTABLE", {"type": "one_time", "date": "2026-09-10"})
        self.service.pause_reminder(reminder.id)
        self.assertEqual(self.service.list_reminders()[0].status, "paused")
        self.service.resume_reminder(reminder.id)
        self.assertEqual(self.service.list_reminders()[0].status, "due")
        self.service.dispatch_due_notifications()
        self.assertEqual(len(self.service.history(reminder.id)), 1)
        self.service.delete_reminder(reminder.id)
        self.assertEqual(self.service.list_reminders(), [])
        with closing(sqlite3.connect(self.database_path)) as connection:
            count = connection.execute("SELECT COUNT(*) FROM reminder_occurrences").fetchone()[0]
        self.assertEqual(count, 0)

    def test_completed_reminder_cannot_be_resumed_and_keeps_completion_state(self) -> None:
        reminder = self.create(
            "FICTIONAL_COMPLETED_RESUME_GUARD",
            {"type": "monthly_day", "day": 10},
        )
        self.service.complete_reminder(reminder.id)
        completed = self.service.get_reminder(reminder.id)
        self.assertIsNotNone(completed)
        completed_at = completed.reminder.completed_at
        occurrences = self.service.history(reminder.id)

        with self.assertRaisesRegex(ValueError, "completed_reminder_cannot_resume"):
            self.service.resume_reminder(reminder.id)

        unchanged = self.service.get_reminder(reminder.id)
        self.assertIsNotNone(unchanged)
        self.assertEqual(unchanged.reminder.lifecycle, REMINDER_COMPLETED)
        self.assertEqual(unchanged.reminder.completed_at, completed_at)
        self.assertEqual(self.service.history(reminder.id), occurrences)

    def test_ai_resume_rejects_completed_item_and_allows_paused_item(self) -> None:
        completed = self.create(
            "FICTIONAL_AI_COMPLETED_RESUME_GUARD",
            {"type": "one_time", "date": "2026-09-10"},
        )
        self.service.complete_reminder(completed.id)
        completed_before = self.service.get_reminder(completed.id)
        self.assertIsNotNone(completed_before)
        completed_at = completed_before.reminder.completed_at
        self.assertIsNotNone(completed_at)
        history_before = self.service.history(completed.id)

        rejected = self.service.ai_resume_item(
            actor_target_id="gale",
            request_id="fictional-completed-resume",
            item_id=completed.id,
        )
        retry = self.service.ai_resume_item(
            actor_target_id="gale",
            request_id="fictional-completed-resume",
            item_id=completed.id,
        )

        self.assertEqual(rejected["status"], "rejected")
        self.assertIn("已完成", rejected["reason"])
        self.assertEqual(retry["status"], "already_processed")
        self.assertEqual(retry["previous_status"], "rejected")
        unchanged = self.service.get_reminder(completed.id)
        self.assertIsNotNone(unchanged)
        self.assertEqual(unchanged.reminder, completed_before.reminder)
        self.assertEqual(unchanged.reminder.lifecycle, REMINDER_COMPLETED)
        self.assertEqual(unchanged.reminder.completed_at, completed_at)
        self.assertEqual(self.service.history(completed.id), history_before)

        paused = self.create(
            "FICTIONAL_AI_PAUSED_RESUME",
            {"type": "one_time", "date": "2026-09-20"},
        )
        pause = self.service.ai_pause_item(
            actor_target_id="gale",
            request_id="fictional-active-pause",
            item_id=paused.id,
        )
        self.assertEqual(pause["status"], "updated")
        self.assertEqual(self.service.get_reminder(paused.id).reminder.lifecycle, REMINDER_PAUSED)
        resumed = self.service.ai_resume_item(
            actor_target_id="gale",
            request_id="fictional-paused-resume",
            item_id=paused.id,
        )
        self.assertEqual(resumed["status"], "updated")
        self.assertEqual(self.service.get_reminder(paused.id).reminder.lifecycle, REMINDER_ACTIVE)
        complete = self.service.ai_complete_item(
            actor_target_id="gale",
            request_id="fictional-resumed-complete",
            item_id=paused.id,
            current_occurrence_only=False,
        )
        self.assertEqual(complete["status"], "updated")
        completed_after_resume = self.service.get_reminder(paused.id)
        self.assertEqual(completed_after_resume.reminder.lifecycle, REMINDER_COMPLETED)
        self.assertIsNotNone(completed_after_resume.reminder.completed_at)

    def test_default_sorting_puts_most_actionable_first(self) -> None:
        self.clock.set(datetime(2026, 8, 1, 10, 0, tzinfo=SHANGHAI))
        self.create("FICTIONAL_FUTURE_LATE", {"type": "one_time", "date": "2026-09-25"})
        self.create("FICTIONAL_TODAY", {"type": "one_time", "date": "2026-09-10"})
        self.create(
            "FICTIONAL_OVERDUE",
            {"type": "one_time", "date": "2026-09-01"},
            {"type": "one_time", "date": "2026-09-05"},
        )
        self.create("FICTIONAL_FUTURE_SOON", {"type": "one_time", "date": "2026-09-12"})
        paused = self.create("FICTIONAL_PAUSED_SORT", {"type": "one_time", "date": "2026-09-01"})
        done = self.create("FICTIONAL_DONE_SORT", {"type": "one_time", "date": "2026-09-01"})
        recurring = self.create("FICTIONAL_RECURRING_DONE_SORT", {"type": "monthly_day", "day": 10})
        self.clock.set(datetime(2026, 9, 10, 10, 0, tzinfo=SHANGHAI))
        self.service.pause_reminder(paused.id)
        self.service.complete_reminder(done.id)
        self.service.complete_occurrence(recurring.id)
        titles = [item.reminder.title for item in self.service.list_reminders()]
        self.assertEqual(
            titles,
            [
                "FICTIONAL_OVERDUE",
                "FICTIONAL_TODAY",
                "FICTIONAL_FUTURE_SOON",
                "FICTIONAL_FUTURE_LATE",
                "FICTIONAL_RECURRING_DONE_SORT",
                "FICTIONAL_PAUSED_SORT",
                "FICTIONAL_DONE_SORT",
            ],
        )

    def test_sorting_status_uses_shanghai_business_day_under_hawaii_host(self) -> None:
        self.clock.set(datetime(2026, 8, 23, 16, 30, tzinfo=ZoneInfo("Pacific/Honolulu")))
        self.create("FICTIONAL_SHANGHAI_TODAY", {"type": "one_time", "date": "2026-08-24"})
        self.create("FICTIONAL_SHANGHAI_FUTURE", {"type": "one_time", "date": "2026-08-25"})

        items = self.service.list_reminders()

        self.assertEqual([item.reminder.title for item in items], [
            "FICTIONAL_SHANGHAI_TODAY",
            "FICTIONAL_SHANGHAI_FUTURE",
        ])
        self.assertEqual(items[0].status, "due")
        self.assertEqual(items[0].next_action_date, "2026-08-24")
        self.assertEqual(items[1].status, "upcoming")

    def test_repeat_until(self) -> None:
        self.create(
            "FICTIONAL_REPEAT_UNTIL",
            {"type": "monthly_day", "day": 10, "repeat_until": "2026-09-30"},
        )
        self.assertEqual(self.service.list_reminders()[0].status, "due")
        self.clock.set(datetime(2026, 10, 1, 10, 0, tzinfo=SHANGHAI))
        self.service.complete_occurrence(self.service.list_reminders()[0].reminder.id)
        self.assertIsNone(self.service.list_reminders()[0].next_action_date)

    def test_repeat_until_is_independent_from_occurrence_deadline(self) -> None:
        self.clock.set(datetime(2027, 2, 7, 10, 0, tzinfo=SHANGHAI))
        reminder = self.create(
            "FICTIONAL_GEMINI",
            {"type": "monthly_day", "day": 7, "repeat_until": "2027-02-07"},
            {"type": "monthly_day", "day": 7},
        )
        item = self.service.list_reminders()[0]
        self.assertEqual(item.next_action_date, "2027-02-07")
        self.assertEqual(item.next_deadline_date, "2027-02-07")
        self.assertEqual(self.service.dispatch_due_notifications(), 1)
        self.assertEqual(len(self.transport.calls), 1)
        self.service.complete_occurrence(reminder.id)
        item = self.service.list_reminders()[0]
        self.assertEqual(item.status, "occurrence_completed")
        self.assertIsNone(item.next_action_date)
        self.assertIsNone(item.next_deadline_date)

    def test_interval_from_completion_uses_actual_completion_date(self) -> None:
        self.clock.set(datetime(2026, 9, 4, 10, 0, tzinfo=SHANGHAI))
        reminder = self.create(
            "FICTIONAL_90_DAY_KEEPALIVE",
            {"type": "interval_from_completion", "anchor_date": "2026-09-01", "days": 90},
        )
        self.assertIsNone(self.service.list_reminders()[0].next_deadline_date)
        self.service.complete_occurrence(reminder.id)
        item = self.service.list_reminders()[0]
        self.assertEqual(item.next_action_date, "2026-12-03")
        self.assertIsNone(item.next_deadline_date)

    def test_offset_deadline_applies_to_every_supported_recurrence_occurrence(self) -> None:
        one_time = self.create(
            "FICTIONAL_ONE_TIME_WINDOW",
            {"type": "one_time", "date": "2026-09-10"},
            {"type": "one_time", "date": "2026-09-12"},
        )
        item = self.service.get_reminder(one_time.id)
        self.assertEqual(item.next_action_date, "2026-09-10")
        self.assertEqual(item.next_deadline_date, "2026-09-12")

        cases = [
            (
                "FICTIONAL_MONTHLY_WINDOW",
                {"type": "monthly_day", "day": 10},
                "2026-09-10",
                "2026-10-10",
            ),
            (
                "FICTIONAL_YEARLY_WINDOW",
                {"type": "yearly_date", "month": 9, "day": 10},
                "2026-09-10",
                "2027-09-10",
            ),
            (
                "FICTIONAL_INTERVAL_WINDOW",
                {"type": "interval_days", "anchor_date": "2026-09-10", "days": 5},
                "2026-09-10",
                "2026-09-15",
            ),
            (
                "FICTIONAL_COMPLETION_WINDOW",
                {"type": "interval_from_completion", "anchor_date": "2026-09-05", "days": 5},
                "2026-09-10",
                "2026-09-15",
            ),
            (
                "FICTIONAL_NTH_WEEKDAY_WINDOW",
                {"type": "nth_weekday", "nth": 2, "weekday": 3},
                "2026-09-10",
                "2026-10-08",
            ),
            (
                "FICTIONAL_YEARLY_NTH_WEEKDAY_WINDOW",
                {"type": "nth_weekday", "month": 9, "nth": 2, "weekday": 3},
                "2026-09-10",
                "2027-09-09",
            ),
            (
                "FICTIONAL_LUNAR_WINDOW",
                {"type": "lunar_yearly", "month": 9, "day": 2, "leap_month": False},
                "2026-10-11",
                lunar_to_solar(2027, 9, 2).isoformat(),
            ),
            (
                "FICTIONAL_A_SHARE_WINDOW",
                {"type": "cn_a_share_on_or_after", "day": 5},
                "2026-10-08",
                "2026-11-05",
            ),
        ]
        for title, action_spec, first_action, next_action in cases:
            with self.subTest(reminder=title):
                reminder = self.create(title, action_spec, {"type": "offset_days", "days": 2})
                item = self.service.get_reminder(reminder.id)
                self.assertEqual(item.next_action_date, first_action)
                self.assertEqual(
                    item.next_deadline_date,
                    (date.fromisoformat(first_action) + timedelta(days=2)).isoformat(),
                )
                self.service.complete_occurrence(reminder.id)
                item = self.service.get_reminder(reminder.id)
                self.assertEqual(item.next_action_date, next_action)
                self.assertEqual(
                    item.next_deadline_date,
                    (date.fromisoformat(next_action) + timedelta(days=2)).isoformat(),
                )
                self.assertNotEqual(item.next_deadline_date, "2026-09-12")

    def test_label_management_rules_and_table_trash_semantics(self) -> None:
        default = self.service.default_label()
        renamed_default = self.service.rename_label(default.id, "默认提醒")
        self.assertEqual(renamed_default.id, default.id)
        custom = self.service.create_label("网络维护")
        other = self.service.create_label("订阅续费")
        table = self.service.create_table("FICTIONAL_LABEL_TABLE", label_id=custom.id)
        self.service.create_reminder(
            ReminderDraft(
                title="FICTIONAL_LABEL_ITEM",
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "one_time", "date": "2026-09-10"},
                table_id=table.id,
            )
        )
        labels = self.service.reorder_labels([other.id, renamed_default.id, custom.id])
        self.assertEqual([label.id for label in labels], [other.id, renamed_default.id, custom.id])
        renamed = self.service.rename_label(custom.id, "网络维护改名")
        self.assertEqual(renamed.name, "网络维护改名")
        with self.assertRaisesRegex(ValueError, "default_reminder_label_cannot_be_deleted"):
            self.service.delete_label(renamed_default.id)
        self.service.delete_label(custom.id)
        self.assertNotIn(custom.id, [label.id for label in self.service.list_labels()])
        self.assertIsNone(self.service.get_table(table.id))
        trash = self.service.list_trash()
        self.assertIn(table.id, [item.table.id for item in trash.tables])
        trashed_table = next(item for item in trash.tables if item.table.id == table.id)
        self.assertEqual(trashed_table.item_count, 1)

    def test_no_deadline_reminder_still_notifies_and_completes(self) -> None:
        reminder = self.create(
            "FICTIONAL_NO_DEADLINE",
            {"type": "one_time", "date": "2026-09-10"},
            None,
        )
        item = self.service.list_reminders()[0]
        self.assertEqual(item.status, "due")
        self.assertIsNone(item.next_deadline_date)
        self.assertEqual(self.service.dispatch_due_notifications(), 1)
        self.assertIn("开始处理", self.transport.calls[0]["message"])
        self.service.complete_reminder(reminder.id)
        self.assertEqual(self.service.list_reminders()[0].status, "completed")

    def test_public_ntfy_default_is_not_used(self) -> None:
        self.repository.set_text_setting("reminder/ntfy_endpoint", "https://ntfy.sh")
        settings = self.service.settings()
        self.assertEqual(settings.ntfy_endpoint, "")

    def test_test_notification_uses_human_gate_copy(self) -> None:
        self.service.send_test_notification()
        self.assertEqual(self.transport.calls[-1]["title"], "S-notes Reminder")
        self.assertEqual(self.transport.calls[-1]["message"], "Reminder ntfy test OK")

    def test_nth_weekday(self) -> None:
        self.create(
            "FICTIONAL_NTH_WEEKDAY",
            {"type": "nth_weekday", "month": 5, "nth": 2, "weekday": "sunday"},
        )
        self.clock.set(datetime(2027, 1, 1, 10, 0, tzinfo=SHANGHAI))
        self.assertEqual(self.service.list_reminders()[0].next_action_date, "2027-05-09")

    def test_yearly_nth_weekday_for_parents_days(self) -> None:
        self.create(
            "FICTIONAL_FATHERS_DAY",
            {"type": "nth_weekday", "month": 6, "nth": 3, "weekday": 6},
        )
        self.create(
            "FICTIONAL_MOTHERS_DAY",
            {"type": "nth_weekday", "month": 5, "nth": 2, "weekday": 6},
        )
        items = {item.reminder.title: item for item in self.service.list_reminders()}
        self.assertEqual(items["FICTIONAL_FATHERS_DAY"].next_action_date, "2027-06-20")
        self.assertEqual(items["FICTIONAL_MOTHERS_DAY"].next_action_date, "2027-05-09")

    def test_nth_weekday_skips_months_without_that_slot(self) -> None:
        self.create(
            "FICTIONAL_FIFTH_MONDAY",
            {"type": "nth_weekday", "nth": 5, "weekday": 0},
        )
        self.assertEqual(self.service.list_reminders()[0].next_action_date, "2026-11-30")

    def test_s_catalog_every_required_schedule_is_expressible(self) -> None:
        # 逐条覆盖产品清单：账单、保号、生日、A股定投都必须能算出正确日期。
        expectations = {
            "FICTIONAL_GEMINI_FAMILY": (
                {"type": "monthly_day", "day": 7, "repeat_until": "2027-02-07"},
                {"type": "monthly_day", "day": 7},
                "2026-10-07",
                "2026-10-07",
            ),
            "FICTIONAL_QQPW_HOME": (
                {"type": "monthly_day", "day": 10},
                {"type": "monthly_day", "day": 15},
                "2026-09-10",
                "2026-09-15",
            ),
            "FICTIONAL_VMISS_TRI": (
                {"type": "monthly_day", "day": 12},
                {"type": "monthly_day", "day": 21},
                "2026-09-12",
                "2026-09-21",
            ),
            "FICTIONAL_GPT_BUSINESS": (
                {"type": "monthly_day", "day": 22},
                {"type": "monthly_day", "day": 29},
                "2026-09-22",
                "2026-09-29",
            ),
            "FICTIONAL_BWG_TEARDOWN": (
                {"type": "one_time", "date": "2026-09-11"},
                {"type": "one_time", "date": "2026-09-18"},
                "2026-09-11",
                "2026-09-18",
            ),
            "FICTIONAL_REDPOCKET_RENEWAL": (
                {"type": "one_time", "date": "2027-06-03"},
                None,
                "2027-06-03",
                None,
            ),
            "FICTIONAL_CTEXCEL_KEEPALIVE": (
                {
                    "type": "interval_from_completion",
                    "anchor_date": "2026-08-05",
                    "days": 90,
                },
                None,
                "2026-11-03",
                None,
            ),
            "FICTIONAL_DAD_LUNAR_BIRTHDAY": (
                {"type": "lunar_yearly", "month": 9, "day": 2, "leap_month": False},
                None,
                "2026-10-11",
                None,
            ),
            "FICTIONAL_MOM_LUNAR_BIRTHDAY": (
                {"type": "lunar_yearly", "month": 7, "day": 4, "leap_month": False},
                None,
                "2027-08-05",
                None,
            ),
            "FICTIONAL_A_SHARE_ETF": (
                {"type": "cn_a_share_on_or_after", "day": 5},
                None,
                "2026-10-08",
                None,
            ),
        }
        for title, (action_spec, deadline_spec, _a, _d) in expectations.items():
            self.create(title, action_spec, deadline_spec)
        items = {item.reminder.title: item for item in self.service.list_reminders()}
        for title, (_spec, _deadline, action, deadline) in expectations.items():
            with self.subTest(reminder=title):
                self.assertEqual(items[title].next_action_date, action)
                self.assertEqual(items[title].next_deadline_date, deadline)

    def test_lunar_yearly_conversion_known_fixture_years(self) -> None:
        self.assertEqual(lunar_to_solar(2025, 1, 1), date(2025, 1, 29))
        self.assertEqual(lunar_to_solar(2026, 1, 1), date(2026, 2, 17))
        self.assertEqual(lunar_to_solar(2026, 8, 15), date(2026, 9, 25))
        self.assertEqual(lunar_to_solar(2025, 6, 1), date(2025, 6, 25))
        self.assertEqual(lunar_to_solar(2050, 1, 1), date(2050, 1, 23))
        self.assertEqual(
            lunar_to_solar(2025, 6, 1, leap_month=True),
            date(2025, 7, 25),
        )
        self.create(
            "FICTIONAL_LUNAR_BIRTHDAY",
            {"type": "lunar_yearly", "month": 9, "day": 2, "leap_month": False},
        )
        self.assertRegex(self.service.list_reminders()[0].next_action_date or "", r"2026-\d\d-\d\d")

    def test_lunar_year_after_embedded_table_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "lunar_year_out_of_supported_range"):
            lunar_to_solar(2051, 1, 1)
        self.clock.set(datetime(2051, 1, 1, 10, 0, tzinfo=SHANGHAI))
        self.create(
            "FICTIONAL_LUNAR_OUT_OF_RANGE",
            {"type": "lunar_yearly", "month": 1, "day": 1, "leap_month": False},
        )
        with self.assertRaisesRegex(ValueError, "lunar_year_out_of_supported_range"):
            self.service.list_reminders()

    def test_a_share_day_five_or_next_actual_trading_day(self) -> None:
        self.assertFalse(is_cn_a_share_trading_day(date(2026, 5, 5)))
        self.assertTrue(is_cn_a_share_trading_day(date(2026, 5, 6)))
        self.clock.set(datetime(2026, 5, 1, 10, 0, tzinfo=SHANGHAI))
        self.create(
            "FICTIONAL_MARKET_DAY",
            {"type": "cn_a_share_on_or_after", "day": 5},
        )
        self.assertEqual(self.service.list_reminders()[0].next_action_date, "2026-05-06")

    def test_a_share_unsupported_future_year_fails_closed(self) -> None:
        with self.assertRaisesRegex(
            ValueError,
            "cn_a_share_calendar_year_not_supported:2027",
        ):
            is_cn_a_share_trading_day(date(2027, 1, 5))
        self.clock.set(datetime(2027, 1, 1, 10, 0, tzinfo=SHANGHAI))
        self.create(
            "FICTIONAL_MARKET_UNSUPPORTED_YEAR",
            {"type": "cn_a_share_on_or_after", "day": 5},
        )
        with self.assertRaisesRegex(
            ValueError,
            "cn_a_share_calendar_year_not_supported:2027",
        ):
            self.service.list_reminders()

    def test_asia_shanghai_independent_from_machine_timezone(self) -> None:
        self.clock.set(datetime(2026, 9, 9, 18, 30, tzinfo=ZoneInfo("UTC")))
        self.create("FICTIONAL_TZ", {"type": "one_time", "date": "2026-09-10"})
        self.assertEqual(self.service.badge_count(), 1)

    def test_ntfy_failure_remains_retryable_and_success_dedups(self) -> None:
        reminder = self.create("FICTIONAL_RETRY", {"type": "one_time", "date": "2026-09-10"})
        failing = FakeTransport(fail=True)
        failing_service = ReminderService(self.repository, transport=failing, clock=self.clock)
        with self.assertRaises(RuntimeError):
            failing_service.dispatch_due_notifications()
        self.assertIsNone(self.service.history(reminder.id)[0].action_notified_at)
        self.assertEqual(self.service.dispatch_due_notifications(), 1)
        self.assertEqual(self.service.dispatch_due_notifications(), 0)

    def test_scheduled_chinese_title_survives_http_header_encoding(self) -> None:
        self.enable_notifications()
        reminder = self.create("中文提醒", {"type": "one_time", "date": "2026-09-10"})
        service = ReminderService(
            self.repository,
            transport=NtfyReminderTransport("https://ntfy.invalid"),
            clock=self.clock,
        )
        requests = []

        def serialize_request(request, **kwargs):
            # Exercise stdlib's actual header encoder without opening a socket.
            connection = HTTPConnection("ntfy.invalid")
            connection.putrequest(request.get_method(), request.selector)
            for name, value in request.header_items():
                connection.putheader(name, value)
            with patch.object(connection, "send"):
                connection.endheaders(request.data)
            requests.append(request)
            response = MagicMock()
            response.__enter__.return_value.status = 200
            return response

        with patch("src.snotes.transport.reminder_ntfy.urlopen", side_effect=serialize_request):
            self.assertEqual(service.run_scheduled_once(), 1)
            self.assertEqual(service.run_scheduled_once(), 0)
        self.assertEqual(len(requests), 1)
        self.assertEqual(str(make_header(decode_header(requests[0].get_header("Title")))), "S-notes 提醒")
        self.assertIn("中文提醒", requests[0].data.decode("utf-8"))
        self.assertIsNotNone(service.history(reminder.id)[0].action_notified_at)

    def test_missed_action_recovery_and_deadline_compaction(self) -> None:
        self.clock.set(datetime(2026, 9, 1, 10, 0, tzinfo=SHANGHAI))
        self.create(
            "FICTIONAL_MISSED_ACTION",
            {"type": "monthly_day", "day": 10},
            {"type": "monthly_day", "day": 15},
        )
        self.clock.set(datetime(2026, 9, 12, 10, 0, tzinfo=SHANGHAI))
        self.assertEqual(self.service.dispatch_due_notifications(), 1)
        self.assertIn("开始处理", self.transport.calls[-1]["message"])

        self.temporary.cleanup()
        self.setUp()
        self.clock.set(datetime(2026, 9, 1, 10, 0, tzinfo=SHANGHAI))
        self.create(
            "FICTIONAL_COMPACTED",
            {"type": "monthly_day", "day": 10},
            {"type": "monthly_day", "day": 15},
        )
        self.clock.set(datetime(2026, 9, 16, 10, 0, tzinfo=SHANGHAI))
        self.assertEqual(self.service.dispatch_due_notifications(), 1)
        self.assertIn("最晚处理日", self.transport.calls[-1]["message"])

    def test_scheduled_window_and_no_real_network_in_unit_tests(self) -> None:
        self.enable_notifications()
        self.create("FICTIONAL_SCHEDULED", {"type": "one_time", "date": "2026-09-10"})
        self.clock.set(datetime(2026, 9, 10, 9, 59, tzinfo=SHANGHAI))
        self.assertEqual(self.service.run_scheduled_once(), 0)
        self.clock.set(datetime(2026, 9, 10, 10, 0, tzinfo=SHANGHAI))
        self.assertEqual(self.service.run_scheduled_once(), 1)
        self.assertEqual(self.service.run_scheduled_once(), 0)
        self.assertEqual(len(self.transport.calls), 1)

    def test_scheduled_helper_uses_shanghai_ten_am_with_hawaii_host(self) -> None:
        self.enable_notifications()
        self.clock.set(datetime(2026, 8, 23, 15, 59, tzinfo=ZoneInfo("Pacific/Honolulu")))
        self.create("FICTIONAL_SHANGHAI_WINDOW", {"type": "one_time", "date": "2026-08-24"})
        self.assertEqual(self.service.run_scheduled_once(), 0)
        self.assertEqual(self.transport.calls, [])

        self.clock.set(datetime(2026, 8, 23, 16, 0, tzinfo=ZoneInfo("Pacific/Honolulu")))
        self.assertEqual(self.service.run_scheduled_once(), 1)
        self.assertEqual(self.service.run_scheduled_once(), 0)
        self.assertEqual(len(self.transport.calls), 1)

    def test_same_day_created_after_ten_is_eligible_after_prior_poll(self) -> None:
        self.enable_notifications()
        self.clock.set(datetime(2026, 8, 23, 16, 30, tzinfo=ZoneInfo("Pacific/Honolulu")))
        self.repository.set_text_setting(
            ReminderService.LAST_SCHEDULED_DATE_KEY,
            "2026-08-24",
        )
        self.create("FICTIONAL_AFTER_TEN_SAME_DAY", {"type": "one_time", "date": "2026-08-24"})

        self.assertEqual(self.service.run_scheduled_once(), 1)
        self.assertEqual(self.service.run_scheduled_once(), 0)
        self.assertEqual(len(self.transport.calls), 1)

    def test_future_business_date_does_not_send_under_hawaii_host(self) -> None:
        self.enable_notifications()
        self.clock.set(datetime(2026, 8, 23, 16, 30, tzinfo=ZoneInfo("Pacific/Honolulu")))
        self.create("FICTIONAL_SHANGHAI_FUTURE_NOTIFY", {"type": "one_time", "date": "2026-08-25"})

        self.assertEqual(self.service.run_scheduled_once(), 0)
        self.assertEqual(self.transport.calls, [])

    def test_independent_reminder_helper_launch_agent_template(self) -> None:
        helper = Path(self.temporary.name) / "bin" / "snotes-reminder-helper"
        plist = desired_launch_agent(helper)
        self.assertEqual(plist["Label"], "com.snotes.reminder-helper")
        self.assertEqual(plist["StartInterval"], LAUNCH_AGENT_INTERVAL_SECONDS)
        self.assertEqual(plist["ProgramArguments"][-1], "--scheduled")
        destination = Path(self.temporary.name) / "LaunchAgents" / "com.snotes.reminder-helper.plist"
        write_launch_agent_template(destination, helper)
        self.assertTrue(destination.is_file())

    def test_helper_composition_uses_public_ntfy_default(self) -> None:
        root = Path(self.temporary.name) / "formal-root"
        service = compose_reminder_service(root)
        self.assertEqual(service._transport.endpoint, "https://ntfy.sh/")

    def test_migration_from_pre_reminder_schema_preserves_existing_content(self) -> None:
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.execute("DROP TABLE reminder_occurrences")
            connection.execute("DROP TABLE reminders")
            connection.execute(
                """
                INSERT INTO notes (
                    title, content_markdown, created_at, updated_at
                ) VALUES ('FICTIONAL_EXISTING_NOTE', 'safe', '2026-01-01', '2026-01-01')
                """
            )
            connection.commit()
        initialize_database(self.database_path)
        with closing(sqlite3.connect(self.database_path)) as connection:
            title = connection.execute("SELECT title FROM notes").fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertEqual(title, "FICTIONAL_EXISTING_NOTE")
        self.assertIn("reminders", tables)
        self.assertIn("reminder_occurrences", tables)

    def test_reminder_v1_migration_assigns_default_table_and_preserves_items(self) -> None:
        old_path = Path(self.temporary.name) / "old-reminder.db"
        initialize_database(old_path)
        with closing(sqlite3.connect(old_path)) as connection:
            connection.execute("DROP TABLE reminder_occurrences")
            connection.execute("DROP TABLE reminders")
            connection.execute(
                """
                CREATE TABLE reminders (
                    id INTEGER PRIMARY KEY,
                    title TEXT NOT NULL,
                    lifecycle TEXT NOT NULL DEFAULT 'active',
                    action_spec_json TEXT NOT NULL,
                    deadline_spec_json TEXT NOT NULL DEFAULT '',
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE reminder_occurrences (
                    id INTEGER PRIMARY KEY,
                    reminder_id INTEGER NOT NULL,
                    occurrence_key TEXT NOT NULL,
                    action_date TEXT NOT NULL,
                    deadline_date TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    completed_at TEXT,
                    action_notified_at TEXT,
                    deadline_notified_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(reminder_id, occurrence_key)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO reminders (
                    id, title, lifecycle, action_spec_json, deadline_spec_json,
                    note, created_at, updated_at
                ) VALUES (
                    42, 'FICTIONAL_OLD', 'active',
                    '{"date":"2026-09-10","type":"one_time"}',
                    '', 'FICTIONAL_NOTE', '2026-09-01T10:00:00+08:00',
                    '2026-09-01T10:00:00+08:00'
                )
                """
            )
            connection.execute(
                """
                INSERT INTO reminder_occurrences (
                    id, reminder_id, occurrence_key, action_date, status,
                    action_notified_at, created_at, updated_at
                ) VALUES (
                    99, 42, '2026-09-10', '2026-09-10', 'pending',
                    '2026-09-10T10:00:00+08:00',
                    '2026-09-01T10:00:00+08:00',
                    '2026-09-01T10:00:00+08:00'
                )
                """
            )
            connection.commit()

        connections = initialize_database(old_path)
        repository = SQLiteReminderRepository(
            connections,
            clock=lambda: datetime.fromtimestamp(self.clock(), SHANGHAI),
        )
        service = ReminderService(repository, transport=self.transport, clock=self.clock)
        item = service.list_reminders()[0]
        self.assertEqual(item.reminder.id, 42)
        self.assertEqual(item.table.title, "原有提醒")
        self.assertEqual(item.label.name, "未归类")
        self.assertTrue(item.table.ai_visible)
        self.assertEqual(item.reminder.table_id, item.table.id)
        occurrences = service.history(42)
        self.assertEqual(occurrences[0].id, 99)
        self.assertEqual(occurrences[0].action_notified_at, "2026-09-10T10:00:00+08:00")

    def test_tables_visibility_and_ai_boundaries(self) -> None:
        default_label = self.service.default_label()
        renamed = self.service.rename_label(default_label.id, "默认提醒")
        self.assertEqual(renamed.id, default_label.id)
        custom = self.service.create_label("网络维护")
        table = self.service.create_table(
            "FICTIONAL_PRIVATE_TABLE",
            label_id=custom.id,
            ai_visible=False,
        )
        reminder = self.service.create_reminder(
            ReminderDraft(
                title="FICTIONAL_HIDDEN",
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "one_time", "date": "2026-09-10"},
                table_id=table.id,
            )
        )
        self.assertEqual(self.service.list_reminders(ReminderQuery(ai_access=True)), [])
        self.assertIsNone(self.service.get_reminder(reminder.id, ai_access=True))
        result = self.service.ai_update_item(
            actor_target_id="gale",
            request_id="fictional-hidden-update",
            item_id=reminder.id,
            title="FICTIONAL_SHOULD_NOT_WRITE",
        )
        self.assertEqual(result["status"], "not_found")
        self.assertEqual(
            self.service.get_reminder(reminder.id).reminder.title,
            "FICTIONAL_HIDDEN",
        )

    def test_ai_create_and_complete_are_idempotent_and_logged(self) -> None:
        label_result = self.service.ai_create_label(
            actor_target_id="gale",
            request_id="fictional-label-request",
            name="订阅续费",
        )
        self.assertEqual(label_result["status"], "created")
        retry_label = self.service.ai_create_label(
            actor_target_id="gale",
            request_id="fictional-label-request",
            name="订阅续费",
        )
        self.assertEqual(retry_label["status"], "already_processed")
        label = next(row for row in self.service.list_labels() if row.name == "订阅续费")
        table_result = self.service.ai_create_table(
            actor_target_id="gale",
            request_id="fictional-create-table",
            title="FICTIONAL_AI_TABLE",
            label_id=label.id,
        )
        table_id = int(table_result["table_id"])
        create_result = self.service.ai_create_item(
            actor_target_id="gale",
            request_id="fictional-create-reminder",
            table_id=table_id,
            draft=ReminderDraft(
                title="FICTIONAL_RETRY_SAFE",
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "monthly_day", "day": 10},
                table_id=table_id,
            ),
        )
        retry_create = self.service.ai_create_item(
            actor_target_id="gale",
            request_id="fictional-create-reminder",
            table_id=table_id,
            draft=ReminderDraft(
                title="FICTIONAL_RETRY_SAFE",
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "monthly_day", "day": 10},
                table_id=table_id,
            ),
        )
        self.assertEqual(create_result["status"], "created")
        self.assertEqual(retry_create["status"], "already_processed")
        self.assertEqual(
            len([item for item in self.service.list_reminders() if item.reminder.title == "FICTIONAL_RETRY_SAFE"]),
            1,
        )
        reminder_id = int(create_result["item_id"])
        complete = self.service.ai_complete_item(
            actor_target_id="gale",
            request_id="fictional-complete-occurrence",
            item_id=reminder_id,
        )
        first_next = self.service.get_reminder(reminder_id).next_action_date
        retry_complete = self.service.ai_complete_item(
            actor_target_id="gale",
            request_id="fictional-complete-occurrence",
            item_id=reminder_id,
        )
        second_next = self.service.get_reminder(reminder_id).next_action_date
        self.assertEqual(complete["status"], "updated")
        self.assertEqual(retry_complete["status"], "already_processed")
        self.assertEqual(first_next, second_next)
        page = self.service.list_ai_operation_logs(offset=0, limit=2)
        self.assertEqual(len(page.rows), 2)
        self.assertTrue(page.has_more)

    def test_ai_relative_days_create_uses_existing_schedule_owner(self) -> None:
        table = self.service.create_table("FICTIONAL_VISIBLE_RELATIVE_TABLE")
        result = self.service.ai_create_item(
            actor_target_id="clawbot",
            request_id="fictional-relative-days-create",
            table_id=table.id,
            draft=ReminderDraft(
                title="FICTIONAL_RELATIVE_DAYS",
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "relative", "unit": "days", "amount": 3},
                table_id=table.id,
            ),
        )
        retry = self.service.ai_create_item(
            actor_target_id="clawbot",
            request_id="fictional-relative-days-create",
            table_id=table.id,
            draft=ReminderDraft(
                title="FICTIONAL_RELATIVE_DAYS",
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "relative", "unit": "days", "amount": 3},
                table_id=table.id,
            ),
        )
        self.assertEqual(result["status"], "created")
        self.assertEqual(retry["status"], "already_processed")
        self.assertEqual(
            len(
                [
                    item
                    for item in self.service.list_reminders()
                    if item.reminder.title == "FICTIONAL_RELATIVE_DAYS"
                ]
            ),
            1,
        )
        item = self.service.get_reminder(int(result["item_id"]))
        self.assertIsNotNone(item)
        self.assertEqual(
            item.reminder.action_spec,
            {"type": "one_time", "date": "2026-09-13"},
        )
        self.assertIsNone(item.reminder.deadline_spec)

        hidden = self.service.create_table(
            "FICTIONAL_HIDDEN_RELATIVE_TABLE",
            ai_visible=False,
        )
        hidden_result = self.service.ai_create_item(
            actor_target_id="clawbot",
            request_id="fictional-relative-hidden-create",
            table_id=hidden.id,
            draft=ReminderDraft(
                title="FICTIONAL_HIDDEN_RELATIVE_DAYS",
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "relative", "unit": "days", "amount": 3},
                table_id=hidden.id,
            ),
        )
        self.assertEqual(hidden_result["status"], "not_found")
        self.assertIsNone(
            next(
                (
                    item
                    for item in self.service.list_reminders()
                    if item.reminder.title == "FICTIONAL_HIDDEN_RELATIVE_DAYS"
                ),
                None,
            )
        )

    def test_ai_subday_relative_create_rejects_before_insert(self) -> None:
        table = self.service.create_table("FICTIONAL_SUBDAY_REJECT_TABLE")
        before = len(self.service.list_reminders())
        for unit in ("minutes", "hours"):
            with self.subTest(unit=unit):
                with self.assertRaisesRegex(ValueError, f"unsupported_relative_reminder_unit:{unit}"):
                    self.service.ai_create_item(
                        actor_target_id="clawbot",
                        request_id=f"fictional-subday-{unit}-create",
                        table_id=table.id,
                        draft=ReminderDraft(
                            title=f"FICTIONAL_SUBDAY_{unit.upper()}",
                            lifecycle=REMINDER_ACTIVE,
                            action_spec={"type": "relative", "unit": unit, "amount": 2},
                            table_id=table.id,
                        ),
                    )
                self.assertEqual(len(self.service.list_reminders()), before)

    def test_ai_create_validates_schedule_before_insert(self) -> None:
        table = self.service.create_table("FICTIONAL_VALIDATE_BEFORE_INSERT")
        before = len(self.service.list_reminders())
        with self.assertRaisesRegex(ValueError, "unsupported_reminder_schedule:notify"):
            self.service.ai_create_item(
                actor_target_id="clawbot",
                request_id="fictional-invalid-notify-create",
                table_id=table.id,
                draft=ReminderDraft(
                    title="FICTIONAL_INVALID_NOTIFY",
                    lifecycle=REMINDER_ACTIVE,
                    action_spec={"type": "notify"},
                    table_id=table.id,
                ),
            )
        self.assertEqual(len(self.service.list_reminders()), before)

    def test_invalid_historical_schedule_row_does_not_break_reads(self) -> None:
        table = self.service.create_table("FICTIONAL_POISONED_TABLE")
        valid = self.create(
            "FICTIONAL_VALID_AFTER_POISON",
            {"type": "one_time", "date": "2026-09-10"},
        )
        timestamp = "2026-09-10T10:00:00+08:00"
        with self.connections.write() as connection:
            connection.execute(
                """
                INSERT INTO reminders (
                    table_id, title, lifecycle, action_spec_json,
                    deadline_spec_json, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    table.id,
                    "FICTIONAL_POISON_NOTIFY",
                    REMINDER_ACTIVE,
                    '{"type":"notify"}',
                    "",
                    "",
                    timestamp,
                    timestamp,
                ),
            )
        items = self.service.list_reminders()
        self.assertIn(valid.id, {item.reminder.id for item in items})
        self.assertNotIn(
            "FICTIONAL_POISON_NOTIFY",
            {item.reminder.title for item in items},
        )
        tables = self.service.list_tables()
        poisoned = next(item for item in tables if item.table.id == table.id)
        self.assertEqual(poisoned.item_count, 0)

    def test_ai_create_rejects_same_request_id_with_different_payload(self) -> None:
        table = self.service.create_table("FICTIONAL_FINGERPRINT_TABLE")
        created = self.service.ai_create_item(
            actor_target_id="clawbot",
            request_id="fictional-fingerprint-create",
            table_id=table.id,
            draft=ReminderDraft(
                title="FICTIONAL_FINGERPRINT",
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "one_time", "date": "2026-09-10"},
                table_id=table.id,
            ),
        )
        retry = self.service.ai_create_item(
            actor_target_id="clawbot",
            request_id="fictional-fingerprint-create",
            table_id=table.id,
            draft=ReminderDraft(
                title="FICTIONAL_FINGERPRINT",
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "one_time", "date": "2026-09-10"},
                table_id=table.id,
            ),
        )
        mismatch = self.service.ai_create_item(
            actor_target_id="clawbot",
            request_id="fictional-fingerprint-create",
            table_id=table.id,
            draft=ReminderDraft(
                title="FICTIONAL_FINGERPRINT",
                lifecycle=REMINDER_ACTIVE,
                action_spec={"type": "one_time", "date": "2026-09-11"},
                table_id=table.id,
            ),
        )
        self.assertEqual(created["status"], "created")
        self.assertEqual(retry["status"], "already_processed")
        self.assertEqual(mismatch["status"], "rejected")
        self.assertIn("request_id", mismatch["reason"])
        self.assertEqual(
            len([item for item in self.service.list_reminders() if item.reminder.title == "FICTIONAL_FINGERPRINT"]),
            1,
        )


if __name__ == "__main__":
    unittest.main()
