"""Reminder 3.5-0 only; all storage and transports are fictional."""
from dataclasses import replace
from datetime import date, datetime, timedelta
import unittest

from tests import test_reminder_r1 as r1
from src.snotes.domain.reminders import ReminderDraft, REMINDER_ACTIVE
from src.snotes.storage.schema import initialize_database


class Reminder350Tests(unittest.TestCase):
    setUp = r1.ReminderR1Tests.setUp
    tearDown = r1.ReminderR1Tests.tearDown

    def create(self, *, multi=False, mode=None, kind="interval_days", deadline=None):
        return self.service.create_reminder(ReminderDraft(
            "FICTIONAL ctexcel", REMINDER_ACTIVE,
            {"type": kind, "anchor_date": "2026-10-01", "days": 90},
            deadline_spec=deadline, multi_notify=multi, recurrence_mode=mode,
        ))

    def at(self, value):
        self.clock.set(datetime.fromisoformat(value + "T10:00:00+08:00"))

    def change(self, reminder, **fields):
        draft = ReminderDraft(
            reminder.title, reminder.lifecycle, reminder.action_spec,
            reminder.deadline_spec, reminder.note, reminder.table_id,
            reminder.multi_notify, reminder.recurrence_mode,
        )
        return self.service.update_reminder(reminder.id, replace(draft, **fields))

    def test_four_nodes_dedupe_restart_toggle_and_no_overdue_chase(self):
        reminder = self.create(multi=True, deadline={"type": "offset_days", "days": 2})
        occurrence_id = self.service.get_reminder(reminder.id).occurrence.id
        for day in ("2026-09-26", "2026-09-30", "2026-10-02", "2026-10-03"):
            self.at(day)
            self.assertEqual(self.service.run_scheduled_once(), 1)
            self.assertEqual(self.service.run_scheduled_once(), 0)
            self.assertEqual(self.service.get_reminder(reminder.id).occurrence.id, occurrence_id)
            # Persisted marks survive reopening and switch changes.
            initialize_database(self.database_path)
            reminder = self.change(reminder, multi_notify=False)
            reminder = self.change(reminder, multi_notify=True)
            self.assertEqual(self.service.dispatch_due_notifications(), 0)
        self.assertEqual(len(self.transport.calls), 4)
        for day in ("2026-10-04", "2026-10-20", "2027-02-01"):
            self.at(day)
            self.assertEqual(self.service.dispatch_due_notifications(), 0)
            self.assertEqual(self.service.badge_count(), 1)
            self.assertEqual(self.service.get_reminder(reminder.id).occurrence.id, occurrence_id)

    def test_deadline_first_and_start_fallback_exact_nodes(self):
        for deadline in ({"type": "one_time", "date": "2026-10-03"}, None):
            for multi in (True, False):
                with self.subTest(deadline=deadline, multi=multi):
                    self.at("2026-08-01")
                    reminder = self.service.create_reminder(ReminderDraft(
                        "FICTIONAL anchor", REMINDER_ACTIVE,
                        {"type": "one_time", "date": "2026-08-27"},
                        deadline_spec=deadline, multi_notify=multi,
                    ))
                    anchor = date(2026, 10, 3) if deadline and multi else date(2026, 8, 27)
                    expected = [(anchor - timedelta(days=n)).isoformat()
                                for n in ((7, 3, 1, 0) if multi else (0,))]
                    actual = []
                    day = date(2026, 8, 19)
                    while day <= date(2026, 10, 5):
                        self.at(day.isoformat())
                        if self.service.dispatch_due_notifications():
                            actual.append(day.isoformat())
                        self.assertEqual(self.service.dispatch_due_notifications(), 0)
                        day += timedelta(days=1)
                    self.assertEqual(actual, expected)
                    if deadline and multi:
                        self.assertEqual(actual, ["2026-09-26", "2026-09-30", "2026-10-02", "2026-10-03"])
                    self.service.complete_occurrence(reminder.id)

    def test_off_normal_node_only_and_badge_until_completion(self):
        reminder = self.create(deadline={"type": "same_day"})
        for day in ("2026-09-24", "2026-09-28", "2026-09-30"):
            self.at(day)
            self.assertEqual(self.service.dispatch_due_notifications(), 0)
        self.at("2026-10-01")
        self.assertEqual(self.service.dispatch_due_notifications(), 1)
        self.assertEqual(self.service.dispatch_due_notifications(), 0)
        self.at("2026-10-15")
        self.assertEqual(self.service.dispatch_due_notifications(), 0)
        self.assertEqual(self.service.badge_count(), 1)
        self.service.complete_occurrence(reminder.id)
        self.assertEqual(self.service.badge_count(), 0)

    def test_missed_advance_nodes_are_not_sent_late(self):
        self.create(multi=True)
        self.at("2026-09-29")
        self.assertEqual(self.service.dispatch_due_notifications(), 0)
        self.at("2026-09-30")
        self.assertEqual(self.service.dispatch_due_notifications(), 1)

    def test_failed_advance_node_can_retry_success_once(self):
        reminder = self.create(multi=True)
        self.at("2026-09-24")
        self.transport.fail = True
        with self.assertRaises(RuntimeError):
            self.service.dispatch_due_notifications()
        self.assertIsNone(self.service.history(reminder.id)[0].advance_7_notified_at)
        self.transport.fail = False
        self.assertEqual(self.service.dispatch_due_notifications(), 1)
        self.assertEqual(self.service.dispatch_due_notifications(), 0)

    def test_overlapping_dispatch_skips_then_uses_persisted_dedupe(self):
        self.create(multi=True)
        self.at("2026-09-24")
        with self.repository.notification_dispatch_lock() as acquired:
            self.assertTrue(acquired)
            self.assertEqual(self.service.dispatch_due_notifications(), 0)
        self.assertEqual(self.service.dispatch_due_notifications(), 1)
        self.assertEqual(self.service.dispatch_due_notifications(), 0)

    def test_90_days_early_completion_both_modes(self):
        for mode, expected in (("due_date", "2026-12-30"), ("completion_date", "2026-12-24")):
            with self.subTest(mode=mode):
                reminder = self.create(mode=mode)
                self.assertEqual(self.service.get_reminder(reminder.id).next_action_date, "2026-10-01")
                self.at("2026-09-25")
                self.service.complete_occurrence(reminder.id)
                self.assertEqual(self.service.get_reminder(reminder.id).next_action_date, expected)

    def test_next_cycle_advance_nodes_and_early_completion(self):
        reminder = self.create(multi=True, mode="completion_date")
        self.at("2026-09-25")
        self.service.complete_occurrence(reminder.id)
        self.at("2026-12-17")
        item = self.service.get_reminder(reminder.id)
        self.assertEqual(item.status, "upcoming")
        self.assertEqual(item.next_action_date, "2026-12-24")
        self.assertEqual(self.service.dispatch_due_notifications(), 1)
        self.assertEqual(self.service.dispatch_due_notifications(), 0)
        self.service.complete_occurrence(reminder.id)
        self.assertEqual(self.service.get_reminder(reminder.id).next_action_date, "2027-03-17")
        self.assertEqual(sum(o.status == "completed" for o in self.service.history(reminder.id)), 2)

    def test_mode_switch_keeps_current_occurrence_and_its_notification_marks(self):
        reminder = self.create(multi=True, mode="due_date")
        self.at("2026-09-24")
        self.service.dispatch_due_notifications()
        before = self.service.get_reminder(reminder.id).occurrence
        reminder = self.change(reminder, recurrence_mode="completion_date")
        self.assertEqual(self.service.get_reminder(reminder.id).occurrence, before)
        self.at("2026-09-25")
        self.service.complete_occurrence(reminder.id)
        self.assertEqual(self.service.get_reminder(reminder.id).next_action_date, "2026-12-24")
        # A mode-only edit cannot move an already materialized next occurrence.
        reminder = self.change(reminder, recurrence_mode="due_date")
        self.assertEqual(self.service.get_reminder(reminder.id).next_action_date, "2026-12-24")

    def test_additive_upgrade_preserves_dates_specs_history_and_legacy_modes(self):
        old = [self.create(kind=kind) for kind in ("interval_days", "interval_from_completion")]
        before = [self.service.get_reminder(r.id) for r in old]
        with self.connections.write() as connection:
            for column in ("multi_notify", "recurrence_mode"):
                connection.execute(f"ALTER TABLE reminders DROP COLUMN {column}")
            for days in (7, 3, 1):
                connection.execute(f"ALTER TABLE reminder_occurrences DROP COLUMN advance_{days}_notified_at")
        initialize_database(self.database_path)
        initialize_database(self.database_path)
        after = [self.service.get_reminder(r.id) for r in old]
        for original, upgraded in zip(before, after):
            self.assertEqual(original.reminder, upgraded.reminder)
            self.assertEqual(original.occurrence, upgraded.occurrence)
            self.assertEqual(original.next_action_date, upgraded.next_action_date)
        self.assertEqual([i.next_action_date for i in after], ["2026-10-01", "2026-12-30"])
        self.assertEqual([i.reminder.effective_recurrence_mode for i in after], ["due_date", "completion_date"])
        for item in after:
            self.assertFalse(item.reminder.multi_notify)
            self.assertIsNone(item.reminder.recurrence_mode)
        self.at("2026-09-25")
        for r, expected in zip(old, ("2026-12-30", "2027-03-24")):
            self.service.complete_occurrence(r.id)
            self.assertEqual(self.service.get_reminder(r.id).next_action_date, expected)

    def test_completion_mode_uses_shanghai_day_and_repeat_until(self):
        reminder = self.create(mode="completion_date")
        self.clock.set(datetime.fromisoformat("2026-09-24T20:00:00-07:00"))
        self.service.complete_occurrence(reminder.id)
        self.assertEqual(self.service.get_reminder(reminder.id).next_action_date, "2026-12-24")
        capped = self.create(mode="completion_date")
        capped = self.change(capped, action_spec={**capped.action_spec, "repeat_until": "2026-12-23"})
        self.service.complete_occurrence(capped.id)
        self.assertIsNone(self.service.get_reminder(capped.id).next_action_date)
