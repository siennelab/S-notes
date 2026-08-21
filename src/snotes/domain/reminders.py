"""Pure Reminder R1 data objects and constants."""

from __future__ import annotations

from dataclasses import dataclass


REMINDER_TIMEZONE = "Asia/Shanghai"
REMINDER_ACTIVE = "active"
REMINDER_PAUSED = "paused"
REMINDER_COMPLETED = "completed"
REMINDER_DELETED = "deleted"

OCCURRENCE_PENDING = "pending"
OCCURRENCE_COMPLETED = "completed"

NOTIFICATION_ACTION = "action"
NOTIFICATION_DEADLINE = "deadline"

REMINDER_STATUS_LABELS = {
    "upcoming": "未到期",
    "due": "待处理",
    "overdue": "已逾期",
    "occurrence_completed": "本次已完成",
    "paused": "已暂停",
    "completed": "已完成",
}


@dataclass(frozen=True, slots=True)
class Reminder:
    id: int
    title: str
    lifecycle: str
    action_spec: dict
    deadline_spec: dict | None
    note: str
    created_at: str
    updated_at: str
    completed_at: str | None = None


@dataclass(frozen=True, slots=True)
class ReminderOccurrence:
    id: int
    reminder_id: int
    occurrence_key: str
    action_date: str
    deadline_date: str | None
    status: str
    completed_at: str | None
    action_notified_at: str | None
    deadline_notified_at: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ReminderDraft:
    title: str
    lifecycle: str
    action_spec: dict
    deadline_spec: dict | None = None
    note: str = ""


@dataclass(frozen=True, slots=True)
class ReminderListItem:
    reminder: Reminder
    occurrence: ReminderOccurrence | None
    status: str
    next_action_date: str | None
    next_deadline_date: str | None

    @property
    def status_label(self) -> str:
        return REMINDER_STATUS_LABELS[self.status]


@dataclass(frozen=True, slots=True)
class ReminderSettings:
    notifications_enabled: bool
    daily_time: str
    ntfy_endpoint: str
    ntfy_topic: str


@dataclass(frozen=True, slots=True)
class ReminderNotification:
    reminder_id: int
    occurrence_id: int
    kind: str
    title: str
    message: str

