"""Application owner for Reminder R1 lifecycle and notification decisions."""

from __future__ import annotations

import secrets
import time
from datetime import date, datetime, timedelta
from typing import Sequence

from src.snotes.application.ports import (
    ReminderNotificationTransport,
    ReminderRepository,
)
from src.snotes.domain.reminder_schedule import (
    deadline_for,
    describe_schedule,
    is_recurring,
    next_action_date,
    occurrence_key,
    today_shanghai,
)
from src.snotes.domain.reminders import (
    NOTIFICATION_ACTION,
    NOTIFICATION_DEADLINE,
    OCCURRENCE_COMPLETED,
    OCCURRENCE_PENDING,
    REMINDER_ACTIVE,
    REMINDER_COMPLETED,
    REMINDER_PAUSED,
    Reminder,
    ReminderDraft,
    ReminderListItem,
    ReminderNotification,
    ReminderOccurrence,
    ReminderSettings,
)
from src.snotes.domain.reminder_schedule import SHANGHAI


class ReminderService:
    LAST_SCHEDULED_DATE_KEY = "reminder/last_scheduled_date"
    LAST_TEST_TOPIC_KEY = "reminder/last_test_topic"

    def __init__(
        self,
        repository: ReminderRepository,
        *,
        transport: ReminderNotificationTransport | None = None,
        clock=time.time,
    ) -> None:
        self._repository = repository
        self._transport = transport
        self._clock = clock

    def create_reminder(self, draft: ReminderDraft) -> Reminder:
        return self._repository.create(self._clean_draft(draft))

    def update_reminder(self, reminder_id: int, draft: ReminderDraft) -> Reminder:
        existing = self._required(reminder_id)
        cleaned = self._clean_draft(draft)
        updated = self._repository.update(reminder_id, cleaned)
        if (
            existing.action_spec != updated.action_spec
            or existing.deadline_spec != updated.deadline_spec
        ):
            self._repository.delete_pending_occurrences(reminder_id)
        return updated

    def delete_reminder(self, reminder_id: int) -> None:
        self._repository.delete(reminder_id)

    def pause_reminder(self, reminder_id: int) -> None:
        self._repository.set_lifecycle(reminder_id, REMINDER_PAUSED)

    def resume_reminder(self, reminder_id: int) -> None:
        self._repository.set_lifecycle(reminder_id, REMINDER_ACTIVE)

    def complete_reminder(self, reminder_id: int) -> None:
        reminder = self._required(reminder_id)
        now = self._timestamp()
        item = self._list_item(reminder, today_shanghai(self._now_datetime))
        if item.occurrence is not None:
            self._repository.mark_occurrence_completed(item.occurrence.id, now)
        self._repository.set_lifecycle(reminder_id, REMINDER_COMPLETED, now)

    def complete_occurrence(self, reminder_id: int) -> None:
        reminder = self._required(reminder_id)
        now = self._timestamp()
        item = self._list_item(reminder, today_shanghai(self._now_datetime))
        if item.occurrence is None:
            return
        self._repository.mark_occurrence_completed(item.occurrence.id, now)

    def list_reminders(self) -> list[ReminderListItem]:
        today = today_shanghai(self._now_datetime)
        items = [
            self._list_item(reminder, today)
            for reminder in self._repository.list_active_records()
        ]
        return sorted(items, key=self._sort_key)

    def history(self, reminder_id: int) -> list[ReminderOccurrence]:
        return self._repository.list_occurrences(reminder_id)

    def badge_count(self) -> int:
        return sum(1 for item in self.list_reminders() if item.status in {"due", "overdue"})

    def settings(self) -> ReminderSettings:
        settings = self._repository.settings()
        endpoint = self._private_ntfy_endpoint(settings.ntfy_endpoint)
        normalized = ReminderSettings(
            settings.notifications_enabled,
            settings.daily_time or "10:00",
            endpoint,
            settings.ntfy_topic,
        )
        if not normalized.ntfy_topic:
            normalized = ReminderSettings(
                normalized.notifications_enabled,
                normalized.daily_time,
                normalized.ntfy_endpoint,
                f"snotes-reminder-{secrets.token_urlsafe(18)}",
            )
        if normalized != settings:
            self._repository.save_settings(normalized)
        return normalized

    @staticmethod
    def _private_ntfy_endpoint(endpoint: str) -> str:
        clean = (endpoint or "").strip().rstrip("/")
        if clean.casefold() in {"https://ntfy.sh", "http://ntfy.sh", "ntfy.sh"}:
            return ""
        return clean

    def save_settings(self, settings: ReminderSettings) -> None:
        self._repository.save_settings(settings)

    def send_test_notification(self) -> None:
        settings = self.settings()
        if self._transport is None:
            raise RuntimeError("reminder_notification_transport_not_configured")
        self._transport.send(
            topic=settings.ntfy_topic,
            title="S-notes Reminder 测试",
            message="FICTIONAL_REMINDER_TEST",
        )
        self._repository.set_text_setting(self.LAST_TEST_TOPIC_KEY, settings.ntfy_topic)

    def run_scheduled_once(self) -> int:
        settings = self.settings()
        if not settings.notifications_enabled:
            return 0
        if not self._notification_window_open(settings.daily_time):
            return 0
        today = today_shanghai(self._now_datetime)
        sent = self.dispatch_due_notifications(today)
        self._repository.set_text_setting(self.LAST_SCHEDULED_DATE_KEY, today.isoformat())
        return sent

    def dispatch_due_notifications(self, today: date | None = None) -> int:
        if self._transport is None:
            raise RuntimeError("reminder_notification_transport_not_configured")
        settings = self.settings()
        business_today = today or today_shanghai(self._now_datetime)
        sent = 0
        for notification in self.pending_notifications(business_today):
            self._transport.send(
                topic=settings.ntfy_topic,
                title=notification.title,
                message=notification.message,
            )
            self._repository.mark_notified(
                notification.occurrence_id,
                notification.kind,
                self._timestamp(),
            )
            sent += 1
        return sent

    def pending_notifications(self, today: date | None = None) -> list[ReminderNotification]:
        business_today = today or today_shanghai(self._now_datetime)
        notifications: list[ReminderNotification] = []
        for item in self.list_reminders():
            occurrence = item.occurrence
            if occurrence is None or item.status not in {"due", "overdue"}:
                continue
            action_date = date.fromisoformat(occurrence.action_date)
            deadline_date = (
                date.fromisoformat(occurrence.deadline_date)
                if occurrence.deadline_date
                else None
            )
            if deadline_date is not None and business_today >= deadline_date:
                if occurrence.deadline_notified_at:
                    continue
                notifications.append(
                    ReminderNotification(
                        item.reminder.id,
                        occurrence.id,
                        NOTIFICATION_DEADLINE,
                        "S-notes 提醒",
                        f"{item.reminder.title} 已超过最晚处理日。",
                    )
                )
                continue
            if business_today >= action_date and not occurrence.action_notified_at:
                notifications.append(
                    ReminderNotification(
                        item.reminder.id,
                        occurrence.id,
                        NOTIFICATION_ACTION,
                        "S-notes 提醒",
                        f"{item.reminder.title} 到了开始处理日期。",
                    )
                )
        return notifications

    def schedule_label(self, reminder: Reminder) -> str:
        return describe_schedule(reminder.action_spec)

    @staticmethod
    def _sort_key(item: ReminderListItem) -> tuple:
        reminder = item.reminder
        status_rank = {
            "overdue": 0,
            "due": 1,
            "upcoming": 2,
            "occurrence_completed": 2,
            "paused": 3,
            "completed": 4,
        }.get(item.status, 5)
        date_value = item.next_action_date or "9999-12-31"
        return (status_rank, date_value, reminder.title.casefold(), reminder.id)

    def _list_item(self, reminder: Reminder, today: date) -> ReminderListItem:
        if reminder.lifecycle == REMINDER_PAUSED:
            return ReminderListItem(reminder, None, "paused", None, None)
        if reminder.lifecycle == REMINDER_COMPLETED:
            return ReminderListItem(reminder, None, "completed", None, None)

        active = self._active_occurrence(reminder, today)
        if active is None:
            return ReminderListItem(reminder, None, "completed", None, None)

        if active.status == OCCURRENCE_COMPLETED:
            next_occurrence = self._next_occurrence_after_completion(reminder, active)
            return ReminderListItem(
                reminder,
                active,
                "occurrence_completed",
                next_occurrence.action_date if next_occurrence else None,
                next_occurrence.deadline_date if next_occurrence else None,
            )
        action = date.fromisoformat(active.action_date)
        deadline = date.fromisoformat(active.deadline_date) if active.deadline_date else None
        if today < action:
            status = "upcoming"
        elif deadline is not None and today > deadline:
            status = "overdue"
        else:
            status = "due"
        return ReminderListItem(reminder, active, status, active.action_date, active.deadline_date)

    def _active_occurrence(self, reminder: Reminder, today: date) -> ReminderOccurrence | None:
        completed = self._latest_completed(reminder.id)
        if completed is not None and is_recurring(reminder.action_spec):
            next_occurrence = self._next_occurrence_after_completion(
                reminder,
                completed,
            )
            if (
                next_occurrence is None
                or date.fromisoformat(next_occurrence.action_date) > today
            ):
                return completed
        current = self._current_uncompleted_occurrence(reminder)
        if current is not None and date.fromisoformat(current.action_date) <= today:
            return current
        if current is not None and not is_recurring(reminder.action_spec):
            return current
        if current is not None and date.fromisoformat(current.action_date) > today:
            return current
        completed_at = completed.completed_at if completed else None
        action = self._current_or_next_action_date(
            reminder,
            today,
            last_completed_at=completed_at,
        )
        if action is None:
            return None
        deadline = deadline_for(action, reminder.deadline_spec)
        return self._repository.upsert_occurrence(
            reminder.id,
            occurrence_key(action),
            action.isoformat(),
            deadline.isoformat() if deadline else None,
        )

    def _current_or_next_action_date(
        self,
        reminder: Reminder,
        today: date,
        *,
        last_completed_at: str | None,
    ) -> date | None:
        start = max(
            self._lookback_start(reminder.action_spec, today),
            self._created_date(reminder),
        )
        candidate = next_action_date(
            reminder.action_spec,
            on_or_after=start,
            last_completed_at=last_completed_at,
        )
        latest_due: date | None = None
        guard = 0
        while candidate is not None and candidate <= today and guard < 500:
            key = occurrence_key(candidate)
            existing = self._repository.get_occurrence(reminder.id, key)
            if existing is None or existing.status != OCCURRENCE_COMPLETED:
                latest_due = candidate
            next_start = candidate + timedelta(days=1)
            candidate = next_action_date(
                reminder.action_spec,
                on_or_after=next_start,
                last_completed_at=last_completed_at,
            )
            guard += 1
        return latest_due or candidate

    @staticmethod
    def _lookback_start(spec: dict, today: date) -> date:
        if spec.get("date"):
            return date.fromisoformat(str(spec["date"]))
        if spec.get("anchor_date"):
            return date.fromisoformat(str(spec["anchor_date"]))
        return date(max(1900, today.year - 2), 1, 1)

    @staticmethod
    def _created_date(reminder: Reminder) -> date:
        created = datetime.fromisoformat(reminder.created_at)
        if created.tzinfo is None:
            created = created.replace(tzinfo=SHANGHAI)
        return created.astimezone(SHANGHAI).date()

    def _current_uncompleted_occurrence(self, reminder: Reminder) -> ReminderOccurrence | None:
        pending = [
            occurrence
            for occurrence in self._repository.list_occurrences(reminder.id)
            if occurrence.status == OCCURRENCE_PENDING
        ]
        return min(pending, key=lambda item: item.action_date) if pending else None

    def _latest_completed(self, reminder_id: int) -> ReminderOccurrence | None:
        completed = [
            occurrence
            for occurrence in self._repository.list_occurrences(reminder_id)
            if occurrence.status == OCCURRENCE_COMPLETED and occurrence.completed_at
        ]
        return max(completed, key=lambda item: item.completed_at or "") if completed else None

    def _next_occurrence_after_completion(
        self,
        reminder: Reminder,
        occurrence: ReminderOccurrence,
    ) -> ReminderOccurrence | None:
        if not is_recurring(reminder.action_spec):
            return None
        after = date.fromisoformat(occurrence.action_date)
        if str(reminder.action_spec.get("type")) != "interval_from_completion":
            after = after + timedelta(days=1)
        action = next_action_date(
            reminder.action_spec,
            on_or_after=after,
            last_completed_at=occurrence.completed_at,
        )
        if action is None:
            return None
        deadline = deadline_for(action, reminder.deadline_spec)
        return self._repository.upsert_occurrence(
            reminder.id,
            occurrence_key(action),
            action.isoformat(),
            deadline.isoformat() if deadline else None,
        )

    def _required(self, reminder_id: int) -> Reminder:
        reminder = self._repository.get(reminder_id)
        if reminder is None:
            raise ValueError("reminder_not_found")
        return reminder

    def _timestamp(self) -> str:
        return self._now_datetime().isoformat(timespec="seconds")

    def _now_datetime(self) -> datetime:
        return datetime.fromtimestamp(self._clock(), SHANGHAI)

    def _notification_window_open(self, daily_time: str) -> bool:
        now = self._now_datetime()
        hour, minute = [int(part) for part in (daily_time or "10:00").split(":", 1)]
        if (now.hour, now.minute) < (hour, minute):
            return False
        return self._repository.get_text_setting(self.LAST_SCHEDULED_DATE_KEY) != now.date().isoformat()

    @staticmethod
    def _clean_draft(draft: ReminderDraft) -> ReminderDraft:
        title = draft.title.strip()
        if not title:
            raise ValueError("reminder_title_required")
        lifecycle = REMINDER_PAUSED if draft.lifecycle == REMINDER_PAUSED else REMINDER_ACTIVE
        return ReminderDraft(title, lifecycle, dict(draft.action_spec), dict(draft.deadline_spec) if draft.deadline_spec else None, draft.note.strip())
