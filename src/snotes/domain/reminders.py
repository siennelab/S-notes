"""Pure Reminder R1 data objects and constants."""

from __future__ import annotations

from dataclasses import dataclass


DEFAULT_REMINDER_LABEL_KEY = "default"
DEFAULT_REMINDER_LABEL_NAME = "未归类"
DEFAULT_REMINDER_TABLE_KEY = "default"
DEFAULT_REMINDER_TABLE_TITLE = "原有提醒"
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
    "deleted": "回收站",
}


@dataclass(frozen=True, slots=True)
class Reminder:
    id: int
    table_id: int
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
    table_id: int | None = None


@dataclass(frozen=True, slots=True)
class ReminderLabel:
    id: int
    label_key: str
    name: str
    sort_order: int
    is_deleted: bool
    created_at: str
    updated_at: str
    deleted_at: str | None = None

    @property
    def is_default(self) -> bool:
        return self.label_key == DEFAULT_REMINDER_LABEL_KEY


@dataclass(frozen=True, slots=True)
class ReminderTable:
    id: int
    table_key: str
    title: str
    label_id: int
    ai_visible: bool
    sort_order: int
    is_deleted: bool
    created_at: str
    updated_at: str
    deleted_at: str | None = None
    trash_group_id: str | None = None

    @property
    def is_default(self) -> bool:
        return self.table_key == DEFAULT_REMINDER_TABLE_KEY


@dataclass(frozen=True, slots=True)
class ReminderQuery:
    query: str = ""
    table_id: int | None = None
    label_id: int | None = None
    ai_visibility: str = "all"
    lifecycle: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    include_deleted_tables: bool = False
    ai_access: bool = False


@dataclass(frozen=True, slots=True)
class ReminderListItem:
    reminder: Reminder
    table: ReminderTable
    label: ReminderLabel
    occurrence: ReminderOccurrence | None
    status: str
    next_action_date: str | None
    next_deadline_date: str | None

    @property
    def status_label(self) -> str:
        return REMINDER_STATUS_LABELS[self.status]


@dataclass(frozen=True, slots=True)
class ReminderTableListItem:
    table: ReminderTable
    label: ReminderLabel
    item_count: int
    matching_item_id: int | None = None
    match_count: int = 0


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


@dataclass(frozen=True, slots=True)
class ReminderTrashSnapshot:
    labels: tuple[ReminderLabel, ...]
    tables: tuple[ReminderTableListItem, ...]


@dataclass(frozen=True, slots=True)
class ReminderOperationLog:
    id: int
    happened_at: str
    actor_target_id: str
    action: str
    table_id: int | None
    table_title: str
    item_id: int | None
    item_title: str
    label_id: int | None
    label_name: str
    request_id: str
    changed_fields: tuple[str, ...]
    before_json: str
    after_json: str


@dataclass(frozen=True, slots=True)
class ReminderOperationPage:
    rows: tuple[ReminderOperationLog, ...]
    total: int
    offset: int
    limit: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.rows) < self.total
